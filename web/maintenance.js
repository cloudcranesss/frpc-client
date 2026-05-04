import { fmtTs, initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  exportBtn: document.querySelector("#export_btn"),
  diagnosticsBtn: document.querySelector("#diagnostics_btn"),
  importFile: document.querySelector("#import_file"),
  previewBtn: document.querySelector("#preview_btn"),
  applyOverwriteBtn: document.querySelector("#apply_overwrite_btn"),
  applyMergeBtn: document.querySelector("#apply_merge_btn"),
  previewOutput: document.querySelector("#preview_output"),
  readOnlyToggle: document.querySelector("#read_only_toggle"),
  refreshSnapshotsBtn: document.querySelector("#refresh_snapshots_btn"),
  snapshotsList: document.querySelector("#snapshots_list"),
  clearBrowserCacheBtn: document.querySelector("#clear_browser_cache_btn"),
  templateName: document.querySelector("#template_name"),
  templateTags: document.querySelector("#template_tags"),
  templateContent: document.querySelector("#template_content"),
  templateCreateBtn: document.querySelector("#template_create_btn"),
  templateReloadBtn: document.querySelector("#template_reload_btn"),
  templatesList: document.querySelector("#templates_list"),
  auditReloadBtn: document.querySelector("#audit_reload_btn"),
  auditList: document.querySelector("#audit_list"),
  hint: document.querySelector("#maintenance_hint"),
};

function setHint(text, level = "info") {
  els.hint.textContent = text;
  els.hint.classList.remove("info", "warn", "error");
  els.hint.classList.add(level);
}

function renderSnapshots(items) {
  els.snapshotsList.innerHTML = "";
  if (!items.length) {
    els.snapshotsList.innerHTML = '<div class="event-row">暂无快照。</div>';
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div class="event-row-top">
        <span class="event-type">#${item.id} ${item.reason}</span>
        <span class="event-time">${fmtTs(item.created_at)}</span>
      </div>
      <div class="channel-actions">
        <button class="btn danger small" data-action="rollback" data-id="${item.id}">回滚到此</button>
      </div>
    `;
    els.snapshotsList.appendChild(row);
  }
}

function renderTemplates(items) {
  els.templatesList.innerHTML = "";
  if (!items.length) {
    els.templatesList.innerHTML = '<div class="event-row">暂无模板。</div>';
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div class="event-row-top">
        <span class="event-type">${item.name} (v${item.version})</span>
        <span class="event-time">${fmtTs(item.updated_at)}</span>
      </div>
      <div class="event-message">标签: ${(item.tags || []).join(", ") || "-"}</div>
      <div class="event-source">变量: ${(item.variables || []).join(", ") || "-"}</div>
      <div class="channel-actions">
        <button class="btn danger small" data-action="delete-template" data-id="${item.id}">删除</button>
      </div>
    `;
    els.templatesList.appendChild(row);
  }
}

function renderAudits(items) {
  els.auditList.innerHTML = "";
  if (!items.length) {
    els.auditList.innerHTML = '<div class="event-row">暂无审计记录。</div>';
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div class="event-row-top">
        <span class="event-type">${item.action}</span>
        <span class="event-time">${fmtTs(item.created_at)}</span>
      </div>
      <div class="event-message">目标: ${item.target}</div>
    `;
    els.auditList.appendChild(row);
  }
}

async function downloadBlob(url, filenameHint, method = "GET") {
  const resp = await fetch(url, { method });
  if (!resp.ok) {
    throw new Error(`下载失败: ${resp.status}`);
  }
  const blob = await resp.blob();
  const urlObj = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = urlObj;
  a.download = filenameHint;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(urlObj);
}

async function loadMaintenanceState() {
  const payload = await request("/api/maintenance/state");
  els.readOnlyToggle.checked = !!payload.read_only;
}

async function refreshSnapshots() {
  const payload = await request("/api/maintenance/snapshots");
  renderSnapshots(payload.items || []);
}

async function refreshTemplates() {
  const payload = await request("/api/templates");
  renderTemplates(payload.items || []);
}

async function refreshAudits() {
  const payload = await request("/api/audit/logs?limit=200");
  renderAudits(payload.items || []);
}

function getImportFile() {
  const file = els.importFile.files?.[0];
  if (!file) {
    throw new Error("请先选择 zip 文件。");
  }
  return file;
}

async function importPreview() {
  const form = new FormData();
  form.append("file", getImportFile());
  const resp = await fetch("/api/maintenance/import/preview", { method: "POST", body: form });
  const payload = await resp.json();
  if (!resp.ok) {
    throw new Error(payload.detail || "导入预检失败。");
  }
  els.previewOutput.textContent = JSON.stringify(payload, null, 2);
}

async function importApply(mode) {
  const form = new FormData();
  form.append("file", getImportFile());
  form.append("mode_form", mode);
  const resp = await fetch("/api/maintenance/import/apply", {
    method: "POST",
    body: form,
  });
  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(payload.detail || "导入执行失败。");
  }
  setHint(`导入完成（${mode}）。`, "info");
  await Promise.all([refreshSnapshots(), refreshAudits()]);
}

async function clearBrowserCacheAndReload() {
  const confirmed = window.confirm("确认清除浏览器本地缓存并刷新页面吗？");
  if (!confirmed) return;
  const errors = [];

  try {
    window.localStorage.clear();
  } catch (error) {
    errors.push(`localStorage: ${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    window.sessionStorage.clear();
  } catch (error) {
    errors.push(`sessionStorage: ${error instanceof Error ? error.message : String(error)}`);
  }
  if ("caches" in window) {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch (error) {
      errors.push(`Cache Storage: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if ("serviceWorker" in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((item) => item.unregister()));
    } catch (error) {
      errors.push(`Service Worker: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  try {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (const raw of cookies) {
      const [nameRaw] = raw.split("=");
      const name = nameRaw.trim();
      if (!name) continue;
      document.cookie = `${name}=; Max-Age=0; path=/`;
    }
  } catch (error) {
    errors.push(`Cookie: ${error instanceof Error ? error.message : String(error)}`);
  }

  if (errors.length > 0) {
    setHint(`缓存清理已执行，部分项清除失败：${errors.join(" | ")}`, "warn");
  } else {
    setHint("浏览器缓存已清理，正在刷新页面。", "info");
  }
  setTimeout(() => window.location.reload(), 220);
}

function bindEvents() {
  els.logoutBtn.addEventListener("click", () => logout());
  els.exportBtn.addEventListener("click", async () => {
    try {
      await downloadBlob("/api/maintenance/export", "frp_bundle_export.zip", "POST");
      setHint("已导出配置包。", "info");
    } catch (error) {
      setHint(`导出失败: ${error.message}`, "error");
    }
  });
  els.diagnosticsBtn.addEventListener("click", async () => {
    try {
      await downloadBlob("/api/maintenance/diagnostics", "frp_diagnostics.zip", "GET");
      setHint("已下载诊断包。", "info");
    } catch (error) {
      setHint(`下载诊断包失败: ${error.message}`, "error");
    }
  });
  els.previewBtn.addEventListener("click", async () => {
    try {
      await importPreview();
      setHint("导入预检完成。", "info");
    } catch (error) {
      setHint(`预检失败: ${error.message}`, "error");
    }
  });
  els.applyOverwriteBtn.addEventListener("click", async () => {
    try {
      await importApply("overwrite");
    } catch (error) {
      setHint(`覆盖导入失败: ${error.message}`, "error");
    }
  });
  els.applyMergeBtn.addEventListener("click", async () => {
    try {
      await importApply("merge");
    } catch (error) {
      setHint(`合并导入失败: ${error.message}`, "error");
    }
  });
  els.readOnlyToggle.addEventListener("change", async () => {
    try {
      const payload = await request("/api/maintenance/read-only", "PUT", { enabled: !!els.readOnlyToggle.checked });
      els.readOnlyToggle.checked = !!payload.read_only;
      setHint(`只读模式已${payload.read_only ? "开启" : "关闭"}。`, "info");
      await refreshAudits();
    } catch (error) {
      setHint(`切换只读模式失败: ${error.message}`, "error");
    }
  });
  els.refreshSnapshotsBtn.addEventListener("click", async () => {
    try {
      await refreshSnapshots();
      setHint("快照已刷新。", "info");
    } catch (error) {
      setHint(`刷新快照失败: ${error.message}`, "error");
    }
  });
  els.clearBrowserCacheBtn.addEventListener("click", async () => {
    try {
      await clearBrowserCacheAndReload();
    } catch (error) {
      setHint(`清除浏览器缓存失败: ${error.message}`, "error");
    }
  });
  els.snapshotsList.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.dataset.action !== "rollback") return;
    const snapshotId = Number(target.dataset.id || 0);
    if (!snapshotId) return;
    if (!window.confirm(`确认回滚到快照 #${snapshotId} 吗？`)) return;
    try {
      await request("/api/maintenance/snapshots/rollback", "POST", { snapshot_id: snapshotId });
      setHint(`已回滚到快照 #${snapshotId}。`, "warn");
      await Promise.all([refreshSnapshots(), refreshAudits()]);
    } catch (error) {
      setHint(`回滚失败: ${error.message}`, "error");
    }
  });
  els.templateCreateBtn.addEventListener("click", async () => {
    const name = els.templateName.value.trim();
    const content = els.templateContent.value;
    const tags = els.templateTags.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!name || !content.trim()) {
      setHint("模板名称和内容不能为空。", "warn");
      return;
    }
    try {
      await request("/api/templates", "POST", { name, content, tags, source_client_id: null });
      els.templateName.value = "";
      els.templateTags.value = "";
      setHint("模板已创建。", "info");
      await Promise.all([refreshTemplates(), refreshAudits()]);
    } catch (error) {
      setHint(`创建模板失败: ${error.message}`, "error");
    }
  });
  els.templateReloadBtn.addEventListener("click", async () => {
    try {
      await refreshTemplates();
      setHint("模板已刷新。", "info");
    } catch (error) {
      setHint(`刷新模板失败: ${error.message}`, "error");
    }
  });
  els.templatesList.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.dataset.action !== "delete-template") return;
    const templateId = Number(target.dataset.id || 0);
    if (!templateId) return;
    if (!window.confirm(`确认删除模板 #${templateId} 吗？`)) return;
    try {
      await request(`/api/templates/${templateId}`, "DELETE");
      setHint("模板已删除。", "info");
      await Promise.all([refreshTemplates(), refreshAudits()]);
    } catch (error) {
      setHint(`删除模板失败: ${error.message}`, "error");
    }
  });
  els.auditReloadBtn.addEventListener("click", async () => {
    try {
      await refreshAudits();
      setHint("审计记录已刷新。", "info");
    } catch (error) {
      setHint(`刷新审计失败: ${error.message}`, "error");
    }
  });
}

async function init() {
  markActiveNav("maintenance");
  initThemePicker(els.themeMode);
  bindEvents();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  await Promise.all([loadMaintenanceState(), refreshSnapshots(), refreshTemplates(), refreshAudits()]);
}

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
