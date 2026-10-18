"""Transactional portrait packs; this module never changes player/game data.

``prepare_pack(source_root, graphics_root, assignments)`` verifies and stages PNGs.
Each assignment needs ``uid`` (or ``id``), ``name``, relative ``path`` and a known
``sha256``. Reused photos are copied once. Call ``PreparedPack.publish()`` only
after the associated player transaction succeeds, or ``abort()`` to discard it.

``publish`` returns a JSON-serializable receipt. ``rollback_pack(receipt)`` restores
one previous pack, provided neither the installed files nor backup have changed.
Only our named subdirectory, its previous generation, and temporary siblings are
managed; other facepacks and source images are never altered.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import tempfile
from typing import Any, Mapping, Sequence
from uuid import uuid4
import xml.etree.ElementTree as ET


PACK_DIRECTORY = "fm26_celebrity_auto"
BACKUP_DIRECTORY = PACK_DIRECTORY + ".previous"
MANIFEST = ".fm26-auto-manifest.json"
OWNER = "fm26-celebrity-auto-portraits"
MAX_ASSIGNMENTS = 100_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


class PackError(ValueError):
    """A pack could not safely be prepared, published, or restored."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PackError("A nonempty relative image path is required")
    text = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    path = PurePosixPath(text)
    if windows.drive or windows.root or path.is_absolute() or ".." in path.parts:
        raise PackError(f"Unsafe relative image path: {value!r}")
    if not path.parts or str(path) == ".":
        raise PackError("An image filename is required")
    return str(path)


def _uid(value: Any) -> int:
    if isinstance(value, bool):
        raise PackError("Player ID must be a positive uint32")
    text = str(value)
    if text.startswith("r-"):
        text = text[2:]
    if not text.isascii() or not text.isdecimal():
        raise PackError(f"Invalid player ID: {value!r}")
    number = int(text)
    if not 1 <= number <= 0xFFFFFFFF:
        raise PackError("Player ID must be a positive uint32")
    return number


def _root(path: Path | str, label: str) -> Path:
    try:
        result = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        raise PackError(f"{label} does not exist: {path}") from exc
    if not result.is_dir():
        raise PackError(f"{label} is not a directory: {path}")
    return result


def _manifest(directory: Path) -> dict[str, Any] | None:
    """Validate our complete tree before overwriting, deleting, or restoring it."""
    if not directory.exists() and not directory.is_symlink():
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise PackError(f"Refusing non-directory/symlink pack: {directory}")
    if directory.resolve() != directory.absolute():
        raise PackError(f"Refusing redirected pack directory: {directory}")
    marker = directory / MANIFEST
    try:
        if marker.is_symlink() or marker.stat().st_size > 32 * 1024 * 1024:
            raise PackError("Invalid ownership manifest")
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError(f"Folder is not an intact pack made by this tool: {directory}") from exc
    if (not isinstance(data, dict) or data.get("owner") != OWNER
            or data.get("schema") != 1 or not isinstance(data.get("pack_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", data["pack_id"])
            or not isinstance(data.get("files"), dict)):
        raise PackError(f"Unrecognized ownership manifest: {directory}")
    expected: dict[str, str] = {}
    for relative, checksum in data["files"].items():
        normalized = _relative_path(relative)
        if normalized != relative or relative == MANIFEST:
            raise PackError("Invalid file path in ownership manifest")
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise PackError("Invalid checksum in ownership manifest")
        expected[relative] = checksum.lower()
    if "config.xml" not in expected:
        raise PackError("Owned portrait pack must contain config.xml")
    actual = set()
    allowed_directories = {".", "faces"}
    for relative in expected:
        allowed_directories.update(str(parent) for parent in PurePosixPath(relative).parents)
    for parent, directories, filenames in os.walk(directory, followlinks=False):
        for name in directories:
            child = Path(parent) / name
            if child.is_symlink() or child.resolve() != child.absolute():
                raise PackError(f"Unexpected redirected directory in pack: {child}")
            if child.relative_to(directory).as_posix() not in allowed_directories:
                raise PackError(f"Unexpected user directory in pack: {child}")
        for name in filenames:
            child = Path(parent) / name
            if child.is_symlink() or not child.is_file():
                raise PackError(f"Unexpected linked/non-file content in pack: {child}")
            relative = child.relative_to(directory).as_posix()
            actual.add(relative)
            if relative != MANIFEST:
                if relative not in expected or _sha256(child) != expected[relative]:
                    raise PackError(f"Pack contains modified or unrecognized file: {child}")
    if actual != set(expected) | {MANIFEST}:
        raise PackError(f"Pack files are missing: {directory}")
    return data


def _check_generation(directory: Path, expected_id: str | None) -> None:
    current = _manifest(directory)
    if (None if current is None else current["pack_id"]) != expected_id:
        raise PackError(f"Pack changed since preparation: {directory}")


@contextmanager
def _lock(root: Path):
    """Avoid two copies of this tool publishing to the same folder at once."""
    lock = root / ("." + PACK_DIRECTORY + ".lock")
    token = uuid4().hex
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PackError("Another portrait-pack operation is active; lock already exists") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(token)
        yield
    finally:
        try:
            if not lock.is_symlink() and lock.read_text(encoding="ascii") == token:
                lock.unlink()
        except OSError:
            # A lock cleanup problem must not report a committed publication as
            # failed and cause the caller to roll back its game transaction.
            pass


def _remove(directory: Path) -> None:
    if directory.is_symlink():
        raise PackError(f"Refusing to remove redirected temporary folder: {directory}")
    shutil.rmtree(directory)


def _copy_png(source: Path, destination: Path | None, expected: str) -> None:
    digest = hashlib.sha256()
    output = None
    try:
        if destination is not None:
            output = destination.open("xb")
        with source.open("rb") as stream:
            signature = stream.read(8)
            if signature != PNG_SIGNATURE:
                raise PackError(f"Image is not a PNG: {source.name}")
            digest.update(signature)
            if output:
                output.write(signature)
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                if output:
                    output.write(block)
        if digest.hexdigest() != expected:
            raise PackError(f"Image checksum differs from the catalog: {source.name}")
    finally:
        if output:
            output.close()


@dataclass
class PreparedPack:
    """Staged pack; publish once or abort. Receipt paths are absolute strings."""

    _root: Path
    _stage: Path
    _data: dict[str, Any]
    _previous_id: str | None
    _backup_id: str | None
    _state: str = "prepared"

    def abort(self) -> None:
        if self._state == "prepared":
            _remove(self._stage)
            self._state = "aborted"

    def publish(self) -> dict[str, Any]:
        if self._state != "prepared":
            raise PackError(f"Cannot publish a pack in state {self._state!r}")
        target = self._root / PACK_DIRECTORY
        backup = self._root / BACKUP_DIRECTORY
        discarded = self._root / ("." + PACK_DIRECTORY + ".discard-" + uuid4().hex)
        moved_backup = False
        moved_target = False
        with _lock(self._root):
            _check_generation(target, self._previous_id)
            _check_generation(backup, self._backup_id)
            _check_generation(self._stage, self._data["pack_id"])
            try:
                if self._previous_id is not None:
                    if self._backup_id is not None:
                        os.replace(backup, discarded)
                        moved_backup = True
                    os.replace(target, backup)
                    moved_target = True
                os.replace(self._stage, target)
            except BaseException:
                if moved_target:
                    os.replace(backup, target)
                if moved_backup:
                    os.replace(discarded, backup)
                raise
            self._state = "published"
            warnings = []
            if moved_backup:
                try:
                    _remove(discarded)
                except Exception as exc:
                    # Publication has committed; cleanup failure must not cause the
                    # caller to undo otherwise successful game changes.
                    warnings.append(f"Old temporary backup was retained: {discarded}: {exc}")
            return {
                "schema": 1, "owner": OWNER, "pack_id": self._data["pack_id"],
                "graphics_root": str(self._root), "target": str(target),
                "backup": str(backup) if self._previous_id is not None else None,
                "previous_pack_id": self._previous_id,
                "assignment_count": self._data["assignment_count"],
                "unique_photo_count": self._data["unique_photo_count"],
                "cleanup_warnings": warnings,
            }


def prepare_pack(source_root: Path | str, graphics_root: Path | str,
                 assignments: Sequence[Mapping[str, Any]]) -> PreparedPack:
    """Verify assignments and stage a complete pack without touching its predecessor.

    ``graphics_root`` must be an existing folder explicitly chosen by the user.
    ID strings may contain the ``r-`` prefix. Paths support Korean characters,
    spaces, commas and Windows separators, but must remain inside source_root.
    """
    source = _root(source_root, "Source image folder")
    graphics = _root(graphics_root, "FM graphics folder")
    if not isinstance(assignments, (list, tuple)) or len(assignments) > MAX_ASSIGNMENTS:
        raise PackError(f"Expected at most {MAX_ASSIGNMENTS} assignments")
    normalized = []
    used_ids = set()
    for row in assignments:
        if not isinstance(row, Mapping):
            raise PackError("Every photo assignment must be a mapping")
        identity = _uid(row.get("uid", row.get("id")))
        if "uid" in row and "id" in row and identity != _uid(row["id"]):
            raise PackError("Assignment uid and id disagree")
        if identity in used_ids:
            raise PackError(f"Duplicate player ID: {identity}")
        used_ids.add(identity)
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or "\x00" in name:
            raise PackError("An assignment needs a nonempty player name")
        relative = _relative_path(row.get("path"))
        checksum = row.get("sha256")
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise PackError("A catalog SHA256 is required for every photo")
        try:
            image = (source / relative).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PackError(f"Source image does not exist: {relative}") from exc
        if not image.is_relative_to(source) or not image.is_file():
            raise PackError(f"Source image is outside the selected image folder: {relative}")
        normalized.append((identity, name, relative, image, checksum.lower()))
    target_data = _manifest(graphics / PACK_DIRECTORY)
    backup_data = _manifest(graphics / BACKUP_DIRECTORY)
    stage = Path(tempfile.mkdtemp(prefix="." + PACK_DIRECTORY + ".stage-", dir=graphics))
    try:
        (stage / "faces").mkdir()
        root = ET.Element("record")
        ET.SubElement(root, "boolean", {"id": "preload", "value": "false"})
        ET.SubElement(root, "boolean", {"id": "amap", "value": "false"})
        maps = ET.SubElement(root, "list", {"id": "maps"})
        files = {}
        checked = set()
        records = []
        for identity, name, relative, image, checksum in sorted(normalized):
            photo = f"faces/{checksum}.png"
            key = (str(image), checksum)
            if key not in checked:
                _copy_png(image, stage / photo if photo not in files else None, checksum)
                checked.add(key)
            files[photo] = checksum
            ET.SubElement(maps, "record", {
                "from": photo[:-4],
                "to": f"graphics/pictures/person/r-{identity}/portrait",
            })
            records.append({"uid": identity, "name": name, "path": relative, "sha256": checksum})
        ET.indent(root, space="  ")
        ET.ElementTree(root).write(stage / "config.xml", encoding="utf-8", xml_declaration=True)
        files["config.xml"] = _sha256(stage / "config.xml")
        data = {
            "schema": 1, "owner": OWNER, "pack_id": uuid4().hex,
            "assignment_count": len(normalized), "unique_photo_count": len(files) - 1,
            "files": files, "assignments": records,
        }
        (stage / MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return PreparedPack(graphics, stage, data,
                            None if target_data is None else target_data["pack_id"],
                            None if backup_data is None else backup_data["pack_id"])
    except BaseException:
        _remove(stage)
        raise


def build_pack(source_root: Path | str, graphics_root: Path | str,
               assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prepare and publish, cleaning the staged folder on publication failure."""
    prepared = prepare_pack(source_root, graphics_root, assignments)
    try:
        return prepared.publish()
    finally:
        prepared.abort()


def _rollback_paths(receipt: Mapping[str, Any]) -> tuple[Path, Path, Path, str | None]:
    if (not isinstance(receipt, Mapping) or receipt.get("schema") != 1
            or receipt.get("owner") != OWNER):
        raise PackError("Invalid portrait-pack receipt")
    for key in ("pack_id", "previous_pack_id"):
        identity = receipt.get(key)
        if identity is None and key == "previous_pack_id":
            continue
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise PackError(f"Invalid {key} in portrait-pack receipt")
    graphics = _root(receipt.get("graphics_root", ""), "FM graphics folder")
    target = graphics / PACK_DIRECTORY
    backup = graphics / BACKUP_DIRECTORY
    previous_id = receipt.get("previous_pack_id")
    if receipt.get("target") != str(target):
        raise PackError("Receipt target differs from this tool's managed folder")
    if receipt.get("backup") != (str(backup) if previous_id is not None else None):
        raise PackError("Receipt backup differs from this tool's managed folder")
    return graphics, target, backup, previous_id


def validate_rollback(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only rollback preflight; raise PackError on any changed generation.

    Validate the receipt, ownership manifests and every declared current/backup
    file hash without creating even a lock file. This lets the caller reject a
    combined game-and-photo undo before making game writes. It is a point-in-time
    check; rollback_pack repeats it under its publication lock before moving files.
    """
    _, target, backup, previous_id = _rollback_paths(receipt)
    _check_generation(target, receipt["pack_id"])
    if previous_id is not None:
        _check_generation(backup, previous_id)
    return {"pack_id": receipt["pack_id"], "previous_pack_id": previous_id,
            "target": str(target), "backup": str(backup) if previous_id is not None else None}


def rollback_pack(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Restore precisely the preceding generation, refusing user modifications.

    The receipt is JSON-serializable and may be persisted beside the game-change
    receipt. It authorizes only this tool's fixed subdirectories in graphics_root.
    A second rollback, or rollback after a subsequent publication, is rejected.
    """
    graphics, target, backup, previous_id = _rollback_paths(receipt)
    removed = graphics / ("." + PACK_DIRECTORY + ".rollback-" + uuid4().hex)
    with _lock(graphics):
        validate_rollback(receipt)
        os.replace(target, removed)
        try:
            if previous_id is not None:
                os.replace(backup, target)
        except BaseException:
            os.replace(removed, target)
            raise
        warnings = []
        try:
            _remove(removed)
        except Exception as exc:
            warnings.append(f"Removed generation retained in temporary folder: {removed}: {exc}")
        return {"rolled_back_pack_id": receipt["pack_id"],
                "restored_pack_id": previous_id, "cleanup_warnings": warnings}
