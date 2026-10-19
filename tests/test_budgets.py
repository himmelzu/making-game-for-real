from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_budget_patch as budgets


def canonical(node):
    node = deepcopy(node)
    for child in node.iter():
        child.tail = None
        if child.text is not None and not child.text.strip():
            child.text = None
    return ET.tostring(node)


class BudgetPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT / "downloads/fm26-unified-data.zip") as archive:
            cls.source = archive.read("FM26_unified.xml")
        cls.output, cls.report = budgets.build(cls.source)

    def test_source_identity_and_determinism(self):
        self.assertEqual(hashlib.sha256(self.source).hexdigest(), budgets.SOURCE_SHA256)
        output, report = budgets.build(self.source)
        self.assertEqual(output, self.output)
        self.assertEqual(report, self.report)
        with self.assertRaisesRegex(ValueError, "Unexpected input XML"):
            budgets.build(self.source + b"\n")

    def test_all_500_non_deleted_participants_have_one_recurring_tier_rule(self):
        self.assertEqual(self.report["league_teams_covered_by_recurring_rule"], 500)
        self.assertEqual(self.report["league_team_counts"], {1: 20, 2: 20, 3: 60, 4: 120, 5: 240, 6: 40})
        root = ET.fromstring(self.output)
        chosen = budgets.funding_rules(root)
        for level, (_, comp) in chosen.items():
            payments = comp.findall("list[@id='appearance_money']")
            self.assertEqual(len(payments), 1)
            self.assertEqual(len(payments[0]), 1)
            payment = payments[0][0]
            self.assertEqual(budgets.value(payment, "cash"), str(budgets.ANNUAL_PAYMENT))
            self.assertEqual(budgets.value(payment, "type"), "each_team")
            date = payment.find("record[@id='payment_date']")
            self.assertIsNotNone(date.find("null[@id='day_of_week']"))
            self.assertEqual(budgets.value(date, "day_of_month"), "11")
            self.assertEqual(budgets.value(date, "month"), "aug")
            self.assertEqual(budgets.value(comp, "year_interval"), "1")
        # Deleted team-assignment records must not leak into the 500 count.
        source_root = ET.fromstring(self.source)
        db, _, _, roster = budgets.inspect_database(source_root)
        deleted = {budgets.value(record, "db_unique_id") for record in db
                   if budgets.value(record, "database_table_type") == "3"
                   and budgets.property_code(record) == "DELE"}
        self.assertTrue(deleted)
        self.assertFalse(deleted & {x["competition_assignment_record_uid"] for x in roster})

    def test_only_confirmed_initial_finance_keys_and_latest_values_are_changed(self):
        before, after = ET.fromstring(self.source), ET.fromstring(self.output)
        old_db, new_db = before.find("list[@id='db_changes']"), after.find("list[@id='db_changes']")
        self.assertEqual(len(old_db), 54726)
        self.assertEqual(len(new_db), 54726)
        expected = {(field["record_index"], code, row["finance_record_uid"]): field["after"]
                    for row in self.report["finance_changes"]
                    for code, field in row["fields"].items()}
        self.assertEqual(len(expected), 117)
        observed = set()
        for index, (old, new) in enumerate(zip(old_db, new_db)):
            if canonical(old) == canonical(new):
                continue
            code, uid = budgets.property_code(new), budgets.value(new, "db_unique_id")
            key = index, code, uid
            self.assertIn(key, expected)
            self.assertEqual(budgets.value(new, "database_table_type"), "3")
            self.assertEqual(budgets.value(new, "new_value"), str(expected[key]))
            self.assertEqual(old.find("*[@id='new_value']").tag, "integer")
            old.remove(old.find("*[@id='new_value']"))
            new.remove(new.find("*[@id='new_value']"))
            self.assertEqual(canonical(old), canonical(new))
            observed.add(key)
        self.assertEqual(observed, set(expected))
        # YG's confirmed original finance key is used, never its shifted
        # competition-assignment key. This guards the observed UID hazard.
        finance_keys = {row["finance_record_uid"] for row in self.report["finance_changes"]}
        self.assertIn("792971584703919109", finance_keys)
        self.assertNotIn("792971584703919110", finance_keys)
        self.assertEqual(self.report["initial_finance_clubs"], 39)

    def test_every_other_rule_and_record_is_preserved(self):
        before, after = ET.fromstring(self.source), ET.fromstring(self.output)
        old_db, new_db = before.find("list[@id='db_changes']"), after.find("list[@id='db_changes']")
        for row in self.report["finance_changes"]:
            for field in row["fields"].values():
                index = field["record_index"]
                old_db[index].find("*[@id='new_value']").set("value", str(field["after"]))
        old_files, new_files = before.find("list[@id='files']"), after.find("list[@id='files']")
        for payment in self.report["recurring_payment_changes"]:
            index = payment["file_index"]
            for comp in [old_files[index], new_files[index]]:
                money = comp.find("list[@id='appearance_money']")
                if money is not None:
                    comp.remove(money)
        self.assertEqual(canonical(before), canonical(after))

    def test_refuses_double_payment_and_changed_finance_schema_before_mutation(self):
        bad = ET.fromstring(self.source)
        _, comp = budgets.funding_rules(bad)[6]
        ET.SubElement(comp, "list", {"id": "appearance_money"})
        snapshot = canonical(bad)
        with self.assertRaisesRegex(ValueError, "already has a payment"):
            budgets.patch_tree(bad)
        self.assertEqual(canonical(bad), snapshot)
        bad = ET.fromstring(self.source)
        _, latest, uids, _ = budgets.inspect_database(bad)
        latest[uids[-1], "Crmb"][1].find("*[@id='new_value']").tag = "real"
        snapshot = canonical(bad)
        with self.assertRaisesRegex(ValueError, "Unexpected current Crmb type"):
            budgets.patch_tree(bad)
        self.assertEqual(canonical(bad), snapshot)

    def test_reports_finance_and_validation_limits(self):
        self.assertIn("UNVERIFIED", self.report["status"])
        self.assertIn("not independently verified", self.report["money_unit"])
        self.assertTrue(any("39 confirmed" in note for note in self.report["notes"]))
        self.assertTrue(any("First payment timing" in note for note in self.report["notes"]))


if __name__ == "__main__":
    unittest.main()
