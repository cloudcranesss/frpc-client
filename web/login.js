import { initThemePicker, request } from "/web/shared.js";

const LAST_USERNAME_KEY = "frp_panel_last_username";

const els = {
  themeMode: document.querySelector("#theme_mode"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginBtn: document.querySelector("#login_btn"),
  loginError: document.querySelector("#login_error"),
};

initThemePicker(els.themeMode);
restoreLastUsername();

els.loginBtn.addEventListener("click", submitLogin);
els.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter") submitLogin();
});

checkAuthStatus();

function restoreLastUsername() {
  const last = window.localStorage.getItem(LAST_USERNAME_KEY) || "";
  els.username.value = last;
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
