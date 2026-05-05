from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_pages_include_theme_picker_and_shared_runtime():
    for page in ("index.html", "settings.html", "login.html"):
        text = _read(page)
        assert 'id="theme_mode"' in text
        assert '<script src="/web/shared.js?v=__ASSET_VERSION__" type="module"></script>' in text
        assert 'data-asset-version="__ASSET_VERSION__"' in text
        assert "data-page=" in text


def test_nav_is_reduced_to_two_entries():
    for page in ("index.html", "settings.html"):
        text = _read(page)
        assert 'href="/" data-nav="dashboard"' in text
        assert 'href="/settings" data-nav="settings"' in text
        assert 'href="/events"' not in text
        assert 'href="/alerts"' not in text
        assert 'href="/maintenance"' not in text


def test_old_page_files_removed():
    assert not (WEB / "events.html").exists()
    assert not (WEB / "alerts.html").exists()
    assert not (WEB / "maintenance.html").exists()
    assert not (WEB / "events.js").exists()
    assert not (WEB / "alerts.js").exists()
    assert not (WEB / "maintenance.js").exists()


def test_dashboard_contains_collapsed_logs_and_preflight_panel():
    text = _read("index.html")
    assert 'id="auto_start"' in text
    assert 'id="jump_links"' in text
    assert 'id="jump_count"' in text
    assert 'id="logs_toggle_btn"' in text
    assert 'id="logs_toggle_label"' in text
    assert 'id="logs_clear_btn"' in text
    assert 'id="logs" class="logs hidden"' in text
    assert 'id="preflight_panel"' in text
    assert 'id="preflight_force_btn"' in text


def test_settings_page_contains_account_alerts_and_backup_sections():
    text = _read("settings.html")
    assert 'id="profile_username"' in text
    assert 'id="profile_current_password"' in text
    assert 'id="alert_channels"' in text
    assert 'id="rules_save_btn"' in text
    assert 'id="export_btn"' in text
    assert 'id="preview_btn"' in text
    assert 'id="diagnostics_btn"' in text
    assert 'id="clear_browser_cache_btn"' in text
    assert 'id="read_only_toggle"' not in text
    assert 'id="refresh_snapshots_btn"' not in text
    assert 'id="template_create_btn"' not in text
    assert 'id="audit_reload_btn"' not in text


def test_theme_runtime_supports_global_theme_and_motion():
    text = _read("shared.js")
    assert "frp_panel_theme_mode" in text
    assert "document.body.dataset.theme" in text
    assert "document.body.dataset.motion" in text


def test_shared_request_formats_structured_error_detail():
    text = _read("shared.js")
    assert "function formatErrorDetail" in text
    assert "Object.entries(detail)" in text
    assert "parsed.detail ?? parsed" in text


def test_settings_download_uses_mixed_http_methods():
    text = _read("settings.js")
    assert 'downloadBlob("/api/maintenance/export", "frp_bundle_export.zip", "POST")' in text
    assert 'downloadBlob("/api/maintenance/diagnostics", "frp_diagnostics.zip", "GET")' in text


def test_settings_has_clear_browser_cache_feature():
    text = _read("settings.js")
    assert "clearBrowserCacheAndReload" in text
    assert "localStorage.clear()" in text
    assert "sessionStorage.clear()" in text
    assert "caches.keys()" in text


def test_login_page_remembers_last_username_only():
    text = _read("login.js")
    assert "frp_panel_last_username" in text
    assert "setItem(LAST_USERNAME_KEY, username)" in text
    assert "不会在浏览器保存密码" in _read("login.html")
    assert 'id="login_form"' in _read("login.html")
    assert 'action="/api/auth/login-form"' in _read("login.html")


def test_svg_sprite_exists_and_has_core_symbols():
    text = _read("icons.svg")
    assert 'id="icon-play"' in text
    assert 'id="icon-stop"' in text
    assert 'id="icon-save"' in text
    assert 'id="icon-refresh"' in text
    assert 'id="icon-login"' in text
    assert 'id="icon-logout"' in text


def test_pages_use_svg_button_icon_markup():
    for page in ("index.html", "settings.html", "login.html"):
        text = _read(page)
        assert 'class="btn-icon"' in text
        assert 'class="btn-label"' in text
        assert "/web/icons.svg#icon-" in text


def test_dynamic_action_buttons_support_svg_click_target():
    settings = _read("settings.js")
    dashboard = _read("dashboard.js")
    assert 'closest("button[data-action]")' in settings
    assert 'closest("button[data-client-id]")' in dashboard


def test_dashboard_uses_jump_links_api_with_safe_open():
    dashboard = _read("dashboard.js")
    assert "/jump-links" in dashboard
    assert 'target="_blank"' in dashboard
    assert 'rel="noopener noreferrer"' in dashboard


def test_dashboard_supports_clear_logs_action():
    dashboard = _read("dashboard.js")
    assert "/logs/clear" in dashboard
    assert "logsClearBtn" in dashboard
