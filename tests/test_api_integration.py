from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.alert_manager import AlertManager
from app.auth import AuthManager
from app.config_store import ConfigStore
from app.frpc_manager import FrpcManager


def _build_import_zip() -> bytes:
    buff = io.BytesIO()
    payload = {
        "schema_version": 1,
        "clients": [],
        "alert_rules": {
            "on_start_failure": True,
            "on_abnormal_exit": True,
            "on_restart_threshold": True,
            "restart_threshold": 3,
        },
        # legacy keys from old versions should be ignored
        "templates": [],
        "snapshots": [],
        "audit_logs": [],
        "maintenance_state": {"read_only": False},
    }
    with zipfile.ZipFile(buff, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(payload, ensure_ascii=False))
    return buff.getvalue()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    alerts = AlertManager(store)
    auth = AuthManager(data_dir, store)

    monkeypatch.setattr(main_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_mod, "config_store", store)
    monkeypatch.setattr(main_mod, "alert_manager", alerts)
    monkeypatch.setattr(main_mod, "auth_manager", auth)

    frpc = FrpcManager(event_callback=main_mod.handle_runtime_event)
    monkeypatch.setattr(main_mod, "frpc_manager", frpc)
    monkeypatch.setattr(main_mod, "login_limiter", main_mod.LoginRateLimiter())

    with TestClient(main_mod.app) as api:
        login = api.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
        assert login.status_code == 200
        yield api


def _first_client_id(client: TestClient) -> str:
    payload = client.get("/api/clients")
    assert payload.status_code == 200
    rows = payload.json()["clients"]
    assert rows
    return str(rows[0]["id"])


def test_low_frequency_endpoints_removed(client: TestClient):
    assert client.get("/api/templates").status_code == 404
    assert client.get("/api/audit/logs").status_code == 404
    assert client.get("/api/maintenance/snapshots").status_code == 404
    assert client.post("/api/maintenance/snapshots/rollback", json={"snapshot_id": 1}).status_code == 404
    assert client.get("/api/maintenance/state").status_code == 404
    assert client.put("/api/maintenance/read-only", json={"enabled": True}).status_code == 404


def test_page_redirects_to_settings(client: TestClient):
    for path in ("/events", "/alerts", "/maintenance"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in {302, 307}
        assert resp.headers["location"] == "/settings"


def test_html_pages_inject_asset_version_and_disable_cache(client: TestClient):
    for path in ("/", "/settings", "/login"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "__ASSET_VERSION__" not in resp.text
        assert "/web/style.css?v=" in resp.text
        assert "/web/shared.js?v=" in resp.text
        assert resp.headers.get("cache-control") == "no-cache, no-store, must-revalidate"


def test_web_static_assets_disable_cache(client: TestClient):
    resp = client.get("/web/shared.js")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "no-store" in cache_control
    assert "no-cache" in cache_control


def test_login_form_fallback_flow(client: TestClient):
    resp = client.post(
        "/api/auth/login-form",
        data={"username": "admin", "password": "admin123456"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/"
    cookie = resp.headers.get("set-cookie", "")
    assert "frp_panel_session=" in cookie


def test_maintenance_download_methods(client: TestClient):
    export_resp = client.post("/api/maintenance/export")
    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"].startswith("application/zip")

    diagnostics_resp = client.get("/api/maintenance/diagnostics")
    assert diagnostics_resp.status_code == 200
    assert diagnostics_resp.headers["content-type"].startswith("application/zip")


def test_import_apply_invalid_mode_returns_structured_detail(client: TestClient):
    bundle = _build_import_zip()
    resp = client.post(
        "/api/maintenance/import/apply?mode=bad",
        files={"file": ("bundle.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("message")
    assert detail.get("mode") == "bad"
    assert detail.get("allowed") == ["overwrite", "merge"]


def test_import_apply_accepts_legacy_keys_and_ignores_them(client: TestClient):
    bundle = _build_import_zip()
    resp = client.post(
        "/api/maintenance/import/apply?mode=merge",
        files={"file": ("bundle.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    exported = client.post("/api/maintenance/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content), "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert "templates" not in manifest
    assert "snapshots" not in manifest
    assert "audit_logs" not in manifest
    assert "maintenance_state" not in manifest


def test_preflight_then_force_start_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    client_id = _first_client_id(client)
    cfg_resp = client.get(f"/api/clients/{client_id}/config")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()
    cfg["config_text"] = "serverAddr = \n"
    put_resp = client.put(f"/api/clients/{client_id}/config", json=cfg)
    assert put_resp.status_code == 200

    preflight = client.post(f"/api/clients/{client_id}/preflight")
    assert preflight.status_code == 200
    preflight_payload = preflight.json()
    assert preflight_payload["ok"] is False
    assert preflight_payload["errors"]

    blocked_start = client.post(f"/api/clients/{client_id}/start")
    assert blocked_start.status_code == 400

    async def fake_start(client_id: str, cfg, config_path):
        _ = (client_id, cfg, config_path)
        return {
            "running": False,
            "pid": None,
            "started_at": None,
            "uptime_sec": 0,
            "last_exit_code": 0,
            "restart_count": 0,
            "last_error": None,
        }

    monkeypatch.setattr(main_mod.frpc_manager, "start", fake_start)
    forced_start = client.post(f"/api/clients/{client_id}/start?force=true")
    assert forced_start.status_code == 200

    events = client.get(f"/api/clients/{client_id}/events?limit=200")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()["items"]]
    assert "preflight_failed" in event_types
    assert "preflight_forced" in event_types


def test_jump_links_follow_client_config(client: TestClient):
    client_id = _first_client_id(client)
    cfg_resp = client.get(f"/api/clients/{client_id}/config")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()
    cfg["config_text"] = (
        'serverAddr = "frp.example.com"\n'
        "serverPort = 7000\n\n"
        "[[proxies]]\n"
        'name = "web-http"\n'
        'type = "http"\n'
        "localPort = 8080\n"
        "remotePort = 8088\n"
    )
    put_resp = client.put(f"/api/clients/{client_id}/config", json=cfg)
    assert put_resp.status_code == 200

    links = client.get(f"/api/clients/{client_id}/jump-links")
    assert links.status_code == 200
    items = links.json()["items"]
    assert len(items) == 1
    assert items[0]["proxy_name"] == "web-http"
    assert items[0]["proxy_type"] == "http"
    assert items[0]["url"] == "http://frp.example.com:8088"


def test_jump_links_support_ini_and_json(client: TestClient):
    client_id = _first_client_id(client)
    cfg_resp = client.get(f"/api/clients/{client_id}/config")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()

    cfg["config_text"] = (
        "[common]\n"
        "server_addr = frp.example.com\n"
        "server_port = 7000\n\n"
        "[api]\n"
        "type = tcp\n"
        "local_port = 9000\n"
        "remote_port = 9100\n"
    )
    put_resp = client.put(f"/api/clients/{client_id}/config", json=cfg)
    assert put_resp.status_code == 200
    links_ini = client.get(f"/api/clients/{client_id}/jump-links")
    assert links_ini.status_code == 200
    ini_items = links_ini.json()["items"]
    assert len(ini_items) == 1
    assert ini_items[0]["url"] == "http://frp.example.com:9100"

    cfg["config_text"] = (
        '{"serverAddr":"frp.example.com","serverPort":7000,'
        '"proxies":[{"name":"web-json","type":"http","localPort":8080,"remotePort":9200}]}'
    )
    put_resp2 = client.put(f"/api/clients/{client_id}/config", json=cfg)
    assert put_resp2.status_code == 200
    links_json = client.get(f"/api/clients/{client_id}/jump-links")
    assert links_json.status_code == 200
    json_items = links_json.json()["items"]
    assert len(json_items) == 1
    assert json_items[0]["proxy_name"] == "web-json"
    assert json_items[0]["url"] == "http://frp.example.com:9200"


def test_clear_client_logs_endpoint(client: TestClient):
    client_id = _first_client_id(client)

    async def seed_logs() -> None:
        state = await main_mod.frpc_manager._get_or_create_state(client_id)
        async with state.lock:
            state.logs.append("line-1")
            state.logs.append("line-2")

    import asyncio

    asyncio.run(seed_logs())

    before = client.get(f"/api/clients/{client_id}/logs?limit=50")
    assert before.status_code == 200
    assert before.json()["items"]

    cleared = client.post(f"/api/clients/{client_id}/logs/clear")
    assert cleared.status_code == 200
    assert cleared.json()["success"] is True

    after = client.get(f"/api/clients/{client_id}/logs?limit=50")
    assert after.status_code == 200
    assert after.json()["items"] == []


def test_auth_profile_update_requires_relogin_and_new_credentials_work(client: TestClient):
    profile = client.get("/api/auth/profile")
    assert profile.status_code == 200
    profile_payload = profile.json()
    assert profile_payload["username"] == "admin"
    assert "8" in profile_payload["password_policy"]

    update = client.put(
        "/api/auth/profile",
        json={
            "new_username": "ops_admin",
            "current_password": "admin123456",
            "new_password": "SafePass1234",
        },
    )
    assert update.status_code == 200
    assert update.json()["success"] is True
    assert update.json()["username"] == "ops_admin"

    protected = client.get("/api/clients")
    assert protected.status_code == 401

    old_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
    assert old_login.status_code == 401
    new_login = client.post("/api/auth/login", json={"username": "ops_admin", "password": "SafePass1234"})
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_client_auto_start_persist_and_boot_trigger(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    client_id = _first_client_id(client)
    cfg_resp = client.get(f"/api/clients/{client_id}/config")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()
    cfg["auto_start"] = True
    put_resp = client.put(f"/api/clients/{client_id}/config", json=cfg)
    assert put_resp.status_code == 200
    assert put_resp.json()["auto_start"] is True

    listing = client.get("/api/clients")
    assert listing.status_code == 200
    row = next(item for item in listing.json()["clients"] if item["id"] == client_id)
    assert row["auto_start"] is True

    async def fake_preflight(_: str):
        return {"ok": True, "errors": [], "warnings": []}

    async def fake_resolve(cid: str):
        state = await main_mod.config_store.load_state()
        return next(item for item in state.clients if item.id == cid)

    calls: list[str] = []

    async def fake_start(cid: str, cfg, config_path):
        _ = (cfg, config_path)
        calls.append(cid)
        return {
            "running": False,
            "pid": None,
            "started_at": None,
            "uptime_sec": 0,
            "last_exit_code": 0,
            "restart_count": 0,
            "last_error": None,
        }

    monkeypatch.setattr(main_mod, "_client_preflight", fake_preflight)
    monkeypatch.setattr(main_mod, "_resolve_start_config", fake_resolve)
    monkeypatch.setattr(main_mod.frpc_manager, "start", fake_start)

    await main_mod._auto_start_clients_on_boot()
    assert calls == [client_id]
