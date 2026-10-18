"""Portrait pack boundaries and failure recovery, using no game installation."""

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from fm26auto import assets


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0V4AAAAASUVORK5CYII="
)


class PortraitPackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "선택한 연예인, 사진"
        self.source.mkdir()
        self.graphics = self.root / "FM graphics"
        self.graphics.mkdir()
        (self.source / "플레디스").mkdir()
        self.image = self.source / "플레디스" / "나나, 사진.png"
        self.image.write_bytes(PNG)
        self.sha = hashlib.sha256(PNG).hexdigest()
        self.target = self.graphics / assets.PACK_DIRECTORY
        self.backup = self.graphics / assets.BACKUP_DIRECTORY

    def row(self, uid=2002042895, **changes):
        row = {"uid": uid, "name": "나나", "path": "플레디스/나나, 사진.png", "sha256": self.sha}
        row.update(changes)
        return row

    def manifest(self, directory=None):
        return json.loads(((directory or self.target) / assets.MANIFEST).read_text(encoding="utf-8"))

    def build(self, uid=2002042895):
        return assets.build_pack(self.source, self.graphics, [self.row(uid)])

    def test_png_bytes_xml_mapping_and_reused_photo(self):
        before = self.image.read_bytes()
        receipt = assets.build_pack(self.source, self.graphics, [self.row(), self.row("r-4294967295")])
        self.assertEqual(receipt["assignment_count"], 2)
        self.assertEqual(receipt["unique_photo_count"], 1)
        self.assertEqual(list((self.target / "faces").glob("*.png")), [self.target / "faces" / (self.sha + ".png")])
        self.assertEqual((self.target / "faces" / (self.sha + ".png")).read_bytes(), before)
        self.assertEqual(self.image.read_bytes(), before)
        maps = ET.parse(self.target / "config.xml").findall("./list[@id='maps']/record")
        self.assertEqual([r.attrib["to"] for r in maps], [
            "graphics/pictures/person/r-2002042895/portrait",
            "graphics/pictures/person/r-4294967295/portrait",
        ])
        self.assertTrue(all(row.attrib["from"] == "faces/" + self.sha for row in maps))
        self.assertEqual(json.loads(json.dumps(receipt)), receipt)

    def test_windows_relative_separators_and_id_alias(self):
        row = self.row(path="플레디스\\나나, 사진.png")
        row["id"] = row.pop("uid")
        assets.build_pack(self.source, self.graphics, [row])
        self.assertEqual(self.manifest()["assignments"][0]["path"], "플레디스/나나, 사진.png")

    def test_empty_pack_can_be_published_and_rolled_back(self):
        receipt = assets.build_pack(self.source, self.graphics, [])
        self.assertEqual(receipt["assignment_count"], 0)
        self.assertEqual(ET.parse(self.target / "config.xml").findall("./list/record"), [])
        assets.rollback_pack(receipt)
        self.assertFalse(self.target.exists())

    def test_prepare_does_not_publish_and_abort_cleans_stage(self):
        prepared = assets.prepare_pack(self.source, self.graphics, [self.row()])
        self.assertFalse(self.target.exists())
        self.assertTrue(prepared._stage.exists())
        prepared.abort()
        prepared.abort()
        self.assertFalse(prepared._stage.exists())
        with self.assertRaises(assets.PackError):
            prepared.publish()

    def test_one_backup_and_rollback(self):
        first = self.build(1)
        second = self.build(2)
        self.assertEqual(self.manifest(self.backup)["pack_id"], first["pack_id"])
        third = self.build(3)
        self.assertEqual(self.manifest(self.backup)["pack_id"], second["pack_id"])
        self.assertEqual(self.manifest()["pack_id"], third["pack_id"])
        result = assets.rollback_pack(json.loads(json.dumps(third)))
        self.assertEqual(result["restored_pack_id"], second["pack_id"])
        self.assertEqual(self.manifest()["pack_id"], second["pack_id"])
        self.assertFalse(self.backup.exists())
        with self.assertRaises(assets.PackError):
            assets.rollback_pack(third)

    def test_first_generation_rollback_removes_only_owned_pack(self):
        other = self.graphics / "women-megapack"
        other.mkdir()
        (other / "config.xml").write_text("original", encoding="utf-8")
        receipt = self.build()
        assets.rollback_pack(receipt)
        self.assertFalse(self.target.exists())
        self.assertEqual((other / "config.xml").read_text(), "original")
        self.assertTrue(self.image.exists())

    def test_foreign_target_and_backup_are_never_overwritten(self):
        for directory in (self.target, self.backup):
            with self.subTest(directory=directory.name):
                directory.mkdir()
                (directory / "keep.txt").write_text("user", encoding="utf-8")
                with self.assertRaises(assets.PackError):
                    self.build()
                self.assertEqual((directory / "keep.txt").read_text(), "user")
                (directory / "keep.txt").unlink()
                directory.rmdir()

    def test_invalid_uid_duplicate_uid_or_disagreeing_alias(self):
        for uid in (0, -1, 4294967296, True, None, "1.0", "１", "r--1"):
            with self.subTest(uid=uid), self.assertRaises(assets.PackError):
                assets.prepare_pack(self.source, self.graphics, [self.row(uid)])
        for rows in ([self.row(1), self.row("r-1")], [self.row(1, id=2)]):
            with self.assertRaises(assets.PackError):
                assets.prepare_pack(self.source, self.graphics, rows)

    def test_maximum_assignments_checked_before_reading_files(self):
        with patch.object(assets, "MAX_ASSIGNMENTS", 1), self.assertRaises(assets.PackError):
            assets.prepare_pack(self.source, self.graphics, [self.row(1), self.row(2)])

    def test_unsafe_relative_paths(self):
        for path in ("../outside.png", "플레디스/../../outside.png", "/tmp/x.png", "C:\\x.png", "\\\\host\\x.png", ".", ""):
            with self.subTest(path=path), self.assertRaises(assets.PackError):
                assets.prepare_pack(self.source, self.graphics, [self.row(path=path)])
        self.assertEqual(list(self.graphics.iterdir()), [])

    def test_source_symlink_outside_selected_folder_rejected(self):
        outside = self.root / "outside.png"
        outside.write_bytes(PNG)
        linked = self.source / "outside-link.png"
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(assets.PackError):
            assets.prepare_pack(self.source, self.graphics, [self.row(path=linked.name)])
        self.assertEqual(outside.read_bytes(), PNG)

    def test_target_symlink_refused(self):
        outside = self.root / "other-pack"
        outside.mkdir()
        try:
            self.target.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(assets.PackError):
            self.build()
        self.assertEqual(list(outside.iterdir()), [])

    def test_hash_or_png_signature_failure_keeps_installed_pack(self):
        receipt = self.build(1)
        for contents, checksum in ((PNG, "0" * 64), (b"not a png", hashlib.sha256(b"not a png").hexdigest())):
            self.image.write_bytes(contents)
            with self.assertRaises(assets.PackError):
                assets.build_pack(self.source, self.graphics, [self.row(2, sha256=checksum)])
            self.assertEqual(self.manifest()["pack_id"], receipt["pack_id"])
            self.assertFalse(any("stage-" in p.name for p in self.graphics.iterdir()))

    def test_second_path_with_reused_checksum_is_also_verified(self):
        (self.source / "different.png").write_bytes(PNG + b"changed")
        with self.assertRaises(assets.PackError):
            assets.build_pack(self.source, self.graphics, [self.row(1), self.row(2, path="different.png")])

    def test_publish_failure_restores_both_generations(self):
        first = self.build(1)
        second = self.build(2)
        original_replace = os.replace
        failed = False

        def fail_install(source, destination):
            nonlocal failed
            if ".stage-" in Path(source).name and Path(destination) == self.target and not failed:
                failed = True
                raise OSError("injected install rename failure")
            return original_replace(source, destination)

        with patch.object(assets.os, "replace", side_effect=fail_install):
            with self.assertRaisesRegex(OSError, "injected"):
                self.build(3)
        self.assertTrue(failed)
        self.assertEqual(self.manifest()["pack_id"], second["pack_id"])
        self.assertEqual(self.manifest(self.backup)["pack_id"], first["pack_id"])
        self.assertEqual(sorted(path.name for path in self.graphics.iterdir()), [assets.PACK_DIRECTORY, assets.BACKUP_DIRECTORY])

    def test_rollback_failure_restores_current_generation(self):
        first = self.build(1)
        second = self.build(2)
        original_replace = os.replace
        failed = False

        def fail_restore(source, destination):
            nonlocal failed
            if Path(source) == self.backup and Path(destination) == self.target and not failed:
                failed = True
                raise OSError("injected restore rename failure")
            return original_replace(source, destination)

        with patch.object(assets.os, "replace", side_effect=fail_restore):
            with self.assertRaisesRegex(OSError, "injected"):
                assets.rollback_pack(second)
        self.assertEqual(self.manifest()["pack_id"], second["pack_id"])
        self.assertEqual(self.manifest(self.backup)["pack_id"], first["pack_id"])

    def test_rollback_refuses_modified_added_missing_or_linked_files(self):
        receipt = self.build(1)
        config = self.target / "config.xml"
        original = config.read_bytes()
        config.write_bytes(original + b"<!-- user -->")
        with self.assertRaises(assets.PackError):
            assets.rollback_pack(receipt)
        config.write_bytes(original)
        extra = self.target / "my-photo.png"
        extra.write_bytes(PNG)
        with self.assertRaises(assets.PackError):
            assets.rollback_pack(receipt)
        extra.unlink()
        config.unlink()
        with self.assertRaises(assets.PackError):
            assets.rollback_pack(receipt)
        config.write_bytes(original)
        assets.rollback_pack(receipt)

    def test_publication_refuses_changed_target_since_prepare(self):
        self.build(1)
        prepared = assets.prepare_pack(self.source, self.graphics, [self.row(2)])
        self.build(3)
        with self.assertRaises(assets.PackError):
            prepared.publish()
        prepared.abort()
        self.assertEqual(self.manifest()["assignments"][0]["uid"], 3)

    def test_modified_backup_is_not_discarded(self):
        self.build(1)
        second = self.build(2)
        extra = self.backup / "user.txt"
        extra.write_text("retain me", encoding="utf-8")
        with self.assertRaises(assets.PackError):
            self.build(3)
        self.assertEqual(extra.read_text(), "retain me")
        self.assertEqual(self.manifest()["pack_id"], second["pack_id"])

    def test_lock_prevents_publication_and_is_not_removed(self):
        lock = self.graphics / ("." + assets.PACK_DIRECTORY + ".lock")
        lock.write_text("someone else", encoding="ascii")
        with self.assertRaises(assets.PackError):
            self.build()
        self.assertEqual(lock.read_text(), "someone else")
        self.assertFalse(self.target.exists())

    def test_receipt_cannot_redirect_rollback(self):
        receipt = self.build()
        for changes in ({"target": str(self.source)}, {"backup": str(self.source)}, {"owner": "other"}):
            forged = dict(receipt, **changes)
            with self.assertRaises(assets.PackError):
                assets.rollback_pack(forged)
        self.assertTrue(self.target.exists())

    def test_validate_rollback_is_read_only_and_returns_generation(self):
        first = self.build(1)
        second = self.build(2)
        before = {str(p.relative_to(self.graphics)): p.read_bytes()
                  for p in self.graphics.rglob("*") if p.is_file()}
        with patch.object(assets.os, "replace", side_effect=AssertionError("preflight moved files")), \
                patch.object(assets.os, "open", side_effect=AssertionError("preflight created a lock")), \
                patch.object(assets.shutil, "rmtree", side_effect=AssertionError("preflight removed files")):
            result = assets.validate_rollback(second)
        self.assertEqual(result["pack_id"], second["pack_id"])
        self.assertEqual(result["previous_pack_id"], first["pack_id"])
        after = {str(p.relative_to(self.graphics)): p.read_bytes()
                 for p in self.graphics.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(sorted(path.name for path in self.graphics.iterdir()),
                         [assets.PACK_DIRECTORY, assets.BACKUP_DIRECTORY])

    def test_validate_rollback_refuses_modified_current_and_backup_files(self):
        self.build(1)
        receipt = self.build(2)
        for directory in (self.target, self.backup):
            with self.subTest(directory=directory.name):
                config = directory / "config.xml"
                original = config.read_bytes()
                config.write_bytes(original + b"<!-- changed by user -->")
                with self.assertRaises(assets.PackError):
                    assets.validate_rollback(receipt)
                self.assertEqual(config.read_bytes(), original + b"<!-- changed by user -->")
                config.write_bytes(original)
        assets.validate_rollback(receipt)

    def test_validate_rollback_refuses_missing_target_or_generation(self):
        receipt = self.build(1)
        for changes in ({"pack_id": None}, {"previous_pack_id": "bad"},
                        {"pack_id": "0" * 32}, {"target": str(self.source)}):
            with self.subTest(changes=changes), self.assertRaises(assets.PackError):
                assets.validate_rollback(dict(receipt, **changes))
        assets.rollback_pack(receipt)
        with self.assertRaises(assets.PackError):
            assets.validate_rollback(receipt)


if __name__ == "__main__":
    unittest.main()
