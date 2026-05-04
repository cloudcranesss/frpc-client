import { fmtTs, initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const EVENT_TYPE_LABELS = {
  manual_stop: "手动停止",
  normal_exit: "正常退出",
  abnormal_exit: "异常退出",
  start_failure: "启动失败",
  auto_restart: "自动重启",
  alert_sent: "告警发送成功",
  alert_failed: "告警发送失败",
  preflight_failed: "预检失败",
  preflight_forced: "强制跳过预检",
};

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  refreshBtn: document.querySelector("#events_refresh_btn"),
  clientSelect: document.querySelector("#events_client"),
  typeSelect: document.querySelector("#events_type"),
  sortSelect: document.querySelector("#events_sort"),
  eventsList: document.querySelector("#events_list"),
  hint: document.querySelector("#events_hint"),
};

const state = {
  clients: [],
  activeClientId: "",
  allEvents: [],
  eventSource: null,
  reconnectDelaySec: 1,
  reconnectTimer: null,
  streamToken: 0,
  flashedKeys: new Set(),
};

function setHint(text, level = "info") {
  els.hint.textContent = text;
  els.hint.classList.remove("info", "warn", "error");
  els.hint.classList.add(level);
}

function eventKey(item) {
  if (item.id) return `id:${item.id}`;
  return `${item.client_id || "-"}|${item.created_at || 0}|${item.event_type || "-"}|${item.message || "-"}`;
}

function normalizeEvent(item, isFresh = false) {
  return {
    ...item,
    __fresh: isFresh,
  };
}

function renderClientOptions() {
  els.clientSelect.innerHTML = "";
  for (const client of state.clients) {
    const option = document.createElement("option");
    option.value = client.id;
    option.textContent = client.name;
    els.clientSelect.appendChild(option);
  }
  if (state.activeClientId) {
    els.clientSelect.value = state.activeClientId;
  }
}

function renderEvents() {
  const typeFilter = els.typeSelect.value;
  const sortOrder = els.sortSelect.value;
  const items = state.allEvents
    .filter((item) => (!typeFilter ? true : item.event_type === typeFilter))
    .sort((a, b) => (sortOrder === "asc" ? a.created_at - b.created_at : b.created_at - a.created_at));

  els.eventsList.innerHTML = "";
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "event-row";
    empty.textContent = "当前条件下暂无事件。";
    els.eventsList.appendChild(empty);
    return;
  }

  for (const item of items) {
    const row = document.createElement("div");
    const key = eventKey(item);
    const shouldFlash = !!item.__fresh && !state.flashedKeys.has(key);
    row.className = `event-row ${shouldFlash ? "flash" : ""}`;
    const eventTypeRaw = item.event_type || "-";
    const eventTypeLabel = EVENT_TYPE_LABELS[eventTypeRaw] || eventTypeRaw;
    row.innerHTML = `
      <div class="event-row-top">
        <span class="event-type">${eventTypeLabel}</span>
        <span class="event-time">${fmtTs(item.created_at)}</span>
      </div>
      <div class="event-message">${item.message || "-"}</div>
      <div class="event-source">来源: ${item.client_id || state.activeClientId || "-"} | 类型键: ${eventTypeRaw}</div>
    `;
    if (shouldFlash) {
      state.flashedKeys.add(key);
      item.__fresh = false;
    }
    els.eventsList.appendChild(row);
  }
}

async function loadClients() {
  const payload = await request("/api/clients");
  state.clients = payload.clients ?? [];
  state.activeClientId = payload.active_client_id || state.clients[0]?.id || "";
  renderClientOptions();
}

async function loadEvents() {
  if (!state.activeClientId) {
    state.allEvents = [];
    renderEvents();
    setHint("暂无客户端。", "warn");
    return;
  }
  const payload = await request(`/api/clients/${encodeURIComponent(state.activeClientId)}/events?limit=500`);
  state.allEvents = (payload.items ?? []).map((item) => normalizeEvent(item, false));
  renderEvents();
  setHint(`已加载 ${state.allEvents.length} 条事件。`, "info");
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
  source.addEventListener("event", (raw) => {
    const item = normalizeEvent(JSON.parse(raw.data), true);
    state.allEvents.push(item);
    if (state.allEvents.length > 800) {
      state.allEvents = state.allEvents.slice(-800);
    }
    renderEvents();
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

function bindEvents() {
  els.logoutBtn.addEventListener("click", () => logout());
  els.refreshBtn.addEventListener("click", async () => {
    try {
      await loadEvents();
      setHint("事件已刷新。", "info");
    } catch (error) {
      setHint(`刷新失败: ${error.message}`, "error");
    }
  });
  els.clientSelect.addEventListener("change", async () => {
    state.activeClientId = els.clientSelect.value;
    try {
      await loadEvents();
      connectStream();
    } catch (error) {
      setHint(`切换客户端失败: ${error.message}`, "error");
    }
  });
  els.typeSelect.addEventListener("change", renderEvents);
  els.sortSelect.addEventListener("change", renderEvents);
}

async function init() {
  markActiveNav("events");
  initThemePicker(els.themeMode);
  bindEvents();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  await loadClients();
  await loadEvents();
  connectStream();
}

window.addEventListener("beforeunload", closeStream);

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
