"""Conservative, journaled local memory mutations; no FM game API is claimed.

This module has been tested with simulated memory, not a running FM26 game.
The caller must validate the exact game build, process lease, loaded save, and
every target identity in ``guard``. Reads/writes cannot be made atomic across
an external game process: the game must be paused and concurrent editors closed.
Only the journal is durable. Undo is conditional and cannot repair arbitrary
partial writes or undo a game's later changes. Journal files stay local.

The complete plan is fsynced once before writes. Progress is then appended and
fsynced in ``<journal>.progress.jsonl``; only completion/failure checkpoints the
whole JSON again. Keep both files together after an interruption. Undo replays
the checksummed progress log before inspecting memory. This avoids rewriting an
O(n)-size plan before/after each of its n mutations.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Protocol
import uuid


class Memory(Protocol):
    def read(self, address: int, size: int) -> bytes: ...
    def write(self, address: int, data: bytes) -> int: ...


FORMAT = "fm26-local-transaction-v1"
MAX_MUTATIONS = 10000
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024 * 1024
PROGRESS_FORMAT = "fm26-local-progress-v1"
ZERO_HASH = "0" * 64


class _Journal(dict):
    """Progress bookkeeping is in attributes, never a private JSON field."""

    def __init__(self, value):
        super().__init__(value)
        self.cursor = len(self["events"])
        self.sequence = self.get("progress_sequence", 0)
        self.digest = self.get("progress_sha256", ZERO_HASH)
        self.progress_failed = False
        self.truncate_at = None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _validate(mutations: list[dict]) -> list[dict]:
    if not isinstance(mutations, list) or len(mutations) > MAX_MUTATIONS:
        raise ValueError("변경 목록은 10,000개 이하여야 합니다.")
    output = []
    for m in mutations:
        if not isinstance(m, dict):
            raise ValueError("변경 항목은 객체여야 합니다.")
        address, before, after = m.get("address"), m.get("before"), m.get("after")
        if type(address) is not int or not 0 < address < 2**64:
            raise ValueError("메모리 주소가 올바르지 않습니다.")
        if type(before) is not bytes or type(after) is not bytes:
            raise ValueError("변경 전후 값은 bytes여야 합니다.")
        if len(before) not in (1, 2, 4, 8) or len(after) != len(before):
            raise ValueError("변경 전후 길이는 같은 1, 2, 4, 8바이트여야 합니다.")
        if address + len(before) > 2**64:
            raise ValueError("메모리 주소 범위를 넘었습니다.")
        for key in ("uid", "random_id"):
            if type(m.get(key)) is not int or not 0 <= m[key] <= 0xFFFFFFFF:
                raise ValueError(key + "는 32비트 정수여야 합니다.")
        label = m.get("label")
        if not isinstance(label, str) or not label or len(label) > 300 or any(ord(c) < 32 for c in label):
            raise ValueError("변경 항목 설명을 확인하세요.")
        output.append({"address": address, "before": before, "after": after,
                       "uid": m["uid"], "random_id": m["random_id"], "label": label})
    ranges = sorted((m["address"], m["address"] + len(m["before"])) for m in output)
    if any(right[0] < left[1] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("변경할 메모리 범위가 중복됩니다.")
    return output


def _sync_directory(parent: Path) -> None:
    # Windows does not support opening a directory for os.fsync.
    if os.name != "nt":
        descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _progress_path(path: Path) -> Path:
    return path.with_name(path.name + ".progress.jsonl")


def _write_snapshot(path: Path, journal: _Journal, *, create: bool = False) -> None:
    snapshot = {**journal, "progress_sequence": journal.sequence,
                "progress_sha256": journal.digest}
    data = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_JOURNAL_BYTES:
        raise ValueError("저널 크기 제한을 넘었습니다.")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    _sync_directory(path.parent)


def _encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _persist(path: Path, journal: _Journal, *, create: bool = False,
             checkpoint: bool = False) -> None:
    journal["updated_at"] = _now()
    progress_path = _progress_path(path)
    if create:
        if progress_path.exists() or progress_path.is_symlink():
            raise FileExistsError("기존 진행 저널을 덮어쓸 수 없습니다.")
        _write_snapshot(path, journal, create=True)
        journal.cursor = len(journal["events"])
        return
    if checkpoint:
        # Full information remains in the ordinary JSON when the operation ends.
        # If interrupted before unlink, the snapshot sequence prevents replaying
        # already-checkpointed progress events twice.
        _write_snapshot(path, journal)
        if progress_path.exists():
            progress_path.unlink()
            _sync_directory(path.parent)
        journal.cursor = len(journal["events"])
        journal.progress_failed = False
        journal.truncate_at = None
        return
    if journal.progress_failed:
        raise OSError("진행 저널 기록 실패 이후 추가 메모리 변경을 중단했습니다.")
    events = journal["events"][journal.cursor:]
    if not events:
        return
    record = {"format": PROGRESS_FORMAT, "transaction_id": journal["transaction_id"],
              "sequence": journal.sequence + 1, "previous_sha256": journal.digest,
              "events": events}
    digest = hashlib.sha256(_encoded(record)).hexdigest()
    data = _encoded({**record, "sha256": digest}) + b"\n"
    try:
        if journal.truncate_at is not None:
            # Only an incomplete final line may be discarded after a crash.
            with progress_path.open("r+b") as stream:
                stream.truncate(journal.truncate_at)
                stream.flush()
                os.fsync(stream.fileno())
            journal.truncate_at = None
        existed = progress_path.exists()
        if existed and progress_path.stat().st_size + len(data) > MAX_PROGRESS_BYTES:
            raise ValueError("진행 저널 크기 제한을 넘었습니다.")
        descriptor = os.open(progress_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not existed:
            _sync_directory(path.parent)
    except Exception:
        journal.progress_failed = True
        raise
    journal.cursor = len(journal["events"])
    journal.sequence += 1
    journal.digest = digest


def _replay_event(journal: dict, event: dict) -> None:
    if not isinstance(event, dict) or not isinstance(event.get("action"), str):
        raise ValueError("진행 저널 이벤트를 확인하세요.")
    action = event["action"]
    if action in ("writing", "applied", "not_written", "rollback_result", "undo_result"):
        i = event.get("index")
        if type(i) is not int or not 0 <= i < len(journal["mutations"]):
            raise ValueError("진행 저널의 변경 번호가 올바르지 않습니다.")
        entry = journal["mutations"][i]
        if action == "writing":
            entry.update(state="writing", attempted=True)
        elif action == "applied":
            entry.update(state="applied", attempted=True)
        elif action == "not_written":
            entry.update(state="not_written", attempted=False)
        else:
            entry[action.removesuffix("_result")] = event.get("status")
    journal["events"].append(event)


def _load_journal(path: Path) -> _Journal:
    if path.stat().st_size > MAX_JOURNAL_BYTES:
        raise ValueError("저널 크기 제한을 넘었습니다.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("format") != FORMAT
            or not isinstance(value.get("events"), list)
            or not isinstance(value.get("mutations"), list)):
        raise ValueError("지원하는 트랜잭션 저널이 아닙니다.")
    journal = _Journal(value)
    if (type(journal.sequence) is not int or journal.sequence < 0
            or not isinstance(journal.digest, str) or len(journal.digest) != 64):
        raise ValueError("저널의 진행 기록을 확인하세요.")
    progress_path = _progress_path(path)
    if not progress_path.exists():
        return journal
    if progress_path.stat().st_size > MAX_PROGRESS_BYTES:
        raise ValueError("진행 저널 크기 제한을 넘었습니다.")
    raw = progress_path.read_bytes()
    complete_end = raw.rfind(b"\n") + 1
    if complete_end != len(raw):
        journal.truncate_at = complete_end
    lines = raw[:complete_end].splitlines()
    previous, sequence = None, None
    snapshot_sequence, snapshot_digest = journal.sequence, journal.digest
    for line in lines:
        try:
            record = json.loads(line)
            digest = record.pop("sha256")
            if digest != hashlib.sha256(_encoded(record)).hexdigest():
                raise ValueError("진행 저널 체크섬이 다릅니다.")
            if (record["format"] != PROGRESS_FORMAT
                    or record["transaction_id"] != journal["transaction_id"]
                    or type(record["sequence"]) is not int
                    or not isinstance(record["events"], list)):
                raise ValueError("다른 트랜잭션의 진행 저널입니다.")
            if sequence is None:
                if record["sequence"] == 1:
                    sequence, previous = 0, ZERO_HASH
                else:
                    sequence, previous = snapshot_sequence, snapshot_digest
            if record["sequence"] != sequence + 1 or record["previous_sha256"] != previous:
                raise ValueError("진행 저널 순서가 다릅니다.")
            sequence, previous = record["sequence"], digest
            if sequence == snapshot_sequence and digest != snapshot_digest:
                raise ValueError("저널과 진행 체크포인트가 다릅니다.")
            if sequence > snapshot_sequence:
                for event in record["events"]:
                    _replay_event(journal, event)
                journal.sequence, journal.digest = sequence, digest
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ValueError("진행 저널 형식을 확인하세요.") from exc
    if sequence is not None and sequence < snapshot_sequence:
        raise ValueError("체크포인트보다 짧은 진행 저널입니다.")
    journal.cursor = len(journal["events"])
    return journal


def _read(memory: Memory, m: dict) -> bytes:
    raw = memory.read(m["address"], len(m["before"]))
    if type(raw) is not bytes or len(raw) != len(m["before"]):
        raise OSError("메모리 읽기 길이가 다릅니다: " + m["label"])
    return raw


def _event(journal: dict, action: str, **details) -> None:
    journal["events"].append({"at": _now(), "action": action, **details})


def _restore(memory: Memory, items: list[dict], journal: dict, path: Path,
             guard: Callable[[], None], operation: str) -> list[dict]:
    """Restore only our exact 'after' value; a failed guard never permits writes."""
    results = []
    indices = [i for i, entry in enumerate(journal["mutations"])
               if entry.get("attempted") and items[i]["before"] != items[i]["after"]]
    for position, i in enumerate(reversed(indices)):
        m, entry = items[i], journal["mutations"][i]
        try:
            guard()
            actual = _read(memory, m)
            if actual == m["before"]:
                outcome = "already_before"
            elif actual != m["after"]:
                outcome = "conflict"
            else:
                _event(journal, operation + "_writing", index=i)
                _persist(path, journal)  # Never restore memory without a durable intent.
                guard()
                # Journal I/O can take time: check the condition again afterwards.
                actual = _read(memory, m)
                if actual == m["before"]:
                    outcome = "already_before"
                elif actual != m["after"]:
                    outcome = "conflict"
                else:
                    written = memory.write(m["address"], m["before"])
                    guard()
                    if type(written) is not int or written != len(m["before"]):
                        raise OSError("복원 중 부분 쓰기가 발생했습니다.")
                    if _read(memory, m) != m["before"]:
                        raise OSError("복원 후 확인 값이 다릅니다.")
                    outcome = "restored"
            result = {"index": i, "status": outcome}
            entry[operation] = outcome
            _event(journal, operation + "_result", **result)
            _persist(path, journal)
            results.append(result)
        except Exception as exc:
            result = {"index": i, "status": "blocked", "error": str(exc)}
            entry[operation] = "blocked"
            results.append(result)
            _event(journal, operation + "_result", **result)
            # A lease failure, I/O failure, or incomplete restoration ends all
            # subsequent restoration writes. Unvisited entries stay unresolved.
            for pending in list(reversed(indices))[position + 1:]:
                results.append({"index": pending, "status": "not_attempted"})
            break
    return results


def _finish(path: Path, journal: dict, result: dict, *, strict: bool = False) -> dict:
    journal["status"] = result["status"]
    journal["result"] = result.copy()
    try:
        _persist(path, journal, checkpoint=True)
    except Exception as exc:
        if strict:
            raise
        result["ok"] = False
        result["journal_error"] = str(exc)
        result["status"] = "journal_error"
    return result


def apply_transaction(memory: Memory, mutations: list[dict], journal_path: Path,
                      guard: Callable[[], None]) -> dict:
    """Apply validated local mutations; return an honest partial-failure result.

    Invalid input and an existing journal raise before any write. Operational
    failures are returned and journaled. The guard must fail on changed leases,
    saves, target identities, or unsupported builds; never reuse stale addresses.
    """
    items = _validate(mutations)
    if not callable(guard):
        raise ValueError("프로세스·세이브 검사 함수가 필요합니다.")
    path = Path(journal_path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("기존 트랜잭션 저널을 덮어쓸 수 없습니다: " + str(path))
    journal = _Journal({"format": FORMAT, "transaction_id": str(uuid.uuid4()), "created_at": _now(),
               "status": "prepared", "events": [], "mutations": [
                   {**m, "before": m["before"].hex(), "after": m["after"].hex(),
                    "state": "pending", "attempted": False} for m in items]})
    result = {"ok": False, "status": "rejected", "applied": 0,
              "rolled_back": 0, "unchanged": 0, "unresolved": [], "journal_path": str(path)}
    try:
        guard()
        for m in items:
            if _read(memory, m) != m["before"]:
                raise ValueError("변경 전 값이 달라졌습니다: " + m["label"])
    except Exception as exc:
        result["error"] = str(exc)
        journal["status"] = "rejected"
        journal["result"] = result.copy()
        _event(journal, "preflight_rejected", error=str(exc))
        _persist(path, journal, create=True)
        return result
    _event(journal, "preflight_passed")
    _persist(path, journal, create=True)  # Failure here permits zero memory writes.
    active_entry, write_started = None, False
    try:
        for i, m in enumerate(items):
            entry = journal["mutations"][i]
            active_entry, write_started = entry, False
            if m["before"] == m["after"]:
                entry["state"] = "unchanged"
                result["unchanged"] += 1
                continue
            guard()
            # Check again after other mutations and journal I/O; no stale preview.
            if _read(memory, m) != m["before"]:
                raise ValueError("적용 직전 값이 달라졌습니다: " + m["label"])
            entry["state"], entry["attempted"] = "writing", True
            _event(journal, "writing", index=i)
            _persist(path, journal)
            guard()
            if _read(memory, m) != m["before"]:
                raise ValueError("저널 저장 중 값이 달라졌습니다: " + m["label"])
            write_started = True
            written = memory.write(m["address"], m["after"])
            guard()
            if type(written) is not int or written != len(m["after"]):
                raise OSError("부분 쓰기가 발생했습니다: " + m["label"])
            if _read(memory, m) != m["after"]:
                raise OSError("변경 후 확인 값이 다릅니다: " + m["label"])
            entry["state"] = "applied"
            result["applied"] += 1
            _event(journal, "applied", index=i)
            _persist(path, journal)
        result.update(ok=True, status="applied" if result["applied"] else "unchanged")
        return _finish(path, journal, result, strict=True)
    except Exception as exc:
        result["ok"] = False
        if active_entry is not None and not write_started:
            # An intent was durable, but our process never called memory.write.
            # Do not undo another editor that changed to the same planned value.
            active_entry["attempted"] = False
            if active_entry["state"] == "writing":
                active_entry["state"] = "not_written"
                _event(journal, "not_written", index=i)
        result["error"] = str(exc)
        _event(journal, "apply_failed", error=str(exc))
        restored = _restore(memory, items, journal, path, guard, "rollback")
        result["rolled_back"] = sum(r["status"] == "restored" for r in restored)
        result["unresolved"] = [r for r in restored if r["status"] not in ("restored", "already_before")]
        result["status"] = "failed_unresolved" if result["unresolved"] else "failed_rolled_back"
        return _finish(path, journal, result)


def undo_transaction(memory: Memory, journal_path: Path, guard: Callable[[], None]) -> dict:
    """Conditionally undo this journal's attempted writes, in reverse order.

    Never use an old journal with a different process or loaded save. The caller
    owns that lease check. Values changed by the game or another editor remain
    untouched and are reported as conflicts. A new save is still recommended.
    """
    if not callable(guard):
        raise ValueError("프로세스·세이브 검사 함수가 필요합니다.")
    path = Path(journal_path)
    journal = _load_journal(path)
    raw_items = journal.get("mutations")
    if not isinstance(raw_items, list) or len(raw_items) > MAX_MUTATIONS:
        raise ValueError("저널의 변경 목록을 확인하세요.")
    try:
        items = _validate([{**m, "before": bytes.fromhex(m["before"]),
                            "after": bytes.fromhex(m["after"])} for m in raw_items])
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("저널의 변경 항목을 확인하세요.") from exc
    if any(type(m.get("attempted")) is not bool for m in raw_items):
        raise ValueError("저널의 적용 이력을 확인하세요.")
    _event(journal, "undo_requested")
    _persist(path, journal)
    restored = _restore(memory, items, journal, path, guard, "undo")
    unresolved = [r for r in restored if r["status"] not in ("restored", "already_before")]
    result = {"ok": not unresolved, "status": "undo_unresolved" if unresolved else "undone",
              "restored": sum(r["status"] == "restored" for r in restored),
              "already_before": sum(r["status"] == "already_before" for r in restored),
              "unresolved": unresolved, "journal_path": str(path)}
    return _finish(path, journal, result)
