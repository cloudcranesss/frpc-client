const els = {
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginBtn: document.querySelector("#login_btn"),
  loginError: document.querySelector("#login_error"),
};

els.loginBtn.addEventListener("click", submitLogin);
els.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    submitLogin();
  }
});

checkAuthStatus();

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
    window.location.replace("/");
  } catch (error) {
    els.loginError.textContent = `登录失败: ${error.message}`;
  } finally {
    els.loginBtn.disabled = false;
  }
}

async function request(url, method = "GET", body = null) {
  const response = await fetch(url, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : null,
  });
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

