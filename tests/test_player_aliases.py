import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from face_identity_index import build_identity_index, patch_player_aliases, property_code, value


class PlayerAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT / "downloads/fm26-unified-data.zip") as archive:
            cls.xml = archive.read("FM26_unified.xml")

    def root(self):
        return ET.fromstring(self.xml)

    def test_four_game_anchors_fit_latest_history_plus_three(self):
        rows = {r["name"]: r for r in build_identity_index(self.root())}
        for name, actual in {"제시카": 2002043293, "이현주": 2002043294,
                             "카리나": 2002042553, "배성연": 2002042923}.items():
            self.assertEqual(rows[name]["latest_record_low32"] + 3, actual)
            self.assertTrue(rows[name]["last_and_max_version_agree"])
        self.assertEqual(int(rows["제시카"]["addr_uid"]) & 0xffffffff, 2002043301)
        self.assertEqual(rows["제시카"]["latest_record_low32"], 2002043290)

    def test_all_aliases_agree_and_only_controlled_values_change(self):
        root = self.root()
        before = copy.deepcopy(root)
        report = patch_player_aliases(root)
        self.assertEqual(report["cohort_players"], 771)
        self.assertGreaterEqual(report["changed_properties"]["Pnti"], 722)
        self.assertEqual(set(report["changed_properties"]), {"Pnti", "Pdob"})
        changes = {x["record_index"] for x in report["changes"]}
        old_db, new_db = before.find("list[@id='db_changes']"), root.find("list[@id='db_changes']")
        self.assertEqual(len(old_db), len(new_db))
        for index, (old, new) in enumerate(zip(old_db, new_db)):
            if index in changes:
                self.assertEqual(value(old, "database_table_type"), "1")
                self.assertIn(property_code(old), {"Pnti", "Pdob"})
                old.remove(old.find("*[@id='new_value']"))
                new = copy.deepcopy(new)
                new.remove(new.find("*[@id='new_value']"))
            self.assertEqual(ET.tostring(old), ET.tostring(new), index)
        before.remove(old_db)
        root_copy = copy.deepcopy(root)
        root_copy.remove(root_copy.find("list[@id='db_changes']"))
        self.assertEqual(ET.tostring(before), ET.tostring(root_copy))
        for person in report["players"]:
            for code, indices in person["record_indices"].items():
                nodes = [new_db[i].find("*[@id='new_value']") for i in indices]
                for node in nodes[1:]:
                    self.assertEqual(
                        ET.canonicalize(ET.tostring(nodes[0], encoding="unicode"), strip_text=True),
                        ET.canonicalize(ET.tostring(node, encoding="unicode"), strip_text=True))

    def test_patch_is_idempotent(self):
        root = self.root()
        patch_player_aliases(root)
        before = ET.tostring(root)
        report = patch_player_aliases(root)
        self.assertEqual(report["changed_records"], 0)
        self.assertEqual(before, ET.tostring(root))

    def test_ambiguous_created_identity_rejected_without_mutation(self):
        root = self.root()
        db = root.find("list[@id='db_changes']")
        target = next(r for r in db if value(r, "database_table_type") == "55"
                      and r.find("record[@id='new_value']/large[@id='db_unique_id']") is not None
                      and value(r.find("record[@id='new_value']"), "db_unique_id") == "1903225325120438693")
        db.append(copy.deepcopy(target))
        before = ET.tostring(root)
        with self.assertRaisesRegex(ValueError, "Ambiguous person creation"):
            patch_player_aliases(root)
        self.assertEqual(before, ET.tostring(root))

    def test_unpatched_original_nationalities_rejected_without_mutation(self):
        root = self.root()
        db = root.find("list[@id='db_changes']")
        record = next(r for r in db if value(r, "db_unique_id") == "1903225325120438693"
                      and property_code(r) == "Pnti")
        record.find("record[@id='new_value']/integer[@id='DBID']").set("value", "774")
        record.find("record[@id='new_value']/large[@id='Nnat']").set("value", str(774 * (2**32 + 1)))
        before = ET.tostring(root)
        with self.assertRaisesRegex(ValueError, "reviewed world assignment"):
            patch_player_aliases(root)
        self.assertEqual(before, ET.tostring(root))


if __name__ == "__main__":
    unittest.main()
