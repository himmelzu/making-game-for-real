'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const R = require('../web/repair-core.js');
const sha = 'a'.repeat(64), otherSha = 'b'.repeat(64);
const mapping = (from, id, prefix = 'r-') => '<record from="' + from + '" to="graphics/pictures/person/' + prefix + id + '/portrait"/>';
const xml = rows => '<?xml version="1.0" encoding="UTF-8"?>\r\n<record>\r\n  <!-- keep & comments -->\r\n  <boolean id="preload" value="false" />\r\n  <boolean id="amap" value="false" />\r\n  <list id="maps">\r\n' + rows + '\r\n  </list>\r\n</record>\r\n';
function input() {
  return {
    configs: [{ path: 'SM/config.xml', text: xml(mapping('1', '1') + '\r\n' + mapping('other', '200')) }],
    roster: [{ id: '999', name: '카리나', club: 'SM' }],
    catalog: [{ name: '카리나', path: 'SM/1.png', sha256: sha }],
    photos: [{ path: 'SM/1.png', sha256: sha }, { path: 'SM/other.png' }]
  };
}
test('Repairs only the actual ID attribute and preserves all unrelated XML bytes', () => {
  const source = input(), plan = R.plan(source);
  assert.equal(plan.assignments.length, 1); assert.equal(plan.changedAssignments, 1); assert.equal(plan.removed.length, 0);
  assert.equal(plan.changedConfigs[0].text, source.configs[0].text.replace('person/r-1/portrait', 'person/r-999/portrait'));
  assert.equal(source.configs[0].text, xml(mapping('1', '1') + '\r\n' + mapping('other', '200')));
  assert.equal(plan.assignments[0].photo, 'SM/1.png');
});
test('Preserves existing r-prefix convention, never infers it from a large numeric ID', () => {
  const source = input(); source.configs[0].text = xml(mapping('1', '1', '')); source.roster[0].id = '2822441025';
  assert.equal(R.plan(source).assignments[0].newTarget, 'graphics/pictures/person/2822441025/portrait');
  source.configs[0].text = xml(mapping('1', '1', 'R-'));
  assert.equal(R.plan(source).assignments[0].newTarget, 'graphics/pictures/person/R-2822441025/portrait');
});
test('Uses exact observed actual IDs and never applies a folder-wide arithmetic offset', () => {
  const source = input(); source.roster[0].id = '2002043293';
  source.configs[0].text = xml(mapping('1', '1') + mapping('other', '2002043284'));
  const plan = R.plan(source);
  assert.equal(plan.assignments[0].newTarget, 'graphics/pictures/person/r-2002043293/portrait');
  assert.ok(plan.changedConfigs[0].text.includes(mapping('other', '2002043284')));
});
test('Removes conflicting targets globally and reports other photos that become unmapped', () => {
  const source = input(); source.configs.push({ path: 'YG/config.xml', text: xml(mapping('incorrect', '999')) });
  source.photos.push({ path: 'YG/incorrect.png' });
  const plan = R.plan(source);
  assert.equal(plan.changedConfigs.length, 2); assert.equal(plan.removed.length, 1); assert.equal(plan.unmapped.length, 1);
  assert.equal(plan.unmapped[0].photo, 'YG/incorrect.png');
  assert.ok(!plan.changedConfigs.find(config => config.path === 'YG/config.xml').text.includes('from="incorrect"'));
  assert.equal(plan.removed[0].correctedName, '카리나');
});
test('Two confirmed photos can swap IDs without deleting each other', () => {
  const source = input(); source.configs[0].text = xml(mapping('1', '1') + mapping('other', '999'));
  source.roster.push({ id: '1', name: '다른 연예인' });
  source.catalog.push({ name: '다른 연예인', path: 'SM/other.png', sha256: otherSha }); source.photos[1].sha256 = otherSha;
  const plan = R.plan(source);
  assert.equal(plan.assignments.length, 2); assert.equal(plan.removed.length, 0);
  assert.ok(plan.changedConfigs[0].text.includes(mapping('1', '999')));
  assert.ok(plan.changedConfigs[0].text.includes(mapping('other', '1')));
});
test('Does not call an alternate retained portrait an unmapped photo', () => {
  const source = input(); source.configs.push({ path: 'YG/config.xml', text: xml(mapping('incorrect', '999') + mapping('incorrect', '888')) });
  const plan = R.plan(source);
  assert.equal(plan.removed.length, 1); assert.equal(plan.unmapped.length, 0);
});
test('Duplicate roster or caption names are excluded rather than guessed', () => {
  const source = input(); source.roster.push({ id: '888', name: '카리나', club: '동명이인' });
  let plan = R.plan(source);
  assert.equal(plan.assignments.length, 0); assert.equal(plan.ambiguous.length, 2); assert.equal(plan.changedConfigs.length, 0);
  source.roster.pop(); source.catalog.push({ name: '카리나', path: 'YG/second.png', sha256: otherSha });
  plan = R.plan(source); assert.equal(plan.assignments.length, 0); assert.equal(plan.ambiguous.length, 1);
});
test('Only explicitly verified aliases match; repeated aliases for one photo do not create ambiguity', () => {
  const source = input(); source.catalog[0].aliases = ['유지민', '유지민', '카리나']; source.roster[0].name = '유지민';
  let plan = R.plan(source);
  assert.equal(plan.assignments.length, 1); assert.equal(plan.assignments[0].name, '유지민'); assert.equal(plan.assignments[0].captionName, '카리나');
  source.roster[0].name = '카리 나'; plan = R.plan(source);
  assert.equal(plan.assignments.length, 0); assert.equal(plan.unmatched.length, 1);
});
test('An alias shared by different photos is ambiguous and never selects a picture by folder order', () => {
  const source = input(); source.catalog[0].aliases = ['유지민']; source.roster[0].name = '유지민';
  source.catalog.push({ name: '다른 사진', path: 'YG/second.png', sha256: otherSha, aliases: ['유지민'] });
  const plan = R.plan(source); assert.equal(plan.assignments.length, 0); assert.equal(plan.ambiguous.length, 1);
});
test('A primary name takes precedence over another photo caption alias and retains caption warnings', () => {
  const source = input(); source.roster[0].name = '이혜인';
  source.catalog[0] = { name: '이혜빈', caption: '이혜인', aliases: ['이혜인'], requiresReview: true, path: 'SM/1.png', sha256: sha };
  source.catalog.push({ name: '이혜인', path: 'SM/other.png', sha256: otherSha }); source.photos[1].sha256 = otherSha;
  let plan = R.plan(source);
  assert.equal(plan.assignments.length, 1); assert.equal(plan.ambiguous.length, 0);
  assert.equal(plan.assignments[0].photo, 'SM/other.png');
  source.roster.push({ id: '888', name: '이혜빈' }); plan = R.plan(source);
  const corrected = plan.assignments.find(row => row.name === '이혜빈');
  assert.equal(corrected.captionName, '이혜인'); assert.equal(corrected.captionDiffers, true); assert.equal(corrected.requiresReview, true);
  assert.equal(corrected.catalogName, '이혜빈'); assert.equal(plan.assignments.length, 2);
});
test('Multiple primary names remain ambiguous and aliases only provide an unambiguous fallback', () => {
  const source = input(); source.roster[0].name = '유지민'; source.catalog[0].aliases = ['유지민'];
  source.catalog.push({ name: '유지민', path: 'YG/second.png', sha256: otherSha }, { name: '유지민', path: 'YG/third.png', sha256: otherSha });
  let plan = R.plan(source); assert.equal(plan.assignments.length, 0); assert.equal(plan.ambiguous.length, 1);
  source.catalog.splice(1); plan = R.plan(source);
  assert.equal(plan.assignments.length, 1); assert.equal(plan.assignments[0].photo, 'SM/1.png');
  source.catalog.push({ name: '다른 사진', path: 'YG/second.png', sha256: otherSha, aliases: ['유지민'] });
  plan = R.plan(source); assert.equal(plan.assignments.length, 0); assert.equal(plan.ambiguous.length, 1);
});
test('Two actual people matching two aliases of one photo fail closed rather than reusing that picture', () => {
  const source = input(); source.catalog[0].aliases = ['유지민']; source.roster.push({ id: '888', name: '유지민' });
  assert.throws(() => R.plan(source), /여러 사진|같은 실제 선수 ID/);
});
test('Alias catalog validation rejects malformed aliases, paths and unverified hashes', () => {
  const source = input();
  for (const aliases of ['name', [null], ['\u0001bad']]) assert.throws(() => R.catalog([{ ...source.catalog[0], aliases }]));
  assert.throws(() => R.catalog([{ ...source.catalog[0], path: 'SM/a.jpg' }]));
  assert.throws(() => R.catalog([{ ...source.catalog[0], sha256: '' }]));
  assert.throws(() => R.catalog([{ ...source.catalog[0], caption: '\u0001bad' }]));
  assert.throws(() => R.catalog([{ ...source.catalog[0], requiresReview: 'yes' }]));
});
test('Missing photos, altered original image hash, duplicate actual IDs and ambiguous source rows stop output', () => {
  const missing = input(); missing.photos.shift(); assert.throws(() => R.plan(missing), /사진을 찾지/);
  const changed = input(); changed.photos[0].sha256 = otherSha; assert.throws(() => R.plan(changed), /SHA-256/);
  const duplicate = input(); duplicate.roster.push({ id: '999', name: '다른 사람' }); assert.throws(() => R.plan(duplicate), /ID가 중복/);
  const noSource = input(); noSource.configs[0].text = xml(mapping('other', '200')); assert.throws(() => R.plan(noSource), /기존 portrait 연결/);
  const doubleSource = input(); doubleSource.configs[0].text = xml(mapping('1', '1') + mapping('1', '2')); assert.throws(() => R.plan(doubleSource), /여러 개/);
});
test('DTD, malformed XML, duplicate attributes and unsafe relative paths fail closed', () => {
  for (const text of [
    '<!DOCTYPE record SYSTEM "https://example.invalid/file"><record><list id="maps"/></record>',
    '<record><list id="maps"></record>', '<record><list id="maps">',
    '<record><list id="maps" id="maps"/></record>', '<record/><record><list id="maps"/></record>',
    '<record><list id="maps"><record from="a" to="b"extra="c"/></list></record>',
    '<record><list id="maps"><record from="a" to="&unknown;"/></list></record>'
  ]) assert.throws(() => R.readConfig({ path: 'SM/config.xml', text }));
  for (const relative of ['../SM/config.xml', '/SM/config.xml', 'C:/config.xml', 'SM/../config.xml', 'SM./config.xml', 'SM/ /config.xml']) assert.throws(() => R.path(relative));
  const badFrom = input(); badFrom.configs[0].text = xml(mapping('../1', '1')); assert.throws(() => R.plan(badFrom), /相対|상대 경로/);
});
test('Case-colliding paths and equivalent source paths fail closed on Windows', () => {
  const source = input(); source.configs.push({ path: 'sm/CONFIG.XML', text: xml('') });
  assert.throws(() => R.plan(source), /config 경로가 중복/);
  const pictures = input(); pictures.photos.push({ path: 'sm/1.PNG', sha256: sha }); assert.throws(() => R.plan(pictures), /사진 경로가 중복/);
});
test('Preserves unknown settings, nested records, entities, single quotes, BOM and line endings', () => {
  const source = input(); source.catalog[0].path = 'SM/a&b.png'; source.photos[0].path = 'SM/a&b.png';
  const original = '\uFEFF<?xml version="1.0" encoding="UTF-8"?>\r\n<record><list id="metadata"><record from="x" to="unchanged"/></list><list id="maps">\r\n' +
    "<record custom='keep &amp; exactly' from='a&amp;b' to='graphics/pictures/person/r-1/portrait'><string id='annotation' value='keep'/></record>\r\n" +
    '<record from="opaque" to="graphics/pictures/club/999/logo"/><record from="other" to="graphics/pictures/person/r-200/portrait"/>\r\n</list></record>';
  source.configs[0].text = original;
  const plan = R.plan(source);
  assert.equal(plan.changedConfigs[0].text, original.replace("to='graphics/pictures/person/r-1/portrait'", "to='graphics/pictures/person/r-999/portrait'"));
});
test('Already-correct mappings need no replacement; unrelated old duplicate targets are reported and preserved', () => {
  const source = input(); source.roster[0].id = '1'; source.configs[0].text = xml(mapping('1', '1') + mapping('other', '200') + mapping('third', '200'));
  const plan = R.plan(source);
  assert.equal(plan.assignments.length, 1); assert.equal(plan.changedAssignments, 0); assert.equal(plan.changedConfigs.length, 0);
  assert.deepEqual(plan.remainingDuplicateTargets, [{ targetKey: 'r-200', count: 2 }]);
});
test('Ordinary and r-portrait paths remain distinct; leading-zero equivalent r-target conflicts are found', () => {
  const source = input(); source.configs[0].text = xml(mapping('1', '1') + mapping('ordinary', '999', '') + mapping('leading', '000999'));
  const plan = R.plan(source);
  assert.equal(plan.removed.length, 1); assert.equal(plan.removed[0].photo, 'SM/leading.png');
  assert.ok(plan.changedConfigs[0].text.includes(mapping('ordinary', '999', '')));
});
test('Real catalog and source configs retain their original number and settings when no actual names match', () => {
  const fs = require('node:fs');
  const catalog = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../data/identities.json'), 'utf8'));
  const source = input(); source.catalog = catalog; source.roster = [{ id: '987', name: '목록에 없는 인물' }];
  const plan = R.plan(source);
  assert.equal(plan.changedConfigs.length, 0); assert.equal(plan.unmatched.length, 1); assert.equal(plan.verifiedCatalogCount, 118);
});
