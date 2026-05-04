import { escapeHtml, initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  clientList: document.querySelector("#client_list"),
  addClientBtn: document.querySelector("#add_client_btn"),
  deleteClientBtn: document.querySelector("#delete_client_btn"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  clientName: document.querySelector("#client_name"),
  frpcPath: document.querySelector("#frpc_path"),
  runArgs: document.querySelector("#run_args"),
  configText: document.querySelector("#config_text"),
  autoPathBtn: document.querySelector("#auto_path_btn"),
  saveBtn: document.querySelector("#save_btn"),
  startBtn: document.querySelector("#start_btn"),
  stopBtn: document.querySelector("#stop_btn"),
  refreshBtn: document.querySelector("#refresh_btn"),
  hint: document.querySelector("#hint"),
  preflightPanel: document.querySelector("#preflight_panel"),
  preflightSummary: document.querySelector("#preflight_summary"),
  preflightErrorsWrap: document.querySelector("#preflight_errors_wrap"),
  preflightErrors: document.querySelector("#preflight_errors"),
  preflightWarningsWrap: document.querySelector("#preflight_warnings_wrap"),
  preflightWarnings: document.querySelector("#preflight_warnings"),
  preflightForceBtn: document.querySelector("#preflight_force_btn"),
  preflightCloseBtn: document.querySelector("#preflight_close_btn"),
  logs: document.querySelector("#logs"),
  jumpCount: document.querySelector("#jump_count"),
  jumpLinks: document.querySelector("#jump_links"),
  statusPill: document.querySelector("#status-pill"),
  accountNewUsername: document.querySelector("#account_new_username"),
  accountCurrentPassword: document.querySelector("#account_current_password"),
  accountNewPassword: document.querySelector("#account_new_password"),
  accountConfirmPassword: document.querySelector("#account_confirm_password"),
  accountPolicy: document.querySelector("#account_policy"),
  accountStrength: document.querySelector("#account_strength"),
  accountSaveBtn: document.querySelector("#account_save_btn"),
};

const state = {
  activeClientId: null,
  clients: [],
  eventSource: null,
  reconnectDelaySec: 1,
  reconnectTimer: null,
  streamToken: 0,
};

const api = {
  async authProfile() {
    return request("/api/auth/profile");
  },
  async updateAuthProfile(payload) {
    return request("/api/auth/profile", "PUT", payload);
  },
  async listClients() {
    return request("/api/clients");
  },
  async createClient(name) {
    return request("/api/clients", "POST", { name });
  },
  async selectClient(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/select`, "POST");
  },
  async deleteClient(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}`, "DELETE");
  },
  async getClientConfig(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/config`);
  },
  async saveClientConfig(clientId, payload) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/config`, "PUT", payload);
  },
  async autoFrpcPath(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/frpc-path/auto`);
  },
  async preflight(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/preflight`, "POST");
  },
  async start(clientId, force = false) {
    const query = force ? "?force=true" : "";
    return request(`/api/clients/${encodeURIComponent(clientId)}/start${query}`, "POST");
  },
  async stop(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/stop`, "POST");
  },
  async status(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/status`);
  },
  async logs(clientId, limit = 120) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/logs?limit=${limit}`);
  },
  async jumpLinks(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/jump-links`);
  },
};

function setHint(text, level = "info") {
  els.hint.textContent = text;
  els.hint.classList.remove("info", "warn", "error");
  els.hint.classList.add(level);
}

function evaluatePasswordStrength(value) {
  const text = String(value || "");
  if (!text) return { ok: false, message: "留空表示不修改密码。" };
  const longEnough = text.length >= 8;
  const hasAlpha = /[A-Za-z]/.test(text);
  const hasDigit = /\d/.test(text);
  if (longEnough && hasAlpha && hasDigit) {
    return { ok: true, message: "密码强度符合要求。" };
  }
  return { ok: false, message: "密码至少8位，且必须同时包含字母和数字。" };
}

function updatePasswordHint() {
  const result = evaluatePasswordStrength(els.accountNewPassword.value);
  els.accountStrength.textContent = result.message;
  els.accountStrength.classList.remove("info", "warn", "error");
  els.accountStrength.classList.add(result.ok ? "info" : "warn");
}

function setListItems(listEl, items) {
  listEl.innerHTML = "";
  for (const text of items) {
    const li = document.createElement("li");
    li.textContent = text;
    listEl.appendChild(li);
  }
}

function hidePreflightPanel() {
  els.preflightPanel.classList.add("hidden");
  els.preflightSummary.textContent = "";
  els.preflightErrorsWrap.classList.add("hidden");
  els.preflightWarningsWrap.classList.add("hidden");
  els.preflightForceBtn.classList.add("hidden");
  els.preflightErrors.innerHTML = "";
  els.preflightWarnings.innerHTML = "";
}

function showPreflightPanel(preflight) {
  const errors = Array.isArray(preflight.errors) ? preflight.errors : [];
  const warnings = Array.isArray(preflight.warnings) ? preflight.warnings : [];
  const ok = !!preflight.ok;
  els.preflightPanel.classList.remove("hidden");
  els.preflightSummary.textContent = ok
    ? `预检通过，警告 ${warnings.length} 条。`
    : `预检未通过，错误 ${errors.length} 条，警告 ${warnings.length} 条。`;

  if (errors.length > 0) {
    els.preflightErrorsWrap.classList.remove("hidden");
    setListItems(els.preflightErrors, errors);
    els.preflightForceBtn.classList.remove("hidden");
  } else {
    els.preflightErrorsWrap.classList.add("hidden");
    els.preflightForceBtn.classList.add("hidden");
    els.preflightErrors.innerHTML = "";
  }

  if (warnings.length > 0) {
    els.preflightWarningsWrap.classList.remove("hidden");
    setListItems(els.preflightWarnings, warnings);
  } else {
    els.preflightWarningsWrap.classList.add("hidden");
    els.preflightWarnings.innerHTML = "";
  }
}

function activeClientExists() {
  return !!state.activeClientId && state.clients.some((item) => item.id === state.activeClientId);
}

function updateStatusPill(status) {
  if (status.running) {
    const restartCount = status.restart_count ?? 0;
    els.statusPill.textContent = `运行中 PID ${status.pid ?? "-"} | 重启 ${restartCount}`;
    els.statusPill.classList.remove("offline");
    els.statusPill.classList.add("online");
    return;
  }
  const suffix = status.last_error ? ` | 错误: ${status.last_error}` : "";
  els.statusPill.textContent = `未运行${suffix}`;
  els.statusPill.classList.remove("online");
  els.statusPill.classList.add("offline");
}

function applyClientsPayload(payload) {
  state.clients = payload.clients ?? [];
  if (payload.active_client_id && state.clients.some((item) => item.id === payload.active_client_id)) {
    state.activeClientId = payload.active_client_id;
  } else if (!activeClientExists()) {
    state.activeClientId = state.clients[0]?.id ?? null;
  }
  renderClientList();
}

function renderClientList() {
  els.clientList.innerHTML = "";
  for (const client of state.clients) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `client-item ${client.id === state.activeClientId ? "active" : ""}`;
    item.dataset.clientId = client.id;
    item.innerHTML = `
      <div class="client-item-title">
        <span>${escapeHtml(client.name)}</span>
        <span class="dot ${client.running ? "online" : "offline"}"></span>
      </div>
      <div class="client-item-meta">PID: ${client.pid ?? "-"} | 重启: ${client.restart_count ?? 0}</div>
    `;
    item.addEventListener("click", () => onSelectClient(client.id));
    els.clientList.appendChild(item);
  }
}

function fillConfig(cfg) {
  els.clientName.value = cfg.name ?? "";
  els.frpcPath.value = cfg.frpc_path ?? "";
  els.runArgs.value = cfg.run_args ?? "";
  els.configText.value = cfg.config_text ?? "";
}

function renderJumpLinks(items) {
  els.jumpLinks.innerHTML = "";
  const count = Array.isArray(items) ? items.length : 0;
  els.jumpCount.textContent = String(count);
  if (!items || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "jump-empty";
    empty.textContent = "暂无可跳转服务。";
    els.jumpLinks.appendChild(empty);
    return;
  }
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "jump-link-item";
    card.innerHTML = `
      <div class="jump-link-title">${escapeHtml(item.proxy_name)} (${escapeHtml(item.proxy_type)})</div>
      <a class="jump-link-url" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a>
    `;
    els.jumpLinks.appendChild(card);
  }
}

function buildPayload() {
  return {
    id: state.activeClientId,
    name: els.clientName.value.trim(),
    frpc_path: els.frpcPath.value.trim(),
    run_args: els.runArgs.value.trim(),
    config_text: els.configText.value,
    env: {},
  };
}

function appendLogLine(line) {
  const lines = (els.logs.textContent ? `${els.logs.textContent}\n${line}` : line).split("\n");
  els.logs.textContent = lines.slice(-600).join("\n");
  els.logs.scrollTop = els.logs.scrollHeight;
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
  if (!activeClientExists()) return;
  const token = ++state.streamToken;
  const source = new EventSource(`/api/clients/${encodeURIComponent(state.activeClientId)}/stream`);
  state.eventSource = source;

  source.addEventListener("status", (event) => {
    updateStatusPill(JSON.parse(event.data));
    state.reconnectDelaySec = 1;
  });
  source.addEventListener("log", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.line) appendLogLine(payload.line);
  });
  source.onerror = () => {
    source.close();
    if (token !== state.streamToken) return;
    const delay = state.reconnectDelaySec;
    state.reconnectDelaySec = Math.min(30, state.reconnectDelaySec * 2);
    state.reconnectTimer = setTimeout(() => {
      if (token === state.streamToken) connectStream();
    }, delay * 1000);
  };
}

async function refreshStatusLogsLinks() {
  if (!activeClientExists()) {
    els.logs.textContent = "";
    renderJumpLinks([]);
    updateStatusPill({ running: false, last_error: null });
    return;
  }
  const [status, logs, links] = await Promise.all([
    api.status(state.activeClientId),
    api.logs(state.activeClientId),
    api.jumpLinks(state.activeClientId),
  ]);
  updateStatusPill(status);
  els.logs.textContent = logs.items.join("\n");
  renderJumpLinks(links.items);
}

async function refreshDashboard() {
  const previousActive = state.activeClientId;
  await loadClients();
  if (!activeClientExists()) {
    closeStream();
    hidePreflightPanel();
    setHint("暂无客户端，请先新增。", "warn");
    return;
  }
  if (previousActive !== state.activeClientId) {
    await loadSelectedClientConfig();
    connectStream();
  }
  await refreshStatusLogsLinks();
}

async function loadClients() {
  const payload = await api.listClients();
  applyClientsPayload(payload);
}

async function loadSelectedClientConfig() {
  if (!activeClientExists()) return;
  const cfg = await api.getClientConfig(state.activeClientId);
  fillConfig(cfg);
}

async function loadAuthProfile() {
  const profile = await api.authProfile();
  els.accountPolicy.textContent = profile.password_policy || "密码至少8位，且必须同时包含字母和数字。";
  els.accountNewUsername.value = "";
  els.accountCurrentPassword.value = "";
  els.accountNewPassword.value = "";
  els.accountConfirmPassword.value = "";
  updatePasswordHint();
}

async function onSelectClient(clientId) {
  try {
    await api.selectClient(clientId);
    await loadClients();
    await loadSelectedClientConfig();
    await refreshStatusLogsLinks();
    connectStream();
    hidePreflightPanel();
    setHint("客户端已切换。");
  } catch (error) {
    setHint(`切换失败: ${error.message}`, "error");
  }
}

async function onCreateClient() {
  const suggested = `客户端 ${state.clients.length + 1}`;
  const name = window.prompt("请输入新客户端名称：", suggested);
  if (name === null) return;
  try {
    const payload = await api.createClient(name.trim() || suggested);
    applyClientsPayload(payload);
    await loadSelectedClientConfig();
    await refreshStatusLogsLinks();
    connectStream();
    hidePreflightPanel();
    setHint("客户端已新增。");
  } catch (error) {
    setHint(`新增失败: ${error.message}`, "error");
  }
}

async function onDeleteClient() {
  if (!activeClientExists()) return;
  const active = state.clients.find((item) => item.id === state.activeClientId);
  const confirmed = window.confirm(`确认删除客户端「${active?.name ?? state.activeClientId}」吗？`);
  if (!confirmed) return;
  try {
    const payload = await api.deleteClient(state.activeClientId);
    applyClientsPayload(payload);
    await loadSelectedClientConfig();
    await refreshStatusLogsLinks();
    connectStream();
    hidePreflightPanel();
    setHint("客户端已删除。");
  } catch (error) {
    setHint(`删除失败: ${error.message}`, "error");
  }
}

async function tryAutoPickPath(saveAfterPick = false, silent = false) {
  if (!activeClientExists()) return false;
  const result = await api.autoFrpcPath(state.activeClientId);
  if (!result.selected_exists) {
    if (!silent) setHint("未检测到可用 frpc。", "warn");
    return false;
  }
  els.frpcPath.value = result.selected_path;
  if (saveAfterPick) {
    await api.saveClientConfig(state.activeClientId, buildPayload());
    await loadClients();
  }
  if (!silent) setHint(`已自动选择: ${result.selected_path}`);
  return true;
}

async function handleAccountSave() {
  const newUsername = els.accountNewUsername.value.trim();
  const currentPassword = els.accountCurrentPassword.value;
  const newPassword = els.accountNewPassword.value;
  const confirmPassword = els.accountConfirmPassword.value;

  if (!currentPassword) {
    setHint("请填写当前密码以确认身份。", "warn");
    return;
  }
  if (!newUsername && !newPassword) {
    setHint("请至少填写新用户名或新密码。", "warn");
    return;
  }
  if (newPassword && newPassword !== confirmPassword) {
    setHint("两次输入的新密码不一致。", "warn");
    return;
  }
  if (newPassword) {
    const strength = evaluatePasswordStrength(newPassword);
    if (!strength.ok) {
      setHint(strength.message, "warn");
      return;
    }
  }
  try {
    await api.updateAuthProfile({
      new_username: newUsername || null,
      current_password: currentPassword,
      new_password: newPassword || null,
    });
    setHint("账号设置已更新，请重新登录。", "info");
    setTimeout(() => {
      window.location.replace("/login");
    }, 600);
  } catch (error) {
    setHint(`账号设置更新失败: ${error.message}`, "error");
  }
}

function bindEvents() {
  els.addClientBtn.addEventListener("click", onCreateClient);
  els.deleteClientBtn.addEventListener("click", onDeleteClient);
  els.logoutBtn.addEventListener("click", () => logout());
  els.autoPathBtn.addEventListener("click", async () => {
    try {
      await tryAutoPickPath(true, false);
    } catch (error) {
      setHint(`自动选择失败: ${error.message}`, "error");
    }
  });
  els.saveBtn.addEventListener("click", async () => {
    if (!activeClientExists()) return;
    try {
      await api.saveClientConfig(state.activeClientId, buildPayload());
      await loadClients();
      hidePreflightPanel();
      setHint("配置已保存。");
    } catch (error) {
      setHint(`保存失败: ${error.message}`, "error");
    }
  });
  els.startBtn.addEventListener("click", async () => {
    if (!activeClientExists()) return;
    try {
      await api.saveClientConfig(state.activeClientId, buildPayload());
      const preflight = await api.preflight(state.activeClientId);
      showPreflightPanel(preflight);
      if (!preflight.ok) {
        setHint("预检未通过，请修复错误或使用强制启动。", "warn");
        return;
      }
      const status = await api.start(state.activeClientId, false);
      updateStatusPill(status);
      await refreshDashboard();
      if ((preflight.warnings || []).length > 0) {
        setHint(`frpc 已启动（存在 ${(preflight.warnings || []).length} 条预检警告）。`, "warn");
      } else {
        setHint("frpc 已启动。");
      }
    } catch (error) {
      setHint(`启动失败: ${error.message}`, "error");
    }
  });
  els.preflightForceBtn.addEventListener("click", async () => {
    if (!activeClientExists()) return;
    const confirmed = window.confirm("预检存在错误，确认忽略并强制启动吗？");
    if (!confirmed) return;
    try {
      const status = await api.start(state.activeClientId, true);
      updateStatusPill(status);
      await refreshDashboard();
      setHint("已执行强制启动。", "warn");
    } catch (error) {
      setHint(`强制启动失败: ${error.message}`, "error");
    }
  });
  els.preflightCloseBtn.addEventListener("click", () => {
    hidePreflightPanel();
  });
  els.stopBtn.addEventListener("click", async () => {
    if (!activeClientExists()) return;
    try {
      const status = await api.stop(state.activeClientId);
      updateStatusPill(status);
      await refreshDashboard();
      setHint("frpc 已停止。");
    } catch (error) {
      setHint(`停止失败: ${error.message}`, "error");
    }
  });
  els.refreshBtn.addEventListener("click", async () => {
    try {
      await refreshDashboard();
      setHint("状态已刷新。");
    } catch (error) {
      setHint(`刷新失败: ${error.message}`, "error");
    }
  });
  els.accountSaveBtn.addEventListener("click", handleAccountSave);
  els.accountNewPassword.addEventListener("input", updatePasswordHint);
}

async function init() {
  markActiveNav("dashboard");
  initThemePicker(els.themeMode);
  bindEvents();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  await Promise.all([loadClients(), loadAuthProfile()]);
  if (!activeClientExists()) {
    setHint("暂无客户端，请先新增。", "warn");
    return;
  }
  await loadSelectedClientConfig();
  if (!els.frpcPath.value.trim() || els.frpcPath.value.trim() === "bin/frpc.exe") {
    await tryAutoPickPath(true, true);
  }
  await refreshStatusLogsLinks();
  connectStream();
  hidePreflightPanel();
}

window.addEventListener("beforeunload", closeStream);

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
