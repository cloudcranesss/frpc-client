const els = {
  clientList: document.querySelector("#client_list"),
  addClientBtn: document.querySelector("#add_client_btn"),
  deleteClientBtn: document.querySelector("#delete_client_btn"),
  userBadge: document.querySelector("#user_badge"),
  changePasswordBtn: document.querySelector("#change_password_btn"),
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
  logs: document.querySelector("#logs"),
  statusPill: document.querySelector("#status-pill"),
};

const state = {
  activeClientId: null,
  clients: [],
};

const api = {
  async authStatus() {
    return request("/api/auth/status");
  },
  async logout() {
    return request("/api/auth/logout", "POST");
  },
  async changePassword(oldPassword, newPassword) {
    return request("/api/auth/change-password", "POST", {
      old_password: oldPassword,
      new_password: newPassword,
    });
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
  async start(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/start`, "POST");
  },
  async stop(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/stop`, "POST");
  },
  async status(clientId) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/status`);
  },
  async logs(clientId, limit = 300) {
    return request(`/api/clients/${encodeURIComponent(clientId)}/logs?limit=${limit}`);
  },
};

function activeClientExists() {
  return !!state.activeClientId && state.clients.some((item) => item.id === state.activeClientId);
}

function setHint(text, isError = false) {
  els.hint.textContent = text;
  els.hint.style.color = isError ? "#9f1d35" : "#607086";
}

function setUsername(username) {
  els.userBadge.textContent = username ? `用户: ${username}` : "未登录";
}

function updateStatusPill(status) {
  if (status.running) {
    els.statusPill.textContent = `运行中 (PID ${status.pid ?? "-"})`;
    els.statusPill.classList.remove("offline");
    els.statusPill.classList.add("online");
    return;
  }
  els.statusPill.textContent = "未运行";
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
    const item = document.createElement("div");
    item.className = `client-item ${client.id === state.activeClientId ? "active" : ""}`;
    item.dataset.clientId = client.id;

    const title = document.createElement("div");
    title.className = "client-item-title";
    title.innerHTML = `
      <span>${escapeHtml(client.name)}</span>
      <span class="dot ${client.running ? "online" : "offline"}"></span>
    `;

    const meta = document.createElement("div");
    meta.className = "client-item-meta";
    const exitCodeText = client.last_exit_code === null ? "-" : String(client.last_exit_code);
    meta.textContent = `PID: ${client.pid ?? "-"}  |  Exit: ${exitCodeText}`;

    item.appendChild(title);
    item.appendChild(meta);
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

async function loadClients() {
  const payload = await api.listClients();
  applyClientsPayload(payload);
}

async function loadSelectedClientConfig() {
  if (!activeClientExists()) {
    return;
  }
  const cfg = await api.getClientConfig(state.activeClientId);
  fillConfig(cfg);
}

async function refreshStatusAndLogs() {
  if (!activeClientExists()) {
    els.logs.textContent = "";
    updateStatusPill({ running: false, pid: null });
    return;
  }
  const [status, logs] = await Promise.all([
    api.status(state.activeClientId),
    api.logs(state.activeClientId),
  ]);
  updateStatusPill(status);
  els.logs.textContent = logs.items.join("\n");
  els.logs.scrollTop = els.logs.scrollHeight;
}

async function refreshDashboard() {
  const previousActive = state.activeClientId;
  await loadClients();
  if (!activeClientExists()) {
    setHint("暂无客户端，请先新增一个。", true);
    return;
  }
  if (previousActive !== state.activeClientId) {
    await loadSelectedClientConfig();
  }
  await refreshStatusAndLogs();
}

async function onSelectClient(clientId) {
  try {
    await api.selectClient(clientId);
    await loadClients();
    await loadSelectedClientConfig();
    await refreshStatusAndLogs();
    setHint("已切换客户端。");
  } catch (error) {
    setHint(`切换失败: ${error.message}`, true);
  }
}

async function onCreateClient() {
  const suggested = `客户端 ${state.clients.length + 1}`;
  const name = window.prompt("请输入新客户端名称：", suggested);
  if (name === null) {
    return;
  }
  try {
    const payload = await api.createClient(name.trim() || suggested);
    applyClientsPayload(payload);
    await loadSelectedClientConfig();
    await refreshStatusAndLogs();
    setHint("已新增客户端。");
  } catch (error) {
    setHint(`新增失败: ${error.message}`, true);
  }
}

async function onDeleteClient() {
  if (!activeClientExists()) {
    return;
  }
  const active = state.clients.find((item) => item.id === state.activeClientId);
  const name = active?.name ?? state.activeClientId;
  const confirmed = window.confirm(`确认删除客户端「${name}」吗？`);
  if (!confirmed) {
    return;
  }
  try {
    const payload = await api.deleteClient(state.activeClientId);
    applyClientsPayload(payload);
    await loadSelectedClientConfig();
    await refreshStatusAndLogs();
    setHint("客户端已删除。");
  } catch (error) {
    setHint(`删除失败: ${error.message}`, true);
  }
}

async function tryAutoPickPath(saveAfterPick = false, silent = false) {
  if (!activeClientExists()) {
    return false;
  }
  const result = await api.autoFrpcPath(state.activeClientId);
  if (!result.selected_exists) {
    if (!silent) {
      setHint("未检测到可用 frpc，请先执行 python scripts/fetch_frpc.py", true);
    }
    return false;
  }
  els.frpcPath.value = result.selected_path;
  if (saveAfterPick) {
    await api.saveClientConfig(state.activeClientId, buildPayload());
    await loadClients();
  }
  if (!silent) {
    setHint(`已自动选择 frpc 路径: ${result.selected_path}`);
  }
  return true;
}

els.addClientBtn.addEventListener("click", onCreateClient);
els.deleteClientBtn.addEventListener("click", onDeleteClient);

els.logoutBtn.addEventListener("click", async () => {
  try {
    await api.logout();
  } finally {
    window.location.replace("/login");
  }
});

els.changePasswordBtn.addEventListener("click", async () => {
  const oldPassword = window.prompt("请输入旧密码：");
  if (oldPassword === null) {
    return;
  }
  const newPassword = window.prompt("请输入新密码（至少8位）：");
  if (newPassword === null) {
    return;
  }
  try {
    await api.changePassword(oldPassword, newPassword);
    setHint("密码已修改，请重新登录。");
    await api.logout();
    window.location.replace("/login");
  } catch (error) {
    setHint(`修改密码失败: ${error.message}`, true);
  }
});

els.autoPathBtn.addEventListener("click", async () => {
  try {
    await tryAutoPickPath(true, false);
  } catch (error) {
    setHint(`自动选择失败: ${error.message}`, true);
  }
});

els.saveBtn.addEventListener("click", async () => {
  if (!activeClientExists()) {
    return;
  }
  try {
    await api.saveClientConfig(state.activeClientId, buildPayload());
    await loadClients();
    setHint("配置已保存。");
  } catch (error) {
    setHint(`保存失败: ${error.message}`, true);
  }
});

els.startBtn.addEventListener("click", async () => {
  if (!activeClientExists()) {
    return;
  }
  try {
    await api.saveClientConfig(state.activeClientId, buildPayload());
    const status = await api.start(state.activeClientId);
    updateStatusPill(status);
    await refreshDashboard();
    setHint("frpc 已启动。");
  } catch (error) {
    setHint(`启动失败: ${error.message}`, true);
  }
});

els.stopBtn.addEventListener("click", async () => {
  if (!activeClientExists()) {
    return;
  }
  try {
    const status = await api.stop(state.activeClientId);
    updateStatusPill(status);
    await refreshDashboard();
    setHint("frpc 已停止。");
  } catch (error) {
    setHint(`停止失败: ${error.message}`, true);
  }
});

els.refreshBtn.addEventListener("click", async () => {
  try {
    await refreshDashboard();
    setHint("状态已刷新。");
  } catch (error) {
    setHint(`刷新失败: ${error.message}`, true);
  }
});

async function init() {
  try {
    const auth = await api.authStatus();
    if (!auth.authenticated) {
      window.location.replace("/login");
      return;
    }
    setUsername(auth.username);

    await loadClients();
    if (!activeClientExists()) {
      setHint("暂无客户端，请先新增一个。", true);
      return;
    }
    await loadSelectedClientConfig();
    if (!els.frpcPath.value.trim() || els.frpcPath.value.trim() === "bin/frpc.exe") {
      await tryAutoPickPath(true, true);
    }
    await refreshStatusAndLogs();
    setHint("已加载多客户端配置。");
  } catch (error) {
    setHint(`初始化失败: ${error.message}`, true);
  }
}

setInterval(async () => {
  try {
    await refreshDashboard();
  } catch {
    // Background refresh should stay silent to avoid interrupting manual actions.
  }
}, 4000);

init();

function escapeHtml(input) {
  return String(input)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function request(url, method = "GET", body = null) {
  const response = await fetch(url, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : null,
  });
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("Unauthorized");
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}
