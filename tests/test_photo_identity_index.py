from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from face_identity_index import build_identity_index, build_photo_identity_index


def record(table, uid, code, payload, version=1):
    return ET.fromstring(
        f'<record><integer id="database_table_type" value="{table}"/>'
        f'<large id="db_unique_id" value="{uid}"/>'
        f'<unsigned id="property" value="{int.from_bytes(code.encode(), "big")}"/>'
        f'{payload}<integer id="version" value="{version}"/></record>')


class PhotoIdentityIndexTests(unittest.TestCase):
    def test_source_copied_and_surname_named_people_have_explicit_evidence(self):
        with zipfile.ZipFile(ROOT / "downloads/fm26-unified-data.zip") as archive:
            root = ET.fromstring(archive.read("FM26_unified.xml"))
        before = ET.tostring(root)
        rows = build_photo_identity_index(root)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(len(rows), 1050)
        self.assertEqual(len(build_identity_index(root)), 1002)
        expected = {
            "전종서": (2002044891, 49309), "소주연": (2002044892, 49310),
            "하연수": (2002044893, 49311), "설인아": (2002044894, 49312),
            "서예지": (2002044895, 49313), "김슬기": (2002044896, 49314),
            "김향기": (2002044897, 49315),
        }
        for name, (low, creation_index) in expected.items():
            row = by_name[name]
            self.assertEqual(row["latest_record_low32"], low)
            self.assertEqual(row["creation_record_index"], creation_index)
            self.assertEqual(row["creation_property"], "COPY")
            self.assertEqual(row["gender_value"], "1")
            self.assertEqual(int(row["creation_uid"]) >> 32, row["source_object_high32"])
            self.assertNotEqual(int(row["copy_source_uid"]) >> 32, row["source_object_high32"])
        self.assertEqual(by_name["원하늘"]["name_property"], "Psna")
        self.assertEqual(by_name["원하늘"]["latest_record_low32"], 2002043246)
        self.assertEqual(by_name["임윤아"]["latest_record_low32"], 2002042552)
        self.assertEqual(before, ET.tostring(root))

    def test_copy_source_does_not_supply_target_gender_and_ambiguity_is_excluded(self):
        root = ET.fromstring('<record><list id="db_changes"/></record>')
        db = root.find("list")
        source, target = (11 << 32) + 101, (22 << 32) + 202
        db.append(record(1, source, "Pife", '<boolean id="new_value" value="1"/>'))
        creation = record(1, source, "COPY", '<record id="new_value">'
                          '<integer id="database_table_type" value="1"/>'
                          '<integer id="dcty" value="2"/>'
                          f'<large id="db_unique_id" value="{target}"/></record>')
        db.append(creation)
        db.append(record(1, target, "Psna", '<string id="new_value" value="Subject"/>'))
        self.assertEqual(build_photo_identity_index(root), [])
        db.append(record(1, target, "Pife", '<boolean id="new_value" value="1"/>'))
        row, = build_photo_identity_index(root)
        self.assertEqual(row["source_object_high32"], 22)
        self.assertEqual(row["name"], "Subject")
        db.append(ET.fromstring(ET.tostring(creation)))
        self.assertEqual(build_photo_identity_index(root), [])
        db.remove(db[-1])
        db.append(record(1, target, "DELE", '<boolean id="new_value" value="true"/>'))
        self.assertEqual(build_photo_identity_index(root), [])


if __name__ == "__main__":
    unittest.main()
