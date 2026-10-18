"""Original Windows x64 process adapter. No injection or remote code execution.

Layout facts are pinned in docs/automation-research.md. These are NOT verified
against a running copy of FM26 in this development environment. A matching
version is a necessary gate, not a claim of verified compatibility.
"""
from __future__ import annotations

import ctypes as C
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import struct
import time
import uuid

SUPPORTED = {(26, 3, 0, 0), (26, 3, 2, 0)}
U32, U64, PTR = C.c_uint32, C.c_uint64, C.c_void_p


class NativeError(RuntimeError):
    pass


class MemoryInfo(C.Structure):
    _fields_ = [('base', U64), ('allocation_base', U64), ('allocation_protect', U32),
                ('partition_id', C.c_uint16), ('region_size', U64), ('state', U32),
                ('protect', U32), ('kind', U32)]


class ProcessEntry(C.Structure):
    _fields_ = [('size', U32), ('usage', U32), ('pid', U32), ('heap', C.c_size_t),
                ('module', U32), ('threads', U32), ('parent', U32), ('priority', C.c_int32),
                ('flags', U32), ('exe', C.c_wchar * 260)]


class ModuleEntry(C.Structure):
    _fields_ = [('size', U32), ('module_id', U32), ('pid', U32), ('global_usage', U32),
                ('process_usage', U32), ('base', PTR), ('image_size', U32), ('handle', PTR),
                ('name', C.c_wchar * 256), ('path', C.c_wchar * 260)]


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def decode_date(raw):
    year, day = raw >> 16, raw & 0x1ff
    if not 1900 <= year <= 2200 or not 1 <= day <= 366:
        raise ValueError('Invalid FM date')
    date = dt.date(year, 1, 1) + dt.timedelta(days=day - 1)
    if date.year != year:
        raise ValueError('Invalid leap day')
    return date.isoformat()


class WindowsMemory:
    def __init__(self):
        if os.name != 'nt' or C.sizeof(PTR) != 8:
            raise NativeError('실제 게임 연결은 64비트 Windows에서만 사용할 수 있습니다.')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        specs = {
            'OpenProcess': ([U32, C.c_int, U32], PTR),
            'CloseHandle': ([PTR], C.c_int),
            'CreateToolhelp32Snapshot': ([U32, U32], PTR),
            'Process32FirstW': ([PTR, C.POINTER(ProcessEntry)], C.c_int),
            'Process32NextW': ([PTR, C.POINTER(ProcessEntry)], C.c_int),
            'Module32FirstW': ([PTR, C.POINTER(ModuleEntry)], C.c_int),
            'Module32NextW': ([PTR, C.POINTER(ModuleEntry)], C.c_int),
            'ReadProcessMemory': ([PTR, PTR, PTR, C.c_size_t, C.POINTER(C.c_size_t)], C.c_int),
            'WriteProcessMemory': ([PTR, PTR, PTR, C.c_size_t, C.POINTER(C.c_size_t)], C.c_int),
            'VirtualQueryEx': ([PTR, PTR, C.POINTER(MemoryInfo), C.c_size_t], C.c_size_t),
            'GetProcessTimes': ([PTR, PTR, PTR, PTR, PTR], C.c_int),
            'GetExitCodeProcess': ([PTR, C.POINTER(U32)], C.c_int),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        self.handle, self.write_handle = None, None
        pids = []
        handle = self._snapshot(2, 0)
        try:
            entry = ProcessEntry()
            entry.size = C.sizeof(entry)
            ok = self.k.Process32FirstW(handle, C.byref(entry))
            while ok:
                if entry.exe.lower() == 'fm.exe':
                    pids.append(entry.pid)
                ok = self.k.Process32NextW(handle, C.byref(entry))
        finally:
            self.k.CloseHandle(handle)
        if len(pids) != 1:
            raise NativeError('FM26의 fm.exe를 하나만 실행하고 세이브를 완전히 불러와 주세요.')
        self.pid = pids[0]
        self.handle = self.k.OpenProcess(0x0400 | 0x0010, False, self.pid)
        if not self.handle:
            raise NativeError('게임 읽기 권한이 없습니다. 게임과 도구의 실행 권한을 맞춰 주세요.')
        self.modules = []
        handle = self._snapshot(0x08 | 0x10, self.pid)
        try:
            entry = ModuleEntry()
            entry.size = C.sizeof(entry)
            ok = self.k.Module32FirstW(handle, C.byref(entry))
            while ok:
                self.modules.append({'name': entry.name.lower(), 'base': int(entry.base or 0),
                                     'size': int(entry.image_size), 'path': entry.path})
                ok = self.k.Module32NextW(handle, C.byref(entry))
        finally:
            self.k.CloseHandle(handle)
        matches = [m for m in self.modules if m['name'] == 'game_plugin.dll']
        if len(matches) != 1:
            self.close()
            raise NativeError('FM26 game_plugin.dll이 없습니다. 게임을 완전히 불러왔는지 확인하세요.')
        self.plugin = matches[0]
        self.db_modules = [m for m in self.modules if m['name'] in {'game_plugin.dll', 'gameassembly.dll'}]
        self.version = self._version(self.plugin['path'])
        self.supported = tuple(self.version) in SUPPORTED
        self.lease = {'pid': self.pid, 'start_time': str(self._creation_time()),
                      'module_path': self.plugin['path'], 'module_version': list(self.version),
                      'module_sha256': file_hash(self.plugin['path'])}
        self._module_stat = self._stat()
        self._offset_cache = {}

    def _snapshot(self, flags, pid):
        handle = self.k.CreateToolhelp32Snapshot(flags, pid)
        if not handle or handle == C.c_void_p(-1).value:
            raise NativeError('Windows 프로세스 목록을 읽지 못했습니다.')
        return handle

    def _creation_time(self):
        times = [U64() for _ in range(4)]
        if not self.k.GetProcessTimes(self.handle, *(C.byref(x) for x in times)):
            raise NativeError('프로세스 생성 시각 확인 실패')
        return times[0].value

    def _stat(self):
        s = Path(self.plugin['path']).stat()
        return s.st_size, s.st_mtime_ns

    def _version(self, path):
        dll = C.WinDLL('version', use_last_error=True)
        dll.GetFileVersionInfoSizeW.argtypes, dll.GetFileVersionInfoSizeW.restype = [C.c_wchar_p, C.POINTER(U32)], U32
        dll.GetFileVersionInfoW.argtypes, dll.GetFileVersionInfoW.restype = [C.c_wchar_p, U32, U32, PTR], C.c_int
        dll.VerQueryValueW.argtypes, dll.VerQueryValueW.restype = [PTR, C.c_wchar_p, C.POINTER(PTR), C.POINTER(U32)], C.c_int
        dummy = U32()
        size = dll.GetFileVersionInfoSizeW(path, C.byref(dummy))
        if not size or size > 8 * 1024 * 1024:
            return ()
        buffer = C.create_string_buffer(size)
        ptr, length = PTR(), U32()
        if not dll.GetFileVersionInfoW(path, 0, size, buffer) or not dll.VerQueryValueW(buffer, '\\', C.byref(ptr), C.byref(length)) or length.value < 52:
            return ()
        values = C.cast(ptr, C.POINTER(U32 * 13)).contents
        if values[0] != 0xFEEF04BD:
            return ()
        return values[2] >> 16, values[2] & 0xffff, values[3] >> 16, values[3] & 0xffff

    def assert_lease(self, expected=None):
        exit_code = U32()
        if not self.handle or not self.k.GetExitCodeProcess(self.handle, C.byref(exit_code)) or exit_code.value != 259:
            raise NativeError('게임 프로세스가 종료됐습니다. 다시 검사하세요.')
        if str(self._creation_time()) != self.lease['start_time'] or self._stat() != self._module_stat:
            raise NativeError('게임 프로세스 또는 DLL이 바뀌었습니다. 다시 검사하세요.')
        if expected is not None and expected != self.lease:
            raise NativeError('다른 게임 세션의 변경 계획입니다.')

    def enable_writes(self):
        self.assert_lease()
        if not self.supported:
            raise NativeError('이 game_plugin.dll 전체 버전은 쓰기 허용 대상이 아닙니다.')
        if file_hash(self.plugin['path']) != self.lease['module_sha256']:
            raise NativeError('검사 후 게임 DLL이 변경됐습니다.')
        if not self.write_handle:
            self.write_handle = self.k.OpenProcess(0x0400 | 0x0010 | 0x0020 | 0x0008, False, self.pid)
        if not self.write_handle:
            raise NativeError('게임 쓰기 권한이 없습니다. 읽기 검사만 수행했습니다.')

    def read(self, address, size):
        if not isinstance(address, int) or address < 0x10000 or size <= 0 or size > 8 * 1024 * 1024:
            raise NativeError('읽기 범위가 올바르지 않습니다.')
        buffer, count = C.create_string_buffer(size), C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, PTR(address), buffer, size, C.byref(count)) or count.value != size:
            raise NativeError('게임 메모리 읽기가 불완전합니다. 게임을 일시정지하고 다시 검사하세요.')
        return buffer.raw

    def write(self, address, data):
        if not self.write_handle or len(data) not in {1, 2, 4, 8}:
            raise NativeError('쓰기 세션 또는 필드 크기가 올바르지 않습니다.')
        buffer, count = C.create_string_buffer(data), C.c_size_t()
        if not self.k.WriteProcessMemory(self.write_handle, PTR(address), buffer, len(data), C.byref(count)) or count.value != len(data):
            raise NativeError('게임 메모리 쓰기가 불완전합니다.')
        return count.value

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def _in_module(self, address, size=1):
        return any(m['base'] <= address and address + size <= m['base'] + m['size'] for m in self.db_modules)

    def _dynamic_offset(self, vtable):
        if vtable not in self._offset_cache:
            try:
                meta = self.u64(vtable - 8)
                off = struct.unpack('<i', self.read(meta + 4, 4))[0] if self._in_module(meta, 8) else 0
                self._offset_cache[vtable] = off if off in {0x288, 0x380} else 0
            except NativeError:
                self._offset_cache[vtable] = 0
        return self._offset_cache[vtable]

    def _text(self, address):
        data = bytearray()
        for offset in range(0, 256, 16):
            block = self.read(address + offset, 16)
            data.extend(block.split(b'\0', 1)[0])
            if b'\0' in block:
                break
        else:
            raise ValueError('Unterminated name')
        result = bytes(data).decode('utf-8', 'strict').strip()
        if len(result) > 100 or any(ord(c) < 32 for c in result):
            raise ValueError('Invalid name')
        return result

    def name_at(self, reference, nested):
        if not reference:
            return ''
        inner = self.u64(reference) if nested else reference
        return self._text(inner + 4) if inner else ''

    def player_at(self, person):
        header = self.read(person, 0x138)
        vtable, = struct.unpack_from('<Q', header)
        if not self._in_module(vtable) or not (offset := self._dynamic_offset(vtable)):
            raise ValueError('Not a player object')
        row_id, uid, random_id = struct.unpack_from('<III', header, 8)
        if uid in {0, 0xffffffff}:
            raise ValueError('Invalid UID')
        player = person - offset
        ca, pa = struct.unpack('<HH', self.read(player + 0x264, 4))
        world_rep, = struct.unpack('<H', self.read(player + 0x262, 2))
        positions = self.read(player + 0x150, 15)
        if not 1 <= ca <= 200 or not 1 <= pa <= 200 or world_rep > 10000 or max(positions) > 20 or max(positions) < 1:
            raise ValueError('Invalid player fields')
        refs = {key: struct.unpack_from('<Q', header, where)[0] for key, where in [('full', 0x40), ('first', 0x50), ('last', 0x58), ('common', 0x60)]}
        names = {key: self.name_at(ref, key != 'full') for key, ref in refs.items()}
        name = names['common'] or ' '.join(x for x in [names['first'], names['last']] if x)
        if not name or not any(c.isalpha() for c in name):
            raise ValueError('Invalid name')
        dob_raw, = struct.unpack_from('<I', header, 0x88)
        return {'uid': uid, 'random_id': random_id, 'row_id': row_id, 'person': person, 'vtable': vtable,
                'player': player, 'name': name, 'full_name': names['full'], 'name_refs': refs,
                'female': bool(header[0x19] & 0x10), 'gender_flags': header[0x19],
                'dob': decode_date(dob_raw), 'dob_raw': dob_raw, 'ca': ca, 'pa': pa,
                'positions': positions.hex(), 'freeze_flags': header[0x132], 'world_rep': world_rep}

    def regions(self):
        regions, address = [], 0x10000
        while address < 0x7fffffffffff:
            info = MemoryInfo()
            size = self.k.VirtualQueryEx(self.handle, PTR(address), C.byref(info), C.sizeof(info))
            if not size:
                break
            if not info.region_size or info.base + info.region_size <= address:
                raise NativeError('Windows 메모리 영역 정보가 잘못되었습니다.')
            if info.state == 0x1000 and info.kind == 0x20000 and not info.protect & (0x100 | 1) and info.protect & (4 | 8):
                regions.append((int(info.base), int(info.region_size)))
            address = info.base + info.region_size
        if sum(size for _, size in regions) > 32 * 1024**3:
            raise NativeError('검사 대상 메모리가 32GiB를 넘습니다. 안전하게 자동 검사를 중단했습니다.')
        return regions

    def scan(self, progress=lambda _: None):
        self.assert_lease()
        started = time.monotonic()
        regions = self.regions()
        total = sum(size for _, size in regions)
        highs = {m['base'] >> 32 for m in self.db_modules} | {(m['base'] + m['size'] - 1) >> 32 for m in self.db_modules}
        pattern = re.compile(b'|'.join(re.escape(struct.pack('<I', n)) for n in sorted(highs)))
        found, duplicates, unread, done = {}, set(), 0, 0
        for start, size in regions:
            position = 0
            while position < size:
                want = min(4 * 1024 * 1024, size - position)
                extra = min(0x140, size - position - want)
                try:
                    data = self.read(start + position, want + extra)
                except NativeError:
                    unread += want
                    done += want
                    position += want
                    continue
                for match in pattern.finditer(data):
                    i = match.start() - 4
                    address = start + position + i
                    if i < 0 or address % 8 or i >= want or i + 16 > len(data):
                        continue
                    vtable, = struct.unpack_from('<Q', data, i)
                    if not self._in_module(vtable) or not self._dynamic_offset(vtable):
                        continue
                    try:
                        person = self.player_at(address)
                    except (NativeError, ValueError, UnicodeError, struct.error):
                        continue
                    uid = person['uid']
                    if uid in found and found[uid]['person'] != address:
                        duplicates.add(uid)
                    else:
                        found[uid] = person
                position += want
                done += want
                progress(f'게임 메모리 검사 {done * 100 // max(1, total)}% · 선수 {len(found):,}명 · {int(time.monotonic()-started)}초')
                self.assert_lease()
        # A final identity read catches moving/freed player objects during the scan.
        for person in found.values():
            try:
                head = self.read(person['person'] + 8, 12)
                if head != struct.pack('<III', person['row_id'], person['uid'], person['random_id']):
                    unread += 1
            except NativeError:
                unread += 1
        return {'revision': uuid.uuid4().hex, 'lease': dict(self.lease), 'players': list(found.values()),
                'complete': bool(found) and unread == 0 and not duplicates,
                'duplicates': sorted(duplicates), 'unread_bytes': unread,
                'scanned_bytes': total, 'elapsed_seconds': round(time.monotonic() - started, 1)}

    def close(self):
        for name in ['write_handle', 'handle']:
            if handle := getattr(self, name, None):
                self.k.CloseHandle(handle)
                setattr(self, name, None)

    def __del__(self):
        if hasattr(self, 'k'):
            self.close()
