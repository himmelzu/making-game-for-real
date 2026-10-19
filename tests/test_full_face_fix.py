from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_full_face_fix import GRAPHICS, build


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(GRAPHICS.is_dir(), "The inspected original celeb facepack is required")
class FullFaceFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name) / "face-current-save.zip"
        cls.config_paths = sorted(GRAPHICS.rglob("config.xml"))
        cls.inputs = [ROOT / "downloads/fm26-unified-data.zip",
                      ROOT / "data/face-catalog.json", *cls.config_paths]
        cls.before = {path: digest(path) for path in cls.inputs}
        cls.report = build(root=ROOT, graphics=GRAPHICS, output=cls.output)
        with zipfile.ZipFile(cls.output) as archive:
            cls.names = archive.namelist()
            cls.crc_error = archive.testzip()
            cls.packaged_report = json.loads(archive.read("FACE_REPAIR_REPORT.json"))
            cls.evidence = json.loads(archive.read("FACE_SOURCE_EVIDENCE.json"))
            cls.configs = {
                name: ET.fromstring(archive.read(name))
                for name in cls.names if name.endswith("/config.xml")
            }
        cls.rows = {row["path"]: row for row in cls.report["mappings"]}
        cls.config_maps = {
            name: [(node.get("from"), node.get("to"))
                   for node in root.findall("list[@id='maps']/record")]
            for name, root in cls.configs.items()
        }

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_delivery_contains_only_configs_and_readable_evidence(self):
        self.assertIsNone(self.crc_error)
        self.assertEqual(len(self.names), len(set(self.names)))
        expected_configs = {path.relative_to(GRAPHICS).as_posix()
                            for path in self.config_paths}
        self.assertEqual(len(expected_configs), 33)
        self.assertEqual(set(self.configs), expected_configs)
        self.assertEqual(set(self.names) - expected_configs,
                         {"FACE_REPAIR_REPORT.json", "FACE_SOURCE_EVIDENCE.json",
                          "FACE_SOURCE_EVIDENCE.csv", "적용방법.txt"})
        for name in self.names:
            self.assertFalse(PurePosixPath(name).is_absolute(), name)
            self.assertNotIn("..", PurePosixPath(name).parts, name)
        self.assertEqual(self.packaged_report, self.report)
        self.assertEqual(self.evidence, self.report["mappings"])

    def test_every_photo_has_evidence_and_exactly_one_primary_per_target(self):
        self.assertEqual(self.report["scope"], "current_save_calibrated_model")
        self.assertEqual(self.report["input_photos"], 963)
        self.assertEqual(self.report["covered_photos"], 963)
        self.assertEqual(len(self.rows), 963)
        self.assertEqual(self.report["unresolved"], [])
        primaries = [row for row in self.rows.values() if row["status"] == "mapped"]
        alternates = [row for row in self.rows.values()
                      if row["status"] == "duplicate_alternate"]
        self.assertEqual(len(primaries), self.report["connections"])
        self.assertEqual(len(primaries), 959)
        self.assertEqual(len(primaries) + len(alternates), 963)
        self.assertEqual(len(alternates), len(self.report["duplicate_alternates"]))
        self.assertEqual({row["path"] for row in alternates},
                         {"SM/1.png", "SM/2.png", "SM/3.png", "SM/4.png"})
        primary_targets = Counter(int(row["target_id"]) for row in primaries)
        self.assertTrue(primary_targets)
        self.assertEqual(set(primary_targets.values()), {1})
        destinations = Counter(target for mappings in self.config_maps.values()
                               for _, target in mappings)
        for path, row in self.rows.items():
            config = (PurePosixPath(path).parent / "config.xml").as_posix()
            source = PurePosixPath(path).stem
            destination = f"graphics/pictures/person/r-{row['target_id']}/portrait"
            self.assertTrue((GRAPHICS / path).is_file(), path)
            self.assertEqual(destinations[destination], 1, path)
            if row["status"] == "mapped":
                self.assertIn((source, destination), self.config_maps[config], path)
            else:
                self.assertFalse(any(old == source for old, _ in self.config_maps[config]), path)
                self.assertIn(int(row["target_id"]), primary_targets, path)

    def test_only_four_confirmed_anchors_calibrate_the_source_history(self):
        expected = {"카리나": 2002042553, "배성연": 2002042923,
                    "제시카": 2002043293, "이현주": 2002043294}
        self.assertEqual(self.report["offset"], 3)
        self.assertEqual({row["name"]: int(row["actual_id"])
                          for row in self.report["anchors"]}, expected)
        self.assertEqual(len(self.report["anchors"]), 4)
        self.assertEqual(self.report["model_inferred_identities"], 955)
        for anchor in self.report["anchors"]:
            self.assertEqual(int(anchor["source_low32"]) + 3, int(anchor["actual_id"]))
            self.assertEqual(anchor["offset"], 3)
        verified_names = {row["source_name"] for row in self.rows.values()
                          if row["in_game_verified"]}
        self.assertEqual(verified_names, set(expected))
        self.assertEqual(sum(row["in_game_verified"] for row in self.rows.values()), 4)
        for row in self.rows.values():
            self.assertEqual(int(row["source_uid"]) & 0xffffffff,
                             int(row["source_low32"]), row["path"])
            self.assertEqual(int(row["target_id"]), int(row["source_low32"]) + 3,
                             row["path"])
            self.assertEqual(row["in_game_verified"],
                             row["source_name"] in expected and row["status"] == "mapped",
                             row["path"])

    def test_caption_context_and_special_filenames_do_not_select_wrong_people(self):
        mld = self.rows["MLD 드림티/2002042952.png"]
        hybe = self.rows["하이브/2002042586.png"]
        self.assertEqual(mld["caption"], "이혜인")
        self.assertEqual(mld["source_name"], "이혜빈")
        self.assertEqual(int(mld["target_id"]), 2002042962)
        self.assertEqual(hybe["source_name"], "이혜인")
        self.assertEqual(int(hybe["target_id"]), 2002042596)
        self.assertNotEqual(mld["source_object_high32"], hybe["source_object_high32"])
        expected_sm = {
            "SM/1.png": ("카리나", 2002042553),
            "SM/2.png": ("김태연", 2002042554),
            "SM/3.png": ("임윤아", 2002042555),
            "SM/4.png": ("윈터", 2002042556),
        }
        for path, (name, actual) in expected_sm.items():
            self.assertEqual(self.rows[path]["source_name"], name, path)
            self.assertEqual(int(self.rows[path]["target_id"]), actual, path)
        for suffix, actual in {"2002043283.png": 2002043293,
                               "2002043284.png": 2002043294}.items():
            row, = [r for path, r in self.rows.items() if path.endswith("/" + suffix)]
            self.assertEqual(int(row["target_id"]), actual)
        expected_copies = {"전종서": 2002044894, "소주연": 2002044895,
                           "하연수": 2002044896, "설인아": 2002044897,
                           "서예지": 2002044898, "김슬기": 2002044899,
                           "김향기": 2002044900, "원하늘": 2002043249}
        for name, actual in expected_copies.items():
            matched = [row for row in self.rows.values() if row["source_name"] == name]
            self.assertTrue(matched, name)
            self.assertEqual({int(row["target_id"]) for row in matched}, {actual}, name)

    def test_original_rows_outside_caption_catalog_survive_except_target_conflicts(self):
        corrected = {f"graphics/pictures/person/r-{row['target_id']}/portrait"
                     for row in self.rows.values()}
        checked, preserved, removed = 0, 0, 0
        for path in self.config_paths:
            name = path.relative_to(GRAPHICS).as_posix()
            root = ET.fromstring(path.read_bytes())
            for node in root.findall("list[@id='maps']/record"):
                source, target = node.get("from"), node.get("to")
                photo_path = (PurePosixPath(name).parent / f"{source}.png").as_posix()
                if photo_path not in self.rows:
                    checked += 1
                    if target not in corrected:
                        preserved += 1
                        self.assertIn((source, target), self.config_maps[name], name)
                    else:
                        removed += 1
                        self.assertNotIn((source, target), self.config_maps[name], name)
        self.assertEqual((checked, preserved, removed), (47, 41, 6))
        self.assertEqual(sum(len(rows) for rows in self.config_maps.values()), 1000)

    def test_build_leaves_editor_data_catalog_and_original_configs_unchanged(self):
        self.assertEqual({path: digest(path) for path in self.inputs}, self.before)


if __name__ == "__main__":
    unittest.main()
