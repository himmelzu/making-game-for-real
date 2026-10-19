'use strict';
(function () {
  const C = window.FMCareer, F = window.FMFaceCore, $ = id => document.getElementById(id);
  let state = null, pool = C.catalog(window.FMYouthCatalog || window.FMCelebrityCatalog), photos = [], active = [], roster = [], busy = false;
  const previewURLs = [];
  $('poolCount').textContent = pool.length + '장 · 이름 자막 확인';
  function status(where, message, error) { $(where).textContent = message; $(where).classList.toggle('error', !!error); }
  function error(where, fn) { return async function () { if (busy) return; busy = true; try { await fn(); } catch (e) { status(where, e.message, true); } finally { busy = false; } }; }
  function download(name, body, type) {
    const url = URL.createObjectURL(body instanceof Blob ? body : new Blob([body], { type: type || 'text/plain;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  function getRoster() { roster = C.players($('roster').value); if ($('explicitFemale').checked) roster.forEach(p => { if (p.female === null) p.female = true; }); status('rosterStatus', roster.length + '명 / 여성으로 표시 ' + roster.filter(p => p.female === true).length + '명'); return roster; }
  function getState() { const key = $('saveKey').value.trim(); if (!state) state = C.newState(key); if (state.saveKey !== key) throw new Error('현재 기록과 세이브 이름이 다릅니다. 다른 세이브는 페이지를 새로 열어 사용하세요.'); return state; }
  function render() {
    previewURLs.splice(0).forEach(url => URL.revokeObjectURL(url));
    const body = $('results'); body.replaceChildren();
    active.slice(0, 500).forEach(a => {
      const row = document.createElement('tr'), photo = document.createElement('td');
      try { const f = findPhoto(a.path), img = document.createElement('img'), url = URL.createObjectURL(f); previewURLs.push(url); img.src = url; img.alt = a.name; img.loading = 'lazy'; img.className = 'preview'; photo.append(img); }
      catch (_) { photo.textContent = '사진 폴더 선택'; }
      row.append(photo); [a.id, a.original, a.name, a.path].forEach(v => { const td = document.createElement('td'); td.textContent = v; row.append(td); }); body.append(row);
    });
    $('faceZip').disabled = !active.length; $('names').disabled = !state || !state.assignments.length; $('saveState').disabled = !state;
    if (active.length > 500) status('exportStatus', '표는 처음 500명만 표시합니다. 내보내기는 모든 기록을 유지하며 얼굴팩은 300명씩 나눕니다.');
  }
  function findPhoto(path) {
    const matches = photos.filter(f => { const p = (f.webkitRelativePath || f.name).normalize('NFC'); return p === path || p.endsWith('/' + path); });
    if (matches.length !== 1) throw new Error(path + ': 사진이 없거나 같은 상대 경로가 여러 개입니다. celeb 폴더를 선택하세요.');
    return matches[0];
  }
  $('sample').addEventListener('click', () => {
    $('roster').value = 'id,name,club,age,gender,newgen,ca,pa,dob\n900000001,예시 유소년,예시 구단,16,F,1,70,150,2009-02-03\n900000002,예시 기존선수,예시 구단,30,F,0,120,160,1995-04-15\n';
    status('rosterStatus', '가상의 샘플입니다. 실제 게임 ID와 선수 데이터로 바꾸어 사용하세요.');
  });
  $('rosterFile').addEventListener('change', error('rosterStatus', async () => {
    const f = $('rosterFile').files[0]; if (!f) return; if (f.size > 32 * 1024 * 1024) throw new Error('목록은 32MiB 이하로 나누어 주세요.');
    let raw = await f.text();
    if (/\.html?$/i.test(f.name) || /^\s*(?:<!doctype|<html|<table)/i.test(raw)) {
      const doc = new DOMParser().parseFromString(raw, 'text/html');
      const tables = [...doc.querySelectorAll('table')].map(t => [...t.rows].map(r => [...r.cells].map(c => c.textContent.trim()))).filter(rows => rows.length > 1);
      if (tables.length !== 1) throw new Error('선수 표가 하나인 HTML을 사용하세요. 표를 선택할 수 없으면 CSV로 정리하세요.');
      raw = tables[0].map(row => row.map(cell => '"' + cell.replace(/"/g, '""') + '"').join(',')).join('\n');
    }
    $('roster').value = raw; getRoster();
  }));
  $('stateFile').addEventListener('change', error('stateStatus', async () => {
    const f = $('stateFile').files[0]; if (!f) return; if (f.size > 64 * 1024 * 1024) throw new Error('기록 파일이 너무 큽니다.');
    const restored = C.restore(await f.text(), $('saveKey').value); state = restored; active = state.assignments; render(); status('stateStatus', state.saveKey + ' / 배정 ' + active.length + '명 / 기준 ' + state.baseline.length + '명 / 마지막 ' + state.lastDate);
  }));
  $('customCatalog').addEventListener('change', error('photoStatus', async () => {
    const f = $('customCatalog').files[0]; if (!f) return; if (f.size > 8 * 1024 * 1024) throw new Error('이름 목록은 8MiB 이하로 나누어 주세요.');
    pool = C.catalog(C.parseCSV(await f.text())); $('poolCount').textContent = pool.length + '장 · 사용자 이름 목록';
    status('photoStatus', '새 목록 ' + pool.length + '장. 기존 배정은 유지하고 새 배정부터 사용합니다.');
  }));
  $('photos').addEventListener('change', () => {
    const result = F.filterPhotos($('photos').files, false); photos = result.photos;
    status('photoStatus', photos.length + '장 선택 / 스태프 제외 ' + result.excludedStaff + '장. 이름 목록에 있는 사진만 자동 배정에 사용합니다.');
    render();
  });
  $('assign').addEventListener('click', error('actionStatus', () => {
    const result = C.propose(getState(), getRoster(), pool, $('gameDate').value, { allowReuse: $('reuse').checked, explicitYouth: $('explicitYouth').checked, onlyUnseen: $('onlyUnseen').checked });
    state = result.state; active = state.assignments; render();
    status('actionStatus', '새 배정 ' + result.added.length + '명 / 누적 ' + active.length + '명 / 이번 재사용 ' + result.reused.length + '명. 이름은 CSV를 보고 게임에서 직접 수정하세요. 기록 JSON도 저장하세요.');
  }));
  $('snapshot').addEventListener('click', error('actionStatus', () => {
    const result = C.propose(getState(), getRoster(), pool, $('gameDate').value, { baselineOnly: true });
    state = result.state; active = state.assignments; render();
    status('actionStatus', '기준 기록 ' + state.baseline.length + '명. 새 배정은 하지 않았습니다. 시즌 기록 JSON을 저장하세요. 기존 기준 CA·PA는 덮어쓰지 않습니다.');
  }));
  $('fixExisting').addEventListener('click', error('actionStatus', () => {
    const matches = C.matchExisting(getRoster(), pool); active = matches.exact; render();
    status('actionStatus', '이름이 정확히 일치하는 사진 ' + active.length + '명 / 동명이인 등 제외 ' + matches.ambiguous.length + '명. 이 대조는 시즌 배정 기록에 추가하지 않습니다. 생성 선수 여부와 사진을 확인 후 내보내세요.');
  }));
  $('saveState').addEventListener('click', error('exportStatus', () => { download('career-state.json', JSON.stringify(getState(), null, 2), 'application/json'); status('exportStatus', '다음 시즌에도 이 JSON을 불러오세요. 기존 기록을 날짜별로 보관하세요.'); }));
  $('names').addEventListener('click', error('exportStatus', () => { download('name-edit-tasks.csv', C.nameTasks(getState()), 'text/csv;charset=utf-8'); status('exportStatus', '이름 수정 CSV를 저장했습니다. 인게임 에디터 또는 별명 기능에서 직접 적용하세요.'); }));
  $('faceZip').addEventListener('click', error('exportStatus', async () => {
    const batch = Number($('batch').value); if (!Number.isInteger(batch) || batch < 1) throw new Error('묶음 번호를 확인하세요.');
    const selected = active.slice((batch - 1) * 300, batch * 300); if (!selected.length) throw new Error('해당 묶음에 선수가 없습니다.');
    status('exportStatus', '사진 경로·PNG 형식·확인된 원본 해시를 검사하는 중…');
    const files = [], byPath = new Map(), indexes = [];
    for (const a of selected) { if (!byPath.has(a.path)) { byPath.set(a.path, files.length); files.push(findPhoto(a.path)); } indexes.push(byPath.get(a.path)); }
    const plan = F.makePlan(selected.map(a => a.id), indexes, files, { targetType: $('regenPrefix').checked ? 'generated' : 'database' });
    const enc = new TextEncoder(), entries = [{ path: 'config.xml', bytes: enc.encode(plan.xml) }];
    for (const asset of plan.assets) {
      const file = files[asset.sourceIndex], bytes = new Uint8Array(await file.arrayBuffer());
      if (!F.hasPngSignature(bytes)) throw new Error(file.name + ': PNG 파일이 아닙니다.');
      const expected = selected.filter(a => byPath.get(a.path) === asset.sourceIndex).map(a => a.sha256).filter(Boolean);
      if (expected.length) {
        if (!crypto.subtle) throw new Error('이 브라우저에서 사진 원본 확인 기능을 사용할 수 없습니다. 최신 Edge 또는 Chrome에서 여세요.');
        const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
        if (expected.some(s => s !== digest)) throw new Error(file.name + ': 확인한 원본 사진과 해시가 다릅니다. 같은 파일명에 다른 얼굴이 있을 수 있습니다.');
      }
      entries.push({ path: asset.path, bytes });
    }
    entries.push({ path: 'assignments.csv', bytes: enc.encode(C.csv(selected, ['id', 'original', 'name', 'path'])) });
    entries.push({ path: 'READ_ME.txt', bytes: enc.encode('config.xml과 fm26_celeb_실제ID.png 파일을 같은 새 폴더에 두고 게임이 읽는 graphics 안에 넣으세요. faces 하위 폴더는 사용하지 않습니다. 이전에 같은 ID로 만든 도우미 폴더는 graphics 밖으로 빼고 교체하세요. FM26 그래픽 표시 문제 보고를 고려해 같은 폴더와 고유 파일명을 사용하지만 실제 게임 표시는 별도 확인이 필요합니다. 선수 이름은 CSV를 보고 직접 수정해야 합니다.\r\n') });
    const zip = F.buildZipParts(entries); download('fm26-faces-batch-' + batch + '.zip', new Blob(zip.parts, { type: 'application/zip' }));
    status('exportStatus', selected.length + '명 얼굴팩을 저장했습니다. 전체 ' + Math.ceil(active.length / 300) + '개 묶음 중 ' + batch + '번입니다. 기존 같은 ID의 매핑을 중복 적용하지 마세요.');
  }));
  $('maintenance').addEventListener('click', error('maintStatus', () => {
    const tasks = C.maintenance(getState(), getRoster(), $('gameDate').value, Number($('ageCap').value), Number($('targetAge').value));
    download('maintenance-tasks.csv', C.csv(tasks, ['id', 'name', 'age', 'proposed_dob', 'current_ca', 'baseline_ca', 'current_pa', 'baseline_pa', 'action']), 'text/csv;charset=utf-8');
    status('maintStatus', tasks.length + '명 점검 목록입니다. 게임에 자동 적용되지 않습니다. 현재 시즌 기록을 먼저 생성해야 기준 능력치가 저장됩니다.');
  }));
})();
