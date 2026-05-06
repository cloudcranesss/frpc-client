from __future__ import annotations

import io
import json
import zipfile

from app.maintenance import (
    build_export_zip,
    extract_template_variables,
    parse_import_zip,
    preflight_config,
    preview_import_bundle,
    redact_sensitive,
)


def test_extract_template_variables():
    content = 'serverAddr = "${SERVER_ADDR}"\nremotePort = ${REMOTE_PORT}\n'
    vars_found = extract_template_variables(content)
    assert vars_found == ["REMOTE_PORT", "SERVER_ADDR"]


def test_preflight_detects_invalid_config():
    result = preflight_config("frpc", "serverAddr = \n", "")
    assert result["ok"] is False
    assert any("Config parse error" in msg for msg in result["errors"])


def test_preflight_supports_ini_and_json():
    ini_text = (
        "[common]\n"
        "server_addr = 127.0.0.1\n"
        "server_port = 7000\n\n"
        "[web]\n"
        "type = tcp\n"
        "local_port = 8080\n"
        "remote_port = 6000\n"
    )
    json_text = (
        '{'
        '"serverAddr":"127.0.0.1",'
        '"serverPort":7000,'
        '"proxies":[{"name":"web","type":"tcp","localPort":8080,"remotePort":6000}]'
        "}"
    )
    ini_result = preflight_config("frpc", ini_text, "")
    json_result = preflight_config("frpc", json_text, "")
    assert ini_result["ok"] is True
    assert any("INI format is supported" in msg for msg in ini_result["warnings"])
    assert json_result["ok"] is True


def test_export_and_import_zip_roundtrip():
    bundle = {
        "schema_version": 1,
        "clients": [
            {
                "id": "default",
                "name": "default",
                "frpc_path": "frpc",
                "run_args": "",
                "config_text": 'serverAddr = "127.0.0.1"\n',
                "env": {"TOKEN": "abc"},
            }
        ],
        "alert_rules": {"on_start_failure": True, "on_abnormal_exit": True, "on_restart_threshold": True, "restart_threshold": 3},
    }
    redacted = redact_sensitive(bundle)
    assert redacted["clients"][0]["env"]["TOKEN"] == "***REDACTED***"
    data = build_export_zip(bundle)
    parsed = parse_import_zip(data)
    assert parsed["schema_version"] == 1
    assert parsed["clients"][0]["id"] == "default"


def test_import_preview_conflicts():
    bundle = {"schema_version": 1, "clients": [{"id": "default", "config_text": "abc"}]}
    preview = preview_import_bundle(bundle, existing_client_ids={"default"})
    assert preview["import_client_count"] == 1
    assert preview["conflicts"] == ["default"]
