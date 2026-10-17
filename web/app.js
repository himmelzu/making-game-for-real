(function () {
  'use strict';
  const core = window.FMFaceCore;
  const $ = function (id) { return document.getElementById(id); };
  const folder = $('photoFolder'), idsInput = $('ids'), sortOrder = $('sortOrder'), includeStaff = $('includeStaff');
  const preview = $('previewSection'), list = $('mappingList'), dialog = $('photoDialog');
  const rowUrls = new Map(), dialogUrls = new Set(), downloadUrls = new Set();
  const collator = new Intl.Collator('ko', { numeric: true, sensitivity: 'base' });
  let originalFiles = [], photos = [], rows = [], currentRow = -1, searchOffset = 0, busy = false;
  const PAGE_SIZE = 40;
  function displayPath(file) { return file.webkitRelativePath || file.name; }
  function sizeText(bytes) { return (bytes / 1024 / 1024).toFixed(1) + 'MiB'; }
  function status(id, message, error) { const el = $(id); el.textContent = message; el.classList.toggle('error', Boolean(error)); }
  function clearUrls(collection) { for (const url of collection.values()) URL.revokeObjectURL(url); collection.clear(); }
  function orderedIndexes() {
    return photos.map(function (_, i) { return i; }).sort(function (a, b) {
      const first = sortOrder.value === 'name' ? photos[a].name : displayPath(photos[a]);
      const second = sortOrder.value === 'name' ? photos[b].name : displayPath(photos[b]);
      const compare = collator.compare(first, second) || collator.compare(displayPath(photos[a]), displayPath(photos[b])) || a - b;
      return sortOrder.value === 'reverse' ? -compare : compare;
    });
  }
  function invalidate() {
    rows = []; currentRow = -1; clearUrls(rowUrls); list.replaceChildren(); preview.hidden = true;
    $('downloadZip').disabled = true; status('inputStatus', ''); status('downloadStatus', '');
    if (dialog.open) dialog.close();
  }
  function updatePlanStatus() {
    const missing = rows.filter(function (row) { return row.photoIndex === null; }).length;
    const used = new Set(rows.filter(function (row) { return row.photoIndex !== null; }).map(function (row) { return row.photoIndex; }));
    const bytes = Array.from(used).reduce(function (sum, index) { return sum + photos[index].size; }, 0);
    let message = '유소년 ' + rows.length + '명 · 폴더의 PNG ' + photos.length + '장 · 선택한 사진 ' + used.size + '장';
    if (missing) message += '\n사진이 없는 선수 ' + missing + '명: 각각 선택하거나 아래 반복 배정 버튼을 사용하세요.';
    else if (photos.length > rows.length) message += '\n연결하지 않은 사진은 ZIP에 넣지 않습니다.';
    else if (photos.length < rows.length) message += '\n같은 사진을 여러 선수에게 연결했습니다. ZIP에는 사진마다 한 번만 복사합니다.';
    status('mappingStatus', message, Boolean(missing));
    $('repeatAssign').hidden = !missing;
    $('downloadZip').disabled = busy || Boolean(missing) || !rows.length;
    $('zipSummary').textContent = '사용 사진 합계 ' + sizeText(bytes) + ' · config.xml + 선택 사진만 포함';
    if (!missing) {
      try { core.makePlan(rows.map(function (r) { return r.id; }), rows.map(function (r) { return r.photoIndex; }), photos); }
      catch (error) { status('mappingStatus', error.message, true); $('downloadZip').disabled = true; }
    }
  }
  function renderRows() {
    clearUrls(rowUrls); list.replaceChildren();
    rows.forEach(function (row, rowIndex) {
      const item = document.createElement('li'); item.className = 'mapping';
      const photo = row.photoIndex === null ? null : photos[row.photoIndex];
      let image;
      if (photo) {
        image = document.createElement('img'); image.className = 'portrait'; image.alt = 'ID ' + row.id + '에 연결할 사진'; image.loading = 'lazy';
        if (!rowUrls.has(row.photoIndex)) rowUrls.set(row.photoIndex, URL.createObjectURL(photo));
        image.src = rowUrls.get(row.photoIndex);
        image.addEventListener('error', function () { image.alt = '사진 읽기 실패'; });
      } else { image = document.createElement('div'); image.className = 'portrait placeholder'; image.textContent = '사진 선택\n필요'; }
      const info = document.createElement('div'); info.className = 'mappingInfo';
      const title = document.createElement('strong'); title.textContent = 'ID ' + row.id;
      const filename = document.createElement('div'); filename.className = 'filename'; filename.textContent = photo ? displayPath(photo) : '아직 연결한 사진이 없습니다.';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary small'; button.textContent = '사진 선택';
      button.addEventListener('click', function () { openPhotoPicker(rowIndex); });
      info.append(title, filename, button); item.append(image, info); list.append(item);
    });
    updatePlanStatus();
  }
  function openPhotoPicker(rowIndex) {
    if (busy) return;
    currentRow = rowIndex; searchOffset = 0; $('photoSearch').value = '';
    $('dialogTitle').textContent = 'ID ' + rows[rowIndex].id + '의 사진 선택';
    renderPhotoPicker(); dialog.showModal(); $('photoSearch').focus();
  }
  function renderPhotoPicker() {
    clearUrls(dialogUrls); $('photoGrid').replaceChildren();
    const query = $('photoSearch').value.trim().toLocaleLowerCase('ko');
    const matches = orderedIndexes().filter(function (index) { return displayPath(photos[index]).toLocaleLowerCase('ko').includes(query); });
    if (searchOffset >= matches.length) searchOffset = 0;
    const page = matches.slice(searchOffset, searchOffset + PAGE_SIZE);
    $('searchStatus').textContent = matches.length ? '검색 결과 ' + matches.length + '장 중 ' + (searchOffset + 1) + '–' + (searchOffset + page.length) + '장' : '일치하는 사진이 없습니다.';
    page.forEach(function (photoIndex) {
      const file = photos[photoIndex], button = document.createElement('button'); button.type = 'button'; button.className = 'photoOption';
      const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy';
      const url = URL.createObjectURL(file); dialogUrls.add(url); image.src = url;
      const name = document.createElement('span'); name.textContent = displayPath(file);
      button.append(image, name);
      button.addEventListener('click', function () {
        if (currentRow < 0 || !rows[currentRow]) return;
        rows[currentRow].photoIndex = photoIndex; dialog.close(); renderRows(); status('downloadStatus', '');
      });
      $('photoGrid').append(button);
    });
    $('morePhotos').hidden = matches.length <= PAGE_SIZE;
    $('morePhotos').textContent = searchOffset + PAGE_SIZE >= matches.length ? '처음 사진으로 돌아가기' : '다음 사진 보기';
    $('morePhotos').dataset.total = String(matches.length);
  }
  function applyPhotoFilter() {
    invalidate();
    if (!originalFiles.length) { status('photoStatus', '선택한 폴더가 없습니다.'); return; }
    const result = core.filterPhotos(originalFiles, includeStaff.checked);
    photos = result.photos;
    status('photoStatus', 'PNG ' + photos.length + '장 선택됨 · 스태프 사진 ' + result.excludedStaff + '장 제외' + (result.ignoredNonPng ? ' · PNG가 아닌 파일 ' + result.ignoredNonPng + '개 제외' : '') + '\n사진은 이 브라우저에서만 처리하며, 연결한 사진만 ZIP에 복사합니다.', !photos.length);
  }
  folder.addEventListener('change', function () {
    if (!folder.files.length) return;
    originalFiles = Array.from(folder.files);
    applyPhotoFilter();
  });
  includeStaff.addEventListener('change', applyPhotoFilter);
  idsInput.addEventListener('input', invalidate);
  sortOrder.addEventListener('change', invalidate);
  $('makePreview').addEventListener('click', function () {
    try {
      if (!photos.length) throw new Error('먼저 PNG 얼굴 사진이 있는 폴더를 선택해 주세요.');
      const ids = core.parseIds(idsInput.value), order = orderedIndexes();
      rows = ids.map(function (id, i) { return { id: id, photoIndex: i < order.length ? order[i] : null }; });
      status('inputStatus', ''); status('downloadStatus', ''); preview.hidden = false; renderRows(); preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { status('inputStatus', error.message, true); }
  });
  $('repeatAssign').addEventListener('click', function () {
    const order = orderedIndexes();
    rows.forEach(function (row, i) { if (row.photoIndex === null) row.photoIndex = order[i % order.length]; });
    renderRows();
  });
  $('closeDialog').addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('close', function () { clearUrls(dialogUrls); $('photoGrid').replaceChildren(); currentRow = -1; });
  $('photoSearch').addEventListener('input', function () { searchOffset = 0; renderPhotoPicker(); });
  $('morePhotos').addEventListener('click', function () {
    searchOffset += PAGE_SIZE; if (searchOffset >= Number($('morePhotos').dataset.total)) searchOffset = 0; renderPhotoPicker();
  });
  function setBusy(value) {
    busy = value;
    [folder, includeStaff, idsInput, sortOrder, $('makePreview'), $('repeatAssign')].forEach(function (el) { el.disabled = value; });
    list.querySelectorAll('button').forEach(function (button) { button.disabled = value; });
    updatePlanStatus();
  }
  $('downloadZip').addEventListener('click', async function () {
    try {
      setBusy(true); status('downloadStatus', '선택한 사진을 확인하고 새 ZIP을 만드는 중입니다…');
      const plan = core.makePlan(rows.map(function (r) { return r.id; }), rows.map(function (r) { return r.photoIndex; }), photos);
      const entries = [{ path: 'config.xml', bytes: new TextEncoder().encode(plan.xml) }];
      for (let index = 0; index < plan.assets.length; index++) {
        const asset = plan.assets[index], file = photos[asset.sourceIndex];
        const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
        if (!core.hasPngSignature(head)) throw new Error(displayPath(file) + ': PNG 파일 내용을 확인할 수 없습니다. 다른 사진을 선택해 주세요.');
        const bytes = new Uint8Array(await file.arrayBuffer());
        if (bytes.length !== file.size) throw new Error(displayPath(file) + ': 사진을 끝까지 읽지 못했습니다.');
        entries.push({ path: asset.path, bytes: bytes });
        status('downloadStatus', '사진 ' + (index + 1) + '/' + plan.assets.length + '장 준비됨…');
      }
      const zip = core.buildZipParts(entries), blob = new Blob(zip.parts, { type: 'application/zip' });
      const url = URL.createObjectURL(blob); downloadUrls.add(url);
      const link = document.createElement('a'), date = new Date();
      const stamp = [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0'), '_', String(date.getHours()).padStart(2, '0'), String(date.getMinutes()).padStart(2, '0'), String(date.getSeconds()).padStart(2, '0')].join('');
      link.href = url; link.download = 'FM26_newgen_faces_' + stamp + '.zip'; document.body.append(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); downloadUrls.delete(url); }, 60000);
      status('downloadStatus', '새 ZIP 다운로드를 요청했습니다. 유소년 ' + plan.mappings.length + '명 · 사진 ' + plan.assets.length + '장 · ' + sizeText(blob.size) + '. 아래 적용 방법을 확인하세요.');
    } catch (error) { status('downloadStatus', error.message || 'ZIP을 만들지 못했습니다.', true); }
    finally { setBusy(false); }
  });
  window.addEventListener('pagehide', function () { clearUrls(rowUrls); clearUrls(dialogUrls); clearUrls(downloadUrls); });
})();
