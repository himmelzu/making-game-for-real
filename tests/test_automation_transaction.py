"""Failure-injection checks for the local engine, not live FM26 validation."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import automation.fm26auto.transaction as transaction_module
from automation.fm26auto.transaction import apply_transaction, undo_transaction


class FakeMemory:
    def __init__(self):
        self.bytes = {}
        self.calls = []
        self.behaviors = {}
        self.on_write = None
        self.read_hook = None

    def seed(self, address, data):
        for offset, value in enumerate(data):
            self.bytes[address + offset] = value

    def read(self, address, size):
        if self.read_hook:
            self.read_hook(address, size)
        return bytes(self.bytes[address + offset] for offset in range(size))

    def write(self, address, data):
        self.calls.append((address, data))
        index = len(self.calls)
        if self.on_write:
            self.on_write(address, data)
        behavior = self.behaviors.get(index)
        if behavior == "raise":
            raise OSError("injected write failure")
        if behavior == "partial":
            self.seed(address, data[:1])
            return 1
        self.seed(address, data)
        if behavior == "raise_after":
            raise OSError("write completed but API failed")
        if behavior == "corrupt":
            self.seed(address, b"!" * len(data))
        return len(data)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "journal.json"
        self.memory = FakeMemory()
        self.items = [self.item(0x1000, b"old1", b"new1", 1),
                      self.item(0x1008, b"old2", b"new2", 2),
                      self.item(0x1010, b"old3", b"new3", 3)]
        for item in self.items:
            self.memory.seed(item["address"], item["before"])
        self.guard_calls = 0

    @staticmethod
    def item(address, before, after, uid):
        return {"address": address, "before": before, "after": after,
                "uid": uid, "random_id": 77 + uid, "label": "테스트 선수 " + str(uid)}

    def guard(self):
        self.guard_calls += 1

    def apply(self, items=None, guard=None):
        return apply_transaction(self.memory, self.items if items is None else items,
                                 self.path, guard or self.guard)

    def journal(self):
        # During an operation, progress lives in the fsynced JSONL sidecar.
        return transaction_module._load_journal(self.path)

    def assert_original(self):
        for item in self.items:
            self.assertEqual(self.memory.read(item["address"], 4), item["before"])

    def test_success_is_durably_journaled_before_each_write_and_reversible(self):
        fsync = __import__("os").fsync
        fsync_count = 0

        def sync(descriptor):
            nonlocal fsync_count
            fsync_count += 1
            return fsync(descriptor)

        def inspect_intent(address, data):
            journal = self.journal()
            entries = [m for m in journal["mutations"] if m["address"] == address]
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["attempted"])
            self.assertEqual(entries[0]["state"], "writing")
            self.assertEqual(entries[0]["after"], data.hex())
            self.assertGreater(fsync_count, len(self.memory.calls))

        self.memory.on_write = inspect_intent
        with mock.patch("automation.fm26auto.transaction.os.fsync", side_effect=sync):
            result = self.apply()
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], 3)
        self.assertGreaterEqual(self.guard_calls, 1 + 3 * 3)
        self.assertEqual(self.journal()["status"], "applied")
        self.memory.on_write = None
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], 3)
        self.assert_original()
        self.assertEqual([address for address, _ in self.memory.calls[3:]], [0x1010, 0x1008, 0x1000])
        self.assertEqual(self.journal()["status"], "undone")

    def test_preflight_checks_all_before_any_write(self):
        self.memory.seed(0x1010, b"oops")
        result = self.apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.memory.calls, [])
        self.assertEqual(self.journal()["status"], "rejected")

    def test_unsupported_build_or_initial_lease_failure_writes_nothing(self):
        def rejected():
            raise RuntimeError("unsupported game build")
        result = self.apply(guard=rejected)
        self.assertFalse(result["ok"])
        self.assertIn("unsupported", result["error"])
        self.assertEqual(self.memory.calls, [])

    def test_invalid_sizes_overlap_duplicate_ranges_and_counts_rejected(self):
        bad = [
            [self.item(0x1000, b"123", b"456", 1)],
            [self.item(0x1000, b"1", b"22", 1)],
            [self.item(-1, b"1", b"2", 1)],
            [self.item(True, b"1", b"2", 1)],
            [self.item(2**64 - 1, b"12", b"34", 1)],
            [self.items[0], self.item(0x1002, b"12", b"34", 4)],
            [self.items[0], self.items[0]],
            [self.items[0]] * 10001,
            [{**self.items[0], "before": bytearray(b"old1")}],
            [{**self.items[0], "random_id": "78"}],
            [{**self.items[0], "label": "bad\x00label"}],
        ]
        for items in bad:
            with self.subTest(items=str(items[:2])):
                with self.assertRaises(ValueError):
                    self.apply(items)
        self.assertEqual(self.memory.calls, [])
        self.assertFalse(self.path.exists())

    def test_adjacent_ranges_and_all_allowed_sizes(self):
        items, address = [], 0x2000
        for i, length in enumerate((1, 2, 4, 8)):
            item = self.item(address, bytes([10]) * length, bytes([20]) * length, i + 1)
            items.append(item)
            self.memory.seed(address, item["before"])
            address += length
        self.assertTrue(self.apply(items)["ok"])

    def test_existing_journal_is_never_replaced(self):
        self.path.write_text("keep me", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(self.path.read_text(), "keep me")
        self.assertEqual(self.memory.calls, [])

    def test_initial_fsync_failure_permits_zero_memory_writes(self):
        with mock.patch("automation.fm26auto.transaction.os.fsync", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.memory.calls, [])

    def test_nth_write_failure_stops_later_writes_and_rolls_back_prior(self):
        self.memory.behaviors[2] = "raise"
        result = self.apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rolled_back"], 1)
        self.assertEqual(self.memory.calls, [(0x1000, b"new1"), (0x1008, b"new2"), (0x1000, b"old1")])
        self.assert_original()

    def test_short_write_is_not_falsely_reported_as_rolled_back(self):
        self.memory.behaviors[2] = "partial"
        result = self.apply()
        self.assertEqual(result["status"], "failed_unresolved")
        self.assertEqual(result["rolled_back"], 1)
        self.assertEqual(result["unresolved"], [{"index": 1, "status": "conflict"}])
        self.assertEqual(self.memory.read(0x1008, 4), b"nld2")
        self.assertNotIn((0x1010, b"new3"), self.memory.calls)
        self.assertEqual(self.memory.read(0x1000, 4), b"old1")

    def test_exception_after_full_write_still_restores_our_exact_after(self):
        self.memory.behaviors[2] = "raise_after"
        result = self.apply()
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(result["rolled_back"], 2)
        self.assert_original()

    def test_readback_mismatch_preserves_concurrent_value_and_reports_conflict(self):
        self.memory.behaviors[1] = "corrupt"
        result = self.apply()
        self.assertEqual(result["status"], "failed_unresolved")
        self.assertEqual(len(self.memory.calls), 1)
        self.assertEqual(self.memory.read(0x1000, 4), b"!!!!")

    def test_one_guard_failure_between_items_can_roll_back_under_renewed_guard(self):
        def one_failure():
            self.guard()
            if self.guard_calls == 5:
                raise RuntimeError("temporary snapshot invalidation")
        result = self.apply(guard=one_failure)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(self.memory.calls, [(0x1000, b"new1"), (0x1000, b"old1")])
        self.assert_original()

    def test_persistent_process_restart_blocks_rollback_and_all_further_writes(self):
        def restarted():
            self.guard()
            if self.guard_calls >= 5:
                raise RuntimeError("process creation time changed")
        result = self.apply(guard=restarted)
        self.assertEqual(result["status"], "failed_unresolved")
        self.assertEqual(result["unresolved"][0]["status"], "blocked")
        self.assertEqual(self.memory.calls, [(0x1000, b"new1")])

    def test_guard_detecting_uid_reuse_after_write_prevents_even_readback(self):
        reads_after_change = 0
        invalid = False

        def write_hook(address, data):
            nonlocal invalid
            invalid = True

        def read_hook(address, size):
            nonlocal reads_after_change
            if invalid:
                reads_after_change += 1

        def changed_identity():
            if invalid:
                raise RuntimeError("same address now belongs to another UID")

        self.memory.on_write, self.memory.read_hook = write_hook, read_hook
        result = self.apply(guard=changed_identity)
        self.assertEqual(result["status"], "failed_unresolved")
        self.assertEqual(reads_after_change, 0)
        self.assertEqual(len(self.memory.calls), 1)

    def test_changed_value_after_preflight_is_rechecked_before_its_write(self):
        def alter_second(address, data):
            if address == 0x1000 and data == b"new1":
                self.memory.seed(0x1008, b"user")
        self.memory.on_write = alter_second
        result = self.apply()
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(self.memory.read(0x1008, 4), b"user")
        self.assertEqual(self.memory.calls, [(0x1000, b"new1"), (0x1000, b"old1")])

    def test_undo_conflict_does_not_clobber_game_or_editor_changes(self):
        self.apply()
        self.memory.seed(0x1008, b"user")
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "undo_unresolved")
        self.assertEqual(result["restored"], 2)
        self.assertEqual(result["unresolved"], [{"index": 1, "status": "conflict"}])
        self.assertEqual(self.memory.read(0x1008, 4), b"user")
        self.assertTrue(any(e["action"] == "undo_result" and e["status"] == "conflict" for e in self.journal()["events"]))

    def test_repeated_undo_is_idempotent_and_rejected_transaction_is_not_undone(self):
        self.apply()
        undo_transaction(self.memory, self.path, self.guard)
        calls = len(self.memory.calls)
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["already_before"], 3)
        self.assertEqual(len(self.memory.calls), calls)

    def test_undo_write_failure_stops_all_later_undo_writes(self):
        self.apply()
        self.memory.behaviors[4] = "partial"
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertFalse(result["ok"])
        self.assertEqual([r["status"] for r in result["unresolved"]], ["blocked", "not_attempted", "not_attempted"])
        self.assertEqual(len(self.memory.calls), 4)

    def test_undo_rejects_corrupt_or_overlapping_journals_before_memory_access(self):
        self.apply()
        journal = self.journal()
        journal["mutations"][1]["address"] = 0x1001
        self.path.write_text(json.dumps(journal), encoding="utf-8")
        calls = len(self.memory.calls)
        with self.assertRaises(ValueError):
            undo_transaction(self.memory, self.path, self.guard)
        self.assertEqual(len(self.memory.calls), calls)

    def test_noops_are_journaled_but_never_written_or_undone(self):
        item = {**self.items[0], "after": b"old1"}
        result = self.apply([item])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(self.memory.calls, [])
        self.assertTrue(undo_transaction(self.memory, self.path, self.guard)["ok"])
        self.assertEqual(self.memory.calls, [])

    def test_change_to_same_planned_value_during_journal_io_is_not_our_write(self):
        persist = transaction_module._persist

        def alter_during_intent(path, journal, **kwargs):
            persist(path, journal, **kwargs)
            last = journal["events"][-1]
            if last["action"] == "writing" and last["index"] == 0:
                self.memory.seed(0x1000, b"new1")

        with mock.patch.object(transaction_module, "_persist", side_effect=alter_during_intent):
            result = self.apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self.memory.calls, [])
        self.assertEqual(self.memory.read(0x1000, 4), b"new1")
        self.assertFalse(self.journal()["mutations"][0]["attempted"])

    def test_rollback_rechecks_condition_after_journal_io(self):
        persist = transaction_module._persist
        self.memory.behaviors[2] = "raise"

        def alter_during_restore_intent(path, journal, **kwargs):
            persist(path, journal, **kwargs)
            last = journal["events"][-1]
            if last["action"] == "rollback_writing":
                self.memory.seed(0x1000, b"USER")

        with mock.patch.object(transaction_module, "_persist", side_effect=alter_during_restore_intent):
            result = self.apply()
        self.assertEqual(result["status"], "failed_unresolved")
        self.assertEqual(result["unresolved"], [{"index": 0, "status": "conflict"}])
        self.assertEqual(len(self.memory.calls), 2)
        self.assertEqual(self.memory.read(0x1000, 4), b"USER")

    def test_final_journal_failure_is_a_failure_and_attempts_rollback(self):
        persist = transaction_module._persist
        failed = False

        def fail_final_once(path, journal, **kwargs):
            nonlocal failed
            if journal["status"] == "applied" and not failed:
                failed = True
                raise OSError("final journal failure")
            return persist(path, journal, **kwargs)

        with mock.patch.object(transaction_module, "_persist", side_effect=fail_final_once):
            result = self.apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(result["rolled_back"], 3)
        self.assert_original()

    def test_persistent_journal_failure_never_permits_unjournaled_rollback(self):
        persist = transaction_module._persist

        def disk_fails_after_first_write(path, journal, **kwargs):
            if self.memory.calls:
                raise OSError("disk became unavailable")
            return persist(path, journal, **kwargs)

        with mock.patch.object(transaction_module, "_persist", side_effect=disk_fails_after_first_write):
            result = self.apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "journal_error")
        self.assertIn("disk", result["journal_error"])
        self.assertEqual(result["unresolved"][0]["status"], "blocked")
        self.assertEqual(self.memory.calls, [(0x1000, b"new1")])

    def test_interrupted_process_can_recover_from_durable_write_intent(self):
        write = self.memory.write

        def crash_after_write(address, data):
            write(address, data)
            raise KeyboardInterrupt("simulated abrupt interruption")

        with mock.patch.object(self.memory, "write", side_effect=crash_after_write):
            with self.assertRaises(KeyboardInterrupt):
                self.apply()
        self.assertEqual(self.journal()["mutations"][0]["state"], "writing")
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], 1)
        self.assert_original()

    def test_rejected_transaction_cannot_undo_values_it_never_wrote(self):
        self.memory.seed(0x1000, b"new1")
        result = self.apply()
        self.assertEqual(result["status"], "rejected")
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], 0)
        self.assertEqual(self.memory.calls, [])
        self.assertEqual(self.memory.read(0x1000, 4), b"new1")

    def test_short_read_during_preflight_is_rejected_without_writes(self):
        read = self.memory.read

        def short_read(address, length):
            return read(address, length)[:-1]

        with mock.patch.object(self.memory, "read", side_effect=short_read):
            result = self.apply()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.memory.calls, [])

    def large_plan(self, count=1500):
        items = []
        for i in range(count):
            item = self.item(0x10000 + i * 8, (i + 1).to_bytes(4, "little"),
                             (i + 10000).to_bytes(4, "little"), i + 1)
            items.append(item)
            self.memory.seed(item["address"], item["before"])
        return items

    def test_1500_mutations_use_only_initial_and_final_full_json_snapshots(self):
        items = self.large_plan()
        snapshot = transaction_module._write_snapshot
        snapshots, progress_sizes = [], []

        def count_snapshot(path, journal, **kwargs):
            snapshots.append((len(journal["mutations"]), kwargs.get("create", False)))
            return snapshot(path, journal, **kwargs)

        def inspect_last_write(address, data):
            if len(self.memory.calls) == len(items):
                progress_sizes.append(transaction_module._progress_path(self.path).stat().st_size)
                base = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertTrue(all(m["state"] == "pending" for m in base["mutations"]))

        self.memory.on_write = inspect_last_write
        with mock.patch.object(transaction_module, "_write_snapshot", side_effect=count_snapshot):
            result = self.apply(items)
            self.assertTrue(result["ok"])
            self.assertEqual(result["applied"], 1500)
            self.assertEqual(snapshots, [(1500, True), (1500, False)])
            self.memory.on_write = None
            restored = undo_transaction(self.memory, self.path, self.guard)
            self.assertTrue(restored["ok"])
            self.assertEqual(restored["restored"], 1500)
            self.assertEqual(snapshots, [(1500, True), (1500, False), (1500, False)])
        self.assertGreater(progress_sizes[0], 0)
        self.assertLess(progress_sizes[0], 3 * 1024 * 1024)
        self.assertFalse(transaction_module._progress_path(self.path).exists())
        final = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(sum(e["action"] == "writing" for e in final["events"]), 1500)
        self.assertEqual(sum(e["action"] == "applied" for e in final["events"]), 1500)

    def test_large_interrupted_plan_replays_progress_and_restores_every_attempted_field(self):
        items = self.large_plan()
        write = self.memory.write

        def crash_after_700(address, data):
            result = write(address, data)
            if len(self.memory.calls) == 700:
                raise KeyboardInterrupt("simulated crash after field 700")
            return result

        with mock.patch.object(self.memory, "write", side_effect=crash_after_700):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(items)
        base = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(all(not m["attempted"] for m in base["mutations"]))
        recovered = self.journal()
        self.assertEqual(sum(m["attempted"] for m in recovered["mutations"]), 700)
        self.assertEqual(recovered["mutations"][699]["state"], "writing")
        self.assertEqual(recovered["mutations"][698]["state"], "applied")
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], 700)
        for item in items:
            self.assertEqual(self.memory.read(item["address"], 4), item["before"])

    def crash_with_one_attempted_write(self):
        write = self.memory.write

        def interrupted(address, data):
            write(address, data)
            raise KeyboardInterrupt("simulated interruption")

        with mock.patch.object(self.memory, "write", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.apply()
        return transaction_module._progress_path(self.path)

    def test_partial_final_progress_line_is_ignored_and_repaired_before_undo(self):
        progress = self.crash_with_one_attempted_write()
        with progress.open("ab") as stream:
            stream.write(b'{"sequence":2,"events":')
        result = undo_transaction(self.memory, self.path, self.guard)
        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], 1)
        self.assert_original()
        self.assertFalse(progress.exists())

    def test_checksum_corruption_or_different_transaction_blocks_undo(self):
        progress = self.crash_with_one_attempted_write()
        raw = progress.read_bytes()
        record = json.loads(raw)
        record["events"][0]["index"] = 1
        progress.write_text(json.dumps(record) + "\n", encoding="utf-8")
        before = len(self.memory.calls)
        with self.assertRaisesRegex(ValueError, "체크섬"):
            undo_transaction(self.memory, self.path, self.guard)
        self.assertEqual(len(self.memory.calls), before)

    def test_checkpoint_and_old_sidecar_surviving_unlink_crash_are_not_double_replayed(self):
        captured = []
        snapshot = transaction_module._write_snapshot

        def capture_progress_before_checkpoint(path, journal, **kwargs):
            progress = transaction_module._progress_path(path)
            if progress.exists():
                captured.append(progress.read_bytes())
            return snapshot(path, journal, **kwargs)

        with mock.patch.object(transaction_module, "_write_snapshot", side_effect=capture_progress_before_checkpoint):
            self.assertTrue(self.apply()["ok"])
        base = self.journal()
        transaction_module._progress_path(self.path).write_bytes(captured[-1])
        recovered = self.journal()
        self.assertEqual(len(recovered["events"]), len(base["events"]))
        self.assertTrue(undo_transaction(self.memory, self.path, self.guard)["ok"])
        self.assert_original()


if __name__ == "__main__":
    unittest.main()
