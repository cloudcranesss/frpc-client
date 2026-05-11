from __future__ import annotations

from app.main import app


def test_legacy_single_client_routes_removed():
    paths = {route.path for route in app.routes}
    assert "/api/config" not in paths
    assert "/api/start" not in paths
    assert "/api/stop" not in paths
    assert "/api/status" not in paths
    assert "/api/logs" not in paths
    assert "/api/jump-links" not in paths
    assert "/api/frpc-path/auto" not in paths


def test_minimal_pages_and_core_routes_exist():
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/console" in paths
    assert "/dashboard" in paths
    assert "/settings" in paths
    assert "/login" in paths
    assert "/events" in paths
    assert "/alerts" in paths
    assert "/maintenance" in paths
    assert "/api/sites/successful" in paths
    assert "/api/sites/stream" in paths
    assert "/api/clients/{client_id}/stream" in paths
    assert "/api/clients/{client_id}/preflight" in paths
    assert "/api/clients/preflight-batch" in paths
    assert "/api/clients/start-batch" in paths
    assert "/api/clients/stop-batch" in paths
    assert "/api/clients/{client_id}/events" in paths
    assert "/api/clients/{client_id}/logs/clear" in paths
    assert "/api/clients/{client_id}/jump-links" in paths
    assert "/api/alerts/channels" in paths
    assert "/api/alerts/rules" in paths
    assert "/api/maintenance/export" in paths
    assert "/api/maintenance/import/preview" in paths
    assert "/api/maintenance/import/apply" in paths
    assert "/api/maintenance/diagnostics" in paths
    assert "/api/auth/profile" in paths
    assert "/api/auth/login-form" in paths
    assert "/health/live" in paths
    assert "/health/ready" in paths


def test_low_frequency_routes_removed():
    paths = {route.path for route in app.routes}
    assert "/api/templates" not in paths
    assert "/api/audit/logs" not in paths
    assert "/api/maintenance/snapshots" not in paths
    assert "/api/maintenance/snapshots/rollback" not in paths
    assert "/api/maintenance/read-only" not in paths
    assert "/api/maintenance/state" not in paths
