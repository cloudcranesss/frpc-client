import { initThemePicker, request } from "/web/shared.js";

const LAST_USERNAME_KEY = "frp_panel_last_username";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  loginForm: document.querySelector("#login_form"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginBtn: document.querySelector("#login_btn"),
  loginError: document.querySelector("#login_error"),
};

initThemePicker(els.themeMode);
restoreLastUsername();
renderFallbackError();

els.loginForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitLogin();
});
els.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter") submitLogin();
});

checkAuthStatus();

function restoreLastUsername() {
  const last = window.localStorage.getItem(LAST_USERNAME_KEY) || "";
  els.username.value = last;
}

function renderFallbackError() {
  if (!els.loginError) return;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("error") || "";
  if (!code) return;
  if (code === "empty") {
    els.loginError.textContent = "请输入用户名和密码。";
    return;
  }
  if (code === "rate_limit") {
    els.loginError.textContent = "登录失败次数过多，请稍后再试。";
    return;
  }
  if (code === "invalid") {
    els.loginError.textContent = "用户名或密码错误。";
    return;
  }
  els.loginError.textContent = "登录失败，请稍后重试。";
}

async function checkAuthStatus() {
  try {
    const status = await request("/api/auth/status");
    if (status.authenticated) {
      window.location.replace("/");
    }
  } catch {
    // ignore
  }
}

async function submitLogin() {
  const username = els.username.value.trim();
  const password = els.password.value;
  if (!username || !password) {
    els.loginError.textContent = "请输入用户名和密码。";
    return;
  }
  els.loginError.textContent = "";
  els.loginBtn.disabled = true;
  try {
    await request("/api/auth/login", "POST", { username, password });
    window.localStorage.setItem(LAST_USERNAME_KEY, username);
    window.location.replace("/");
  } catch (error) {
    els.loginError.textContent = `登录失败: ${error.message}`;
  } finally {
    els.loginBtn.disabled = false;
  }
}
