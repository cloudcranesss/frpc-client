from __future__ import annotations

from app.main import app


def test_legacy_routes_removed():
    paths = {route.path for route in app.routes}
    assert "/api/config" not in paths
    assert "/api/start" not in paths
    assert "/api/stop" not in paths
    assert "/api/status" not in paths
    assert "/api/logs" not in paths
    assert "/api/jump-links" not in paths
    assert "/api/frpc-path/auto" not in paths


def test_new_routes_exist():
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/events" in paths
    assert "/alerts" in paths
    assert "/maintenance" in paths
    assert "/api/clients/{client_id}/stream" in paths
    assert "/api/clients/{client_id}/preflight" in paths
    assert "/api/clients/{client_id}/events" in paths
    assert "/api/alerts/channels" in paths
    assert "/api/alerts/rules" in paths
    assert "/api/templates" in paths
    assert "/api/audit/logs" in paths
    assert "/api/maintenance/export" in paths
    assert "/api/maintenance/import/preview" in paths
    assert "/api/maintenance/import/apply" in paths
    assert "/api/maintenance/snapshots" in paths
    assert "/api/maintenance/snapshots/rollback" in paths
    assert "/api/maintenance/read-only" in paths
    assert "/api/maintenance/state" in paths
    assert "/api/maintenance/diagnostics" in paths
    assert "/api/auth/profile" in paths
    assert "/health/live" in paths
    assert "/health/ready" in paths
