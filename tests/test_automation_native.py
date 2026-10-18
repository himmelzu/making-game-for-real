"""Check the real scanner/decoder against a synthetic native address space."""
import datetime as dt
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automation'))
from fm26auto.native import WindowsMemory, NativeError, decode_date


class NativeFixture(WindowsMemory):
    def __init__(self):
        self.base = 0x7FFB12340000
        self.db_modules = [{'base': self.base, 'size': 0x2000, 'name': 'game_plugin.dll'}]
        self.lease = {'pid': 99, 'start_time': '1', 'module_sha256': 'a' * 64}
        self._offset_cache = {}
        self.heap_start, self.heap_size = 0x20000, 0x20000
        self.raw = {self.heap_start + i: 0 for i in range(self.heap_size)}
        self.raw.update({self.base + i: 0 for i in range(0x2000)})
        self.vtable, self.meta = self.base + 0x800, self.base + 0x100
        self.put(self.vtable - 8, struct.pack('<Q', self.meta))
        self.put(self.meta + 4, struct.pack('<i', 0x288))
        self.put(0x30000, struct.pack('<Q', 0x31000))
        self.put(0x31000, struct.pack('<I', 9) + '연예인'.encode() + b'\0' * 16)
        self.put(0x32000, struct.pack('<I', 9) + '전체명'.encode() + b'\0' * 16)
        self.person = 0x21000
        self.add_player(self.person, 9000001, 1234)
        self.read_failure = False

    def put(self, address, data):
        self.raw.update({address + i: value for i, value in enumerate(data)})

    def add_player(self, person, uid, random_id, female=True):
        self.put(person, struct.pack('<QIII', self.vtable, uid - 9000000, uid, random_id))
        self.put(person + 0x19, bytes([0x12 if female else 0x02]))
        for offset in [0x50, 0x58, 0x60]:
            self.put(person + offset, struct.pack('<Q', 0x30000))
        self.put(person + 0x40, struct.pack('<Q', 0x32000))
        date = dt.date(2009, 2, 3)
        self.put(person + 0x88, struct.pack('<I', date.year << 16 | date.timetuple().tm_yday))
        self.put(person + 0x132, b'\x01')
        self.put(person - 0x288 + 0x264, struct.pack('<HH', 80, 160))
        self.put(person - 0x288 + 0x150, bytes([20]) + bytes(14))

    def read(self, address, size):
        if self.read_failure:
            raise NativeError('simulated unreadable memory')
        try:
            return bytes(self.raw[address + i] for i in range(size))
        except KeyError as error:
            raise NativeError('unmapped address') from error

    def regions(self):
        return [(self.heap_start, self.heap_size)]

    def assert_lease(self, expected=None):
        if expected is not None and expected != self.lease:
            raise NativeError('different lease')


class NativeTests(unittest.TestCase):
    def test_real_scanner_finds_one_woman_through_module_vtable_and_metadata(self):
        memory = NativeFixture()
        progress = []
        snapshot = memory.scan(progress.append)
        self.assertTrue(snapshot['complete'])
        self.assertEqual(len(snapshot['players']), 1)
        person = snapshot['players'][0]
        self.assertEqual((person['uid'], person['random_id']), (9000001, 1234))
        self.assertEqual((person['name'], person['full_name']), ('연예인', '전체명'))
        self.assertEqual(person['dob'], '2009-02-03')
        self.assertTrue(person['female'])
        self.assertEqual((person['ca'], person['pa']), (80, 160))
        self.assertTrue(progress)

    def test_real_scanner_flags_duplicate_valid_player_objects(self):
        memory = NativeFixture()
        memory.add_player(0x25000, 9000001, 1234)
        snapshot = memory.scan()
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['duplicates'], [9000001])

    def test_real_scanner_does_not_treat_staff_offset_as_player(self):
        memory = NativeFixture()
        memory.put(memory.meta + 4, struct.pack('<i', 0x100))
        snapshot = memory.scan()
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['players'], [])

    def test_real_scanner_rejects_invalid_ca_and_name_encoding(self):
        memory = NativeFixture()
        memory.put(memory.person - 0x288 + 0x264, struct.pack('<H', 999))
        self.assertEqual(memory.scan()['players'], [])

    def test_real_scanner_excludes_future_placeholder_reputation(self):
        memory = NativeFixture()
        memory.put(memory.person - 0x288 + 0x262, b'\xff\xff')
        self.assertEqual(memory.scan()['players'], [])
        memory = NativeFixture()
        memory.put(0x31004, b'\xff\xff\0')
        self.assertEqual(memory.scan()['players'], [])

    def test_real_scanner_distinguishes_male_and_reports_incomplete_reads(self):
        memory = NativeFixture()
        memory.put(memory.person + 0x19, b'\x02')
        self.assertFalse(memory.scan()['players'][0]['female'])
        memory.read_failure = True
        snapshot = memory.scan()
        self.assertFalse(snapshot['complete'])
        self.assertGreater(snapshot['unread_bytes'], 0)

    def test_packed_fm_dates_preserve_flags_and_reject_out_of_year_day(self):
        self.assertEqual(decode_date(2024 << 16 | 0x8000 | 60), '2024-02-29')
        with self.assertRaises(ValueError):
            decode_date(2023 << 16 | 366)


if __name__ == '__main__':
    unittest.main()
