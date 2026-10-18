"""Local-only application orchestration. Runtime changes remain experimental."""
from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import uuid

from . import planner
from .assets import prepare_pack, rollback_pack, validate_rollback
from .native import WindowsMemory
from .transaction import apply_transaction, undo_transaction


class ApplyError(ValueError):
    def __init__(self, message, transaction):
        super().__init__(message)
        self.transaction = transaction


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.fm26-', delete=False) as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
        tmp = Path(file.name)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path, default=None):
    if not Path(path).exists():
        return copy.deepcopy(default)
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError('도구 기록 파일이 너무 큽니다.')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class Backend:
    def __init__(self, resource_root, data_root, memory_factory=WindowsMemory):
        self.resources, self.data_root = Path(resource_root), Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.catalog = read_json(self.resources / 'data/identities.json')
        with (self.resources / 'data/celebrity-cohort.csv').open(encoding='utf-8-sig', newline='') as file:
            self.cohort = list(csv.DictReader(file))
        self.settings = read_json(self.data_root / 'settings.json', {'career_label': '여자슈퍼연예인리그', 'game_date': '', 'photos_path': '', 'graphics_path': ''})
        self.memory_factory, self.memory, self.snapshot, self.proposal = memory_factory, None, None, None
        self._lock = threading.Lock()
        self.busy, self.progress, self.last_result = False, '', None
        self.date_info, self.last_transaction = {}, None
        self.snapshot_stale = False
        self.preview_include_existing = False

    @property
    def career_root(self):
        label = self.settings['career_label']
        digest = hashlib.sha256(label.encode('utf-8')).hexdigest()[:24]
        return self.data_root / 'careers' / digest

    @property
    def state_path(self):
        return self.career_root / 'state.json'

    def state(self):
        return read_json(self.state_path)

    def _date(self):
        value = self.date_info['date'] if self.date_info.get('source') == 'exact-aob' else self.settings.get('game_date', '')
        try:
            date = dt.date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError('현재 게임 날짜를 입력하세요. Windows의 오늘 날짜를 쓰면 안 됩니다.') from error
        if not 2025 <= date.year <= 2200:
            raise ValueError('게임 날짜 범위를 확인하세요(2025~2200년).')
        return date.isoformat()

    def _scan_gate(self):
        if self.snapshot is None or self.memory is None:
            raise ValueError('게임을 먼저 검사하세요.')
        if self.snapshot_stale:
            raise ValueError('마지막 적용으로 검사 값이 바뀌었습니다. 다시 게임을 검사하세요.')
        self.memory.assert_lease(self.snapshot['lease'])
        if not self.snapshot['complete']:
            raise ValueError('불완전한 검사 또는 중복 ID가 있습니다. 게임을 일시정지하고 다시 검사하세요.')
        if len(self.snapshot['players']) < 10:
            raise ValueError('읽은 선수가 너무 적습니다. 세이브를 완전히 불러온 뒤 검사하세요.')

    def _protected_gate(self, state):
        if len(state.get('protected', {})) < 5 or len(state.get('donors', {})) < 5:
            raise ValueError('이름·생일이 맞는 기존 연예인과 사진 이름 제공자가 각각 5명 이상 확인돼야 합니다. DB 크기와 사진 목록을 확인하세요.')

    def status(self):
        state = self.state()
        snapshot = self.snapshot
        scan = None
        if snapshot is not None:
            scan = {'players': len(snapshot['players']), 'female': sum(p['female'] for p in snapshot['players']),
                    'eligible_donors': len((state or {}).get('donors', {})),
                    'version': '.'.join(map(str, snapshot['lease'].get('module_version', []))) or '알 수 없음',
                    'can_write': bool(self.memory and self.memory.supported and snapshot['complete'] and not self.snapshot_stale),
                    'stale': self.snapshot_stale,
                    'complete': snapshot['complete'], 'duplicates': len(snapshot['duplicates']),
                    'date_source': self.date_info.get('source', 'manual'),
                    'date_message': self.date_info.get('message', ''),
                    'module_sha256': snapshot['lease']['module_sha256']}
        return {'busy': self.busy, 'progress': self.progress, 'connected': snapshot is not None,
                'can_undo': bool(self.last_transaction and self.memory and self.memory.supported),
                'settings': dict(self.settings), 'scan': scan,
                'state': {'exists': state is not None, 'protected': len((state or {}).get('protected', {})),
                          'assigned': len((state or {}).get('assignments', {})), 'seen': len((state or {}).get('seen', []))},
                'preview': self.proposal, 'last_result': self.last_result}

    def run(self, action, body):
        if not self._lock.acquire(blocking=False):
            raise ValueError('다른 작업이 진행 중입니다.')
        self.busy, self.progress = True, '작업 준비 중'
        try:
            method = getattr(self, 'action_' + action, None)
            if action not in {'settings', 'scan', 'baseline', 'preview', 'apply', 'undo'} or method is None:
                raise ValueError('알 수 없는 작업입니다.')
            method(body)
            return self.status()
        except Exception as error:
            self.last_result = {'ok': False, 'message': str(error)}
            if isinstance(error, ApplyError):
                self.last_result['transaction'] = error.transaction
            raise
        finally:
            self.busy = False
            self.progress = ''
            self._lock.release()

    def action_settings(self, body):
        values = {}
        for key in ['career_label', 'game_date', 'photos_path', 'graphics_path']:
            value = body.get(key, self.settings.get(key, ''))
            if not isinstance(value, str) or len(value) > 1500 or any(ord(c) < 32 for c in value):
                raise ValueError('설정 형식이 잘못되었습니다.')
            values[key] = value.strip()
        if not values['career_label'] or len(values['career_label']) > 100:
            raise ValueError('세이브 구분 이름을 입력하세요.')
        if values['game_date']:
            dt.date.fromisoformat(values['game_date'])
        if values['career_label'] != self.settings['career_label']:
            self.snapshot, self.proposal, self.last_transaction = None, None, None
            self.date_info = {}
            self.snapshot_stale = False
        elif self.date_info.get('source') == 'exact-aob':
            values['game_date'] = self.date_info['date']
        self.settings = values
        self.proposal = None
        atomic_json(self.data_root / 'settings.json', values)
        self.last_result = {'ok': True, 'message': '설정을 저장했습니다. 새 세이브는 별도 이름을 사용하세요.'}

    def action_scan(self, body):
        if self.memory is not None:
            self.memory.close()
        self.snapshot, self.proposal = None, None
        self.snapshot_stale, self.last_transaction = False, None
        self.memory = self.memory_factory()
        self.snapshot = self.memory.scan(lambda message: setattr(self, 'progress', message))
        self.date_info = {'source': 'manual', 'message': '정확한 게임 날짜 자동 읽기에 실패했습니다. 현재 게임 날짜를 직접 입력하세요.'}
        try:
            from .dateprobe import find_current_date
            info = find_current_date(self.memory, self.memory.db_modules)
            if 2025 <= dt.date.fromisoformat(info['date']).year <= 2200:
                self.settings['game_date'] = info['date']
                self.date_info = {**info, 'message': '게임의 현재 날짜를 직접 읽었습니다. 다음 경기 날짜나 PC 날짜를 사용하지 않습니다.'}
                atomic_json(self.data_root / 'settings.json', self.settings)
        except (ValueError, OSError, RuntimeError):
            pass
        diagnostic = {'lease': self.snapshot['lease'], 'complete': self.snapshot['complete'],
                      'duplicates': self.snapshot['duplicates'], 'players': len(self.snapshot['players']),
                      'female': sum(p['female'] for p in self.snapshot['players']),
                      'unread_bytes': self.snapshot.get('unread_bytes'), 'supported_version': self.memory.supported,
                      'date_source': self.date_info.get('source')}
        atomic_json(self.data_root / 'last-diagnostic.json', diagnostic)
        self.last_result = {'ok': True, 'message': f"읽기 검사: 선수 {len(self.snapshot['players']):,}명. 실제 게임에서 검증되지 않은 실험적 어댑터입니다."}

    def action_baseline(self, body):
        self._scan_gate()
        if self.state() is not None:
            raise ValueError('이 커리어의 기준 기록이 이미 있습니다. 새 세이브는 다른 구분 이름을 사용하세요.')
        state = planner.initialize(self.snapshot, self._date(), self.catalog, self.cohort)
        self._protected_gate(state)
        state['career_label'] = self.settings['career_label']
        atomic_json(self.state_path, state)
        self.proposal = None
        self.last_result = {'ok': True, 'message': f"기존 선수 {len(state['seen']):,}명을 기준으로 등록했습니다. 이번 등록은 게임을 변경하지 않았습니다. 다음 유소년 입단 때 다시 검사하세요."}

    def _plan(self, selected, maintain=True):
        self._scan_gate()
        state = self.state()
        if state is None:
            raise ValueError('처음에는 기존 선수 기준을 등록하세요.')
        self._protected_gate(state)
        original_seen = set(state['seen'])
        if self.preview_include_existing:
            state = copy.deepcopy(state)
            protected = set(state['protected'])
            donors = {d['donor_key'] for d in state['donors'].values()}
            assignments = set(state['assignments'])
            today = dt.date.fromisoformat(self._date())
            allowed = set()
            for p in self.snapshot['players']:
                key = f"{p['uid']}:{p['random_id']}"
                dob = dt.date.fromisoformat(p['dob'])
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                if p['female'] is True and 14 <= age <= 18 and key not in protected | donors | assignments:
                    allowed.add(key)
            state['seen'] = sorted(set(state['seen']) - allowed)
        result = planner.plan(self.snapshot, state, self._date(), self.catalog, selected_keys=selected, maintain=maintain)
        result['next_state']['seen'] = sorted(original_seen | set(result['next_state']['seen']))
        return result

    def action_preview(self, body):
        self.preview_include_existing = body.get('include_existing') is True
        base = self._plan(None)
        selected = {p['key'] for p in base['candidates'][:1000]}
        suggested = self._plan(selected)
        assignments = suggested['next_state']['assignments']
        candidates = []
        for item in base['candidates']:
            assignment = assignments.get(item['key'], {})
            candidates.append({**item, 'assigned_name': assignment.get('name', '선택 시 배정'), 'path': assignment.get('path', '')})
        by_key = {f"{p['uid']}:{p['random_id']}": p for p in self.snapshot['players']}
        maintenance = [{'uid': by_key[p['key']]['uid'], 'name': p['name'],
                        'action': f"능력치 고정·나이 점검 ({p['mutation_count']}개 변경)"} for p in base['maintenance'] if p['mutation_count']]
        self.proposal = {'revision': self.snapshot['revision'], 'candidates': candidates, 'maintenance': maintenance,
                         'warnings': ['새로 발견된 14~18세 여성 선수입니다. 뒤늦게 로드된 기존 선수도 포함될 수 있습니다.',
                                      '이름 참조 재사용·능력치 고정·나이 변경은 실제 FM26 저장/재로드로 검증하지 못한 실험 기능입니다.',
                                      '이미 확정된 은퇴 계획을 취소하거나 완전한 은퇴 방지를 보장하지 않습니다.'] + base['warnings']}
        self.last_result = {'ok': True, 'message': '게임을 변경하지 않고 적용 후보를 계산했습니다. 실제 새 유소년인지 확인하고 선택하세요.'}

    def _guard(self, plan, deep=False):
        self.memory.assert_lease(plan['lease'])
        if plan['revision'] != self.snapshot['revision']:
            raise ValueError('검사 결과가 바뀌었습니다.')
        if deep:
            players = {p['uid']: p for p in self.snapshot['players']}
            required = {m['uid'] for m in plan['mutations']}
            state = self.state()
            required |= {int(key.split(':')[0]) for key in state.get('anchors', [])}
            named_uids = {m['uid'] for m in plan['mutations'] if m['label'].startswith('name.')}
            for key, assignment in plan['next_state']['assignments'].items():
                if int(key.split(':')[0]) in named_uids:
                    required.add(int(assignment['donor_key'].split(':')[0]))
            for uid in required:
                p = players.get(uid)
                if p is None:
                    raise ValueError('대상 선수 또는 이름 제공자가 없습니다. 다시 검사하세요.')
                expected = struct.pack('<III', p['row_id'], p['uid'], p['random_id'])
                if self.memory.read(p['person'] + 8, 12) != expected:
                    raise ValueError('검사 후 선수 객체 또는 세이브가 바뀌었습니다. 다시 검사하세요.')
        if self.date_info.get('source') == 'exact-aob':
            address = self.date_info['address']
            raw = struct.unpack('<I', self.memory.read(address, 4))[0]
            if raw != self.date_info['raw']:
                raise ValueError('검사 후 게임 날짜가 바뀌었습니다. 게임을 일시정지하고 다시 검사하세요.')

    def action_apply(self, body):
        if body.get('paused') is not True or body.get('acknowledge_experimental') is not True:
            raise ValueError('별도 세이브 저장·게임 일시정지와 실험판 안내 확인이 필요합니다.')
        if not self.proposal or body.get('revision') != self.proposal['revision']:
            raise ValueError('변경 미리보기를 다시 만들어 주세요.')
        selected = body.get('selected_keys')
        if not isinstance(selected, list) or len(selected) > 1000 or any(not isinstance(k, str) for k in selected):
            raise ValueError('신규 후보는 한 번에 최대 1,000명 선택합니다.')
        plan = self._plan(set(selected), maintain=body.get('maintain') is True)
        self._guard(plan, deep=True)
        self.memory.enable_writes()
        guarded_memory = IdentityGuardedMemory(self.memory, plan, self.snapshot)
        assignments = []
        by_key = {f"{p['uid']}:{p['random_id']}": p for p in self.snapshot['players']}
        for key, a in plan['next_state']['assignments'].items():
            if key in by_key:
                assignments.append({'uid': by_key[key]['uid'], **a})
        prepared = None
        if assignments:
            if not self.settings['photos_path'] or not self.settings['graphics_path']:
                raise ValueError('기존 celeb 폴더와 FM graphics 폴더를 선택하세요.')
            self.progress = '원본 사진 해시와 얼굴팩을 준비하는 중'
            prepared = prepare_pack(Path(self.settings['photos_path']), Path(self.settings['graphics_path']), assignments)
        identifier = uuid.uuid4().hex
        journal = self.career_root / 'transactions' / (identifier + '.json')
        before = self.state()
        receipt = None
        try:
            atomic_json(self.career_root / 'state-before-last-apply.json', before)
            self.progress = f"{len(plan['mutations']):,}개 필드 일괄 적용 중"
            result = apply_transaction(guarded_memory, plan['mutations'], journal, lambda: self._guard(plan))
            if not result.get('ok'):
                self.snapshot_stale = True
                self.proposal = None
                unresolved = result.get('unresolved', [])
                raise ApplyError('게임 적용이 중단됐습니다. 복원 결과=' + str(result.get('status')) + ', 미해결=' + str(unresolved) + '. 새 적용 전에 게임의 별도 세이브를 확인하세요. 저널: ' + str(journal), result)
            try:
                if prepared is not None:
                    receipt = prepared.publish()
                atomic_json(self.state_path, plan['next_state'])
            except Exception as error:
                undo = undo_transaction(guarded_memory, journal, lambda: self._guard(plan))
                if receipt is not None:
                    rollback_pack(receipt)
                raise ValueError('얼굴팩/기록 저장에 실패해 게임 변경 복원을 시도했습니다. ' + str(error) + ' / ' + str(undo)) from error
            self.last_transaction = {'plan': plan, 'journal': str(journal), 'receipt': receipt, 'before': before, 'memory': guarded_memory}
            self.snapshot_stale = True
            new_count = len(plan['next_state']['assignments']) - len(before['assignments'])
            self.proposal = None
            self.last_result = {'ok': True, 'message': f"신규 {new_count}명 이름 배정, {len(plan['mutations'])}개 필드 적용·재읽기를 마쳤습니다. 얼굴팩 적용을 위해 그래픽을 새로고침하고 별도 이름으로 저장·재로드하여 확인하세요. 게임 검증 완료를 뜻하지 않습니다."}
        finally:
            if prepared is not None:
                prepared.abort()

    def action_undo(self, body):
        if self.last_transaction is None:
            raise ValueError('현재 도구 실행에서 되돌릴 작업이 없습니다. 이전 게임 세션의 주소는 재사용하지 않습니다.')
        record = self.last_transaction
        self._guard(record['plan'], deep=True)
        if self.state() != record['plan']['next_state']:
            raise ValueError('기록 파일이 마지막 적용 뒤 변경됐습니다. 자동 복원을 중단합니다.')
        if record['receipt'] is not None:
            validate_rollback(record['receipt'])
        result = undo_transaction(record['memory'], Path(record['journal']), lambda: self._guard(record['plan']))
        if not result.get('ok'):
            raise ValueError('일부 값이 변경되어 복원이 중단되거나 충돌했습니다. 게임의 별도 세이브를 사용하세요.')
        if record['receipt'] is not None:
            rollback_pack(record['receipt'])
        atomic_json(self.state_path, record['before'])
        self.last_transaction, self.proposal = None, None
        self.snapshot_stale = True
        self.last_result = {'ok': True, 'message': '현재 값이 도구의 적용 값과 일치하는 항목을 되돌렸습니다. 게임 화면은 새로 불러와 확인하세요.'}

    def close(self):
        if self.memory is not None:
            self.memory.close()


class IdentityGuardedMemory:
    """Validate the actual target identity for each write, including rollback.

    This keeps per-write work bounded, rather than rereading all 771 protected
    players for every byte mutation. There is no claim of atomic game access.
    """
    def __init__(self, memory, plan, snapshot):
        self.memory = memory
        by_uid = {p['uid']: p for p in snapshot['players']}
        self.allowed = {}
        for mutation in plan['mutations']:
            player = by_uid[mutation['uid']]
            if player['random_id'] != mutation['random_id'] or player['female'] is not True:
                raise ValueError('대상 식별/성별이 일치하지 않습니다.')
            self.allowed[mutation['address']] = (mutation, player)
        self.donors_by_target = {}
        named_uids = {m['uid'] for m in plan['mutations'] if m['label'].startswith('name.')}
        for key, a in plan['next_state']['assignments'].items():
            uid = int(key.split(':')[0])
            if uid in named_uids:
                self.donors_by_target[uid] = by_uid[int(a['donor_key'].split(':')[0])]

    def read(self, address, size):
        return self.memory.read(address, size)

    def _identity(self, player):
        expected = struct.pack('<III', player['row_id'], player['uid'], player['random_id'])
        if self.memory.read(player['person'] + 8, 12) != expected:
            raise ValueError('쓰기 대상 객체가 교체됐습니다. 다른 세이브나 로딩 상태일 수 있습니다.')
        if 'vtable' in player and self.memory.read(player['person'], 8) != struct.pack('<Q', player['vtable']):
            raise ValueError('선수 클래스가 바뀌었습니다.')
        if not self.memory.read(player['person'] + 0x19, 1)[0] & 0x10:
            raise ValueError('현재 여성 선수로 확인되지 않아 쓰기를 중단합니다.')

    def write(self, address, data):
        pair = self.allowed.get(address)
        if pair is None:
            raise ValueError('변경 계획에 없는 메모리 쓰기입니다.')
        mutation, player = pair
        if data not in {mutation['before'], mutation['after']}:
            raise ValueError('변경 계획에 없는 값입니다.')
        self._identity(player)
        if mutation['label'].startswith('name.') and data == mutation['after']:
            donor = self.donors_by_target[player['uid']]
            self._identity(donor)
            for field, offset in planner.NAME_OFFSETS.items():
                if self.memory.read(donor['person'] + offset, 8) != struct.pack('<Q', donor['name_refs'][field]):
                    raise ValueError('이름 제공자의 문자열 참조가 바뀌었습니다.')
        return self.memory.write(address, data)


def choose_folder(kind):
    if os.name != 'nt' or kind not in {'photos', 'graphics'}:
        raise ValueError('폴더 선택 대화상자는 Windows에서만 사용할 수 있습니다.')
    # Fixed authored command; no request text is interpolated into PowerShell.
    command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); $s = New-Object -ComObject Shell.Application; $f = $s.BrowseForFolder(0, 'FM26 folder', 0, 0); if ($f) { $f.Self.Path }"
    run = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-Command', command], capture_output=True, timeout=180, creationflags=0x08000000)
    if run.returncode:
        raise ValueError('Windows 폴더 선택을 열지 못했습니다. 경로를 직접 입력할 수 있습니다.')
    return run.stdout.decode('utf-8-sig', 'replace').strip()
