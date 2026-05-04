from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_pages_include_theme_picker_and_shared_runtime():
    for page in ("index.html", "events.html", "alerts.html", "maintenance.html", "login.html"):
        text = _read(page)
        assert 'id="theme_mode"' in text
        assert '<script src="/web/shared.js" type="module"></script>' in text
        assert "data-page=" in text


def test_theme_runtime_supports_global_theme_and_motion():
    text = _read("shared.js")
    assert "frp_panel_theme_mode" in text
    assert "document.body.dataset.theme" in text
    assert "document.body.dataset.motion" in text


def test_shared_request_formats_structured_error_detail():
    text = _read("shared.js")
    assert "function formatErrorDetail" in text
    assert "Object.entries(detail)" in text
    assert "payload.detail ?? payload" in text


def test_events_page_has_sse_reconnect_backoff_logic():
    text = _read("events.js")
    assert "reconnectDelaySec" in text
    assert "source.onerror" in text
    assert "setTimeout(() => {" in text
    assert "Math.min(30, state.reconnectDelaySec * 2)" in text


def test_maintenance_download_uses_mixed_http_methods():
    text = _read("maintenance.js")
    assert 'downloadBlob("/api/maintenance/export", "frp_bundle_export.zip", "POST")' in text
    assert 'downloadBlob("/api/maintenance/diagnostics", "frp_diagnostics.zip", "GET")' in text


def test_pages_are_chinese_and_nav_no_english_labels():
    for page in ("index.html", "events.html", "alerts.html", "maintenance.html", "login.html"):
        text = _read(page)
        assert "控制台" in text or "账号登录" in text
        assert ">Dashboard<" not in text
        assert ">Events<" not in text
        assert ">Alerts<" not in text
        assert ">Maintenance<" not in text


def test_events_page_has_event_type_zh_mapping():
    text = _read("events.js")
    assert "EVENT_TYPE_LABELS" in text
    assert 'start_failure: "启动失败"' in text
    assert "类型键:" in text


def test_login_page_remembers_last_username_only():
    text = _read("login.js")
    assert "frp_panel_last_username" in text
    assert "setItem(LAST_USERNAME_KEY, username)" in text
    assert "不会在浏览器保存密码" in _read("login.html")
