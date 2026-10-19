(function () {
  'use strict';
  const core = window.FMFaceLayout;
  const folder = document.getElementById('layoutFolder');
  const status = document.getElementById('layoutStatus');
  const results = document.getElementById('layoutResults');
  let run = 0;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function filePath(file) { return file.webkitRelativePath || file.name; }
  function parseConfig(text) {
    core.guardXml(text);
    const documentXml = new DOMParser().parseFromString(text, 'application/xml');
    if (documentXml.getElementsByTagName('parsererror').length || !documentXml.documentElement) throw new Error('XML 문법 오류가 있습니다.');
    const root = documentXml.documentElement;
    if (root.tagName !== 'record') throw new Error('얼굴팩의 최상위 record 요소가 없습니다.');
    const maps = Array.from(root.children).filter(node => node.tagName === 'list' && node.getAttribute('id') === 'maps');
    if (maps.length !== 1) throw new Error('얼굴팩의 list id="maps"를 하나 찾을 수 있어야 합니다.');
    return Array.from(maps[0].children).filter(node => node.tagName === 'record').map(node => ({ from: node.getAttribute('from'), to: node.getAttribute('to') }));
  }
  function appendPaths(parent, title, paths) {
    const details = element('details'), list = element('ul');
    details.append(element('summary', title));
    paths.forEach(path => list.append(element('li', path)));
    details.append(list); parent.append(details);
  }
  function render(report, ignored) {
    const c = report.counts;
    const failed = report.configs.some(config => config.error) || c.missing || c.invalid || c.ambiguous || c.invalidPng;
    status.className = 'status ' + (failed ? 'error' : 'ok');
    status.textContent = 'config.xml ' + report.configs.length + '개 · PNG ' + report.pngs.length + '개\n' +
      '얼굴 연결 ' + c.mappings + '개: 경로 일치 ' + c.ok + '개, 대소문자 차이 ' + c.case + '개, 누락 ' + c.missing + '개, 잘못된 경로 ' + c.invalid + '개, 중복 경로 ' + c.ambiguous + '개, PNG 헤더 오류 ' + c.invalidPng + '개.\n' +
      'FM26 하위 사진 경로 호환성 주의: ' + c.nestedSourcePaths + '개.\n' +
      '그 외 파일 ' + ignored + '개와 얼굴 portrait 이외 연결 ' + c.ignored + '개는 읽거나 검사하지 않았습니다.';
    if (c.nestedSourcePaths) results.append(element('p', '파일 배치가 올바르더라도 FM26에서 from="faces/face_0001" 같은 하위 사진 경로가 표시되지 않았다는 보고가 있습니다. 모든 환경에서 발생한다고 확인된 문제는 아닙니다. PNG를 config.xml과 같은 폴더에 두고 from="fm26_celeb_실제ID"처럼 다른 팩과 겹치지 않는 고유 파일명을 쓰는 배치를 권장합니다. 이 검사는 원본을 바꾸지 않습니다.', 'note'));
    if (!failed && c.mappings) results.append(element('p', '선택한 폴더 안에서 사진 경로가 연결됩니다. 실제 게임 ID·선수 이름 일치, 다른 얼굴팩과의 충돌, 게임의 그래픽 폴더 위치·캐시 적용 여부는 이 검사로 확인할 수 없습니다.', 'muted'));
    if (!c.mappings) results.append(element('p', '검사할 얼굴 portrait 연결이 없습니다. 생성한 ZIP을 풀고 config.xml과 PNG가 함께 들어 있는 폴더를 선택했는지 확인하세요.', 'note'));
    report.configs.forEach(config => {
      const bad = config.checks.filter(check => check.status !== 'ok' || check.compatibilityWarning);
      if (!config.error && !bad.length) return;
      const heading = element('h3', config.path);
      results.append(heading);
      if (config.error) { results.append(element('p', config.error, 'error')); return; }
      const list = element('ul');
      bad.forEach(check => {
        const item = element('li');
        const labels = { ok: '파일 위치 정상 · FM26 하위 경로 호환성 주의', missing: '사진 누락', invalid: '잘못된 경로', ambiguous: '동일 경로 여러 개', 'invalid-png': 'PNG 헤더 오류', case: '대소문자 또는 유니코드 표기 차이' };
        item.append(element('strong', labels[check.status] + ': ' + String(check.from)));
        item.append(element('div', '대상: ' + check.to));
        if (check.expected) item.append(element('div', 'config.xml 기준 예상 위치: ' + check.expected));
        if (check.matches.length) item.append(element('div', '찾은 위치: ' + check.matches.join(' / ')));
        if (check.candidates.length) item.append(element('div', '이름이 같은 다른 파일: ' + check.candidates.join(' / ')));
        if (check.hint) item.append(element('p', check.hint, 'note'));
        if (check.compatibilityWarning) item.append(element('p', check.compatibilityWarning, 'note'));
        list.append(item);
      });
      results.append(list);
    });
    if (report.conflicts.length) {
      const entries = report.conflicts.map(group => group[0].to + ' ← ' + group.map(item => item.config + ' : ' + item.from).join(' / '));
      appendPaths(results, '선택한 폴더 안에서 동일 선수 ID를 여러 번 연결한 항목 ' + entries.length + '개', entries);
    }
    if (report.invalidPngs.length) appendPaths(results, 'PNG 확장자이나 PNG 헤더가 아닌 파일 ' + report.invalidPngs.length + '개', report.invalidPngs);
    if (report.unreferencedPngs.length) appendPaths(results, '이 폴더의 config.xml에서 연결하지 않은 PNG ' + report.unreferencedPngs.length + '개', report.unreferencedPngs);
    results.hidden = false;
  }
  folder.addEventListener('change', async function () {
    const current = ++run;
    results.replaceChildren(); results.hidden = true;
    status.className = 'status';
    const files = Array.from(folder.files || []);
    if (!files.length) { status.textContent = '검사할 폴더를 선택해 주세요.'; return; }
    try {
      if (files.length > core.MAX_FILES) throw new Error('전체 파일은 50,000개까지 검사합니다. 생성한 얼굴팩 폴더만 선택해 주세요.');
      const pngFiles = files.filter(file => /\.png$/i.test(file.name));
      const configFiles = files.filter(file => /^config\.xml$/i.test(file.name));
      if (!configFiles.length) throw new Error('config.xml이 없습니다. 사진만 들어 있는 하위 폴더 대신 config.xml이 함께 들어 있는 압축 해제 폴더를 선택해 주세요.');
      if (configFiles.length > core.MAX_CONFIGS) throw new Error('config.xml은 64개까지 검사합니다. 생성한 얼굴팩 폴더만 선택해 주세요.');
      if (configFiles.some(file => file.size > core.MAX_XML_BYTES) || configFiles.reduce((sum, file) => sum + file.size, 0) > core.MAX_TOTAL_XML_BYTES) throw new Error('XML은 한 개 5MiB, 합계 20MiB까지 읽습니다.');
      status.textContent = '읽기 전용으로 config.xml과 PNG 헤더를 확인하는 중입니다…';
      const configs = [];
      for (const file of configFiles) {
        try { configs.push({ path: filePath(file), mappings: parseConfig(await file.text()) }); }
        catch (error) { configs.push({ path: filePath(file), error: error.message }); }
        if (run !== current) return;
      }
      const pngs = [], signature = [137, 80, 78, 71, 13, 10, 26, 10];
      for (let i = 0; i < pngFiles.length; i += 32) {
        const chunk = await Promise.all(pngFiles.slice(i, i + 32).map(async file => {
          const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
          return { path: filePath(file), validPng: bytes.length === 8 && signature.every((byte, n) => bytes[n] === byte) };
        }));
        if (run !== current) return;
        pngs.push(...chunk);
      }
      render(core.inspectPack({ configs: configs, pngs: pngs }), files.length - pngFiles.length - configFiles.length);
    } catch (error) {
      if (run !== current) return;
      status.className = 'status error'; status.textContent = error.message;
    }
  });
})();
