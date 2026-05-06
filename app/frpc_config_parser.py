from __future__ import annotations

import configparser
import json
import tomllib
from typing import Any


SUPPORTED_CONFIG_FORMATS = ("toml", "ini", "json")


def detect_config_format(config_text: str) -> str:
    text = config_text.strip()
    if not text:
        return "toml"

    first = text[0]
    if first in {"{", "["}:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return "json"
        except json.JSONDecodeError:
            pass

    try:
        tomllib.loads(text)
        return "toml"
    except tomllib.TOMLDecodeError:
        pass

    try:
        _parse_ini_payload(text)
        return "ini"
    except Exception as exc:
        raise ValueError(f"unsupported config format: {exc}") from exc


def parse_proxy_config(config_text: str) -> tuple[str, dict[str, Any]]:
    text = config_text.strip()
    if not text:
        raise ValueError("config_text is empty")

    first = text[0]
    if first in {"{", "["}:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return "json", _normalize_payload(payload)
        except json.JSONDecodeError:
            pass

    try:
        payload = tomllib.loads(text)
        return "toml", _normalize_payload(payload)
    except tomllib.TOMLDecodeError:
        pass

    try:
        payload = _parse_ini_payload(text)
    except Exception as exc:
        raise ValueError(f"unsupported config format: {exc}") from exc
    return "ini", _normalize_payload(payload)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    server_addr = str(payload.get("serverAddr", "")).strip()
    server_port = payload.get("serverPort")
    proxies = payload.get("proxies", [])
    if not isinstance(proxies, list):
        proxies = []
    normalized_proxies: list[dict[str, Any]] = []
    for idx, row in enumerate(proxies):
        if not isinstance(row, dict):
            continue
        normalized_proxies.append(
            {
                "name": str(row.get("name", f"proxy-{idx + 1}")).strip() or f"proxy-{idx + 1}",
                "type": str(row.get("type", "tcp")).strip().lower() or "tcp",
                "remotePort": row.get("remotePort"),
                "localPort": row.get("localPort"),
            }
        )
    return {
        "serverAddr": server_addr,
        "serverPort": server_port,
        "proxies": normalized_proxies,
    }


def _parse_ini_payload(config_text: str) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(config_text)

    common = _find_section(parser, "common")
    common_values = _section_map(parser, common)
    server_addr = _pick_value(common_values, "server_addr", "serverAddr", "serveraddr", default="")
    server_port_raw = _pick_value(common_values, "server_port", "serverPort", "serverport", default=None)
    try:
        server_port = int(server_port_raw) if server_port_raw not in (None, "") else None
    except (TypeError, ValueError):
        server_port = server_port_raw

    proxies: list[dict[str, Any]] = []
    for section in parser.sections():
        if section.lower() == "common":
            continue
        values = _section_map(parser, section)
        proxy_type = str(_pick_value(values, "type", "proxy_type", default="tcp")).strip().lower() or "tcp"
        remote_port_raw = _pick_value(values, "remote_port", "remotePort", "remoteport", default=None)
        local_port_raw = _pick_value(values, "local_port", "localPort", "localport", default=None)
        remote_port: Any = remote_port_raw
        local_port: Any = local_port_raw
        try:
            if remote_port_raw not in (None, ""):
                remote_port = int(remote_port_raw)
        except (TypeError, ValueError):
            remote_port = remote_port_raw
        try:
            if local_port_raw not in (None, ""):
                local_port = int(local_port_raw)
        except (TypeError, ValueError):
            local_port = local_port_raw

        proxy_name = section.split(":", 1)[-1]
        proxies.append(
            {
                "name": proxy_name,
                "type": proxy_type,
                "remotePort": remote_port,
                "localPort": local_port,
            }
        )

    return {
        "serverAddr": str(server_addr).strip(),
        "serverPort": server_port,
        "proxies": proxies,
    }


def _find_section(parser: configparser.ConfigParser, name: str) -> str | None:
    for section in parser.sections():
        if section.lower() == name.lower():
            return section
    return None


def _section_map(parser: configparser.ConfigParser, section: str | None) -> dict[str, str]:
    if section is None:
        return {}
    return {key: value for key, value in parser.items(section)}


def _pick_value(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
        for actual_key, value in mapping.items():
            if actual_key.lower() == key.lower():
                return value
    return default
