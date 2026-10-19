'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const layout = require('../web/layout-core.js');
const target = id => 'graphics/pictures/person/r-' + id + '/portrait';
const config = (path, from, id = '2002079023') => ({ path, mappings: [{ from, to: target(id) }] });
const png = (path, validPng = true) => ({ path, validPng });

test('generated arbitrary filename resolves relative to config and does not need player ID', () => {
  const report = layout.inspectPack({ configs: [config('newfolder/config.xml', 'faces/face_0001')], pngs: [png('newfolder/faces/face_0001.png')] });
  assert.equal(report.counts.ok, 1);
  assert.equal(report.configs[0].checks[0].expected, 'newfolder/faces/face_0001.png');
  assert.deepEqual(report.unreferencedPngs, []);
  assert.equal(report.counts.nestedSourcePaths, 1);
  assert.match(report.configs[0].checks[0].compatibilityWarning, /모든 FM26 환경에서 실패한다는 뜻은 아닙니다/);
  assert.deepEqual(report.warnings.nestedSourcePaths, [{ config: 'newfolder/config.xml', from: 'faces/face_0001', to: target('2002079023'), expected: 'newfolder/faces/face_0001.png' }]);
});
test('flat PNG beside config resolves without subfolder compatibility warning', () => {
  const report = layout.inspectPack({ configs: [config('newfolder/config.xml', 'face_0001')], pngs: [png('newfolder/face_0001.png')] });
  assert.equal(report.counts.ok, 1);
  assert.equal(report.counts.nestedSourcePaths, 0);
  assert.equal(report.configs[0].checks[0].compatibilityWarning, '');
  assert.deepEqual(report.warnings.nestedSourcePaths, []);
});
test('config inside faces with faces/ source detects repeated directory and actual alternate', () => {
  const report = layout.inspectPack({ configs: [config('pack/faces/config.xml', 'faces/face_0001')], pngs: [png('pack/faces/face_0001.png')] });
  const check = report.configs[0].checks[0];
  assert.equal(check.status, 'missing');
  assert.equal(check.expected, 'pack/faces/faces/face_0001.png');
  assert.deepEqual(check.candidates, ['pack/faces/face_0001.png']);
  assert.match(check.hint, /faces\/faces/);
  assert.match(check.hint, /한 단계 위/);
});
test('nested config is valid when its sources correctly use its own directory', () => {
  const report = layout.inspectPack({ configs: [config('pack/faces/config.xml', 'face_0001')], pngs: [png('pack/faces/face_0001.png')] });
  assert.equal(report.counts.ok, 1);
  assert.equal(report.configs[0].checks[0].hint, '');
});
test('all missing sources retain full expected paths and candidates from different folders', () => {
  const record = config('pack/config.xml', 'faces/face_0001');
  record.mappings.push({ from: 'faces/face_0002', to: target('2002079024') });
  const report = layout.inspectPack({ configs: [record], pngs: [png('pack/wrong/face_0001.png'), png('pack/other/face_0001.png')] });
  assert.equal(report.counts.missing, 2);
  assert.equal(report.configs[0].checks.length, 2);
  assert.deepEqual(report.configs[0].checks[0].candidates, ['pack/wrong/face_0001.png', 'pack/other/face_0001.png']);
  assert.equal(report.configs[0].checks[1].expected, 'pack/faces/face_0002.png');
});
test('accepts explicit PNG extension and Windows relative separators; flags case differences', () => {
  const report = layout.inspectPack({ configs: [config('pack/config.xml', 'faces\\FACE_0001.PNG')], pngs: [png('pack/faces/face_0001.png')] });
  assert.equal(report.counts.case, 1);
  assert.equal(report.configs[0].checks[0].expected, 'pack/faces/FACE_0001.PNG');
});
test('same basename at correct exact path wins while casefold collisions remain ambiguous', () => {
  const first = layout.inspectPack({ configs: [config('p/config.xml', 'faces/a')], pngs: [png('p/faces/a.png'), png('p/other/a.png')] });
  assert.equal(first.counts.ok, 1);
  const second = layout.inspectPack({ configs: [config('p/config.xml', 'faces/A')], pngs: [png('p/faces/a.png'), png('P/FACES/a.PNG')] });
  assert.equal(second.counts.ambiguous, 1);
});
test('invalid source traversal never resolves, while other valid mappings continue', () => {
  const record = config('p/config.xml', '../a');
  record.mappings.push({ from: 'faces/b', to: target('2') });
  const report = layout.inspectPack({ configs: [record], pngs: [png('p/faces/b.png')] });
  assert.equal(report.counts.invalid, 1);
  assert.equal(report.counts.ok, 1);
  assert.equal(report.counts.nestedSourcePaths, 1);
  assert.equal(report.configs[0].checks[0].compatibilityWarning, '');
  for (const path of ['/a', 'a/../b', 'a//b', 'C:/photo', '\\absolute', 'a\u0000']) assert.throws(() => layout.resolveSource('p/config.xml', path));
});
test('invalid PNG header is distinct from missing image and unreferenced pictures are reported', () => {
  const report = layout.inspectPack({ configs: [config('p/config.xml', 'faces/a')], pngs: [png('p/faces/a.png', false), png('p/faces/extra.png')] });
  assert.equal(report.counts.invalidPng, 1);
  assert.equal(report.counts.missing, 0);
  assert.deepEqual(report.invalidPngs, ['p/faces/a.png']);
  assert.deepEqual(report.unreferencedPngs, ['p/faces/extra.png']);
});
test('duplicate portrait targets across configs are reported without claiming load order', () => {
  const report = layout.inspectPack({ configs: [config('p/config.xml', 'faces/a'), config('p/sub/config.xml', 'b')], pngs: [png('p/faces/a.png'), png('p/sub/b.png')] });
  assert.equal(report.counts.ok, 2);
  assert.equal(report.conflicts.length, 1);
  assert.deepEqual(report.conflicts[0].map(item => item.config), ['p/config.xml', 'p/sub/config.xml']);
});
test('DTD and entity declarations fail closed before DOM parsing and XML size is bounded', () => {
  for (const text of ['<!DOCTYPE record><record/>', '<!doctype record SYSTEM "https://x"><record/>', '<!ENTITY p "x">', '<! DOCTYPE record>']) assert.throws(() => layout.guardXml(text), /DTD/);
  assert.equal(layout.guardXml('<?xml version="1.0"?><record/>'), '<?xml version="1.0"?><record/>');
  assert.throws(() => layout.guardXml('x'.repeat(layout.MAX_XML_BYTES + 1)), /5MiB/);
});
test('invalid XML records errors, other-target records are ignored and input stays unchanged', () => {
  const input = { configs: [{ path: 'p/config.xml', error: 'XML 문법 오류' }, { path: 'p/more/config.xml', mappings: [{ from: 'logo', to: 'graphics/pictures/club/1/logo' }] }], pngs: [png('p/a.png')] };
  const before = JSON.stringify(input), report = layout.inspectPack(input);
  assert.equal(report.configs[0].error, 'XML 문법 오류');
  assert.equal(report.counts.ignored, 1);
  assert.equal(report.counts.mappings, 0);
  assert.equal(JSON.stringify(input), before);
});
