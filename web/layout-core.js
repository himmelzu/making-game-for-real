(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FMFaceLayout = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_FILES = 50000;
  const MAX_CONFIGS = 64;
  const MAX_MAPPINGS = 20000;
  const MAX_XML_BYTES = 5 * 1024 * 1024;
  const MAX_TOTAL_XML_BYTES = 20 * 1024 * 1024;

  function safePath(value) {
    if (typeof value !== 'string' || !value || /[\u0000-\u001f\u007f:]/.test(value)) {
      throw new Error('비어 있거나 안전하지 않은 파일 경로입니다.');
    }
    const path = value.replace(/\\/g, '/');
    if (path.split('/').some(part => !part || part === '.' || part === '..')) {
      throw new Error('절대 경로·상위 폴더·빈 경로는 확인할 수 없습니다.');
    }
    return path;
  }
  function folded(value) { return value.normalize('NFC').toLowerCase(); }
  function basename(value) { return value.slice(value.lastIndexOf('/') + 1); }
  function dirname(value) { return value.includes('/') ? value.slice(0, value.lastIndexOf('/')) : ''; }
  function join(directory, file) { return directory ? directory + '/' + file : file; }

  function guardXml(text) {
    if (typeof text !== 'string') throw new Error('XML 텍스트가 올바르지 않습니다.');
    if (new TextEncoder().encode(text).length > MAX_XML_BYTES) throw new Error('config.xml 한 개는 5MiB 이하여야 합니다.');
    if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(text)) throw new Error('DTD·외부 엔터티가 있는 XML은 읽지 않습니다.');
    return text;
  }
  function resolveSource(configPath, from) {
    const config = safePath(configPath);
    const source = safePath(from);
    return join(dirname(config), /\.png$/i.test(source) ? source : source + '.png');
  }
  function isPortraitTarget(to) {
    return typeof to === 'string' && /^graphics\/pictures\/person\/(?:r-)?[0-9]+\/portrait$/i.test(to);
  }

  // Pure manifest check: no filesystem access, mutation, XML execution or image decoding.
  function inspectPack(input) {
    if (!input || !Array.isArray(input.pngs) || !Array.isArray(input.configs)) throw new Error('폴더 목록이 올바르지 않습니다.');
    if (input.pngs.length > MAX_FILES || input.configs.length > MAX_CONFIGS) throw new Error('파일 또는 config.xml 개수가 검사 한도를 넘습니다.');
    const pngs = input.pngs.map(item => ({ path: safePath(item.path), validPng: item.validPng !== false }));
    const exact = new Map(), loose = new Map(), byName = new Map();
    function add(map, key, item) { if (!map.has(key)) map.set(key, []); map.get(key).push(item); }
    pngs.forEach(item => {
      add(exact, item.path, item); add(loose, folded(item.path), item); add(byName, folded(basename(item.path)), item);
    });
    const referenced = new Set(), targets = new Map(), nestedSourcePaths = [];
    const counts = { mappings: 0, ok: 0, case: 0, missing: 0, invalid: 0, ambiguous: 0, invalidPng: 0, ignored: 0, nestedSourcePaths: 0 };
    const configs = input.configs.map(config => {
      const result = { path: safePath(config.path), error: config.error || '', checks: [], ignored: 0 };
      if (result.error) return result;
      if (!Array.isArray(config.mappings)) throw new Error('XML 연결 목록이 올바르지 않습니다.');
      config.mappings.forEach(mapping => {
        if (!mapping || !isPortraitTarget(mapping.to)) { result.ignored++; counts.ignored++; return; }
        counts.mappings++;
        if (counts.mappings > MAX_MAPPINGS) throw new Error('얼굴 연결은 합계 20,000개까지 검사합니다.');
        const check = { from: mapping.from, to: mapping.to, expected: '', status: '', matches: [], candidates: [], hint: '', compatibilityWarning: '' };
        try { check.expected = resolveSource(result.path, mapping.from); }
        catch (error) { check.status = 'invalid'; check.hint = error.message; counts.invalid++; result.checks.push(check); return; }
        if (safePath(mapping.from).includes('/')) {
          check.compatibilityWarning = '실제 파일 위치가 맞아도 FM26에서 하위 사진 경로가 표시되지 않았다는 보고가 있습니다. 모든 FM26 환경에서 실패한다는 뜻은 아닙니다. PNG를 config.xml 바로 옆에 두고 from에는 다른 팩과 겹치지 않는 고유 파일명만 쓰는 배치를 권장합니다.';
          counts.nestedSourcePaths++;
          nestedSourcePaths.push({ config: result.path, from: mapping.from, to: mapping.to, expected: check.expected });
        }
        const exactMatches = exact.get(check.expected) || [];
        const matches = exactMatches.length ? exactMatches : (loose.get(folded(check.expected)) || []);
        check.matches = matches.map(item => item.path);
        if (matches.length > 1) { check.status = 'ambiguous'; counts.ambiguous++; }
        else if (matches.length === 1) {
          referenced.add(matches[0].path);
          if (!matches[0].validPng) { check.status = 'invalid-png'; counts.invalidPng++; }
          else if (!exactMatches.length) { check.status = 'case'; counts.case++; }
          else { check.status = 'ok'; counts.ok++; }
        } else {
          check.status = 'missing'; counts.missing++;
          check.candidates = (byName.get(folded(basename(check.expected))) || []).map(item => item.path);
          const directory = dirname(result.path), source = safePath(mapping.from);
          const first = source.split('/')[0];
          const shortened = join(directory, source.split('/').slice(1).join('/'));
          const withoutRepeated = /\.png$/i.test(shortened) ? shortened : shortened + '.png';
          if (source.includes('/') && folded(basename(directory)) === folded(first) && loose.has(folded(withoutRepeated))) {
            check.hint = 'config.xml이 ' + first + ' 폴더 안에 있어 경로에 ' + first + '/' + first + '가 중복됩니다. config.xml을 한 단계 위로 옮겨 ' + first + ' 폴더와 나란히 두는 배치를 확인하세요.';
          }
        }
        const targetKey = folded(mapping.to);
        if (!targets.has(targetKey)) targets.set(targetKey, []);
        targets.get(targetKey).push({ config: result.path, from: mapping.from, expected: check.expected, to: mapping.to });
        result.checks.push(check);
      });
      return result;
    });
    const conflicts = Array.from(targets.values()).filter(entries => entries.length > 1);
    return { configs: configs, pngs: pngs, counts: counts, conflicts: conflicts,
      warnings: { nestedSourcePaths: nestedSourcePaths },
      invalidPngs: pngs.filter(item => !item.validPng).map(item => item.path),
      unreferencedPngs: pngs.filter(item => !referenced.has(item.path)).map(item => item.path) };
  }
  return { MAX_FILES: MAX_FILES, MAX_CONFIGS: MAX_CONFIGS, MAX_MAPPINGS: MAX_MAPPINGS,
    MAX_XML_BYTES: MAX_XML_BYTES, MAX_TOTAL_XML_BYTES: MAX_TOTAL_XML_BYTES,
    safePath: safePath, guardXml: guardXml, resolveSource: resolveSource,
    isPortraitTarget: isPortraitTarget, inspectPack: inspectPack };
});
