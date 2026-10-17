(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FMCareer = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_ROWS = 100000;
  function need(ok, message) { if (!ok) throw new Error(message); }
  function clean(value) { return String(value == null ? '' : value).normalize('NFC').trim(); }
  function id(value) {
    const s = clean(value).replace(/^r-/i, '');
    need(/^[1-9][0-9]{0,17}$/.test(s), '선수 ID는 0이 아닌 숫자여야 합니다.');
    return s;
  }
  function text(value, label, max) {
    const s = clean(value);
    need(s.length <= (max || 200) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(s), label + ' 형식을 확인하세요.');
    return s;
  }
  function number(value, min, max, label) {
    const s = clean(value);
    if (s === '') return null;
    need(/^\d+$/.test(s), label + '는 정수여야 합니다.');
    const n = Number(s);
    need(n >= min && n <= max, label + ' 범위를 확인하세요.');
    return n;
  }
  function date(value) {
    const s = clean(value);
    need(/^\d{4}-\d{2}-\d{2}$/.test(s), '날짜는 YYYY-MM-DD 형식으로 입력하세요.');
    const d = new Date(s + 'T12:00:00Z');
    need(Number.isFinite(d.getTime()) && d.toISOString().slice(0, 10) === s, '존재하지 않는 날짜입니다.');
    return s;
  }
  function parseCSV(raw) {
    raw = String(raw).replace(/^\uFEFF/, '');
    need(raw.length <= 32 * 1024 * 1024, 'CSV는 32MiB 이하로 나누어 주세요.');
    const first = raw.split(/\r?\n/, 1)[0];
    const delimiter = first.includes('\t') && !first.includes(',') ? '\t' : ',';
    const rows = []; let row = [], cell = '', quoted = false, closed = false;
    for (let i = 0; i <= raw.length; i++) {
      const c = i === raw.length ? '\n' : raw[i];
      if (quoted) {
        if (c === '"') { if (raw[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; closed = true; } }
        else { need(i < raw.length, 'CSV 따옴표가 닫히지 않았습니다.'); cell += c; }
      } else if (c === '"') {
        need(cell === '' && !closed, 'CSV 따옴표 위치를 확인하세요.'); quoted = true;
      } else if (c === delimiter || c === '\n' || c === '\r') {
        row.push(cell); cell = ''; closed = false;
        if (c !== delimiter) {
          if (row.some(x => x.trim())) rows.push(row);
          row = []; if (c === '\r' && raw[i + 1] === '\n') i++;
        }
      } else { need(!closed || c === ' ', '닫는 따옴표 뒤에 잘못된 문자가 있습니다.'); if (!closed) cell += c; }
      need(rows.length <= MAX_ROWS + 1, 'CSV 인원이 너무 많습니다.');
    }
    need(rows.length > 1, '머리글과 선수 데이터가 필요합니다.');
    const headers = rows.shift().map(x => clean(x).toLowerCase());
    need(new Set(headers).size === headers.length && headers.every(Boolean), 'CSV 머리글이 비었거나 중복되었습니다.');
    return rows.map((cells, i) => {
      need(cells.length === headers.length, (i + 2) + '행의 열 개수가 다릅니다.');
      const o = Object.create(null); headers.forEach((h, j) => { o[h] = cells[j]; }); return o;
    });
  }
  function flag(v, yes, no, label) {
    const s = clean(v).toLowerCase();
    if (!s) return null;
    if (yes.includes(s)) return true;
    if (no.includes(s)) return false;
    throw new Error(label + ' 값이 올바르지 않습니다.');
  }
  function players(raw) {
    const seen = new Set();
    const aliases = { uid: 'id', uniqueid: 'id', '고유id': 'id', '고유번호': 'id', '고유번호id': 'id', '이름': 'name', '선수': 'name', '선수이름': 'name', '나이': 'age', '성별': 'gender', '구단': 'club', '소속': 'club', '팀': 'club', '소속구단': 'club', team: 'club', '현재능력': 'ca', '잠재능력': 'pa', '생년월일': 'dob', '유소년': 'newgen' };
    return parseCSV(raw).map((row, i) => {
      const o = Object.create(null);
      for (const [key, v] of Object.entries(row)) {
        const canonical = aliases[key.replace(/[\s_]+/g, '')] || key;
        need(!Object.hasOwn(o, canonical), '같은 뜻의 열이 중복되었습니다: ' + canonical);
        o[canonical] = v;
      }
      const uid = id(o.id); need(!seen.has(uid), 'ID ' + uid + '가 중복되었습니다.'); seen.add(uid);
      const name = text(o.name, '이름'); need(name, (i + 2) + '행의 이름이 없습니다.');
      return { id: uid, name, club: text(o.club, '구단'), age: number(o.age, 0, 120, '나이'),
        female: flag(o.gender, ['f', 'female', '여', '여자', '여성'], ['m', 'male', '남', '남자', '남성'], '성별'),
        newgen: flag(o.newgen, ['1', 'true', 'yes', 'y'], ['0', 'false', 'no', 'n'], '유소년 여부'),
        ca: number(o.ca, 1, 200, 'CA'), pa: number(o.pa, 1, 200, 'PA'), dob: clean(o.dob) ? date(o.dob) : '' };
    });
  }
  function safePath(v) {
    const s = text(v, '사진 경로', 1000).replace(/\\/g, '/');
    need(s && !s.startsWith('/') && !/[\u0000-\u001f:]/.test(s) && s.split('/').every(x => x && x !== '.' && x !== '..') && /\.png$/i.test(s), '사진은 폴더 안의 PNG 상대 경로로 지정하세요.');
    return s;
  }
  function catalog(rows) {
    const seen = new Set();
    need(Array.isArray(rows) && rows.length > 0 && rows.length <= 10000, '연예인 목록 크기를 확인하세요.');
    return rows.map(o => {
      const name = text(o.name, '연예인 이름'), path = safePath(o.path);
      need(name && !seen.has(path), '연예인 이름이 없거나 사진 경로가 중복되었습니다.'); seen.add(path);
      const sha256 = clean(o.sha256).toLowerCase();
      need(!sha256 || /^[0-9a-f]{64}$/.test(sha256), '사진 SHA-256 형식을 확인하세요.');
      return { name, path, sha256 };
    });
  }
  function newState(saveKey) {
    const key = text(saveKey, '세이브 이름'); need(key, '세이브 구분 이름을 입력하세요.');
    return { format: 'fm26-career-v1', saveKey: key, assignments: [], baseline: [], seen: [], next: 0, lastDate: '' };
  }
  function restore(raw, key) {
    need(String(raw).length <= 64 * 1024 * 1024, '기록 파일이 너무 큽니다.');
    const s = JSON.parse(raw); need(s && s.format === 'fm26-career-v1', '이 도구의 기록 파일이 아닙니다.');
    const out = newState(s.saveKey); need(out.saveKey === clean(key), '세이브 이름이 다릅니다. 다른 세이브의 ID를 함께 쓰지 마세요.');
    need(Array.isArray(s.assignments) && Array.isArray(s.baseline) && Array.isArray(s.seen) && s.assignments.length <= MAX_ROWS && s.baseline.length <= MAX_ROWS && s.seen.length <= MAX_ROWS, '기록 인원 형식이 올바르지 않습니다.');
    out.assignments = s.assignments.map(a => ({ id: id(a.id), name: text(a.name, '연예인 이름'), path: safePath(a.path),
      original: text(a.original, '원래 이름'), assignedDate: date(a.assignedDate), sha256: clean(a.sha256).toLowerCase() }));
    need(out.assignments.every(a => a.name && (!a.sha256 || /^[0-9a-f]{64}$/.test(a.sha256))), '사진 기록 형식을 확인하세요.');
    out.baseline = s.baseline.map(p => ({ id: id(p.id), name: text(p.name, '이름'), ca: number(p.ca, 1, 200, 'CA'), pa: number(p.pa, 1, 200, 'PA'), dob: p.dob ? date(p.dob) : '' }));
    out.seen = s.seen.map(id);
    for (const a of [out.assignments.map(x => x.id), out.baseline.map(x => x.id), out.seen]) need(new Set(a).size === a.length, '기록에 중복 ID가 있습니다.');
    out.next = number(s.next, 0, Number.MAX_SAFE_INTEGER, '사진 순서'); need(out.next !== null, '사진 순서가 없습니다.');
    out.lastDate = s.lastDate ? date(s.lastDate) : '';
    return out;
  }
  function propose(state, roster, identities, gameDate, options) {
    date(gameDate); options = options || {};
    need(!state.lastDate || gameDate >= state.lastDate, '이전 기록보다 과거 날짜입니다. 세이브를 되돌렸다면 당시 기록도 불러오세요.');
    const copy = restore(JSON.stringify(state), state.saveKey);
    const pool = catalog(identities), assigned = new Map(copy.assignments.map(a => [a.id, a]));
    const known = new Set(copy.seen), baseline = new Map(copy.baseline.map(x => [x.id, x]));
    const added = [], reused = [];
    for (const p of [...roster].sort((a, b) => a.id.length - b.id.length || a.id.localeCompare(b.id))) {
      if (p.female !== true) continue;
      if (assigned.has(p.id)) {
        const a = assigned.get(p.id);
        need(p.name === a.original || p.name === a.name, 'ID ' + p.id + '의 이름이 기록과 다릅니다. 세이브/ID를 확인하세요.');
        continue;
      }
      const qualifies = p.newgen === true || (options.explicitYouth && p.newgen === null);
      if (!options.baselineOnly && qualifies && p.age !== null && p.age <= 21 && (!options.onlyUnseen || !known.has(p.id))) {
        if (!options.allowReuse && copy.next >= pool.length) break;
        const item = pool[copy.next % pool.length];
        const a = { id: p.id, name: item.name, path: item.path, sha256: item.sha256, original: p.name, assignedDate: gameDate };
        if (copy.next >= pool.length) reused.push(p.id);
        copy.next++; copy.assignments.push(a); assigned.set(p.id, a); added.push(a);
      }
    }
    for (const p of roster) {
      if (p.female !== true) continue;
      known.add(p.id);
      if (!baseline.has(p.id)) baseline.set(p.id, { id: p.id, name: p.name, ca: p.ca, pa: p.pa, dob: p.dob });
    }
    copy.baseline = [...baseline.values()]; copy.seen = [...known]; copy.lastDate = gameDate;
    need(copy.seen.length <= MAX_ROWS && copy.assignments.length <= MAX_ROWS, '기록 인원 제한을 넘었습니다.');
    return { state: copy, added, reused };
  }
  function csv(rows, headers) {
    function cell(v) {
      let s = String(v == null ? '' : v);
      // Spreadsheet formula protection; never used as the authoritative JSON record.
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return '\uFEFF' + [headers, ...rows.map(o => headers.map(h => o[h]))].map(r => r.map(cell).join(',')).join('\r\n') + '\r\n';
  }
  function nameTasks(state) { return csv(state.assignments, ['id', 'original', 'name', 'path', 'assignedDate']); }
  function maintenance(state, roster, gameDate, cap, target) {
    date(gameDate); need(Number.isInteger(cap) && Number.isInteger(target) && cap >= 25 && cap <= 50 && target >= 18 && target < cap, '유지 나이 설정을 확인하세요.');
    const baseline = new Map(state.baseline.map(p => [p.id, p]));
    const out = [];
    for (const p of roster) {
      if (p.female !== true) continue;
      const b = baseline.get(p.id); if (!b) continue;
      need(p.name === b.name || state.assignments.some(a => a.id === p.id && [a.original, a.name].includes(p.name)), '기준 기록과 선수 이름이 다릅니다: ' + p.id);
      let dob = '';
      if (p.age !== null && p.age >= cap) {
        const year = Number(gameDate.slice(0, 4)) - target;
        // A birthday today gives exactly target age, including Feb 29 in leap years.
        const mmdd = gameDate.slice(4) === '-02-29' ? '-02-28' : gameDate.slice(4);
        dob = year + mmdd; date(dob);
      }
      const caDrop = b.ca !== null && p.ca !== null && b.ca > p.ca;
      if (dob || caDrop) out.push({ id: p.id, name: p.name, age: p.age, proposed_dob: dob, current_ca: p.ca,
        baseline_ca: b.ca, current_pa: p.pa, baseline_pa: b.pa, action: '인게임 에디터에서 직접 확인·수정. 자동 적용 아님' });
    }
    return out;
  }
  function matchExisting(roster, identities) {
    const pool = catalog(identities), names = new Map();
    pool.forEach(p => { if (!names.has(p.name)) names.set(p.name, []); names.get(p.name).push(p); });
    const counts = new Map(); roster.forEach(p => counts.set(p.name, (counts.get(p.name) || 0) + 1));
    const exact = [], ambiguous = [];
    for (const p of roster) {
      if (p.female !== true || !names.has(p.name)) continue;
      const matches = names.get(p.name);
      if (matches.length !== 1 || counts.get(p.name) !== 1) { ambiguous.push(p); continue; }
      exact.push({ id: p.id, original: p.name, ...matches[0] });
    }
    return { exact, ambiguous };
  }
  return { parseCSV, players, catalog, id, date, newState, restore, propose, csv, nameTasks, maintenance, matchExisting };
});
