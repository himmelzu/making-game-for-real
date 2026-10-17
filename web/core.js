(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FMFaceCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_IDS = 500;
  const MAX_PHOTO_BYTES = 20 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 128 * 1024 * 1024;
  const encoder = new TextEncoder();

  function parseIds(raw) {
    const tokens = String(raw).trim().split(/[\s,;]+/).filter(Boolean);
    if (!tokens.length) throw new Error('게임에서 확인한 유소년 ID를 한 개 이상 입력해 주세요.');
    if (tokens.length > MAX_IDS) throw new Error('한 번에 최대 ' + MAX_IDS + '명까지 연결할 수 있습니다.');
    const seen = new Set();
    return tokens.map(function (token, index) {
      const match = /^(?:r-)?([0-9]{1,18})$/i.exec(token);
      if (!match) throw new Error((index + 1) + '번째 ID 형식을 확인해 주세요. 숫자 또는 r-숫자만 입력합니다.');
      const id = match[1].replace(/^0+(?=\d)/, '');
      if (id === '0') throw new Error('ID 0은 사용할 수 없습니다. 게임에 표시된 ID를 확인해 주세요.');
      if (seen.has(id)) throw new Error('ID ' + id + '가 중복되었습니다. 한 선수는 한 번만 입력해 주세요.');
      seen.add(id);
      return id;
    });
  }

  function filterPhotos(files, includeStaff) {
    const photos = [];
    let ignoredNonPng = 0, excludedStaff = 0;
    Array.from(files).forEach(function (file) {
      if (!/\.png$/i.test(file.name)) { ignoredNonPng++; return; }
      const path = file.webkitRelativePath || file.name;
      const folders = path.replace(/\\/g, '/').split('/').slice(0, -1);
      const isStaff = folders.some(function (folder) {
        const segment = folder.normalize('NFC').toLowerCase();
        return segment === '스텝' || segment === '스태프' || segment === 'staff';
      });
      if (!includeStaff && isStaff) { excludedStaff++; return; }
      photos.push(file);
    });
    return { photos: photos, ignoredNonPng: ignoredNonPng, excludedStaff: excludedStaff };
  }

  function escapeXml(value) {
    const text = String(value);
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/.test(text)) {
      throw new Error('XML에서 사용할 수 없는 문자가 있습니다.');
    }
    return text.replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c];
    });
  }

  function safeZipPath(path) {
    if (typeof path !== 'string' || !path || path.includes('\\') || /[\u0000-\u001f\u007f:]/.test(path)) {
      throw new Error('안전하지 않은 ZIP 파일 경로입니다.');
    }
    if (path.split('/').some(function (part) { return !part || part === '.' || part === '..'; })) {
      throw new Error('ZIP 파일 경로에 빈 경로나 상위 폴더를 사용할 수 없습니다.');
    }
    const bytes = encoder.encode(path);
    if (bytes.length > 65535) throw new Error('ZIP 파일명이 너무 깁니다.');
    return bytes;
  }

  function makePlan(ids, photoIndexes, photos) {
    if (!Array.isArray(ids) || !Array.isArray(photoIndexes) || !Array.isArray(photos)) {
      throw new Error('사진 연결 정보가 올바르지 않습니다.');
    }
    const canonicalIds = parseIds(ids.join('\n'));
    if (canonicalIds.length !== ids.length || photoIndexes.length !== ids.length) {
      throw new Error('선수 ID와 사진 연결 개수가 다릅니다.');
    }
    const assets = [];
    const bySource = new Map();
    let totalPhotoBytes = 0;
    const mappings = canonicalIds.map(function (id, index) {
      const sourceIndex = photoIndexes[index];
      if (!Number.isInteger(sourceIndex) || sourceIndex < 0 || sourceIndex >= photos.length) {
        throw new Error('ID ' + id + '의 사진을 선택해 주세요.');
      }
      let asset = bySource.get(sourceIndex);
      if (!asset) {
        const photo = photos[sourceIndex];
        if (!photo || !Number.isSafeInteger(photo.size) || photo.size <= 0 || photo.size > MAX_PHOTO_BYTES) {
          throw new Error('사진 한 장은 0바이트 초과, 20MiB 이하여야 합니다.');
        }
        totalPhotoBytes += photo.size;
        if (totalPhotoBytes > MAX_TOTAL_BYTES) throw new Error('선택한 사진 합계가 128MiB를 넘습니다. 인원을 나누어 만들어 주세요.');
        const path = 'faces/face_' + String(assets.length + 1).padStart(4, '0') + '.png';
        safeZipPath(path);
        asset = { sourceIndex: sourceIndex, path: path, from: path.slice(0, -4) };
        assets.push(asset);
        bySource.set(sourceIndex, asset);
      }
      return { id: id, sourceIndex: sourceIndex, from: asset.from, to: 'graphics/pictures/person/r-' + id + '/portrait' };
    });
    const lines = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<record>',
      '  <boolean id="preload" value="false"/>',
      '  <boolean id="amap" value="false"/>',
      '  <list id="maps">'
    ];
    mappings.forEach(function (mapping) {
      lines.push('    <record from="' + escapeXml(mapping.from) + '" to="' + escapeXml(mapping.to) + '"/>');
    });
    lines.push('  </list>', '</record>', '');
    return { assets: assets, mappings: mappings, xml: lines.join('\n'), totalPhotoBytes: totalPhotoBytes };
  }

  const crcTable = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
    crcTable[i] = c >>> 0;
  }
  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = (c >>> 8) ^ crcTable[(c ^ bytes[i]) & 255];
    return (c ^ 0xffffffff) >>> 0;
  }
  function hasPngSignature(bytes) {
    const signature = [137, 80, 78, 71, 13, 10, 26, 10];
    return bytes.length >= signature.length && signature.every(function (byte, index) { return bytes[index] === byte; });
  }

  // ZIP STORE preserves PNG bytes. Parts can be passed directly to Blob to avoid an extra full-archive copy.
  function buildZipParts(entries) {
    if (!Array.isArray(entries) || !entries.length || entries.length > 65535) throw new Error('ZIP 파일 개수가 올바르지 않습니다.');
    const parts = [], central = [], seen = new Set();
    let offset = 0, total = 0;
    entries.forEach(function (entry) {
      const name = safeZipPath(entry.path);
      if (seen.has(entry.path)) throw new Error('ZIP 안의 파일 경로가 중복되었습니다.');
      seen.add(entry.path);
      if (!(entry.bytes instanceof Uint8Array)) throw new Error('ZIP에 넣을 파일 데이터가 올바르지 않습니다.');
      const bytes = entry.bytes, size = bytes.length;
      total += size;
      if (total > MAX_TOTAL_BYTES + 1024 * 1024) throw new Error('ZIP 용량 제한을 넘었습니다. 인원을 나누어 만들어 주세요.');
      const checksum = crc32(bytes);
      const local = new Uint8Array(30 + name.length), lv = new DataView(local.buffer);
      lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true); lv.setUint16(6, 0x0800, true);
      lv.setUint16(12, 10273, true); lv.setUint32(14, checksum, true);
      lv.setUint32(18, size, true); lv.setUint32(22, size, true); lv.setUint16(26, name.length, true);
      local.set(name, 30);
      const directory = new Uint8Array(46 + name.length), cv = new DataView(directory.buffer);
      cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x0800, true); cv.setUint16(14, 10273, true); cv.setUint32(16, checksum, true);
      cv.setUint32(20, size, true); cv.setUint32(24, size, true); cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true); directory.set(name, 46);
      parts.push(local, bytes); central.push(directory); offset += local.length + size;
    });
    const centralSize = central.reduce(function (sum, part) { return sum + part.length; }, 0);
    const end = new Uint8Array(22), ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, entries.length, true); ev.setUint16(10, entries.length, true);
    ev.setUint32(12, centralSize, true); ev.setUint32(16, offset, true);
    return { parts: parts.concat(central, [end]), size: offset + centralSize + end.length };
  }

  return { MAX_IDS: MAX_IDS, MAX_PHOTO_BYTES: MAX_PHOTO_BYTES, MAX_TOTAL_BYTES: MAX_TOTAL_BYTES,
    parseIds: parseIds, filterPhotos: filterPhotos, escapeXml: escapeXml, safeZipPath: safeZipPath, makePlan: makePlan,
    crc32: crc32, hasPngSignature: hasPngSignature, buildZipParts: buildZipParts };
});
