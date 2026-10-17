'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const core = require('../web/core.js');
const encode = text => new TextEncoder().encode(text);

test('normalizes numeric / r-prefixed IDs and leading zeroes', () => {
  assert.deepEqual(core.parseIds('  r-000123\n456, R-789 ; 12\t34  '), ['123', '456', '789', '12', '34']);
});
test('rejects missing IDs, zero, duplicate forms, injection and excessive input', () => {
  for (const raw of ['', '  \n  ', '0', 'r-000', '123\nr-123', '000123,123', '../12', 'r-1/portrait', '12"><record', '-123', '1.2', 'r-', '１２３']) {
    assert.throws(() => core.parseIds(raw), Error, raw);
  }
  assert.throws(() => core.parseIds(Array.from({ length: 501 }, (_, i) => String(i + 1)).join('\n')), /500/);
});
test('staff filter matches exact folder segments, not partial names or filenames', () => {
  const paths = ['celeb/스텝/a.png', 'celeb/스태프/b.PNG', 'celeb/staff/c.png', 'celeb/Staff/d.png',
    'celeb/스텝들/e.png', 'celeb/staffing/f.png', 'celeb/스태프사진/g.png', 'celeb/스텝.png', 'celeb/선수/h.png', 'celeb/config.xml'];
  const files = paths.map(p => ({ name: p.split('/').at(-1), webkitRelativePath: p, size: 10 }));
  const normal = core.filterPhotos(files, false);
  assert.equal(normal.photos.length, 5);
  assert.equal(normal.excludedStaff, 4);
  assert.equal(normal.ignoredNonPng, 1);
  const include = core.filterPhotos(files, true);
  assert.equal(include.photos.length, 9);
  assert.equal(include.excludedStaff, 0);
  assert.equal(include.ignoredNonPng, 1);
});
test('provided celeb directory contains 963 player PNGs and 47 excluded staff PNGs', { skip: !process.env.FM26_FACE_TEST_SOURCE }, () => {
  const path = require('node:path'), folder = process.env.FM26_FACE_TEST_SOURCE;
  const files = [];
  function visit(directory, relative) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name), next = relative ? relative + '/' + entry.name : entry.name;
      if (entry.isDirectory()) visit(target, next);
      else if (entry.isFile()) files.push({ name: entry.name, webkitRelativePath: 'celeb/' + next });
    }
  }
  visit(folder, '');
  const normal = core.filterPhotos(files, false), include = core.filterPhotos(files, true);
  assert.equal(normal.photos.length, 963);
  assert.equal(normal.excludedStaff, 47);
  assert.equal(include.photos.length, 1010);
  assert.equal(include.excludedStaff, 0);
});
test('escapes XML attribute characters and rejects invalid XML controls', () => {
  assert.equal(core.escapeXml('&<>"\''), '&amp;&lt;&gt;&quot;&apos;');
  assert.throws(() => core.escapeXml('bad\u0000text'), /XML/);
});
test('mapping keeps identical source filenames separate and omits original paths', () => {
  const photos = [{ name: 'same.png', size: 10 }, { name: 'same.png', size: 20 }];
  const plan = core.makePlan(['123', 'r-456'], [0, 1], photos);
  assert.deepEqual(plan.assets.map(a => a.path), ['faces/face_0001.png', 'faces/face_0002.png']);
  assert.equal(plan.mappings[0].to, 'graphics/pictures/person/r-123/portrait');
  assert.equal(plan.mappings[1].from, 'faces/face_0002');
  assert.match(plan.xml, /from="faces\/face_0001" to="graphics\/pictures\/person\/r-123\/portrait"/);
  assert.ok(!plan.xml.includes('same.png'));
  assert.equal(plan.totalPhotoBytes, 30);
});
test('shared photos are copied once; unselected photos are omitted', () => {
  const plan = core.makePlan(['1', '2', '3'], [1, 1, 0], [{ size: 10 }, { size: 20 }, { size: 999 }]);
  assert.equal(plan.assets.length, 2);
  assert.equal(plan.mappings[0].from, plan.mappings[1].from);
  assert.equal(plan.totalPhotoBytes, 30);
  assert.deepEqual(plan.assets.map(a => a.sourceIndex), [1, 0]);
});
test('rejects missing mappings, duplicate IDs, out-of-range selections and size limits', () => {
  for (const indexes of [[null], [-1], [1], [0.5], []]) {
    assert.throws(() => core.makePlan(['1'], indexes, [{ size: 10 }]));
  }
  assert.throws(() => core.makePlan(['1', 'r-1'], [0, 0], [{ size: 10 }]), /중복/);
  for (const size of [0, -1, NaN, Infinity, 1.5, core.MAX_PHOTO_BYTES + 1]) {
    assert.throws(() => core.makePlan(['1'], [0], [{ size }]));
  }
  const count = 7;
  assert.throws(() => core.makePlan(Array.from({ length: count }, (_, i) => String(i + 1)), Array.from({ length: count }, (_, i) => i), Array.from({ length: count }, () => ({ size: core.MAX_PHOTO_BYTES }))), /128MiB/);
});
test('ZIP paths reject traversal, absolute paths, controls and duplicate filenames', () => {
  for (const path of ['../face.png', '/face.png', 'faces/../face.png', 'faces/./face.png', 'faces//face.png', 'faces\\face.png', 'C:/face.png', 'face\u0000.png', '', 'faces/']) {
    assert.throws(() => core.safeZipPath(path), Error, path);
  }
  assert.throws(() => core.buildZipParts([{ path: 'config.xml', bytes: encode('a') }, { path: 'config.xml', bytes: encode('b') }]), /중복/);
  assert.throws(() => core.buildZipParts([{ path: 'config.xml', bytes: 'text' }]), /데이터/);
});
test('CRC32 matches the standard test vector and PNG signature validation', () => {
  assert.equal(core.crc32(encode('123456789')), 0xcbf43926);
  assert.equal(core.crc32(new Uint8Array()), 0);
  assert.equal(core.hasPngSignature(Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])), true);
  assert.equal(core.hasPngSignature(encode('not a PNG')), false);
  assert.equal(core.hasPngSignature(new Uint8Array()), false);
});
test('ZIP local headers, central directory, data CRC and EOCD remain consistent', () => {
  const entries = [{ path: 'config.xml', bytes: encode('<record/>') }, { path: 'faces/face_0001.png', bytes: Uint8Array.from([0, 1, 2, 255]) }];
  const result = core.buildZipParts(entries);
  const zip = Buffer.concat(result.parts.map(p => Buffer.from(p)));
  assert.equal(zip.length, result.size);
  let offset = 0;
  for (const entry of entries) {
    assert.equal(zip.readUInt32LE(offset), 0x04034b50);
    assert.equal(zip.readUInt16LE(offset + 6), 0x0800);
    assert.equal(zip.readUInt16LE(offset + 8), 0);
    const size = zip.readUInt32LE(offset + 18), nameLength = zip.readUInt16LE(offset + 26);
    assert.equal(size, entry.bytes.length);
    assert.equal(zip.readUInt32LE(offset + 14), core.crc32(entry.bytes));
    assert.equal(zip.subarray(offset + 30, offset + 30 + nameLength).toString('utf8'), entry.path);
    assert.deepEqual(zip.subarray(offset + 30 + nameLength, offset + 30 + nameLength + size), Buffer.from(entry.bytes));
    offset += 30 + nameLength + size;
  }
  assert.equal(zip.readUInt32LE(offset), 0x02014b50);
  assert.equal(zip.readUInt32LE(zip.length - 22), 0x06054b50);
  assert.equal(zip.readUInt16LE(zip.length - 12), entries.length);
  assert.equal(zip.readUInt32LE(zip.length - 6), offset);
  if (process.env.FM26_TEST_ZIP) fs.writeFileSync(process.env.FM26_TEST_ZIP, zip);
});
