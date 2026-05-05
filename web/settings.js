import { initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
  settingsHint: document.querySelector("#settings_hint"),
  profileUsername: document.querySelector("#profile_username"),
  profileCurrentPassword: document.querySelector("#profile_current_password"),
  profileNewPassword: document.querySelector("#profile_new_password"),
  profileNewPasswordConfirm: document.querySelector("#profile_new_password_confirm"),
  profileSaveBtn: document.querySelector("#profile_save_btn"),
  passwordPolicy: document.querySelector("#password_policy"),
  alertName: document.querySelector("#alert_name"),
  alertWebhookUrl: document.querySelector("#alert_webhook_url"),
  alertTimeoutSec: document.querySelector("#alert_timeout_sec"),
  alertEnabled: document.querySelector("#alert_enabled"),
  alertCreateBtn: document.querySelector("#alert_create_btn"),
  alertReloadBtn: document.querySelector("#alert_reload_btn"),
  alertChannels: document.querySelector("#alert_channels"),
  ruleStartFailure: document.querySelector("#rule_start_failure"),
  ruleAbnormalExit: document.querySelector("#rule_abnormal_exit"),
  ruleRestartThreshold: document.querySelector("#rule_restart_threshold"),
  ruleRestartThresholdValue: document.querySelector("#rule_restart_threshold_value"),
  rulesSaveBtn: document.querySelector("#rules_save_btn"),
  exportBtn: document.querySelector("#export_btn"),
  diagnosticsBtn: document.querySelector("#diagnostics_btn"),
  importFile: document.querySelector("#import_file"),
  previewBtn: document.querySelector("#preview_btn"),
  applyOverwriteBtn: document.querySelector("#apply_overwrite_btn"),
  applyMergeBtn: document.querySelector("#apply_merge_btn"),
  previewOutput: document.querySelector("#preview_output"),
  clearBrowserCacheBtn: document.querySelector("#clear_browser_cache_btn"),
};

const state = {
  channels: [],
  currentUsername: "",
};

function setHint(text, level = "info") {
  els.settingsHint.textContent = text;
  els.settingsHint.classList.remove("info", "warn", "error");
  els.settingsHint.classList.add(level);
}

function renderAlertChannels() {
  els.alertChannels.innerHTML = "";
  if (state.channels.length === 0) {
    const empty = document.createElement("div");
    empty.className = "channel-item";
    empty.textContent = "暂无告警渠道。";
    els.alertChannels.appendChild(empty);
    return;
  }

  for (const channel of state.channels) {
    const row = document.createElement("div");
    row.className = "channel-item";
    row.innerHTML = `
      <div class="channel-info">${channel.name}</div>
      <div class="channel-url">${channel.webhook_url}</div>
      <div class="channel-meta">超时=${channel.timeout_sec}s | ${channel.enabled ? "启用" : "停用"}</div>
      <div class="channel-actions">
        <button type="button" class="btn secondary small" data-action="toggle" data-id="${channel.id}">
          <svg class="btn-icon" aria-hidden="true"><use href="/web/icons.svg#icon-power"></use></svg>
          <span class="btn-label">${channel.enabled ? "停用" : "启用"}</span>
        </button>
        <button type="button" class="btn danger small" data-action="delete" data-id="${channel.id}">
          <svg class="btn-icon" aria-hidden="true"><use href="/web/icons.svg#icon-trash"></use></svg>
          <span class="btn-label">删除</span>
        </button>
      </div>
    `;
    els.alertChannels.appendChild(row);
  }
}

async function loadProfile() {
  const payload = await request("/api/auth/profile");
  state.currentUsername = payload.username || "";
  els.profileUsername.value = state.currentUsername;
  els.passwordPolicy.textContent = `密码策略：${payload.password_policy || ""}`;
}

async function loadAlertChannels() {
  const payload = await request("/api/alerts/channels");
  state.channels = payload.items || [];
  renderAlertChannels();
}

async function loadAlertRules() {
  const rules = await request("/api/alerts/rules");
  els.ruleStartFailure.checked = !!rules.on_start_failure;
  els.ruleAbnormalExit.checked = !!rules.on_abnormal_exit;
  els.ruleRestartThreshold.checked = !!rules.on_restart_threshold;
  els.ruleRestartThresholdValue.value = String(rules.restart_threshold ?? 3);
}

function getImportFile() {
  const file = els.importFile.files?.[0];
  if (!file) throw new Error("请先选择 zip 文件。");
  return file;
}

async function downloadBlob(url, filenameHint, method = "GET") {
  const resp = await fetch(url, { method });
  if (!resp.ok) throw new Error(`下载失败: ${resp.status}`);
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

async function importPreview() {
  const form = new FormData();
  form.append("file", getImportFile());
  const resp = await fetch("/api/maintenance/import/preview", { method: "POST", body: form });
  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(payload.detail || "导入预检失败。");
  els.previewOutput.textContent = JSON.stringify(payload, null, 2);
}

async function importApply(mode) {
  const form = new FormData();
  form.append("file", getImportFile());
  form.append("mode_form", mode);
  const resp = await fetch("/api/maintenance/import/apply", { method: "POST", body: form });
  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (typeof payload.detail === "object") {
      throw new Error(JSON.stringify(payload.detail));
    }
    throw new Error(payload.detail || "导入失败。");
  }
  setHint(`导入完成（${mode}）。`, "info");
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
  if (errors.length > 0) {
    setHint(`缓存清理已执行，部分失败：${errors.join(" | ")}`, "warn");
  } else {
    setHint("缓存清理完成，正在刷新。", "info");
  }
  setTimeout(() => window.location.reload(), 220);
}

function bindEvents() {
  els.logoutBtn.addEventListener("click", () => logout());
  els.profileSaveBtn.addEventListener("click", async () => {
    const newUsername = els.profileUsername.value.trim();
    const currentPassword = els.profileCurrentPassword.value;
    const newPassword = els.profileNewPassword.value;
    const confirmPassword = els.profileNewPasswordConfirm.value;

    if (!currentPassword) {
      setHint("请填写当前密码。", "warn");
      return;
    }
    if (newPassword || confirmPassword) {
      if (newPassword !== confirmPassword) {
        setHint("两次新密码不一致。", "warn");
        return;
      }
      if (newPassword.length < 8) {
        setHint("新密码至少 8 位。", "warn");
        return;
      }
    }
    try {
      await request("/api/auth/profile", "PUT", {
        new_username: newUsername === state.currentUsername ? null : newUsername,
        current_password: currentPassword,
        new_password: newPassword || null,
      });
      setHint("账号更新成功，请重新登录。", "info");
      setTimeout(() => window.location.replace("/login"), 200);
    } catch (error) {
      setHint(`账号更新失败: ${error.message}`, "error");
    }
  });

  els.alertCreateBtn.addEventListener("click", async () => {
    const name = els.alertName.value.trim();
    const webhookUrl = els.alertWebhookUrl.value.trim();
    const timeoutSec = Number.parseInt(els.alertTimeoutSec.value, 10) || 5;
    const enabled = !!els.alertEnabled.checked;
    if (!name || !webhookUrl) {
      setHint("请填写渠道名称和 Webhook 地址。", "warn");
      return;
    }
    try {
      await request("/api/alerts/channels", "POST", {
        name,
        webhook_url: webhookUrl,
        timeout_sec: timeoutSec,
        enabled,
      });
      els.alertName.value = "";
      els.alertWebhookUrl.value = "";
      await loadAlertChannels();
      setHint("渠道已创建。", "info");
    } catch (error) {
      setHint(`创建渠道失败: ${error.message}`, "error");
    }
  });
  els.alertReloadBtn.addEventListener("click", async () => {
    try {
      await loadAlertChannels();
      setHint("渠道已刷新。", "info");
    } catch (error) {
      setHint(`刷新渠道失败: ${error.message}`, "error");
    }
  });
  els.alertChannels.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest("button[data-action]");
    if (!(button instanceof HTMLElement)) return;
    const action = button.dataset.action;
    const id = Number(button.dataset.id || 0);
    if (!action || !id) return;
    const channel = state.channels.find((item) => item.id === id);
    if (!channel) return;

    if (action === "toggle") {
      await request(`/api/alerts/channels/${id}`, "PUT", {
        name: channel.name,
        webhook_url: channel.webhook_url,
        timeout_sec: channel.timeout_sec,
        enabled: !channel.enabled,
      });
      await loadAlertChannels();
      setHint("渠道已更新。", "info");
      return;
    }

    if (action === "delete") {
      if (!window.confirm(`确认删除「${channel.name}」吗？`)) return;
      await request(`/api/alerts/channels/${id}`, "DELETE");
      await loadAlertChannels();
      setHint("渠道已删除。", "info");
    }
  });
  els.rulesSaveBtn.addEventListener("click", async () => {
    try {
      await request("/api/alerts/rules", "PUT", {
        on_start_failure: !!els.ruleStartFailure.checked,
        on_abnormal_exit: !!els.ruleAbnormalExit.checked,
        on_restart_threshold: !!els.ruleRestartThreshold.checked,
        restart_threshold: Number.parseInt(els.ruleRestartThresholdValue.value, 10) || 3,
      });
      setHint("规则已保存。", "info");
    } catch (error) {
      setHint(`保存规则失败: ${error.message}`, "error");
    }
  });

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
  els.clearBrowserCacheBtn.addEventListener("click", async () => {
    try {
      await clearBrowserCacheAndReload();
    } catch (error) {
      setHint(`清理缓存失败: ${error.message}`, "error");
    }
  });
}

async function init() {
  markActiveNav("settings");
  initThemePicker(els.themeMode);
  bindEvents();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  await Promise.all([loadProfile(), loadAlertChannels(), loadAlertRules()]);
}

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
