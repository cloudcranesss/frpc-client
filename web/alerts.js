import { initThemePicker, logout, markActiveNav, request, requireAuth, setUserBadge } from "/web/shared.js";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  userBadge: document.querySelector("#user_badge"),
  logoutBtn: document.querySelector("#logout_btn"),
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
  hint: document.querySelector("#alerts_hint"),
};

const state = {
  channels: [],
};

function setHint(text, level = "info") {
  els.hint.textContent = text;
  els.hint.classList.remove("info", "warn", "error");
  els.hint.classList.add(level);
}

async function loadAlertChannels() {
  const payload = await request("/api/alerts/channels");
  state.channels = payload.items ?? [];
  renderAlertChannels();
}

async function loadAlertRules() {
  const rules = await request("/api/alerts/rules");
  els.ruleStartFailure.checked = !!rules.on_start_failure;
  els.ruleAbnormalExit.checked = !!rules.on_abnormal_exit;
  els.ruleRestartThreshold.checked = !!rules.on_restart_threshold;
  els.ruleRestartThresholdValue.value = String(rules.restart_threshold ?? 3);
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
        <button type="button" class="btn secondary small" data-action="toggle" data-id="${channel.id}">${channel.enabled ? "停用" : "启用"}</button>
        <button type="button" class="btn danger small" data-action="delete" data-id="${channel.id}">删除</button>
      </div>
    `;
    els.alertChannels.appendChild(row);
  }
}

async function handleChannelActions(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const action = target.dataset.action;
  const id = Number(target.dataset.id || 0);
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
    const ok = window.confirm(`确认删除「${channel.name}」吗？`);
    if (!ok) return;
    await request(`/api/alerts/channels/${id}`, "DELETE");
    await loadAlertChannels();
    setHint("渠道已删除。", "info");
  }
}

function bindEvents() {
  els.logoutBtn.addEventListener("click", () => logout());
  els.alertChannels.addEventListener("click", (event) => {
    handleChannelActions(event).catch((error) => {
      setHint(`操作失败: ${error.message}`, "error");
    });
  });
  els.alertCreateBtn.addEventListener("click", async () => {
    const name = els.alertName.value.trim();
    const webhookUrl = els.alertWebhookUrl.value.trim();
    const timeoutSec = Number.parseInt(els.alertTimeoutSec.value, 10) || 5;
    const enabled = !!els.alertEnabled.checked;
    if (!name || !webhookUrl) {
      setHint("请填写渠道名称和地址。", "warn");
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
      setHint(`创建失败: ${error.message}`, "error");
    }
  });
  els.alertReloadBtn.addEventListener("click", async () => {
    try {
      await loadAlertChannels();
      setHint("渠道已刷新。", "info");
    } catch (error) {
      setHint(`刷新失败: ${error.message}`, "error");
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
      setHint(`保存失败: ${error.message}`, "error");
    }
  });
}

async function init() {
  markActiveNav("alerts");
  initThemePicker(els.themeMode);
  bindEvents();
  const auth = await requireAuth();
  setUserBadge(els.userBadge, auth.username);
  await Promise.all([loadAlertChannels(), loadAlertRules()]);
}

init().catch((error) => {
  setHint(`初始化失败: ${error.message}`, "error");
});
