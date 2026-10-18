"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const sessionKey = location.hash.slice(1);
  const MAX_CANDIDATES = 1000;
  const fields = {
    photos_path: $("photos-path"),
    graphics_path: $("graphics-path"),
    game_date: $("game-date"),
    career_label: $("career-label")
  };
  const selected = new Set();
  let status = null;
  let initialized = false;
  let dirty = false;
  let gameDateDirty = false;
  let requesting = false;
  let polling = false;
  let generation = 0;
  let revision = null;
  let renderedPreview = null;
  let previewFresh = false;
  let pollingError = false;
  let errorMessage = "";
  let localMessage = "";
  const number = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString("ko-KR") : "—";
  const text = (id, value) => {
    const node = $(id);
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  };
  const candidates = () => Array.isArray(status?.preview?.candidates) ? status.preview.candidates : [];
  const maintenance = () => Array.isArray(status?.preview?.maintenance) ? status.preview.maintenance : [];
  const busy = () => requesting || Boolean(status?.busy);
  const assignable = candidate => typeof candidate.key === "string" && candidate.key.length > 0 && Boolean(candidate.assigned_name) && Boolean(candidate.path);

  function settingValues() {
    return Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value.trim()]));
  }

  const validFolders = values => Boolean(values.photos_path && values.graphics_path && values.career_label);

  function validDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [year, month, day] = value.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return year >= 1900 && year <= 2200 && date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  }
  const validSettings = values => validFolders(values) && validDate(values.game_date);
  const saveableSettings = values => validFolders(values) && (!values.game_date || validDate(values.game_date));

  async function api(path, method = "GET", body) {
    const options = {
      method,
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Session-Key": sessionKey }
    };
    if (body !== undefined) options.body = JSON.stringify(body);
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); }
    catch { throw new Error("로컬 도구가 올바른 응답을 보내지 않았습니다. 실행 창의 오류를 확인하세요."); }
    if (!response.ok) throw new Error(String(data.error || `요청에 실패했습니다 (${response.status}).`));
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("로컬 도구의 응답 형식을 확인할 수 없습니다.");
    return data;
  }

  function acceptStatus(data, syncSettings = false) {
    status = data;
    if (!initialized || syncSettings) {
      for (const [key, input] of Object.entries(fields)) input.value = String(data.settings?.[key] ?? "");
      initialized = true;
      dirty = false;
      gameDateDirty = false;
    } else if (data.scan?.date_source === "exact-aob" && validDate(data.settings?.game_date) && !gameDateDirty) {
      fields.game_date.value = data.settings.game_date;
    }
    const nextRevision = data.preview?.revision || null;
    if (data.scan?.stale === true) previewFresh = false;
    if (nextRevision !== revision) {
      revision = nextRevision;
      previewFresh = false;
      selected.clear();
      $("paused").checked = false;
      $("experimental-ack").checked = false;
    }
    const keys = new Set(candidates().filter(assignable).map(candidate => candidate.key));
    for (const key of selected) if (!keys.has(key)) selected.delete(key);
    render();
  }

  function renderPreview() {
    const preview = status?.preview || {};
    const signature = JSON.stringify([preview.revision, candidates(), maintenance(), preview.warnings]);
    if (signature === renderedPreview) return;
    renderedPreview = signature;
    const list = $("candidate-list");
    list.replaceChildren();
    for (const candidate of candidates()) {
      const available = assignable(candidate);
      const label = document.createElement("label");
      label.className = `candidate${available ? "" : " unavailable"}`;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.candidateKey = String(candidate.key ?? "");
      checkbox.dataset.assignable = available ? "yes" : "no";
      checkbox.checked = selected.has(candidate.key);
      checkbox.disabled = busy() || !available;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && selected.size < MAX_CANDIDATES) selected.add(candidate.key);
        else if (checkbox.checked) checkbox.checked = false;
        else selected.delete(candidate.key);
        renderControls();
      });
      const body = document.createElement("span");
      body.className = "candidate-body";
      const title = document.createElement("span");
      title.className = "candidate-title";
      const age = candidate.age === null || candidate.age === undefined ? "나이 미확인" : `${candidate.age}세`;
      title.textContent = `${candidate.name || "현재 이름 미확인"} · ${age}`;
      const assigned = document.createElement("span");
      assigned.className = "candidate-name";
      assigned.textContent = candidate.assigned_name ? `→ ${candidate.assigned_name}` : "배정할 이름 미확인";
      const details = document.createElement("span");
      details.className = "candidate-detail";
      details.textContent = `선수 ID ${candidate.uid ?? "미확인"}`;
      const photo = document.createElement("span");
      photo.className = "candidate-detail";
      photo.textContent = candidate.path ? `사진: ${candidate.path}` : "배정할 사진이 없어 선택할 수 없습니다.";
      body.append(title, assigned, details, photo);
      label.append(checkbox, body);
      list.append(label);
    }
    $("candidates-empty").hidden = candidates().length > 0;
    text("candidates-empty", preview.revision ? "이번 스캔에서 배정할 후보가 없습니다." : "스캔과 첫 등록을 마친 뒤 미리보기를 실행하세요.");

    const warnings = $("preview-warnings");
    warnings.replaceChildren();
    const warningItems = Array.isArray(preview.warnings) ? preview.warnings : [];
    for (const warning of warningItems) {
      const item = document.createElement("li");
      item.textContent = typeof warning === "string" ? warning : String(warning?.message ?? "확인할 경고 항목이 있습니다.");
      warnings.append(item);
    }
    warnings.hidden = warningItems.length === 0;

    text("maintenance-summary", `유지 작업 미리보기 ${number(maintenance().length)}건`);
    const maintenanceList = $("maintenance-list");
    maintenanceList.replaceChildren();
    for (const operation of maintenance()) {
      const item = document.createElement("li");
      item.textContent = `${operation.name || "이름 미확인"} (ID ${operation.uid ?? "미확인"}) · ${operation.action || "유지 작업"}`;
      maintenanceList.append(item);
    }
    if (maintenance().length === 0) {
      const item = document.createElement("li");
      item.textContent = "미리보기에 표시된 유지 작업이 없습니다.";
      maintenanceList.append(item);
    }
  }

  function renderControls() {
    const working = busy();
    const sessionReady = Boolean(sessionKey) && initialized;
    const configured = validSettings(status?.settings || {}) && !dirty;
    const foldersConfigured = validFolders(status?.settings || {}) && !dirty;
    const scanned = Boolean(status?.connected) && Number(status?.scan?.players) > 0;
    const writable = Boolean(status?.connected) && status?.scan?.can_write === true;
    const stale = status?.scan?.stale === true;
    const hasBaseline = status?.state?.exists === true;
    for (const input of Object.values(fields)) input.disabled = working || !sessionKey;
    $("pick-photos").disabled = working || !sessionReady;
    $("pick-graphics").disabled = working || !sessionReady;
    $("save-settings").disabled = working || !sessionReady || !saveableSettings(settingValues());
    $("scan").disabled = working || !sessionReady || !foldersConfigured;
    $("baseline").disabled = working || !configured || !scanned || hasBaseline;
    $("preview").disabled = working || !configured || !scanned || !hasBaseline || stale;
    $("include-existing").disabled = working;
    $("select-all").disabled = working || stale || candidates().every(candidate => !assignable(candidate));
    $("select-none").disabled = working || selected.size === 0;
    $("maintain").disabled = working;
    $("paused").disabled = working;
    $("experimental-ack").disabled = working;
    for (const checkbox of $("candidate-list").querySelectorAll("input[type=checkbox]")) {
      checkbox.disabled = working || stale || checkbox.dataset.assignable !== "yes" || (!selected.has(checkbox.dataset.candidateKey) && selected.size >= MAX_CANDIDATES);
      checkbox.checked = selected.has(checkbox.dataset.candidateKey);
    }
    text("selected-count", `${number(selected.size)}명 선택 · 최대 1,000명`);
    text("baseline", hasBaseline ? "첫 등록 완료" : "현재 선수 첫 등록");
    text("settings-note", dirty ? "설정이 바뀌었습니다. 저장한 뒤 스캔하세요." : initialized ? "저장된 설정을 사용합니다." : "설정을 불러오는 중입니다.");
    let reason = "";
    if (!sessionKey) reason = "도구 실행 창에서 이 페이지를 다시 여세요. 연결 키가 없습니다.";
    else if (working) reason = "현재 작업이 끝날 때까지 기다려 주세요.";
    else if (!foldersConfigured) reason = "폴더 2개와 커리어 이름을 저장하세요. 변경한 설정이 있으면 다시 저장하세요.";
    else if (!configured) reason = "게임 날짜가 필요합니다. 스캔으로 읽거나 게임 화면의 날짜를 입력하고 저장하세요.";
    else if (!scanned) reason = "먼저 게임 읽기 스캔을 실행하세요.";
    else if (stale) reason = "적용 후 다시 검사가 필요합니다. 게임 읽기 스캔과 미리보기를 다시 실행하세요.";
    else if (!writable) reason = "현재 게임 버전 또는 연결 상태에서 쓰기를 지원하지 않아 적용을 차단했습니다.";
    else if (!hasBaseline) reason = "현재 선수 첫 등록을 완료하세요.";
    else if (!revision || !previewFresh) reason = "현재 설정과 스캔으로 배정 미리보기를 다시 실행하세요.";
    else if (selected.size > MAX_CANDIDATES) reason = "한 번에 적용할 선수는 최대 1,000명입니다.";
    else if (selected.size === 0 && !($("maintain").checked && maintenance().length > 0)) reason = "배정할 후보를 선택하거나 미리보기에 나온 유지 작업을 선택하세요.";
    else if (!$("paused").checked || !$("experimental-ack").checked) reason = "별도 세이브·일시정지와 실험판 확인 항목 두 개를 체크하세요.";
    $("apply").disabled = Boolean(reason);
    text("apply-reason", reason || `선수 ${number(selected.size)}명과 ${$("maintain").checked ? `유지 작업 ${number(maintenance().length)}건` : "유지 작업 없이"}을 한 번에 적용합니다.`);
    $("undo").disabled = working || !configured || status?.can_undo !== true || !hasBaseline || !$("paused").checked || !$("experimental-ack").checked;
  }

  function render() {
    $("error").hidden = !errorMessage;
    text("error", errorMessage);
    const connected = Boolean(status?.connected);
    text("connection", connected ? "게임 연결됨" : "게임 연결 전");
    $("connection").classList.toggle("connected", connected);
    const progress = status?.busy ? String(status.progress || "게임 데이터를 처리하는 중입니다.") : requesting ? localMessage || "요청을 처리하는 중입니다." : localMessage || "작업을 선택하세요. 이 도구는 자동으로 반복 적용하지 않습니다.";
    text("progress", progress);
    $("progress").classList.toggle("busy", busy());
    for (const [id, key] of [["players-count", "players"], ["female-count", "female"], ["donor-count", "eligible_donors"]]) text(id, status?.scan ? number(status.scan[key]) : "—");
    text("game-version", status?.scan?.version || "미확인");
    const dateSource = status?.scan?.date_source;
    const dateMessage = status?.scan?.date_message;
    text("date-source", dateSource === "exact-aob" ? `게임에서 읽은 날짜입니다.${dateMessage ? ` ${dateMessage}` : ""}` : dateSource === "manual" ? `게임 화면에서 확인한 날짜를 입력하고 저장하세요.${dateMessage ? ` ${dateMessage}` : ""}` : dateMessage || "먼저 폴더와 커리어 이름을 저장한 뒤 스캔하면 날짜 읽기를 시도합니다.");
    const canWrite = connected && status?.scan?.can_write === true;
    const scanExists = Number(status?.scan?.players) > 0;
    const stale = status?.scan?.stale === true;
    text("write-support", stale ? "적용 후 다시 검사가 필요합니다. 게임 읽기 스캔을 다시 실행하세요. 마지막 적용은 되돌릴 수 있는 경우 별도로 되돌립니다." : !scanExists ? "스캔 후 지원 버전을 확인합니다." : canWrite ? "버전 확인상 적용 가능한 상태입니다. 실제 게임 쓰기 검증을 대신하지는 않습니다." : "지원 버전을 확인하지 못했거나 일치하지 않아 쓰기를 차단했습니다.");
    $("write-support").classList.toggle("supported", canWrite && !stale);
    $("write-support").classList.toggle("unsupported", stale || (scanExists && !canWrite));
    const state = status?.state;
    text("state-summary", state?.exists ? `현재 기록: 보호 선수 ${number(state.protected)}명 · 배정 기록 ${number(state.assigned)}명 · 발견한 선수 ${number(state.seen)}명` : "아직 커리어 기록이 없습니다.");
    text("baseline-note", state?.exists ? "첫 등록이 있습니다. 유소년이 들어온 뒤 다시 스캔하고 미리보기를 실행하세요." : "첫 등록은 현재 선수들을 기록해 이후 새로 발견되는 선수와 구분하는 단계입니다.");
    const result = status?.last_result;
    $("last-result").hidden = !result?.message;
    text("last-result", result?.message || "");
    $("last-result").classList.toggle("failed", result?.ok === false);
    renderPreview();
    renderControls();
  }

  async function run(path, body, message, syncSettings = false) {
    if (busy()) return;
    requesting = true;
    generation += 1;
    errorMessage = "";
    pollingError = false;
    localMessage = message;
    if (path !== "/api/preview") previewFresh = false;
    render();
    try {
      const data = await api(path, "POST", body);
      generation += 1;
      acceptStatus(data, syncSettings);
      if (path === "/api/preview") previewFresh = Boolean(data.preview?.revision);
      localMessage = "";
    } catch (error) {
      generation += 1;
      errorMessage = error instanceof Error ? error.message : "처리에 실패했습니다.";
      localMessage = "요청을 완료하지 못했습니다.";
    } finally {
      requesting = false;
      render();
    }
  }

  async function refresh() {
    if (!sessionKey || polling || document.hidden) return;
    polling = true;
    const epoch = generation;
    try {
      const data = await api("/api/status");
      if (epoch === generation) {
        if (pollingError) { errorMessage = ""; pollingError = false; }
        acceptStatus(data);
      }
    } catch (error) {
      if (epoch === generation && !requesting) {
        errorMessage = error instanceof Error ? error.message : "로컬 도구에 연결할 수 없습니다.";
        pollingError = true;
        if (status) status = { ...status, connected: false };
        render();
      }
    } finally { polling = false; }
  }

  async function pickFolder(kind) {
    if (busy()) return;
    requesting = true;
    generation += 1;
    errorMessage = "";
    localMessage = "Windows 폴더 선택 창에서 폴더를 고르세요.";
    render();
    try {
      const data = await api("/api/folders", "POST", { kind });
      if (typeof data.path === "string" && data.path) {
        fields[kind === "photos" ? "photos_path" : "graphics_path"].value = data.path;
        dirty = true;
        previewFresh = false;
        localMessage = "폴더를 선택했습니다. 나머지 설정을 확인하고 저장하세요.";
      } else localMessage = "폴더 선택을 취소했습니다.";
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "폴더 선택에 실패했습니다.";
      localMessage = "폴더를 선택하지 못했습니다.";
    } finally {
      generation += 1;
      requesting = false;
      render();
    }
  }

  $("settings-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!saveableSettings(settingValues())) {
      errorMessage = "폴더 2개와 커리어 이름을 입력하세요. 날짜를 직접 입력한다면 올바른 게임 날짜를 사용하세요.";
      render();
      return;
    }
    run("/api/settings", settingValues(), "설정을 저장하는 중입니다.", true);
  });
  for (const [key, input] of Object.entries(fields)) input.addEventListener("input", () => { dirty = true; if (key === "game_date") gameDateDirty = true; previewFresh = false; renderControls(); });
  $("pick-photos").addEventListener("click", () => pickFolder("photos"));
  $("pick-graphics").addEventListener("click", () => pickFolder("graphics"));
  $("scan").addEventListener("click", () => run("/api/scan", {}, "게임을 읽기 전용으로 스캔하는 중입니다."));
  $("baseline").addEventListener("click", () => run("/api/baseline", {}, "현재 선수의 첫 기록을 만드는 중입니다."));
  $("include-existing").addEventListener("change", () => {
    previewFresh = false;
    selected.clear();
    $("paused").checked = false;
    $("experimental-ack").checked = false;
    localMessage = "후보 범위가 바뀌었습니다. 배정 미리보기를 다시 실행하세요.";
    render();
  });
  $("preview").addEventListener("click", () => run("/api/preview", { include_existing: $("include-existing").checked }, "이번 시즌 배정과 유지 작업을 준비하는 중입니다."));
  $("select-all").addEventListener("click", () => { for (const candidate of candidates()) { if (selected.size >= MAX_CANDIDATES) break; if (assignable(candidate)) selected.add(candidate.key); } renderControls(); });
  $("select-none").addEventListener("click", () => { selected.clear(); renderControls(); });
  for (const id of ["maintain", "paused", "experimental-ack"]) $(id).addEventListener("change", renderControls);
  $("apply").addEventListener("click", () => {
    renderControls();
    if ($("apply").disabled) return;
    run("/api/apply", { revision, selected_keys: candidates().filter(candidate => selected.has(candidate.key)).map(candidate => candidate.key), maintain: $("maintain").checked, acknowledge_experimental: $("experimental-ack").checked, paused: $("paused").checked }, "선택한 작업을 적용하는 중입니다. 게임을 일시정지 상태로 유지하세요.");
  });
  $("undo").addEventListener("click", () => run("/api/undo", {}, "마지막 적용을 되돌리는 중입니다. 게임을 일시정지 상태로 유지하세요."));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  if (!sessionKey) {
    errorMessage = "연결 키가 없는 페이지입니다. 도구 실행 창에서 브라우저를 다시 여세요.";
    localMessage = "로컬 도구 연결을 확인할 수 없습니다.";
  }
  render();
  refresh();
  setInterval(refresh, 1500);
})();
