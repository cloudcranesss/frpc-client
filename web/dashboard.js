import { escapeHtml, initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  hint: document.querySelector("#hint"),
  statusMeta: document.querySelector("#status_meta"),
  statusPill: document.querySelector("#status-pill"),
  jumpCount: document.querySelector("#jump_count"),
  jumpLinks: document.querySelector("#jump_links"),
  clientList: document.querySelector("#client_list"),
  addClientBtn: document.querySelector("#add_client_btn"),
  deleteClientBtn: document.querySelector("#delete_client_btn"),
  clientName: document.querySelector("#client_name"),
  frpcPath: document.querySelector("#frpc_path"),
  runArgs: document.querySelector("#run_args"),
  autoStart: document.querySelector("#auto_start"),
  configText: document.querySelector("#config_text"),
  autoPathBtn: document.querySelector("#auto_path_btn"),
  saveBtn: document.querySelector("#save_btn"),
  startBtn: document.querySelector("#start_btn"),
  stopBtn: document.querySelector("#stop_btn"),
  refreshBtn: document.querySelector("#refresh_btn"),
  logs: document.querySelector("#logs"),
  logsClearBtn: document.querySelector("#logs_clear_btn"),
  logsToggleBtn: document.querySelector("#logs_toggle_btn"),
  logsToggleLabel: document.querySelector("#logs_toggle_label"),
  preflightPanel: document.querySelector("#preflight_panel"),
  preflightCloseBtn: document.querySelector("#preflight_close_btn"),
  preflightSummary: document.querySelector("#preflight_summary"),
  preflightErrorsWrap: document.querySelector("#preflight_errors_wrap"),
  preflightErrors: document.querySelector("#preflight_errors"),
  preflightWarningsWrap: document.querySelector("#preflight_warnings_wrap"),
  preflightWarnings: document.querySelector("#preflight_warnings"),
  preflightForceBtn: document.querySelector("#preflight_force_btn"),
  batchClientList: document.querySelector("#batch_client_list"),
  batchSelectedCount: document.querySelector("#batch_selected_count"),
  batchSelectAllBtn: document.querySelector("#batch_select_all_btn"),
  batchInvertBtn: document.querySelector("#batch_invert_btn"),
  batchClearBtn: document.querySelector("#batch_clear_btn"),
  batchForceStart: document.querySelector("#batch_force_start"),
  batchSkipFailed: document.querySelector("#batch_skip_failed"),
  batchPreflightBtn: document.querySelector("#batch_preflight_btn"),
  batchStartBtn: document.querySelector("#batch_start_btn"),
  batchStopBtn: document.querySelector("#batch_stop_btn"),
  batchHint: document.querySelector("#batch_hint"),
  batchResultSummary: document.querySelector("#batch_result_summary"),
  batchResultList: document.querySelector("#batch_result_list"),
};

const state = {
  clients: [],
  activeClientId: "",
  logsExpanded: false,
  eventSource: null,
  reconnectDelaySec: 1,
  reconnectTimer: null,
  streamToken: 0,
  batchSelectedIds: new Set(),
};

function setHint(text, level = "info") {
  if (!els.hint) return;
  els.hint.textContent = text;
  els.hint.classList.remove("info", "warn", "error");
  els.hint.classList.add(level);
}

function setBatchHint(text, level = "info") {
  if (!els.batchHint) return;
  els.batchHint.textContent = text;
  els.batchHint.classList.remove("info", "warn", "error");
  els.batchHint.classList.add(level);
}

function setBatchSummary(text, level = "info") {
  if (!els.batchResultSummary) return;
  els.batchResultSummary.textContent = text;
  els.batchResultSummary.classList.remove("info", "warn", "error");
  els.batchResultSummary.classList.add(level);
}

function setStatusMeta(text, level = "info") {
  if (!els.statusMeta) return;
  els.statusMeta.textContent = text;
  els.statusMeta.classList.remove("info", "warn", "error");
  els.statusMeta.classList.add(level);
}

function setStatus(status) {
  if (!els.statusPill) return;
  const running = !!status.running;
  els.statusPill.textContent = running ? "运行中" : "未运行";
  els.statusPill.classList.toggle("online", running);
  els.statusPill.classList.toggle("offline", !running);

  const parts = [];
  if (status.pid) parts.push(`PID: ${status.pid}`);
  parts.push(`重启次数: ${status.restart_count || 0}`);
  if (!running && status.last_exit_code !== null && status.last_exit_code !== undefined) {
    parts.push(`退出码: ${status.last_exit_code}`);
  }
  if (status.last_error) {
    parts.push(`异常: ${status.last_error}`);
    setStatusMeta(parts.join(" | "), "warn");
    return;
  }
  setStatusMeta(parts.join(" | "), "info");
}

function toggleLogs(expanded) {
  if (!els.logs || !els.logsToggleLabel) return;
  state.logsExpanded = expanded;
  els.logs.classList.toggle("hidden", !expanded);
  els.logsToggleLabel.textContent = expanded ? "折叠日志" : "展开日志";
}

function appendLogLine(line) {
  if (!els.logs) return;
  const next = line.endsWith("\n") ? line : `${line}\n`;
  els.logs.textContent += next;
  if (els.logs.textContent.length > 160_000) {
    els.logs.textContent = els.logs.textContent.slice(-140_000);
  }
  els.logs.scrollTop = els.logs.scrollHeight;
}

function renderJumpLinks(items, errorMessage = "") {
  if (!els.jumpLinks || !els.jumpCount) return;
  els.jumpCount.textContent = String(items.length);
  els.jumpLinks.innerHTML = "";

  if (errorMessage) {
    const row = document.createElement("div");
    row.className = "jump-empty";
    row.textContent = errorMessage;
    els.jumpLinks.appendChild(row);
    return;
  }

  if (items.length === 0) {
    const row = document.createElement("div");
    row.className = "jump-empty";
    row.textContent = "暂无可跳转链接。";
    els.jumpLinks.appendChild(row);
    return;
  }

  for (const item of items) {
    const box = document.createElement("div");
    box.className = "jump-link-item";
    box.innerHTML = `
      <div class="jump-link-title">${escapeHtml(item.proxy_name || "-")} · ${escapeHtml(item.proxy_type || "-")}</div>
      <a class="jump-link-url" href="${escapeHtml(item.url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url || "-")}</a>
    `;
    els.jumpLinks.appendChild(box);
  }
}

function renderClients() {
  if (!els.clientList) return;
  els.clientList.innerHTML = "";
  for (const client of state.clients) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `client-item ${client.id === state.activeClientId ? "active" : ""}`;
    row.dataset.clientId = client.id;
    row.innerHTML = `
      <div class="client-item-title">
        <span>${escapeHtml(client.name)}</span>
        <span class="dot ${client.running ? "online" : "offline"}"></span>
      </div>
      <div class="client-item-meta">ID: ${escapeHtml(client.id)}</div>
    `;
    els.clientList.appendChild(row);
  }
}

function syncBatchSelectionWithClients() {
  const validIds = new Set(state.clients.map((item) => item.id));
  for (const clientId of [...state.batchSelectedIds]) {
    if (!validIds.has(clientId)) state.batchSelectedIds.delete(clientId);
  }
}

function updateBatchSelectedCount() {
  if (!els.batchSelectedCount) return;
  els.batchSelectedCount.textContent = String(state.batchSelectedIds.size);
}

function renderBatchClientList() {
  if (!els.batchClientList) return;
  els.batchClientList.innerHTML = "";
  if (!state.clients.length) {
    const row = document.createElement("div");
    row.className = "jump-empty";
    row.textContent = "暂无客户端可批量操作。";
    els.batchClientList.appendChild(row);
    updateBatchSelectedCount();
    return;
  }

  for (const client of state.clients) {
    const row = document.createElement("label");
    row.className = "batch-client-row";
    row.innerHTML = `
      <input type="checkbox" data-client-id="${escapeHtml(client.id)}" ${state.batchSelectedIds.has(client.id) ? "checked" : ""} />
      <span class="batch-client-name">${escapeHtml(client.name)}</span>
      <span class="dot ${client.running ? "online" : "offline"}"></span>
    `;
    els.batchClientList.appendChild(row);
  }
  updateBatchSelectedCount();
}

function formatBatchDetail(detail) {
  if (detail === undefined || detail === null) return "";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail, null, 2);
  } catch {
    return String(detail);
  }
}

function renderBatchResult(payload) {
  const total = Number(payload.total || 0);
  const success = Number(payload.success || 0);
  const failed = Number(payload.failed || 0);
  setBatchSummary(`总计 ${total}，成功 ${success}，失败 ${failed}。`, failed > 0 ? "warn" : "info");

  if (!els.batchResultList) return;
  els.batchResultList.innerHTML = "";
  const items = Array.isArray(payload.items) ? payload.items : [];
  if (!items.length) {
    els.batchResultList.classList.add("hidden");
    return;
  }
  els.batchResultList.classList.remove("hidden");

  for (const item of items) {
    const detailText = formatBatchDetail(item.detail);
    const block = document.createElement("div");
    block.className = `batch-result-item ${item.ok ? "ok" : "fail"}`;
    block.innerHTML = `
      <div class="batch-result-main">
        <span class="batch-result-client">${escapeHtml(item.client_id || "-")}</span>
        <span class="batch-result-msg">${escapeHtml(item.message || (item.ok ? "成功" : "失败"))}</span>
      </div>
      ${detailText ? `<details><summary>详情</summary><pre>${escapeHtml(detailText)}</pre></details>` : ""}
    `;
    els.batchResultList.appendChild(block);
  }
}

function selectedClientIds() {
  return [...state.batchSelectedIds];
}

function selectedClientNames(clientIds) {
  const clientMap = new Map(state.clients.map((item) => [item.id, item.name]));
  return clientIds.map((clientId) => clientMap.get(clientId) || clientId);
}

function ensureBatchSelection() {
  const ids = selectedClientIds();
  if (!ids.length) {
    setBatchHint("请先选择至少一个客户端。", "warn");
    return null;
  }
  return ids;
}

function confirmBatchAction(actionName, ids) {
  const names = selectedClientNames(ids);
  const preview = names.slice(0, 6).join("、");
  const suffix = names.length > 6 ? ` 等 ${names.length} 个` : `（共 ${names.length} 个）`;
  return window.confirm(`确认${actionName}以下客户端？\n${preview}${suffix}`);
}

async function loadClients() {
  const payload = await request("/api/clients");
  state.clients = payload.clients || [];
  const known = state.clients.some((item) => item.id === state.activeClientId);
  state.activeClientId = known ? state.activeClientId : payload.active_client_id || state.clients[0]?.id || "";
  renderClients();
  syncBatchSelectionWithClients();
  renderBatchClientList();
}

async function loadActiveConfig() {
  if (!state.activeClientId) return;
  const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/config`);
  if (!els.clientName || !els.frpcPath || !els.runArgs || !els.autoStart || !els.configText) return;
  els.clientName.value = payload.name || "";
  els.frpcPath.value = payload.frpc_path || "";
  els.runArgs.value = payload.run_args || "";
  els.autoStart.checked = !!payload.auto_start;
  els.configText.value = payload.config_text || "";
}

async function loadStatusAndLogs() {
  if (!state.activeClientId) return;
  const [status, logs] = await Promise.all([
    request(`/api/clients/${encodeURIComponent(state.activeClientId)}/status`),
    request(`/api/clients/${encodeURIComponent(state.activeClientId)}/logs?limit=800`),
  ]);
  setStatus(status);
  if (!els.logs) return;
  els.logs.textContent = (logs.items || []).join("\n");
  els.logs.scrollTop = els.logs.scrollHeight;
}

async function clearLogs() {
  if (!state.activeClientId) return;
  await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/logs/clear`, "POST");
  if (els.logs) {
    els.logs.textContent = "";
    els.logs.scrollTop = 0;
  }
  setHint("日志已清空。", "info");
}

async function loadJumpLinks() {
  if (!state.activeClientId) {
    renderJumpLinks([]);
    return;
  }
  try {
    const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/jump-links`);
    renderJumpLinks(payload.items || []);
  } catch (error) {
    renderJumpLinks([], `跳转链接加载失败: ${error.message}`);
  }
}

function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function connectStream() {
  closeStream();
  if (!state.activeClientId) return;
  const token = ++state.streamToken;
  const source = new EventSource(`/api/clients/${encodeURIComponent(state.activeClientId)}/stream`);
  state.eventSource = source;

  source.onopen = () => {
    state.reconnectDelaySec = 1;
  };

  source.addEventListener("status", (raw) => {
    const payload = JSON.parse(raw.data || "{}");
    setStatus(payload);
  });

  source.addEventListener("log", (raw) => {
    const payload = JSON.parse(raw.data || "{}");
    if (payload.line) appendLogLine(String(payload.line));
  });

  source.addEventListener("event", (raw) => {
    const payload = JSON.parse(raw.data || "{}");
    const type = payload.event_type || "event";
    const message = payload.message || "";
    appendLogLine(`[${type}] ${message}`);
  });

  source.onerror = () => {
    source.close();
    if (token !== state.streamToken) return;
    const delay = state.reconnectDelaySec;
    state.reconnectDelaySec = Math.min(30, state.reconnectDelaySec * 2);
    state.reconnectTimer = setTimeout(() => {
      if (token === state.streamToken) {
        connectStream();
      }
    }, delay * 1000);
  };
}

function hidePreflightPanel() {
  if (!els.preflightPanel) return;
  els.preflightPanel.classList.add("hidden");
  if (
    !els.preflightForceBtn ||
    !els.preflightErrorsWrap ||
    !els.preflightWarningsWrap ||
    !els.preflightErrors ||
    !els.preflightWarnings ||
    !els.preflightSummary
  ) {
    return;
  }
  els.preflightForceBtn.classList.add("hidden");
  els.preflightErrorsWrap.classList.add("hidden");
  els.preflightWarningsWrap.classList.add("hidden");
  els.preflightErrors.innerHTML = "";
  els.preflightWarnings.innerHTML = "";
  els.preflightSummary.textContent = "";
}

function showPreflightPanel(payload) {
  if (
    !els.preflightPanel ||
    !els.preflightSummary ||
    !els.preflightErrorsWrap ||
    !els.preflightErrors ||
    !els.preflightWarningsWrap ||
    !els.preflightWarnings ||
    !els.preflightForceBtn
  ) {
    return;
  }
  const errors = payload.errors || [];
  const warnings = payload.warnings || [];
  els.preflightPanel.classList.remove("hidden");
  els.preflightSummary.textContent = `错误 ${errors.length} 条，警告 ${warnings.length} 条`;

  els.preflightErrors.innerHTML = "";
  if (errors.length) {
    els.preflightErrorsWrap.classList.remove("hidden");
    for (const item of errors) {
      const li = document.createElement("li");
      li.textContent = String(item);
      els.preflightErrors.appendChild(li);
    }
  } else {
    els.preflightErrorsWrap.classList.add("hidden");
  }

  els.preflightWarnings.innerHTML = "";
  if (warnings.length) {
    els.preflightWarningsWrap.classList.remove("hidden");
    for (const item of warnings) {
      const li = document.createElement("li");
      li.textContent = String(item);
      els.preflightWarnings.appendChild(li);
    }
  } else {
    els.preflightWarningsWrap.classList.add("hidden");
  }
  els.preflightForceBtn.classList.toggle("hidden", errors.length === 0);
}

async function startClient(force = false) {
  if (!state.activeClientId) return;
  await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/start?force=${force ? "true" : "false"}`, "POST");
  setHint(force ? "已强制启动请求。" : "已启动。", "info");
  hidePreflightPanel();
  await Promise.all([loadClients(), loadStatusAndLogs(), loadJumpLinks()]);
}

async function runPreflightAndStart() {
  if (!state.activeClientId) return;
  const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/preflight`, "POST");
  if (payload.ok) {
    await startClient(false);
    return;
  }
  showPreflightPanel(payload);
  setHint("预检未通过，请处理错误或选择强制启动。", "warn");
}

async function saveConfig() {
  if (!state.activeClientId) return;
  if (!els.clientName || !els.frpcPath || !els.runArgs || !els.autoStart || !els.configText) return;
  await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/config`, "PUT", {
    id: state.activeClientId,
    name: els.clientName.value.trim(),
    frpc_path: els.frpcPath.value.trim(),
    run_args: els.runArgs.value.trim(),
    auto_start: !!els.autoStart.checked,
    config_text: els.configText.value,
    env: {},
  });
  setHint("配置已保存。", "info");
  await Promise.all([loadClients(), loadJumpLinks()]);
}

async function autoDetectFrpcPath() {
  if (!state.activeClientId) return;
  const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/frpc-path/auto`);
  if (!els.frpcPath) return;
  els.frpcPath.value = payload.selected_path || "";
  if (payload.selected_exists) {
    setHint(`已选择自动路径: ${payload.selected_path}`, "info");
  } else {
    setHint("未检测到可用 frpc，建议手动设置路径。", "warn");
  }
}

async function runBatchPreflight() {
  const ids = ensureBatchSelection();
  if (!ids) return;
  const payload = await request("/api/clients/preflight-batch", "POST", { client_ids: ids });
  renderBatchResult(payload);
  setBatchHint("批量预检已完成。", payload.failed > 0 ? "warn" : "info");
}

async function runBatchStart() {
  const ids = ensureBatchSelection();
  if (!ids) return;
  if (!confirmBatchAction("批量启动", ids)) return;
  const force = !!els.batchForceStart?.checked;
  const skipFailed = !!els.batchSkipFailed?.checked;
  const payload = await request("/api/clients/start-batch", "POST", {
    client_ids: ids,
    force,
    skip_failed_preflight: skipFailed,
  });
  renderBatchResult(payload);
  setBatchHint("批量启动已执行。", payload.failed > 0 ? "warn" : "info");
  await Promise.all([loadClients(), loadStatusAndLogs(), loadJumpLinks()]);
}

async function runBatchStop() {
  const ids = ensureBatchSelection();
  if (!ids) return;
  if (!confirmBatchAction("批量停止", ids)) return;
  const payload = await request("/api/clients/stop-batch", "POST", { client_ids: ids });
  renderBatchResult(payload);
  setBatchHint("批量停止已执行。", payload.failed > 0 ? "warn" : "info");
  await Promise.all([loadClients(), loadStatusAndLogs(), loadJumpLinks()]);
}

function bindEvents() {
  els.logoutBtn?.addEventListener("click", () => logout());

  els.logsToggleBtn?.addEventListener("click", () => {
    toggleLogs(!state.logsExpanded);
  });
  els.logsClearBtn?.addEventListener("click", () => {
    clearLogs().catch((error) => setHint(`清空日志失败: ${error.message}`, "error"));
  });

  els.clientList?.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest("button[data-client-id]");
    if (!(button instanceof HTMLElement)) return;
    const clientId = button.dataset.clientId || "";
    if (!clientId || clientId === state.activeClientId) return;
    try {
      await request(`/api/clients/${encodeURIComponent(clientId)}/select`, "POST");
      state.activeClientId = clientId;
      hidePreflightPanel();
      await Promise.all([loadClients(), loadActiveConfig(), loadStatusAndLogs(), loadJumpLinks()]);
      connectStream();
    } catch (error) {
      setHint(`切换客户端失败: ${error.message}`, "error");
    }
  });

  els.batchClientList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.type !== "checkbox") return;
    const clientId = target.dataset.clientId || "";
    if (!clientId) return;
    if (target.checked) {
      state.batchSelectedIds.add(clientId);
    } else {
      state.batchSelectedIds.delete(clientId);
    }
    updateBatchSelectedCount();
  });

  els.batchSelectAllBtn?.addEventListener("click", () => {
    for (const client of state.clients) state.batchSelectedIds.add(client.id);
    renderBatchClientList();
  });
  els.batchInvertBtn?.addEventListener("click", () => {
    const next = new Set();
    for (const client of state.clients) {
      if (!state.batchSelectedIds.has(client.id)) next.add(client.id);
    }
    state.batchSelectedIds = next;
    renderBatchClientList();
  });
  els.batchClearBtn?.addEventListener("click", () => {
    state.batchSelectedIds.clear();
    renderBatchClientList();
  });
  els.batchPreflightBtn?.addEventListener("click", () => {
    runBatchPreflight().catch((error) => setBatchHint(`批量预检失败: ${error.message}`, "error"));
  });
  els.batchStartBtn?.addEventListener("click", () => {
    runBatchStart().catch((error) => setBatchHint(`批量启动失败: ${error.message}`, "error"));
  });
  els.batchStopBtn?.addEventListener("click", () => {
    runBatchStop().catch((error) => setBatchHint(`批量停止失败: ${error.message}`, "error"));
  });

  els.addClientBtn?.addEventListener("click", async () => {
    try {
      const payload = await request("/api/clients", "POST", { name: "" });
      state.activeClientId = payload.active_client_id || state.activeClientId;
      await Promise.all([loadClients(), loadActiveConfig(), loadStatusAndLogs(), loadJumpLinks()]);
      connectStream();
      setHint("已新增客户端。", "info");
    } catch (error) {
      setHint(`新增失败: ${error.message}`, "error");
    }
  });

  els.deleteClientBtn?.addEventListener("click", async () => {
    if (!state.activeClientId) return;
    const current = state.clients.find((item) => item.id === state.activeClientId);
    const ok = window.confirm(`确认删除客户端「${current?.name || state.activeClientId}」吗？`);
    if (!ok) return;
    try {
      const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}`, "DELETE");
      state.batchSelectedIds.delete(state.activeClientId);
      state.activeClientId = payload.active_client_id || payload.clients?.[0]?.id || "";
      await Promise.all([loadClients(), loadActiveConfig(), loadStatusAndLogs(), loadJumpLinks()]);
      connectStream();
      setHint("客户端已删除。", "info");
    } catch (error) {
      setHint(`删除失败: ${error.message}`, "error");
    }
  });

  els.autoPathBtn?.addEventListener("click", () => {
    autoDetectFrpcPath().catch((error) => setHint(`自动路径失败: ${error.message}`, "error"));
  });
  els.saveBtn?.addEventListener("click", () => {
    saveConfig().catch((error) => setHint(`保存失败: ${error.message}`, "error"));
  });
  els.startBtn?.addEventListener("click", () => {
    runPreflightAndStart().catch((error) => setHint(`启动失败: ${error.message}`, "error"));
  });
  els.stopBtn?.addEventListener("click", async () => {
    if (!state.activeClientId) return;
    try {
      await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/stop`, "POST");
      setHint("已停止。", "info");
      await Promise.all([loadClients(), loadStatusAndLogs(), loadJumpLinks()]);
    } catch (error) {
      setHint(`停止失败: ${error.message}`, "error");
    }
  });
  els.refreshBtn?.addEventListener("click", async () => {
    try {
      await Promise.all([loadClients(), loadActiveConfig(), loadStatusAndLogs(), loadJumpLinks()]);
      setHint("已刷新。", "info");
    } catch (error) {
      setHint(`刷新失败: ${error.message}`, "error");
    }
  });

  els.preflightCloseBtn?.addEventListener("click", hidePreflightPanel);
  els.preflightForceBtn?.addEventListener("click", () => {
    startClient(true).catch((error) => setHint(`强制启动失败: ${error.message}`, "error"));
  });
}

async function init() {
  markActiveNav("console");
  initThemePicker(els.themeMode);
  toggleLogs(false);
  hidePreflightPanel();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  bindEvents();
  await loadClients();
  if (!state.activeClientId) {
    renderJumpLinks([]);
    setHint("暂无客户端，请先新增。", "warn");
    return;
  }
  await Promise.all([loadActiveConfig(), loadStatusAndLogs(), loadJumpLinks()]);
  connectStream();
}

window.addEventListener("beforeunload", closeStream);

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
