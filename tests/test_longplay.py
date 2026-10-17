"""Static checks only: these do not replace FM26 Editor or game validation."""
import collections
import csv
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_longplay', ROOT / 'tools/build_longplay.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class LongplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT / 'downloads/fm26-review.zip') as z:
            cls.source = z.read('cups_test.xml')
        cls.before = ET.fromstring(cls.source)
        with (ROOT / 'data/celebrity-cohort.csv').open(encoding='utf-8-sig', newline='') as f:
            cls.cohort = list(csv.DictReader(f))

    def test_wrong_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            BUILDER.build(self.source + b' ', self.cohort, dt.date(2025, 7, 1), False)

    def test_young_and_nationality_outputs_preserve_rules_and_other_fields(self):
        for spread in (False, True):
            with self.subTest(spread=spread):
                data, report = BUILDER.build(self.source, self.cohort, dt.date(2025, 7, 1), spread)
                root = ET.fromstring(data)
                before = ET.fromstring(self.source)
                before_db = before.find("list[@id='db_changes']")
                after_db = root.find("list[@id='db_changes']")
                self.assertEqual(len(after_db), 54726)
                latest = {}
                for node in after_db:
                    if BUILDER.value(node, 'database_table_type') == '1':
                        latest[BUILDER.value(node, 'db_unique_id'), BUILDER.value(node, 'property')] = node.find("*[@id='new_value']")
                for person in report:
                    dob_node = latest[person['person_uid'], BUILDER.prop('Pdob')]
                    actual = dt.date(int(dob_node.get('year')), int(dob_node.get('month')), int(dob_node.get('day')))
                    self.assertEqual(actual.isoformat(), person['new_dob'])
                    nat = latest[person['person_uid'], BUILDER.prop('Pnti')]
                    self.assertEqual(BUILDER.value(nat, 'DBID'), str(person['nation_dbid']))
                    self.assertEqual(int(BUILDER.value(nat, 'Nnat')), person['nation_dbid'] * (2**32 + 1))
                edits = collections.Counter()
                for b, a in zip(before_db, after_db):
                    if ET.tostring(b) == ET.tostring(a):
                        continue
                    name = int(BUILDER.value(b, 'property')).to_bytes(4, 'big').decode('ascii')
                    self.assertIn(name, ['Pdob', 'Pnti'] if spread else ['Pdob'])
                    edits[name] += 1
                    for node in [a, b]:
                        node.remove(node.find("*[@id='new_value']"))
                    self.assertEqual(ET.tostring(b), ET.tostring(a))
                # 105 of the selected 771 already had their assigned birth year.
                self.assertEqual(edits['Pdob'], 666)
                self.assertEqual(edits['Pnti'], 771 if spread else 0)
                before.remove(before_db)
                root.remove(after_db)
                self.assertEqual(ET.tostring(before), ET.tostring(root))
                for p in report:
                    dob = dt.date.fromisoformat(p['new_dob'])
                    asof = dt.date(2025, 7, 1)
                    age = asof.year - dob.year - ((asof.month, asof.day) < (dob.month, dob.day))
                    self.assertEqual(age, p['age_asof'])
                    self.assertIn(age, range(18, 23))
                if spread:
                    counts = collections.Counter(p['nation_dbid'] for p in report)
                    self.assertEqual(len(counts), 16)
                    self.assertEqual(set(counts.values()), {48, 49})
                    gks = collections.Counter(p['nation_dbid'] for p in report if p['role'] == 'GK')
                    self.assertGreaterEqual(min(gks.values()), 4)
                expected = '247c644252d2e1312c579bd6633a26788163e5d027d888f193931621d762963b' if spread else '88c4d8ac31452e9693d3f6fc2403299d58eadd1619462bff2827766773b92ec5'
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def test_caption_catalog_contains_verified_paths_and_hashes(self):
        pool = json.loads((ROOT / 'data/identities.json').read_text())
        self.assertEqual(len(pool), 118)
        self.assertEqual(len({p['path'] for p in pool}), 118)
        for p in pool:
            self.assertEqual(p['evidence'], 'printed_caption')
            self.assertEqual(len(p['sha256']), 64)
            self.assertNotIn('..', p['path'].split('/'))


if __name__ == '__main__':
    unittest.main()
