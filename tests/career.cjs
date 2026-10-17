'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const C = require('../web/career-core.js');
const pool = [{ name: '연예인 가', path: 'SM/가.png' }, { name: '연예인 나', path: 'YG/나.png' }];
const roster = raw => C.players('id,name,club,age,gender,newgen,ca,pa,dob\n' + raw);
test('CSV supports Korean, quoted comma, newline, escaped quotes, BOM and TSV', () => {
  const p = roster('123,"성, 이름","두\n줄",16,F,1,80,160,2009-01-01');
  assert.equal(p[0].name, '성, 이름'); assert.equal(p[0].club, '두\n줄');
  assert.equal(C.parseCSV('\uFEFFname\tpath\n"윤""아"\tSM/a.png')[0].name, '윤"아');
  for (const s of ['id,id\n1,2', 'id,name\n1', 'id,name\n1,"unclosed', 'id,name\n1,"a"x']) assert.throws(() => C.parseCSV(s));
});
test('Rejects duplicate IDs and invalid dates, keeps 18-digit ID strings exact', () => {
  assert.throws(() => roster('1,a,c,16,F,1,,,\n1,b,c,16,F,1,,,'), /중복/);
  assert.equal(roster('999999999999999999,a,c,16,F,1,,,')[0].id, '999999999999999999');
  for (const d of ['2025-02-29', '2025-13-01', '2030-02-30']) assert.throws(() => C.date(d));
  assert.equal(C.date('2024-02-29'), '2024-02-29');
});
test('Only explicit female youth <=21 qualify; unknown gender is excluded', () => {
  const r = roster('1,a,c,16,F,1,,,\n2,b,c,16,M,1,,,\n3,c,c,16,,1,,,\n4,d,c,30,F,1,,,\n5,e,c,16,F,0,,,\n6,f,c,17,F,,,,');
  let a = C.propose(C.newState('save'), r, pool, '2025-07-01', { allowReuse: true });
  assert.deepEqual(a.added.map(p => p.id), ['1']);
  a = C.propose(C.newState('save'), r, pool, '2025-07-01', { allowReuse: true, explicitYouth: true });
  assert.deepEqual(a.added.map(p => p.id), ['1', '6']);
});
test('30 seasons, club changes, disappearing players, repeated imports preserve assignments', () => {
  let state = C.newState('longsave');
  for (let i = 0; i < 30; i++) {
    const r = roster((100 + i) + ',신규' + i + ',구단' + i + ',16,F,1,70,140,' + (i ? '\n100,신규0,이적한 구단,20,F,1,80,140,' : ''));
    const date = (2025 + i) + '-07-01';
    const result = C.propose(state, r, pool, date, { allowReuse: true });
    state = C.restore(JSON.stringify(result.state), 'longsave');
    const again = C.propose(state, r, pool, date, { allowReuse: true });
    assert.equal(again.added.length, 0); assert.equal(state.assignments.length, i + 1);
    assert.equal(state.assignments[0].name, pool[0].name);
  }
  assert.equal(state.next, 30); assert.equal(state.seen.length, 30);
  assert.throws(() => C.restore(JSON.stringify(state), 'another-save'), /세이브/);
  assert.throws(() => C.propose(state, [], pool, '2025-07-01'), /과거/);
});
test('No-reuse mode stops at pool size, existing identities never shift on pool change', () => {
  const r = roster('1,a,c,16,F,1,,,\n2,b,c,16,F,1,,,\n3,c,c,16,F,1,,,');
  let a = C.propose(C.newState('save'), r, pool, '2025-07-01', { allowReuse: false });
  assert.equal(a.added.length, 2);
  a = C.propose(a.state, r, pool.slice().reverse(), '2026-07-01', { allowReuse: true });
  assert.equal(a.state.assignments[0].name, pool[0].name); assert.equal(a.added.length, 1);
});
test('Baseline-only snapshot never assigns identities, even to explicit youth', () => {
  const r = roster('1,a,c,16,F,1,70,150,2009-01-01');
  const a = C.propose(C.newState('save'), r, pool, '2025-07-01', { baselineOnly: true });
  assert.equal(a.added.length, 0); assert.equal(a.state.next, 0); assert.equal(a.state.baseline[0].ca, 70);
});
test('Unseen-only mode excludes prior observed IDs, name mismatch blocks cross-save mistakes', () => {
  const old = roster('1,a,c,16,F,0,,,');
  const a = C.propose(C.newState('save'), old, pool, '2025-07-01');
  const changed = roster('1,a,c,16,F,1,,,');
  assert.equal(C.propose(a.state, changed, pool, '2026-07-01', { onlyUnseen: true }).added.length, 0);
  const b = C.propose(a.state, changed, pool, '2026-07-01', { allowReuse: true });
  assert.throws(() => C.propose(b.state, roster('1,완전히다른사람,c,16,F,1,,,'), pool, '2026-07-01'), /이름/);
});
test('Maintenance compares original baseline even after repeated snapshots', () => {
  const old = roster('1,a,c,22,F,0,150,180,2003-01-01');
  let a = C.propose(C.newState('save'), old, pool, '2025-07-01');
  const now = roster('1,a,이적,30,F,0,130,180,2003-01-01');
  a = C.propose(a.state, now, pool, '2033-02-28');
  const tasks = C.maintenance(a.state, now, '2033-02-28', 28, 22);
  assert.equal(tasks.length, 1); assert.equal(tasks[0].baseline_ca, 150); assert.equal(tasks[0].proposed_dob, '2011-02-28');
  assert.equal(C.maintenance(a.state, now, '2032-02-29', 28, 22)[0].proposed_dob, '2010-02-28');
});
test('Existing photo fixes exclude duplicate people and duplicate celebrity names', () => {
  const r = roster('1,연예인 가,c,20,F,0,,,\n2,연예인 나,c,20,F,0,,,\n3,연예인 나,c,20,F,0,,,');
  const m = C.matchExisting(r, pool); assert.equal(m.exact.length, 1); assert.equal(m.ambiguous.length, 2);
  assert.equal(C.matchExisting(r, pool.concat({ name: '연예인 가', path: 'other/a.png' })).exact.length, 0);
});
test('State validation rejects path traversal, forged hash, duplicate IDs, and bad cursor', () => {
  const s = C.propose(C.newState('save'), roster('1,a,c,16,F,1,,,'), pool, '2025-07-01').state;
  for (const mutation of [x => { x.assignments[0].path = '../secret.png'; }, x => { x.assignments[0].sha256 = 'bad'; }, x => x.assignments.push(x.assignments[0]), x => { x.next = -1; }]) {
    const clone = JSON.parse(JSON.stringify(s)); mutation(clone); assert.throws(() => C.restore(JSON.stringify(clone), 'save'));
  }
  assert.match(C.csv([{ name: '=1+1' }], ['name']), /'=1\+1/);
});
