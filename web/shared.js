const THEME_KEY = "frp_panel_theme_mode";

function applySvgAssetVersion() {
  const version = (document.body?.dataset.assetVersion || "").trim();
  if (!version) return;
  const encoded = encodeURIComponent(version);
  for (const useEl of document.querySelectorAll('use[href^="/web/icons.svg#"], use[xlink\\:href^="/web/icons.svg#"]')) {
    const current = useEl.getAttribute("href") || useEl.getAttribute("xlink:href") || "";
    if (!current || current.includes("?v=")) continue;
    const hashIndex = current.indexOf("#");
    const fragment = hashIndex >= 0 ? current.slice(hashIndex) : "";
    const versioned = `/web/icons.svg?v=${encoded}${fragment}`;
    useEl.setAttribute("href", versioned);
    useEl.setAttribute("xlink:href", versioned);
  }
}

function getPreferredThemeMode() {
  const value = window.localStorage.getItem(THEME_KEY);
  if (value === "light" || value === "dark" || value === "system") {
    return value;
  }
  return "system";
}

function resolveThemeMode(mode) {
  if (mode === "light") return "light";
  if (mode === "dark") return "dark";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  return media.matches ? "dark" : "light";
}

function resolveMotionMode() {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  return media.matches ? "reduced" : "full";
}

export function applyThemeMode(mode) {
  const effective = resolveThemeMode(mode);
  document.body.dataset.theme = effective;
}

export function applyMotionMode() {
  document.body.dataset.motion = resolveMotionMode();
}

export function initThemePicker(selectEl) {
  applySvgAssetVersion();
  applyMotionMode();
  const current = getPreferredThemeMode();
  applyThemeMode(current);
  if (!selectEl) return;

  selectEl.value = current;
  selectEl.addEventListener("change", () => {
    const value = selectEl.value;
    window.localStorage.setItem(THEME_KEY, value);
    applyThemeMode(value);
  });

  const colorMedia = window.matchMedia("(prefers-color-scheme: dark)");
  colorMedia.addEventListener("change", () => {
    if (getPreferredThemeMode() === "system") {
      applyThemeMode("system");
    }
  });
  const motionMedia = window.matchMedia("(prefers-reduced-motion: reduce)");
  motionMedia.addEventListener("change", () => {
    applyMotionMode();
  });
}

export function markActiveNav(page) {
  for (const item of document.querySelectorAll("[data-nav]")) {
    item.classList.toggle("active", item.dataset.nav === page);
  }
}

function formatErrorDetail(detail) {
  if (detail === null || detail === undefined) return "";
  if (typeof detail === "string") return detail;
  if (typeof detail === "number" || typeof detail === "boolean") return String(detail);
  if (Array.isArray(detail)) {
    return detail.map((item) => formatErrorDetail(item)).filter(Boolean).join(" | ");
  }
  if (typeof detail === "object") {
    const mapped = Object.entries(detail).map(([key, value]) => {
      const text = formatErrorDetail(value);
      return text ? `${key}: ${text}` : key;
    });
    return mapped.filter(Boolean).join("; ");
  }
  return String(detail);
}

export async function request(url, method = "GET", body = null) {
  const headers = {};
  let payload = null;
  if (body !== null && body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(url, { method, headers, body: payload });
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("未登录或会话已失效。");
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const parsed = await response.json();
      detail = formatErrorDetail(parsed.detail ?? parsed) || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function requireAuth() {
  const auth = await request("/api/auth/status");
  if (!auth.authenticated) {
    window.location.replace("/login");
    throw new Error("未登录或会话已失效。");
  }
  return auth;
}

export async function logout() {
  try {
    await request("/api/auth/logout", "POST");
  } finally {
    window.location.replace("/login");
  }
}

export function setUserBadge(el, username) {
  if (!el) return;
  el.textContent = username ? `用户：${username}` : "未登录";
}

export function fmtTs(value) {
  if (!value) return "-";
  const d = new Date(Number(value) * 1000);
  if (Number.isNaN(d.getTime())) return "-";
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
}

export function escapeHtml(input) {
  return String(input)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
