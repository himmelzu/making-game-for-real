'use strict';
(function () {
  const R = window.FMFaceRepair, C = window.FMCareer, F = window.FMFaceCore, $ = id => document.getElementById(id);
  // A dedicated full-caption catalog may be bundled without changing the
  // shared youth tool's original catalog or guessing any actual player IDs.
  const fallbackCatalog = window.FMCelebrityCatalog.concat([{
    name: '이현주', path: '2부리그/무소속,블리츠웨이,바로엔터,제이와이드/2002043284.png',
    sha256: '6f3dd28ba96c2b356425f93cde2b8a398b14b86321c552a43fd708c703e5e341'
  }]);
  const reviewedCatalog = Array.isArray(window.FMRepairCatalog) ? window.FMRepairCatalog : fallbackCatalog;
  const catalog = R.catalog(reviewedCatalog.filter(row => row.alternate !== true)), encoder = new TextEncoder();
  $('repairCatalogCount').textContent = '검수한 사진 ' + reviewedCatalog.length + '장 · 자동 대조 목록 ' + catalog.length + '장' +
    (reviewedCatalog.length !== catalog.length ? ' (대체 사진 ' + (reviewedCatalog.length - catalog.length) + '장 제외)' : '') +
    '. 사진 이름 검수와 현재 세이브 ID 확인은 별개입니다.';
  const reportedFolder = '2부리그/무소속,블리츠웨이,바로엔터,제이와이드';
  const reportedHashes = {
    '2002043283.png': '2a8e9031d1af3f1729059de70beeea5ba5d3b20e05cd83e2eb275e97af826aef',
    '2002043284.png': '6f3dd28ba96c2b356425f93cde2b8a398b14b86321c552a43fd708c703e5e341',
    '2002043293.png': 'd8951762374c1d933b39592ac09a7485f846b6ea12d2c1e08132c5898745949b',
    '2002043294.png': '1940c7a6590f2d4551c8edf0a4b09c8b70e744691813d2fed7fb4223d19f80df'
  };
  let selected = null, result = null, busy = false, reportedReady = false;
  const previewURLs = [];
  function status(where, message, error) { $(where).textContent = message; $(where).classList.toggle('error', !!error); }
  function invalidate() {
    result = null; $('repairResults').hidden = true; $('repairDownload').disabled = true; $('repairReviewed').checked = false;
    previewURLs.splice(0).forEach(url => URL.revokeObjectURL(url));
    $('repairExportStatus').textContent = '';
  }
  function action(where, fn) {
    return async function () {
      if (busy) return;
      busy = true; $('repairPreview').disabled = true; $('repairDownload').disabled = true;
      ['repairFolder', 'repairRosterFile', 'repairRoster', 'actualRoster', 'reportedCases', 'repairReviewed'].forEach(id => { $(id).disabled = true; });
      try { await fn(); }
      catch (error) { invalidate(); status(where, error.message || String(error), true); }
      finally {
        busy = false; $('repairPreview').disabled = false;
        ['repairFolder', 'repairRosterFile', 'repairRoster', 'actualRoster', 'repairReviewed'].forEach(id => { $(id).disabled = false; });
        $('reportedCases').disabled = !reportedReady; updateDownload();
      }
    };
  }
  function updateDownload() { $('repairDownload').disabled = busy || !result || !result.changedConfigs.length || !$('repairReviewed').checked; }
  function download(filename, data, type) {
    const url = URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type: type || 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  function inventory(files) {
    const relevant = [...files].filter(file => /(?:\.png|(?:^|\/)config\.xml)$/i.test(file.webkitRelativePath || file.name));
    if (!relevant.length) throw new Error('config.xml과 PNG가 있는 celeb 폴더를 선택하세요.');
    const roots = new Set(), found = new Set(), photos = [], configs = [];
    relevant.forEach(file => {
      const relative = (file.webkitRelativePath || '').replace(/\\/g, '/');
      if (!relative.includes('/')) throw new Error('개별 파일 대신 celeb 폴더 전체를 선택하세요.');
      const separator = relative.indexOf('/'); roots.add(relative.slice(0, separator));
      const path = R.path(relative.slice(separator + 1)), normalized = R.key(path);
      if (found.has(normalized)) throw new Error('대소문자만 다른 경로 또는 중복 파일이 있습니다: ' + path);
      found.add(normalized);
      if (/(^|\/)config\.xml$/i.test(path)) configs.push({ path, file });
      else photos.push({ path, file });
    });
    if (roots.size !== 1 || !configs.length || !photos.length) throw new Error('config.xml과 PNG가 함께 있는 celeb 폴더 하나를 선택하세요.');
    if (configs.length > 256 || photos.length > 20000) throw new Error('celeb 폴더만 선택하세요. config는 256개, 사진은 20,000장까지 처리합니다.');
    if (configs.reduce((total, item) => total + item.file.size, 0) > 32 * 1024 * 1024) throw new Error('config 전체 용량은 32MiB 이하여야 합니다.');
    return { root: [...roots][0], photos, configs };
  }
  async function readConfig(item) {
    if (item.file.size > 4 * 1024 * 1024) throw new Error(item.path + ': config가 4MiB보다 큽니다.');
    // Keep a UTF-8 BOM if it exists; output uses the same original text.
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await item.file.arrayBuffer());
    const declaration = /^\uFEFF?\s*<\?xml\b[^?]*encoding\s*=\s*(['"])([^'"]+)\1/i.exec(text);
    if (declaration && !/^utf-?8$/i.test(declaration[2])) throw new Error(item.path + ': UTF-8 XML만 처리합니다. 원본 인코딩을 임의로 바꾸지 않습니다.');
    if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(text)) throw new Error(item.path + ': DTD·외부 엔티티는 처리하지 않습니다.');
    const doc = new DOMParser().parseFromString(text, 'application/xml');
    if (doc.querySelector('parsererror')) throw new Error(item.path + ': XML 문법 오류가 있습니다. 원본 config를 확인하세요.');
    R.readConfig({ path: item.path, text });
    return { path: item.path, text };
  }
  async function hashPhoto(photo) {
    if (photo.file.size > 20 * 1024 * 1024) throw new Error(photo.path + ': 사진 하나가 20MiB보다 큽니다.');
    const bytes = new Uint8Array(await photo.file.arrayBuffer());
    if (!F.hasPngSignature(bytes)) throw new Error(photo.path + ': PNG 형식이 아닙니다.');
    return hashBytes(bytes);
  }
  async function hashBytes(bytes) {
    if (!crypto.subtle) throw new Error('이 브라우저에서 원본 SHA-256을 검사할 수 없습니다. 최신 Edge 또는 Chrome에서 여세요.');
    return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
  }
  async function checkReportedCases() {
    const config = selected.configs.find(item => R.key(item.path) === R.key(reportedFolder + '/config.xml'));
    if (!config || config.file.size > 4 * 1024 * 1024) return false;
    const configSha = await hashBytes(new Uint8Array(await config.file.arrayBuffer()));
    if (configSha !== 'd9eb2d892f5a4f59e5cc6b1524eef079137afadd7165032d5169337cde551ac1') return false;
    for (const [filename, expected] of Object.entries(reportedHashes)) {
      const photo = selected.photos.find(item => R.key(item.path) === R.key(reportedFolder + '/' + filename));
      if (!photo || await hashPhoto(photo) !== expected) return false;
    }
    return true;
  }
  function render() {
    const body = $('repairRows'); body.replaceChildren();
    result.assignments.forEach(row => {
      const tr = document.createElement('tr'), imageCell = document.createElement('td');
      const photo = selected.photos.find(item => R.key(item.path) === R.key(row.photo));
      const image = document.createElement('img'), url = URL.createObjectURL(photo.file); previewURLs.push(url);
      image.src = url; image.alt = row.name; image.loading = 'lazy'; imageCell.append(image); tr.append(imageCell);
      [row.name + ' · ' + row.id + (row.club ? '\n' + row.club : '') + (row.captionDiffers ? '\n사진 원문 캡션: ' + row.captionName : '') +
        (row.requiresReview ? '\n이름 표기 검토 필요: 사진과 선수 이름을 직접 확인하세요.' : ''), row.photo, row.oldTarget + '\n→ ' + row.newTarget + (row.changed ? '' : '\n(기존 연결과 같음)')].forEach(value => {
        const td = document.createElement('td'); td.textContent = value; td.style.whiteSpace = 'pre-wrap'; tr.append(td);
      });
      body.append(tr);
    });
    const counts = $('repairCounts'); counts.replaceChildren();
    ['확인된 연결 ' + result.assignments.length + '명', '연결 변경 ' + result.changedAssignments + '명', '교체 config ' + result.changedConfigs.length + '개', '충돌 매핑 제외 ' + result.removed.length + '개'].forEach(label => {
      const badge = document.createElement('span'); badge.textContent = label; counts.append(badge);
    });
    const captionWarnings = result.assignments.filter(row => row.captionDiffers || row.requiresReview).length;
    const lines = [
      '자동 대조 사진 목록 ' + catalog.length + '장 중 유일하게 대조되는 경우만 처리합니다. 정확히 같은 기본 이름을 우선하고, 기본 이름이 없을 때만 검수 별칭을 사용합니다. 사진 목록의 동명이인과 선수 명단의 동명이인은 제외합니다.',
      '사진 원문 캡션이 다르거나 이름 표기 검토가 필요한 연결: ' + captionWarnings + '명 (위 사진과 이름을 직접 확인하세요.)',
      '이름이 사진 목록에 없는 선수: ' + result.unmatched.length + '명 (변경하지 않음)',
      '동명이인으로 제외: ' + result.ambiguous.length + '명' + (result.ambiguous.length ? '\n' + result.ambiguous.map(row => '  ' + row.name + ' · ' + row.id + (row.club ? ' · ' + row.club : '')).join('\n') : ''),
      '수정 ID에 겹쳐 제외하는 매핑: ' + result.removed.length + '개' + (result.removed.length ? '\n' + result.removed.map(row => '  ' + row.config + ' / ' + row.photo + ' / ' + row.oldTarget + ' → ' + row.correctedName + '의 확인된 사진으로 교체').join('\n') : ''),
      '이 과정에서 portrait 연결을 잃는 다른 사진: ' + result.unmapped.length + '장' + (result.unmapped.length ? '\n' + result.unmapped.map(row => '  ' + row.photo + ' — 실제 이름·ID 확인이 더 필요합니다.').join('\n') : ''),
      '수정 대상 밖에서 남는 중복 연결: ' + result.remainingDuplicateTargets.length + '개' + (result.remainingDuplicateTargets.length ? '\n' + result.remainingDuplicateTargets.map(row => '  ' + row.targetKey + ': ' + row.count + '개 — 임의 수정하지 않음').join('\n') : ''),
      '선택한 celeb 폴더 밖의 다른 팩은 검사하지 않았습니다. 전체 사진의 이름·ID 복구 완료를 뜻하지 않습니다.'
    ];
    $('repairImpactText').textContent = lines.join('\n\n');
    $('repairImpactSummary').textContent = '충돌 영향: 연결을 잃는 사진 ' + result.unmapped.length + '장 · 동명이인 제외 ' + result.ambiguous.length + '명';
    $('repairImpact').open = !!(result.unmapped.length || result.ambiguous.length || result.remainingDuplicateTargets.length || captionWarnings);
    $('repairResults').hidden = false;
    status('repairStatus', result.assignments.length ? '원본 사진 해시와 실제 이름·ID 대조를 마쳤습니다. 아래 사진과 충돌 영향을 확인하세요.' : '현재 이름 목록과 유일하게 일치하는 사진이 없습니다. 실제 이름·ID와 celeb 폴더를 확인하세요.');
    if (result.assignments.length && !result.changedConfigs.length) status('repairExportStatus', '대조한 사진은 이미 같은 ID에 연결되어 있습니다. 교체할 config가 없습니다. 캐시와 다른 설치 폴더의 같은 ID 연결을 확인하세요.');
  }
  $('repairFolder').addEventListener('change', action('folderStatus', async () => {
    invalidate(); selected = null; reportedReady = false; $('reportedCases').disabled = true;
    selected = inventory($('repairFolder').files);
    const label = selected.root + ' / PNG ' + selected.photos.length + '장 / config.xml ' + selected.configs.length + '개';
    status('folderStatus', label + '\n알려주신 제시카·이현주 사례에 맞는 원본 파일인지 검사하는 중…');
    reportedReady = await checkReportedCases();
    status('folderStatus', label + (reportedReady ? '\n원본 config와 관련 사진 4장의 해시가 일치합니다. 두 사례 불러오기 버튼을 사용할 수 있습니다.' : '\n두 사례 전용 원본 해시는 일치하지 않습니다. 실제 이름·ID 직접 입력 기능으로 대조할 수 있습니다.'));
  }));
  $('reportedCases').addEventListener('click', () => {
    if (busy || !selected || !reportedReady) return;
    invalidate(); $('repairRoster').value = 'id,name,club\n2002043293,제시카,X싱어즈\n2002043294,이현주,X싱어즈\n';
    $('actualRoster').checked = true;
    status('repairStatus', '현재 세이브에서 직접 알려주신 제시카 2002043293·이현주 2002043294를 넣었습니다. 이름·사진·충돌 대조하기를 누르세요. XML 후보 번호나 일괄 밀림 값은 사용하지 않습니다.');
  });
  $('repairRoster').addEventListener('input', invalidate);
  $('actualRoster').addEventListener('change', invalidate);
  $('repairReviewed').addEventListener('change', updateDownload);
  $('repairRosterFile').addEventListener('change', action('repairStatus', async () => {
    invalidate(); const file = $('repairRosterFile').files[0]; if (!file) return;
    if (file.size > 32 * 1024 * 1024) throw new Error('선수 목록은 32MiB 이하로 나누어 주세요.');
    let text = await file.text();
    if (/\.html?$/i.test(file.name) || /^\s*(?:<!doctype|<html|<table)/i.test(text)) {
      // A detached template keeps HTML resources/scripts inert while reading
      // table text; the imported HTML is never inserted into the visible page.
      const template = document.createElement('template'); template.innerHTML = text;
      const tables = [...template.content.querySelectorAll('table')].map(table => [...table.rows].map(row => [...row.cells].map(cell => cell.textContent.trim()))).filter(rows => rows.length > 1);
      if (tables.length !== 1) throw new Error('선수 표가 하나인 HTML을 사용하거나 CSV로 정리하세요.');
      text = tables[0].map(row => row.map(cell => '"' + cell.replace(/"/g, '""') + '"').join(',')).join('\n');
    }
    const roster = C.players(text); $('repairRoster').value = text;
    status('repairStatus', roster.length + '명의 ID·이름을 읽었습니다. 현재 게임에서 확인한 목록인지 체크하고 대조하세요.');
  }));
  $('repairPreview').addEventListener('click', action('repairStatus', async () => {
    invalidate();
    if (!selected) throw new Error('먼저 config.xml과 PNG가 있는 celeb 폴더를 선택하세요.');
    if (!$('actualRoster').checked) throw new Error('현재 게임에서 확인한 여자 연예인 선수 명단인지 체크하세요.');
    const roster = C.players($('repairRoster').value), configs = [], photos = selected.photos.map(photo => ({ path: photo.path, sha256: '' }));
    const names = new Map(), identityNames = R.nameIndex(catalog), candidates = new Set();
    roster.forEach(row => names.set(row.name, (names.get(row.name) || 0) + 1));
    names.forEach((count, name) => {
      const matches = identityNames.get(name) || [];
      if (count === 1 && matches.length === 1) candidates.add(matches[0]);
    });
    status('repairStatus', '기존 config와 일치 후보 ' + candidates.size + '장의 원본 사진을 검사하는 중…');
    for (const item of selected.configs) configs.push(await readConfig(item));
    const byPath = new Map(selected.photos.map((photo, index) => [R.key(photo.path), { ...photo, index }]));
    for (const candidate of candidates) {
      const photo = byPath.get(R.key(candidate.path));
      if (!photo) throw new Error(candidate.name + ': 검수한 사진이 없습니다. celeb 폴더 전체를 선택하세요.');
      photos[photo.index].sha256 = await hashPhoto(photo);
    }
    result = R.plan({ configs, roster, catalog, photos }); render();
  }));
  $('repairDownload').addEventListener('click', action('repairExportStatus', async () => {
    if (!result || !$('repairReviewed').checked || !result.changedConfigs.length) throw new Error('먼저 대조 결과와 백업 안내를 확인하세요.');
    const entries = result.changedConfigs.map(config => ({ path: config.path, bytes: encoder.encode(config.text) }));
    const readme = [
      'FM26 기존 연예인 얼굴 config 교체 자료', '',
      '사진 원본은 포함되어 있지 않습니다. 현재 선택한 celeb 폴더에만 사용하세요.',
      '1. 현재 celeb 폴더 또는 원본 config를 graphics 폴더 밖에 백업합니다.',
      '2. 이 ZIP을 별도 임시 폴더에 풉니다.',
      '3. SM/config.xml 같은 하위 폴더 구조를 유지해 기존 celeb 폴더 안에 복사하고 config.xml을 교체합니다.',
      '4. 원래 PNG 파일은 유지합니다. 예전 config를 다른 XML 이름으로 graphics 안에 보관하지 마세요.',
      '5. 게임을 완전히 종료하고 다시 실행해 선수 이름·사진을 확인합니다. 얼굴 연결만 바꾸므로 새 세이브는 필요하지 않습니다.',
      '6. 되돌릴 때는 백업한 원본 config를 복원합니다.', '',
      '확인된 연결: ' + result.assignments.length + '명 / 연결 변경: ' + result.changedAssignments + '명 / 교체 config: ' + result.changedConfigs.length + '개',
      '수정 대상 ID에 겹치는 다른 사진 매핑 제외: ' + result.removed.length + '개 / portrait 연결을 잃는 다른 사진: ' + result.unmapped.length + '장',
      '연결을 잃는 사진은 UNMAPPED_PHOTOS.csv를 확인하세요. 이 사진의 실제 이름·ID는 추가 확인이 필요합니다.',
      '동명이인 제외: ' + result.ambiguous.length + '명 / 검수 이름 목록에 없는 선수: ' + result.unmatched.length + '명',
      '검수 사진 목록 ' + catalog.length + '장 안에서 실제 명단과 대조되는 연결만 처리했습니다. 사진 캡션 전수 검수나 이 파일의 생성은 전체 실제 ID 확인·전체 복구 완료를 뜻하지 않습니다.',
      '선택한 폴더 밖의 다른 팩은 검사하지 않았습니다. 같은 ID를 연결한 다른 config가 있으면 별도로 확인하세요.',
      '실제 ID만 사용했고, 사진 파일 번호·XML 후보 번호·일괄 offset을 실제 ID로 추정하지 않았습니다.',
      '이름·국적·능력치·세이브는 수정하지 않습니다. EXE/CMD/PowerShell/Python 파일이 없습니다.'
    ].join('\r\n');
    entries.push({ path: 'REPAIR_README.txt', bytes: encoder.encode(readme) });
    entries.push({ path: 'VERIFIED_CONNECTIONS.csv', bytes: encoder.encode(C.csv(result.assignments, ['id', 'name', 'catalogName', 'captionName', 'captionDiffers', 'requiresReview', 'club', 'photo', 'config', 'oldTarget', 'newTarget', 'changed'])) });
    entries.push({ path: 'REMOVED_CONFLICTS.csv', bytes: encoder.encode(C.csv(result.removed, ['config', 'photo', 'oldTarget', 'correctedName', 'reason'])) });
    entries.push({ path: 'UNMAPPED_PHOTOS.csv', bytes: encoder.encode(C.csv(result.unmapped, ['photo', 'config', 'oldTarget', 'correctedName', 'reason'])) });
    const zip = F.buildZipParts(entries);
    download('fm26-face-config-repair.zip', new Blob(zip.parts, { type: 'application/zip' }));
    status('repairExportStatus', result.changedConfigs.length + '개 config 교체 ZIP을 저장했습니다. 먼저 graphics 밖에 백업한 뒤 기존 config 위치에 덮어쓰세요. 사진 PNG를 이동하거나 지우지 마세요.');
  }));
})();
