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


def test_maintenance_download_methods(client: TestClient):
    export_resp = client.post("/api/maintenance/export")
    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"].startswith("application/zip")

    diagnostics_resp = client.get("/api/maintenance/diagnostics")
    assert diagnostics_resp.status_code == 200
    assert diagnostics_resp.headers["content-type"].startswith("application/zip")


def test_import_apply_invalid_mode_returns_structured_detail_and_audit(client: TestClient):
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

    logs = client.get("/api/audit/logs?limit=200")
    assert logs.status_code == 200
    items = logs.json()["items"]
    assert any(item["action"] == "maintenance_import_apply_failed" and item["target"] == "mode" for item in items)


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

    audits = client.get("/api/audit/logs?limit=500")
    assert audits.status_code == 200
    rows = audits.json()["items"]
    assert any(item["action"] == "preflight_client" and item["target"] == client_id for item in rows)
    assert any(
        item["action"] == "start_client"
        and item["target"] == client_id
        and bool(item.get("detail", {}).get("force"))
        for item in rows
    )


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
