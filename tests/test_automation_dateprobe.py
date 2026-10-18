"""Native date discovery on synthetic memory, never a live process."""

import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("automation_dateprobe", ROOT / "automation/fm26auto/dateprobe.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class Memory:
    def __init__(self, size=8192, base=0x100000):
        self.base, self.data = base, bytearray(size)
        self.requests = []
        self.fail = False
        self.short = False
        self.unstable = False
        self.date_address = None
        self.date_reads = 0

    def read(self, address, size):
        self.requests.append((address, size))
        if self.fail:
            raise OSError("unreadable")
        begin = address - self.base
        if begin < 0 or begin + size > len(self.data):
            raise OSError("outside region")
        result = bytes(self.data[begin:begin + size])
        if self.short:
            return result[:-1]
        if address == self.date_address and size == 4:
            self.date_reads += 1
            if self.unstable and self.date_reads == 2:
                return struct.pack("<I", struct.unpack("<I", result)[0] + 1)
        return result

    def install(self, hit=100, target=300, year=2035, day=183, extra_bits=0):
        signature = PROBE.PREFIX + struct.pack("<i", target - hit - 7) + PROBE.SUFFIX
        self.data[hit:hit + len(signature)] = signature
        self.data[target:target + 4] = struct.pack("<I", (year << 16) | extra_bits | day)
        self.date_address = self.base + target
        return self

    def modules(self, **updates):
        module = {"name": "game_plugin.dll", "base": self.base, "size": len(self.data)}
        module.update(updates)
        return [module]


class DateProbeTests(unittest.TestCase):
    def test_exact_static_value_not_pointer_is_read(self):
        memory = Memory().install(year=2035, day=183)
        result = PROBE.find_current_date(memory, memory.modules())
        self.assertEqual(result, {"date": "2035-07-02", "address": memory.base + 300,
                                  "raw": (2035 << 16) | 183, "source": "exact-aob"})
        self.assertEqual(memory.date_reads, 2)

    def test_negative_signed_rip_displacement(self):
        memory = Memory().install(hit=1000, target=100, year=2055, day=1)
        self.assertEqual(PROBE.find_current_date(memory, memory.modules())["date"], "2055-01-01")

    def test_time_flags_do_not_pollute_day_mask(self):
        memory = Memory().install(year=2028, day=60, extra_bits=0xE600)
        result = PROBE.find_current_date(memory, memory.modules())
        self.assertEqual(result["date"], "2028-02-29")
        self.assertEqual(result["raw"] & 0xFE00, 0xE600)

    def test_cross_chunk_signature_is_found_once_and_reads_are_bounded(self):
        memory = Memory(PROBE.CHUNK_SIZE + 4096).install(
            hit=PROBE.CHUNK_SIZE - 5, target=500, year=2040, day=366)
        result = PROBE.find_current_date(memory, memory.modules())
        self.assertEqual(result["date"], "2040-12-31")
        self.assertLessEqual(max(size for _, size in memory.requests), PROBE.CHUNK_SIZE)

    def test_overlap_match_is_not_counted_twice(self):
        memory = Memory(PROBE.CHUNK_SIZE + 4096).install(
            hit=PROBE.CHUNK_SIZE - 15, target=500)
        self.assertEqual(PROBE.find_current_date(memory, memory.modules())["source"], "exact-aob")

    def test_no_signature_or_multiple_signatures_fail(self):
        for memory in (Memory(), Memory().install()):
            if memory.date_address is not None:
                memory.install(hit=900, target=600)
            with self.subTest(multiple=memory.date_address is not None), self.assertRaises(PROBE.DateProbeError):
                PROBE.find_current_date(memory, memory.modules())

    def test_wrong_module_is_ignored_and_module_case_is_insensitive(self):
        memory = Memory().install()
        with self.assertRaises(PROBE.DateProbeError):
            PROBE.find_current_date(memory, memory.modules(name="GameAssembly.dll"))
        self.assertEqual(PROBE.find_current_date(memory, memory.modules(name="GAME_PLUGIN.DLL"))["source"],
                         "exact-aob")

    def test_duplicate_modules_invalid_ranges_and_size_cap_fail_before_read(self):
        memory = Memory().install()
        bad = [memory.modules() * 2, memory.modules(size=0),
               memory.modules(size=PROBE.MAX_MODULE_SIZE + 1), memory.modules(base=0),
               memory.modules(base=PROBE.MAX_ADDRESS), memory.modules(size=True)]
        for modules in bad:
            with self.subTest(modules=modules), self.assertRaises(PROBE.DateProbeError):
                PROBE.find_current_date(memory, modules)
        self.assertEqual(memory.requests, [])

    def test_unreadable_or_partial_chunks_fail(self):
        for flag in ("fail", "short"):
            memory = Memory().install()
            setattr(memory, flag, True)
            with self.subTest(flag=flag), self.assertRaises(PROBE.DateProbeError):
                PROBE.find_current_date(memory, memory.modules())

    def test_unstable_date_fails(self):
        memory = Memory().install()
        memory.unstable = True
        with self.assertRaisesRegex(PROBE.DateProbeError, "changed while reading"):
            PROBE.find_current_date(memory, memory.modules())

    def test_invalid_year_day_and_nonleap_day366_fail(self):
        for year, day in ((1899, 1), (2201, 1), (2025, 0), (2025, 366), (2024, 367)):
            memory = Memory().install(year=year, day=day)
            with self.subTest(year=year, day=day), self.assertRaises(PROBE.DateProbeError):
                PROBE.find_current_date(memory, memory.modules())

    def test_target_outside_module_or_implausible_address_fails(self):
        for displacement in (len(Memory().data) + 100, -0x200000):
            memory = Memory().install()
            memory.data[103:107] = struct.pack("<i", displacement)
            with self.subTest(displacement=displacement), self.assertRaises(PROBE.DateProbeError):
                PROBE.find_current_date(memory, memory.modules())


if __name__ == "__main__":
    unittest.main()
