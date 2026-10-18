"""End-to-end app transactions against a simulated FM26 process, never the game."""
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from fm26auto.backend import Backend, IdentityGuardedMemory
from fm26auto.native import decode_date, WindowsMemory, NativeError
from fm26auto.planner import NAME_OFFSETS

spec = importlib.util.spec_from_file_location('fm26_auto_main', ROOT / 'automation/main.py')
MAIN = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MAIN)
PNG = bytes.fromhex('89504e470d0a1a0a') + b'local-simulated-png-fixture'


class GameStub:
    def __init__(self):
        self.supported = True
        self.lease = {'pid': 123, 'start_time': '1', 'module_version': [26, 3, 2, 0], 'module_sha256': 'a' * 64}
        self.db_modules, self.rows, self.bytes, self.names = [], [], {}, {}
        self.writes = []
        self.fail_at = None
        for i in range(12):
            self.add(100 + i, '연예인' + str(i), female=i < 6, born='2000-01-02')

    def store(self, address, value):
        self.bytes.update({address + i: b for i, b in enumerate(value)})

    def add(self, uid, name, female=True, born='2019-02-03'):
        from datetime import date
        index = len(self.rows)
        person = 0x20000 + index * 0x1000
        refs = {k: 0x900000 + index * 0x1000 + j * 0x100 for j, k in enumerate(NAME_OFFSETS)}
        self.names.update({ref: name for ref in refs.values()})
        dob = date.fromisoformat(born)
        raw = dob.year << 16 | dob.timetuple().tm_yday
        row = {'uid': uid, 'random_id': 10000 + uid, 'row_id': index + 1, 'person': person, 'player': person - 0x288,
               'vtable': 0x123450000, 'name': name, 'full_name': name, 'name_refs': refs,
               'female': female, 'dob': born, 'dob_raw': raw, 'ca': 100, 'pa': 160, 'freeze_flags': 0x01}
        self.store(person, struct.pack('<QIII', row['vtable'], row['row_id'], uid, row['random_id']))
        for key, offset in NAME_OFFSETS.items():
            self.store(person + offset, struct.pack('<Q', refs[key]))
        self.store(person + 0x88, struct.pack('<I', raw))
        self.store(person + 0x132, b'\x01')
        self.store(person + 0x19, bytes([0x12 if female else 0x02]))
        self.store(row['player'] + 0x264, struct.pack('<HH', 100, 160))
        self.rows.append(row)
        return row

    def current(self, row):
        row = copy.deepcopy(row)
        row['name_refs'] = {k: struct.unpack('<Q', self.read(row['person'] + v, 8))[0] for k, v in NAME_OFFSETS.items()}
        row['name'] = self.names[row['name_refs']['common']]
        row['full_name'] = self.names[row['name_refs']['full']]
        row['dob_raw'] = struct.unpack('<I', self.read(row['person'] + 0x88, 4))[0]
        row['dob'] = decode_date(row['dob_raw'])
        row['freeze_flags'] = self.read(row['person'] + 0x132, 1)[0]
        row['ca'], row['pa'] = struct.unpack('<HH', self.read(row['player'] + 0x264, 4))
        return row

    def scan(self, progress):
        progress('모의 검사')
        import uuid
        return {'revision': uuid.uuid4().hex, 'lease': copy.deepcopy(self.lease), 'complete': True,
                'duplicates': [], 'players': [self.current(r) for r in self.rows], 'unread_bytes': 0}

    def read(self, address, size):
        return bytes(self.bytes[address + i] for i in range(size))

    def write(self, address, data):
        if self.fail_at is not None and len(self.writes) == self.fail_at:
            raise OSError('simulated process failure')
        self.writes.append((address, data))
        self.store(address, data)
        return len(data)

    def assert_lease(self, expected=None):
        if expected is not None and expected != self.lease:
            raise ValueError('changed process lease')

    def enable_writes(self):
        if not self.supported:
            raise ValueError('unsupported version')

    def close(self):
        pass


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.resources = self.root / 'resources'
        (self.resources / 'data').mkdir(parents=True)
        self.photos, self.graphics = self.root / 'photos', self.root / 'graphics'
        self.photos.mkdir()
        self.graphics.mkdir()
        catalog = []
        for i in range(6):
            name = '연예인' + str(i)
            path = f'group/{i}.png'
            photo = self.photos / path
            photo.parent.mkdir(exist_ok=True)
            photo.write_bytes(PNG)
            catalog.append({'name': name, 'path': path, 'sha256': hashlib.sha256(PNG).hexdigest()})
        (self.resources / 'data/identities.json').write_text(json.dumps(catalog), encoding='utf-8')
        with (self.resources / 'data/celebrity-cohort.csv').open('w', encoding='utf-8', newline='') as file:
            w = csv.DictWriter(file, fieldnames=['name', 'DOB_in_mod'])
            w.writeheader()
            w.writerows({'name': r['name'], 'DOB_in_mod': '2000-01-02'} for r in catalog)
        self.game = GameStub()
        self.app = Backend(self.resources, self.root / 'user', lambda: self.game)
        self.app.run('settings', {'career_label': '테스트', 'game_date': '2025-07-01', 'photos_path': str(self.photos), 'graphics_path': str(self.graphics)})

    def baseline(self):
        self.app.run('scan', {})
        self.app.run('baseline', {})

    def new_season(self):
        self.baseline()
        youth = self.game.add(9000001, 'Generated Girl')
        self.game.add(9000002, 'Generated Boy', female=False)
        self.game.store(self.game.rows[0]['player'] + 0x264, struct.pack('<H', 80))
        self.app.run('settings', {'game_date': '2035-07-01'})
        self.app.run('scan', {})
        self.app.run('preview', {})
        self.assertEqual(len(self.app.proposal['candidates']), 1)
        return youth

    def apply(self):
        return self.app.run('apply', {'revision': self.app.proposal['revision'],
                                    'selected_keys': [self.app.proposal['candidates'][0]['key']],
                                    'maintain': True, 'paused': True, 'acknowledge_experimental': True})

    def test_no_csv_baseline_to_next_intake_name_face_maintenance_and_undo(self):
        youth = self.new_season()
        self.assertEqual(self.game.writes, [])
        self.apply()
        assignment = self.app.state()['assignments'][f"{youth['uid']}:{youth['random_id']}"]
        current = self.game.current(youth)
        self.assertEqual(current['name'], assignment['name'])
        self.assertEqual(current['full_name'], assignment['name'])
        self.assertEqual((current['ca'], current['pa']), (100, 160))
        old = self.game.current(self.game.rows[0])
        self.assertEqual(old['freeze_flags'], 0x21)
        self.assertEqual(old['ca'], 100)
        self.assertEqual(old['dob'], '2013-01-02')
        self.assertEqual(self.game.read(old['person'] + 0x19, 1), b'\x12')
        output = self.graphics / 'fm26_celebrity_auto'
        self.assertIn('r-9000001/portrait', (output / 'config.xml').read_text())
        self.assertEqual(next((output / 'faces').iterdir()).read_bytes(), PNG)
        self.app.run('undo', {})
        self.assertEqual(self.app.state()['assignments'], {})
        self.assertEqual(self.game.current(youth)['name'], 'Generated Girl')
        self.assertEqual(self.game.current(self.game.rows[0])['dob'], '2000-01-02')
        self.assertEqual(self.game.current(self.game.rows[0])['ca'], 80)
        self.assertFalse(output.exists())

    def test_successful_apply_requires_fresh_scan_before_new_preview(self):
        self.new_season()
        self.apply()
        self.assertTrue(self.app.status()['scan']['stale'])
        self.assertFalse(self.app.status()['scan']['can_write'])
        self.assertTrue(self.app.status()['can_undo'])
        with self.assertRaisesRegex(ValueError, '다시'):
            self.app.run('preview', {})

    def test_modified_portrait_pack_blocks_undo_before_memory_changes(self):
        self.new_season()
        self.apply()
        output = self.graphics / 'fm26_celebrity_auto'
        next((output / 'faces').iterdir()).write_bytes(PNG + b'user edits')
        count = len(self.game.writes)
        with self.assertRaises(ValueError):
            self.app.run('undo', {})
        self.assertEqual(len(self.game.writes), count)
        self.assertEqual(len(self.app.state()['assignments']), 1)

    def test_settings_date_may_be_empty_until_exact_probe(self):
        self.app.run('settings', {'game_date': ''})
        self.app.run('scan', {})
        with self.assertRaisesRegex(ValueError, '날짜'):
            self.app.run('baseline', {})
        self.assertEqual(self.game.writes, [])

    def test_already_present_youth_need_explicit_include_option(self):
        youth = self.game.add(9000020, 'Existing young girl', born='2009-01-01')
        self.baseline()
        self.app.run('preview', {})
        self.assertEqual(self.app.proposal['candidates'], [])
        self.app.run('preview', {'include_existing': True})
        self.assertEqual([p['uid'] for p in self.app.proposal['candidates']], [youth['uid']])
        self.apply()
        self.assertEqual(len(self.app.state()['assignments']), 1)
        self.assertEqual(len(self.app.state()['seen']), 13)

    def test_memory_failure_keeps_state_and_pack_and_requires_rescan(self):
        self.new_season()
        self.game.fail_at = 1
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.app.state()['assignments'], {})
        self.assertTrue(self.app.snapshot_stale)
        self.assertIn('transaction', self.app.status()['last_result'])
        self.assertFalse((self.graphics / 'fm26_celebrity_auto').exists())

    def test_automatic_date_cannot_be_overridden_by_stale_manual_setting(self):
        self.app.date_info = {'source': 'exact-aob', 'date': '2030-07-01', 'raw': 0, 'address': 123456}
        self.app.run('settings', {'game_date': '2040-01-01'})
        self.assertEqual(self.app._date(), '2030-07-01')
        self.assertEqual(self.app.settings['game_date'], '2030-07-01')

    def test_wrong_version_and_missing_ack_and_stale_preview_write_nothing(self):
        self.new_season()
        body = {'revision': self.app.proposal['revision'], 'selected_keys': [], 'maintain': True}
        with self.assertRaises(ValueError):
            self.app.run('apply', body)
        self.game.supported = False
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.apply()
        self.assertEqual(self.game.writes, [])

    def test_photo_hash_failure_happens_before_memory_writes(self):
        self.new_season()
        for path in self.photos.rglob('*.png'):
            path.write_bytes(PNG + b'changed')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.game.writes, [])
        self.assertFalse((self.graphics / 'fm26_celebrity_auto').exists())
        self.assertEqual(self.app.state()['assignments'], {})

    def test_reused_target_address_is_rejected_before_write(self):
        youth = self.new_season()
        self.game.store(youth['person'] + 12, struct.pack('<I', 777))
        with self.assertRaisesRegex(ValueError, '바뀌'):
            self.apply()
        self.assertEqual(self.game.writes, [])

    def test_changed_donor_pointer_cannot_be_written_to_another_player(self):
        self.new_season()
        proposal = self.app._plan({self.app.proposal['candidates'][0]['key']})
        wrapped = IdentityGuardedMemory(self.game, proposal, self.app.snapshot)
        m = next(m for m in proposal['mutations'] if m['label'].startswith('name.'))
        donor = wrapped.donors_by_target[m['uid']]
        self.game.store(donor['person'] + 0x40, struct.pack('<Q', 0xDEADBEEF))
        with self.assertRaisesRegex(ValueError, '문자열'):
            wrapped.write(m['address'], m['after'])
        self.assertEqual(self.game.writes, [])

    def test_two_name_donors_are_checked_for_the_corresponding_target(self):
        first = self.new_season()
        second = self.game.add(9000010, 'Second Girl')
        self.app.run('scan', {})
        selected = {f"{p['uid']}:{p['random_id']}" for p in [first, second]}
        proposal = self.app._plan(selected)
        wrapped = IdentityGuardedMemory(self.game, proposal, self.app.snapshot)
        mutation = next(m for m in proposal['mutations'] if m['uid'] == second['uid'] and m['label'].startswith('name.'))
        donor = wrapped.donors_by_target[second['uid']]
        self.game.store(donor['person'] + 0x40, struct.pack('<Q', 0xDEADBEEF))
        with self.assertRaisesRegex(ValueError, '문자열'):
            wrapped.write(mutation['address'], mutation['after'])
        self.assertEqual(self.game.writes, [])

    def test_guarded_write_denies_unknown_address_or_unplanned_value(self):
        self.new_season()
        proposal = self.app._plan({self.app.proposal['candidates'][0]['key']})
        wrapped = IdentityGuardedMemory(self.game, proposal, self.app.snapshot)
        m = proposal['mutations'][0]
        with self.assertRaises(ValueError):
            wrapped.write(m['address'] + 1, m['after'])
        with self.assertRaises(ValueError):
            wrapped.write(m['address'], b'badvalue')

    def test_gender_changed_after_scan_blocks_target_or_donor(self):
        youth = self.new_season()
        proposal = self.app._plan({self.app.proposal['candidates'][0]['key']})
        wrapped = IdentityGuardedMemory(self.game, proposal, self.app.snapshot)
        m = next(m for m in proposal['mutations'] if m['uid'] == youth['uid'] and m['label'].startswith('name.'))
        self.game.store(youth['person'] + 0x19, b'\x02')
        with self.assertRaisesRegex(ValueError, '여성'):
            wrapped.write(m['address'], m['after'])
        self.game.store(youth['person'] + 0x19, b'\x12')
        donor = wrapped.donors_by_target[youth['uid']]
        self.game.store(donor['person'] + 0x19, b'\x02')
        with self.assertRaisesRegex(ValueError, '여성'):
            wrapped.write(m['address'], m['after'])
        self.assertEqual(self.game.writes, [])

    def test_local_server_requires_session_and_origin_and_json(self):
        server = MAIN.create_server(self.app, ROOT / 'automation/ui', 'secret')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + '/api/status')
        self.assertEqual(caught.exception.code, 403)
        request = urllib.request.Request(base + '/api/status', headers={'X-Session-Key': 'secret'})
        with urllib.request.urlopen(request) as response:
            self.assertIn('settings', json.load(response))
        request = urllib.request.Request(base + '/api/settings', data=b'{}', headers={'X-Session-Key': 'secret', 'Origin': 'https://elsewhere.invalid', 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_native_date_decoder_rejects_invalid_and_no_windows_connection(self):
        self.assertEqual(decode_date(2024 << 16 | 366), '2024-12-31')
        with self.assertRaises(ValueError):
            decode_date(2025 << 16 | 366)
        if sys.platform != 'win32':
            with self.assertRaises(NativeError):
                WindowsMemory()


if __name__ == '__main__':
    unittest.main()
