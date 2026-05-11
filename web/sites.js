import { escapeHtml, fmtTs, initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const UNKNOWN_REGION = "内网/未知";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  sitesRefreshBtn: document.querySelector("#sites_refresh_btn"),
  sitesSummary: document.querySelector("#sites_summary"),
  sitesList: document.querySelector("#sites_list"),
  sitesHint: document.querySelector("#sites_hint"),
  sitesRegionFilter: document.querySelector("#sites_region_filter"),
  sitesClientFilter: document.querySelector("#sites_client_filter"),
  sitesProxyFilter: document.querySelector("#sites_proxy_filter"),
};

const state = {
  items: [],
  eventSource: null,
  reconnectDelaySec: 1,
  reconnectTimer: null,
  streamToken: 0,
  regionFilter: "",
  clientKeyword: "",
  proxyKeyword: "",
};

function setHint(text, level = "info") {
  if (!els.sitesHint) return;
  els.sitesHint.textContent = text;
  els.sitesHint.classList.remove("info", "warn", "error");
  els.sitesHint.classList.add(level);
}

function regionLabel(item) {
  return String(item.region_label || "").trim() || UNKNOWN_REGION;
}

function formatRegion(item) {
  const values = [item.region_country, item.region_province, item.region_city]
    .map((v) => String(v || "").trim())
    .filter(Boolean);
  return values.length ? values.join("/") : UNKNOWN_REGION;
}

function normalizeEndpoint(item) {
  const host = String(item.server_addr || "").trim();
  const port = Number.parseInt(String(item.remote_port ?? ""), 10);
  const hasEndpoint = !!host && Number.isInteger(port) && port > 0;
  return {
    host,
    port: hasEndpoint ? port : null,
    endpoint: hasEndpoint ? `${host}:${port}` : "-",
    openUrl: hasEndpoint ? `http://${host}:${port}` : (item.url || "#"),
  };
}

function groupSites(rows) {
  const grouped = new Map();
  for (const item of rows) {
    const label = regionLabel(item);
    if (!grouped.has(label)) grouped.set(label, []);
    grouped.get(label).push(item);
  }

  const labels = [...grouped.keys()].sort((a, b) => {
    if (a === UNKNOWN_REGION && b !== UNKNOWN_REGION) return 1;
    if (a !== UNKNOWN_REGION && b === UNKNOWN_REGION) return -1;
    return a.localeCompare(b, "zh-CN");
  });

  return labels.map((label) => {
    const items = grouped.get(label) || [];
    items.sort((a, b) => {
      const left = `${a.client_name || ""}|${a.proxy_name || ""}|${a.server_addr || ""}|${a.remote_port || 0}`;
      const right = `${b.client_name || ""}|${b.proxy_name || ""}|${b.server_addr || ""}|${b.remote_port || 0}`;
      return left.localeCompare(right, "zh-CN");
    });
    return { label, items };
  });
}

function updateRegionFilterOptions() {
  if (!els.sitesRegionFilter) return;
  const labels = [...new Set(state.items.map((item) => regionLabel(item)))].sort((a, b) => {
    if (a === UNKNOWN_REGION && b !== UNKNOWN_REGION) return 1;
    if (a !== UNKNOWN_REGION && b === UNKNOWN_REGION) return -1;
    return a.localeCompare(b, "zh-CN");
  });
  const current = state.regionFilter;
  els.sitesRegionFilter.innerHTML = '<option value="">全部地区</option>';
  for (const label of labels) {
    const option = document.createElement("option");
    option.value = label;
    option.textContent = label;
    option.selected = label === current;
    els.sitesRegionFilter.appendChild(option);
  }
}

function filteredSites() {
  const region = state.regionFilter.trim();
  const clientKw = state.clientKeyword.trim().toLowerCase();
  const proxyKw = state.proxyKeyword.trim().toLowerCase();
  return state.items.filter((item) => {
    if (region && regionLabel(item) !== region) return false;
    const clientName = String(item.client_name || item.client_id || "").toLowerCase();
    const proxyName = String(item.proxy_name || "").toLowerCase();
    if (clientKw && !clientName.includes(clientKw)) return false;
    if (proxyKw && !proxyName.includes(proxyKw)) return false;
    return true;
  });
}

function renderSites() {
  const visibleRows = filteredSites();
  if (els.sitesSummary) {
    els.sitesSummary.textContent = `当前可访问站点：${visibleRows.length} / ${state.items.length} 个`;
  }
  if (!els.sitesList) return;
  els.sitesList.innerHTML = "";
  if (visibleRows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "jump-empty";
    empty.textContent = "暂无符合筛选条件的站点。";
    els.sitesList.appendChild(empty);
    return;
  }

  const groups = groupSites(visibleRows);
  for (const group of groups) {
    const section = document.createElement("section");
    section.className = "sites-region-group";

    const title = document.createElement("h3");
    title.className = "sites-region-title";
    title.textContent = `${group.label} (${group.items.length})`;
    section.appendChild(title);

    const groupList = document.createElement("div");
    groupList.className = "sites-region-list";

    for (const item of group.items) {
      const { endpoint, openUrl } = normalizeEndpoint(item);
      const card = document.createElement("article");
      card.className = "site-card";
      card.innerHTML = `
        <div class="site-card-top">
          <strong>${escapeHtml(item.proxy_name || "-")}</strong>
          <span class="pill online">可访问</span>
        </div>
        <div class="site-card-meta">
          <span>客户端：${escapeHtml(item.client_name || item.client_id || "-")}</span>
          <span>类型：${escapeHtml((item.proxy_type || "").toUpperCase() || "-")}</span>
        </div>
        <div class="site-card-meta">
          <span>地区：${escapeHtml(formatRegion(item))}</span>
        </div>
        <a class="jump-link-url" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(endpoint)}</a>
        <div class="site-card-meta">
          <span>探活状态：${item.probe_ok ? "连通" : "失败"}</span>
          <span>延迟：${item.latency_ms ?? "-"} ms</span>
          <span>检查时间：${fmtTs(item.last_checked_at)}</span>
        </div>
      `;
      groupList.appendChild(card);
    }

    section.appendChild(groupList);
    els.sitesList.appendChild(section);
  }
}

async function loadSnapshot() {
  const payload = await request("/api/sites/successful");
  state.items = payload.items || [];
  updateRegionFilterOptions();
  renderSites();
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
  const token = ++state.streamToken;
  const source = new EventSource("/api/sites/stream");
  state.eventSource = source;

  source.onopen = () => {
    state.reconnectDelaySec = 1;
  };

  source.addEventListener("snapshot", (raw) => {
    const payload = JSON.parse(raw.data || "{}");
    state.items = payload.items || [];
    updateRegionFilterOptions();
    renderSites();
  });

  source.addEventListener("update", (raw) => {
    const payload = JSON.parse(raw.data || "{}");
    state.items = payload.items || [];
    updateRegionFilterOptions();
    renderSites();
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

function bindEvents() {
  els.logoutBtn?.addEventListener("click", () => logout());
  els.sitesRefreshBtn?.addEventListener("click", async () => {
    try {
      await loadSnapshot();
      setHint("站点列表已刷新。", "info");
    } catch (error) {
      setHint(`刷新失败: ${error.message}`, "error");
    }
  });
  els.sitesRegionFilter?.addEventListener("change", () => {
    state.regionFilter = String(els.sitesRegionFilter?.value || "");
    renderSites();
  });
  els.sitesClientFilter?.addEventListener("input", () => {
    state.clientKeyword = String(els.sitesClientFilter?.value || "");
    renderSites();
  });
  els.sitesProxyFilter?.addEventListener("input", () => {
    state.proxyKeyword = String(els.sitesProxyFilter?.value || "");
    renderSites();
  });
}

async function init() {
  markActiveNav("home");
  initThemePicker(els.themeMode);
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  bindEvents();
  await loadSnapshot();
  connectStream();
}

window.addEventListener("beforeunload", closeStream);

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
