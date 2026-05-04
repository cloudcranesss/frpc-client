from __future__ import annotations

import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time
import tomllib
import zipfile
from typing import Any


SENSITIVE_KEYWORDS = ("password", "token", "secret", "apikey", "api_key", "auth", "private")
VARIABLE_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def redact_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if any(flag in lowered for flag in SENSITIVE_KEYWORDS):
                out[str(key)] = "***REDACTED***"
            else:
                out[str(key)] = redact_sensitive(value)
        return out
    if isinstance(data, list):
        return [redact_sensitive(item) for item in data]
    return data


def extract_template_variables(content: str) -> list[str]:
    found = {item.strip() for item in VARIABLE_RE.findall(content) if item.strip()}
    return sorted(found)


def build_export_zip(bundle: dict[str, Any]) -> bytes:
    payload = dict(bundle)
    payload["exported_at"] = time.time()
    payload["schema_version"] = int(bundle.get("schema_version", 1))

    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(payload, ensure_ascii=False, indent=2))
        clients = list(bundle.get("clients") or [])
        for client in clients:
            if not isinstance(client, dict):
                continue
            client_id = str(client.get("id", "")).strip()
            if not client_id:
                continue
            config_text = str(client.get("config_text", ""))
            zf.writestr(f"configs/{client_id}.toml", config_text)
    return buff.getvalue()


def parse_import_zip(content: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
        if "manifest.json" not in zf.namelist():
            raise ValueError("manifest.json missing in import package")
        raw = zf.read("manifest.json").decode("utf-8")
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid manifest payload")
    return data


def preview_import_bundle(bundle: dict[str, Any], existing_client_ids: set[str]) -> dict[str, Any]:
    clients = list(bundle.get("clients") or [])
    imported_ids: list[str] = []
    conflicts: list[str] = []
    warnings: list[str] = []
    for item in clients:
        if not isinstance(item, dict):
            continue
        client_id = str(item.get("id", "")).strip()
        if not client_id:
            warnings.append("client entry with empty id")
            continue
        imported_ids.append(client_id)
        if client_id in existing_client_ids:
            conflicts.append(client_id)
        cfg = str(item.get("config_text", "")).strip()
        if not cfg:
            warnings.append(f"{client_id}: empty config_text")
    schema_version = int(bundle.get("schema_version", 0) or 0)
    if schema_version <= 0:
        warnings.append("manifest missing schema_version")
    return {
        "schema_version": schema_version,
        "import_client_count": len(imported_ids),
        "conflicts": conflicts,
        "warnings": warnings,
        "ready": len(warnings) == 0,
    }


def preflight_config(
    frpc_path: str,
    config_text: str,
    run_args: str,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    path_value = frpc_path.strip()
    if not path_value:
        errors.append("frpc_path is empty")
    else:
        if "/" in path_value or "\\" in path_value:
            if not Path(path_value).exists():
                errors.append(f"frpc executable not found: {path_value}")
        else:
            if shutil.which(path_value) is None and shutil.which(f"{path_value}.exe") is None:
                warnings.append(f"frpc command not found in PATH: {path_value}")

    if not config_text.strip():
        errors.append("config_text is empty")
        return {"ok": False, "errors": errors, "warnings": warnings}

    try:
        payload = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"TOML parse error: {exc}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    server_addr = str(payload.get("serverAddr", "")).strip()
    if not server_addr:
        errors.append("serverAddr is required")

    server_port = payload.get("serverPort")
    if server_port is None:
        warnings.append("serverPort is missing")
    else:
        try:
            port = int(server_port)
            if port < 1 or port > 65535:
                errors.append("serverPort out of range")
        except (TypeError, ValueError):
            errors.append("serverPort must be integer")

    proxies = payload.get("proxies")
    if proxies is None:
        warnings.append("no proxies configured")
        proxies = []
    if not isinstance(proxies, list):
        errors.append("proxies must be an array")
        proxies = []

    remote_ports: dict[int, str] = {}
    for idx, proxy in enumerate(proxies):
        if not isinstance(proxy, dict):
            errors.append(f"proxies[{idx}] must be object")
            continue
        name = str(proxy.get("name", f"proxy-{idx + 1}")).strip()
        ptype = str(proxy.get("type", "tcp")).strip().lower()
        if ptype not in {"tcp", "udp", "http", "https", "stcp", "xtcp"}:
            warnings.append(f"{name}: unknown proxy type `{ptype}`")
        if "remotePort" in proxy:
            try:
                rport = int(proxy.get("remotePort"))
                if rport < 1 or rport > 65535:
                    errors.append(f"{name}: remotePort out of range")
                elif rport in remote_ports:
                    errors.append(f"{name}: remotePort conflict with {remote_ports[rport]}")
                else:
                    remote_ports[rport] = name
            except (TypeError, ValueError):
                errors.append(f"{name}: remotePort must be integer")
        elif ptype in {"tcp", "udp"}:
            warnings.append(f"{name}: remotePort missing")

    if run_args.strip() and "--config" in run_args:
        warnings.append("run_args contains --config; this may override managed config")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def system_diagnostics(data_dir: Path, events: list[dict[str, Any]], bundle: dict[str, Any]) -> dict[str, Any]:
    disk = shutil.disk_usage(data_dir)
    return {
        "generated_at": time.time(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "cwd": os.getcwd(),
        "data_dir": str(data_dir),
        "disk_total_mb": round(disk.total / (1024 * 1024), 2),
        "disk_free_mb": round(disk.free / (1024 * 1024), 2),
        "events": redact_sensitive(events),
        "bundle_summary": redact_sensitive(
            {
                "schema_version": bundle.get("schema_version"),
                "clients": [
                    {
                        "id": str(item.get("id", "")),
                        "name": str(item.get("name", "")),
                        "frpc_path": str(item.get("frpc_path", "")),
                    }
                    for item in list(bundle.get("clients") or [])
                    if isinstance(item, dict)
                ],
                "alert_rules": bundle.get("alert_rules", {}),
                "templates_count": len(list(bundle.get("templates") or [])),
            }
        ),
    }
