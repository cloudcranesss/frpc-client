from __future__ import annotations

import asyncio
import json
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .frpc_config_parser import detect_config_format


def default_frpc_path() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "bin/windows/frpc.exe"
    return "bin/linux/frpc"


def default_config_text() -> str:
    return (
        'serverAddr = "127.0.0.1"\n'
        "serverPort = 7000\n\n"
        "[[proxies]]\n"
        'name = "web"\n'
        'type = "tcp"\n'
        'localIP = "127.0.0.1"\n'
        "localPort = 80\n"
        "remotePort = 6000\n"
    )


@dataclass(slots=True)
class ProxyClientConfig:
    id: str
    name: str
    frpc_path: str = field(default_factory=default_frpc_path)
    run_args: str = ""
    config_text: str = field(default_factory=default_config_text)
    env: dict[str, str] = field(default_factory=dict)
    auto_start: bool = False


@dataclass(slots=True)
class ClientState:
    active_client_id: str
    clients: list[ProxyClientConfig] = field(default_factory=list)


def make_client(name: str = "新客户端", client_id: str | None = None) -> ProxyClientConfig:
    cid = client_id or uuid4().hex[:12]
    return ProxyClientConfig(id=cid, name=name)


class ConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._store_file = data_dir / "store.json"
        self._legacy_db_file = data_dir / "app.db"
        self._legacy_client_file = data_dir / "client_config.json"
        self._legacy_active_config_file = data_dir / "frpc.toml"
        self._legacy_auth_file = data_dir / "auth.json"
        self._configs_dir = data_dir / "configs"
        self._lock = asyncio.Lock()

    @property
    def legacy_auth_file(self) -> Path:
        return self._legacy_auth_file

    async def init(self) -> None:
        await asyncio.to_thread(self._data_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._configs_dir.mkdir, parents=True, exist_ok=True)
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    async def load_state(self) -> ClientState:
        async with self._lock:
            return await asyncio.to_thread(self._load_state_sync)

    async def save_state(self, state: ClientState) -> ClientState:
        normalized = self._normalize_state(state)
        async with self._lock:
            await asyncio.to_thread(self._save_state_sync, normalized)
        return normalized

    async def append_runtime_event(
        self,
        client_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        exit_type: str | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._append_runtime_event_sync,
                client_id,
                event_type,
                message,
                payload or {},
                exit_type,
            )

    async def list_runtime_events(self, client_id: str, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 1000))
        async with self._lock:
            return await asyncio.to_thread(self._list_runtime_events_sync, client_id, safe_limit)

    async def list_alert_channels(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_alert_channels_sync)

    async def create_alert_channel(
        self,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_alert_channel_sync,
                name,
                webhook_url,
                timeout_sec,
                enabled,
            )

    async def update_alert_channel(
        self,
        channel_id: int,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_alert_channel_sync,
                channel_id,
                name,
                webhook_url,
                timeout_sec,
                enabled,
            )

    async def delete_alert_channel(self, channel_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_alert_channel_sync, channel_id)

    async def get_alert_rules(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._get_alert_rules_sync)

    async def update_alert_rules(
        self,
        on_start_failure: bool,
        on_abnormal_exit: bool,
        on_restart_threshold: bool,
        restart_threshold: int,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_alert_rules_sync,
                on_start_failure,
                on_abnormal_exit,
                on_restart_threshold,
                max(1, restart_threshold),
            )

    async def get_user(self, username: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_user_sync, username)

    async def upsert_user(
        self,
        username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._upsert_user_sync,
                username,
                password_salt,
                password_hash,
                max(100_000, int(iterations)),
            )

    async def count_users(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._count_users_sync)

    async def create_session(self, token: str, username: str, expires_at: float) -> None:
        async with self._lock:
            await asyncio.to_thread(self._create_session_sync, token, username, float(expires_at))

    async def get_session_username(self, token: str | None) -> str | None:
        if not token:
            return None
        async with self._lock:
            return await asyncio.to_thread(self._get_session_username_sync, token, time.time())

    async def delete_session(self, token: str | None) -> None:
        if not token:
            return
        async with self._lock:
            await asyncio.to_thread(self._delete_session_sync, token)

    async def delete_sessions_by_username(self, username: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_sessions_by_username_sync, username)

    async def replace_user(
        self,
        current_username: str,
        new_username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> str:
        async with self._lock:
            return await asyncio.to_thread(
                self._replace_user_sync,
                current_username,
                new_username,
                password_salt,
                password_hash,
                max(100_000, int(iterations)),
            )

    async def dump_bundle(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._dump_bundle_sync)

    async def apply_bundle(self, bundle: dict[str, Any], mode: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._apply_bundle_sync, bundle, mode)

    def frpc_config_file(self, client_id: str, config_text: str | None = None) -> Path:
        safe = self._safe_filename(client_id)
        ext = "toml"
        if config_text is not None:
            try:
                fmt = detect_config_format(config_text)
                ext = "json" if fmt == "json" else ("ini" if fmt == "ini" else "toml")
            except ValueError:
                ext = "toml"
        return self._configs_dir / f"{safe}.{ext}"

    def _init_sync(self) -> None:
        if not self._store_file.exists():
            payload = self._build_initial_payload()
            self._write_store(payload)
        payload = self._normalize_store_payload(self._read_store())
        self._write_store(payload)
        self._sync_config_files(payload)

    def _load_state_sync(self) -> ClientState:
        payload = self._read_store()
        return self._state_from_payload(payload.get("state", {}))

    def _save_state_sync(self, state: ClientState) -> None:
        payload = self._read_store()
        payload["state"] = self._serialize_state(state)
        self._write_store(payload)
        self._sync_config_files(payload)

    def _append_runtime_event_sync(
        self,
        client_id: str,
        event_type: str,
        message: str,
        payload_data: dict[str, Any],
        exit_type: str | None,
    ) -> None:
        payload = self._read_store()
        counters = payload.setdefault("counters", {})
        next_id = int(counters.get("runtime_event_id", 0)) + 1
        counters["runtime_event_id"] = next_id
        events = payload.setdefault("runtime_events", [])
        events.append(
            {
                "id": next_id,
                "client_id": client_id,
                "event_type": event_type,
                "exit_type": exit_type,
                "message": message,
                "payload": payload_data,
                "created_at": time.time(),
            }
        )
        if len(events) > 10000:
            payload["runtime_events"] = events[-10000:]
        self._write_store(payload)

    def _list_runtime_events_sync(self, client_id: str, limit: int) -> list[dict[str, Any]]:
        payload = self._read_store()
        events = payload.get("runtime_events", [])
        rows = [item for item in events if str(item.get("client_id", "")) == client_id]
        rows = rows[-limit:]
        return [
            {
                "id": int(item.get("id", 0)),
                "client_id": str(item.get("client_id", "")),
                "event_type": str(item.get("event_type", "")),
                "exit_type": (str(item.get("exit_type")) if item.get("exit_type") else None),
                "message": str(item.get("message", "")),
                "payload": (item.get("payload") if isinstance(item.get("payload"), dict) else {}),
                "created_at": float(item.get("created_at", 0)),
            }
            for item in rows
        ]

    def _list_alert_channels_sync(self) -> list[dict[str, Any]]:
        payload = self._read_store()
        rows = payload.get("alert_channels", [])
        return [
            {
                "id": int(item.get("id", 0)),
                "name": str(item.get("name", "")),
                "webhook_url": str(item.get("webhook_url", "")),
                "timeout_sec": int(item.get("timeout_sec", 5)),
                "enabled": bool(item.get("enabled", True)),
                "created_at": float(item.get("created_at", 0)),
                "updated_at": float(item.get("updated_at", 0)),
            }
            for item in rows
        ]

    def _create_alert_channel_sync(
        self,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any]:
        payload = self._read_store()
        counters = payload.setdefault("counters", {})
        next_id = int(counters.get("alert_channel_id", 0)) + 1
        counters["alert_channel_id"] = next_id
        now = time.time()
        row = {
            "id": next_id,
            "name": name,
            "webhook_url": webhook_url,
            "timeout_sec": max(1, int(timeout_sec)),
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
        }
        payload.setdefault("alert_channels", []).append(row)
        self._write_store(payload)
        return row

    def _update_alert_channel_sync(
        self,
        channel_id: int,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any] | None:
        payload = self._read_store()
        now = time.time()
        for item in payload.setdefault("alert_channels", []):
            if int(item.get("id", 0)) != channel_id:
                continue
            item["name"] = name
            item["webhook_url"] = webhook_url
            item["timeout_sec"] = max(1, int(timeout_sec))
            item["enabled"] = bool(enabled)
            item["updated_at"] = now
            self._write_store(payload)
            return {
                "id": int(item["id"]),
                "name": str(item["name"]),
                "webhook_url": str(item["webhook_url"]),
                "timeout_sec": int(item["timeout_sec"]),
                "enabled": bool(item["enabled"]),
                "created_at": float(item["created_at"]),
                "updated_at": float(item["updated_at"]),
            }
        return None

    def _delete_alert_channel_sync(self, channel_id: int) -> bool:
        payload = self._read_store()
        rows = payload.setdefault("alert_channels", [])
        kept = [item for item in rows if int(item.get("id", 0)) != channel_id]
        if len(kept) == len(rows):
            return False
        payload["alert_channels"] = kept
        self._write_store(payload)
        return True

    def _get_alert_rules_sync(self) -> dict[str, Any]:
        payload = self._read_store()
        rules = payload.get("alert_rules", {})
        return {
            "on_start_failure": bool(rules.get("on_start_failure", True)),
            "on_abnormal_exit": bool(rules.get("on_abnormal_exit", True)),
            "on_restart_threshold": bool(rules.get("on_restart_threshold", True)),
            "restart_threshold": max(1, int(rules.get("restart_threshold", 3))),
        }

    def _update_alert_rules_sync(
        self,
        on_start_failure: bool,
        on_abnormal_exit: bool,
        on_restart_threshold: bool,
        restart_threshold: int,
    ) -> dict[str, Any]:
        payload = self._read_store()
        payload["alert_rules"] = {
            "on_start_failure": bool(on_start_failure),
            "on_abnormal_exit": bool(on_abnormal_exit),
            "on_restart_threshold": bool(on_restart_threshold),
            "restart_threshold": max(1, int(restart_threshold)),
        }
        self._write_store(payload)
        return self._get_alert_rules_sync()

    def _get_user_sync(self, username: str) -> dict[str, Any] | None:
        payload = self._read_store()
        for row in payload.get("users", []):
            if str(row.get("username", "")) != username:
                continue
            return {
                "username": str(row.get("username", "")),
                "password_salt": str(row.get("password_salt", "")),
                "password_hash": str(row.get("password_hash", "")),
                "iterations": int(row.get("iterations", 210000)),
            }
        return None

    def _upsert_user_sync(
        self,
        username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> None:
        payload = self._read_store()
        users = payload.setdefault("users", [])
        now = time.time()
        for row in users:
            if str(row.get("username", "")) != username:
                continue
            row["password_salt"] = password_salt
            row["password_hash"] = password_hash
            row["iterations"] = max(100_000, int(iterations))
            row["updated_at"] = now
            self._write_store(payload)
            return
        users.append(
            {
                "username": username,
                "password_salt": password_salt,
                "password_hash": password_hash,
                "iterations": max(100_000, int(iterations)),
                "created_at": now,
                "updated_at": now,
            }
        )
        self._write_store(payload)

    def _count_users_sync(self) -> int:
        payload = self._read_store()
        return len(payload.get("users", []))

    def _create_session_sync(self, token: str, username: str, expires_at: float) -> None:
        payload = self._read_store()
        payload.setdefault("sessions", {})[token] = {
            "username": username,
            "expires_at": float(expires_at),
            "created_at": time.time(),
        }
        self._write_store(payload)

    def _get_session_username_sync(self, token: str, now: float) -> str | None:
        payload = self._read_store()
        sessions = payload.setdefault("sessions", {})
        expired = [key for key, row in sessions.items() if float(row.get("expires_at", 0)) <= now]
        for key in expired:
            sessions.pop(key, None)
        if expired:
            self._write_store(payload)

        row = sessions.get(token)
        if not row:
            return None
        if float(row.get("expires_at", 0)) <= now:
            sessions.pop(token, None)
            self._write_store(payload)
            return None
        return str(row.get("username", ""))

    def _delete_session_sync(self, token: str) -> None:
        payload = self._read_store()
        sessions = payload.setdefault("sessions", {})
        if token in sessions:
            sessions.pop(token, None)
            self._write_store(payload)

    def _delete_sessions_by_username_sync(self, username: str) -> None:
        payload = self._read_store()
        sessions = payload.setdefault("sessions", {})
        kept: dict[str, dict[str, Any]] = {}
        for token, row in sessions.items():
            if str(row.get("username", "")) == username:
                continue
            kept[token] = row
        if len(kept) != len(sessions):
            payload["sessions"] = kept
            self._write_store(payload)

    def _replace_user_sync(
        self,
        current_username: str,
        new_username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> str:
        payload = self._read_store()
        users = payload.setdefault("users", [])
        current_idx: int | None = None
        for idx, row in enumerate(users):
            name = str(row.get("username", ""))
            if name == current_username:
                current_idx = idx
            if name == new_username and name != current_username:
                return "username_exists"
        if current_idx is None:
            return "user_not_found"

        now = time.time()
        users[current_idx] = {
            "username": new_username,
            "password_salt": password_salt,
            "password_hash": password_hash,
            "iterations": max(100_000, int(iterations)),
            "created_at": float(users[current_idx].get("created_at", now)),
            "updated_at": now,
        }
        self._write_store(payload)
        return "ok"

    def _dump_bundle_sync(self) -> dict[str, Any]:
        payload = self._read_store()
        state = self._state_from_payload(payload.get("state", {}))
        clients = [
            {
                "id": item.id,
                "name": item.name,
                "frpc_path": item.frpc_path,
                "run_args": item.run_args,
                "config_text": item.config_text,
                "env": item.env,
                "auto_start": bool(item.auto_start),
            }
            for item in state.clients
        ]
        return {
            "schema_version": 1,
            "active_client_id": state.active_client_id,
            "clients": clients,
            "alert_channels": self._list_alert_channels_sync(),
            "alert_rules": self._get_alert_rules_sync(),
            "exported_at": time.time(),
        }

    def _apply_bundle_sync(self, bundle: dict[str, Any], mode: str) -> None:
        if mode not in {"overwrite", "merge"}:
            raise ValueError(f"invalid mode: {mode}")
        payload = self._read_store()
        imported = self._normalize_state(
            ClientState(
                active_client_id=str(bundle.get("active_client_id", "")),
                clients=[
                    ProxyClientConfig(
                        id=str(item.get("id", "")),
                        name=str(item.get("name", "")),
                        frpc_path=str(item.get("frpc_path", default_frpc_path())),
                        run_args=str(item.get("run_args", "")),
                        config_text=str(item.get("config_text", default_config_text())),
                        env=self._safe_env(item.get("env", {})),
                        auto_start=bool(item.get("auto_start", False)),
                    )
                    for item in list(bundle.get("clients") or [])
                    if isinstance(item, dict)
                ],
            )
        )

        current = self._state_from_payload(payload.get("state", {}))
        if mode == "overwrite":
            next_state = imported
            payload["alert_channels"] = []
            payload.setdefault("counters", {})["alert_channel_id"] = 0
        else:
            existing = {item.id for item in current.clients}
            merged = list(current.clients)
            for item in imported.clients:
                if item.id in existing:
                    continue
                merged.append(item)
            active = current.active_client_id or imported.active_client_id
            next_state = self._normalize_state(ClientState(active_client_id=active, clients=merged))

        payload["state"] = self._serialize_state(next_state)
        for row in list(bundle.get("alert_channels") or []):
            if not isinstance(row, dict):
                continue
            self._create_alert_channel_on_payload(
                payload,
                name=str(row.get("name", "")),
                webhook_url=str(row.get("webhook_url", "")),
                timeout_sec=int(row.get("timeout_sec", 5)),
                enabled=bool(row.get("enabled", True)),
            )

        rules = bundle.get("alert_rules")
        if isinstance(rules, dict):
            payload["alert_rules"] = {
                "on_start_failure": bool(rules.get("on_start_failure", True)),
                "on_abnormal_exit": bool(rules.get("on_abnormal_exit", True)),
                "on_restart_threshold": bool(rules.get("on_restart_threshold", True)),
                "restart_threshold": max(1, int(rules.get("restart_threshold", 3))),
            }

        self._write_store(payload)
        self._sync_config_files(payload)

    def _create_alert_channel_on_payload(
        self,
        payload: dict[str, Any],
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> None:
        counters = payload.setdefault("counters", {})
        next_id = int(counters.get("alert_channel_id", 0)) + 1
        counters["alert_channel_id"] = next_id
        now = time.time()
        payload.setdefault("alert_channels", []).append(
            {
                "id": next_id,
                "name": name,
                "webhook_url": webhook_url,
                "timeout_sec": max(1, int(timeout_sec)),
                "enabled": bool(enabled),
                "created_at": now,
                "updated_at": now,
            }
        )

    def _build_initial_payload(self) -> dict[str, Any]:
        state = self._migrate_legacy_client_state()
        payload = {
            "schema_version": 1,
            "state": self._serialize_state(state),
            "runtime_events": [],
            "alert_channels": [],
            "alert_rules": {
                "on_start_failure": True,
                "on_abnormal_exit": True,
                "on_restart_threshold": True,
                "restart_threshold": 3,
            },
            "users": [],
            "sessions": {},
            "counters": {"runtime_event_id": 0, "alert_channel_id": 0},
        }
        if self._legacy_db_file.exists():
            self._backup_file(self._legacy_db_file)
        return payload

    def _migrate_legacy_client_state(self) -> ClientState:
        if self._legacy_client_file.exists():
            payload = self._read_json_file(self._legacy_client_file)
            state = self._state_from_legacy_payload(payload)
            state = self._normalize_state(state)
            self._backup_file(self._legacy_client_file)
            self._backup_file(self._legacy_active_config_file)
            return state
        default_client = make_client(name="默认客户端", client_id="default")
        return ClientState(active_client_id=default_client.id, clients=[default_client])

    def _state_from_legacy_payload(self, payload: dict[str, Any]) -> ClientState:
        if "clients" not in payload:
            single = self._legacy_single_client(payload)
            return ClientState(active_client_id=single.id, clients=[single])
        clients: list[ProxyClientConfig] = []
        raw_clients = payload.get("clients")
        if isinstance(raw_clients, list):
            for index, item in enumerate(raw_clients):
                if isinstance(item, dict):
                    clients.append(self._legacy_client_item(item, index))
        active_id = str(payload.get("active_client_id", "")).strip()
        return ClientState(active_client_id=active_id, clients=clients)

    def _legacy_single_client(self, payload: dict[str, Any]) -> ProxyClientConfig:
        raw_frpc_path = str(payload.get("frpc_path", default_frpc_path()))
        if raw_frpc_path in {"bin/frpc.exe", "bin/frpc"}:
            raw_frpc_path = default_frpc_path()
        return ProxyClientConfig(
            id="default",
            name=str(payload.get("name", "默认客户端")).strip() or "默认客户端",
            frpc_path=raw_frpc_path,
            run_args=str(payload.get("run_args", "")),
            config_text=str(payload.get("config_text", default_config_text())),
            env=self._safe_env(payload.get("env", {})),
            auto_start=bool(payload.get("auto_start", False)),
        )

    def _legacy_client_item(self, payload: dict[str, Any], index: int) -> ProxyClientConfig:
        raw_id = str(payload.get("id", "")).strip() or f"client-{index + 1}"
        client_id = self._safe_filename(raw_id) or uuid4().hex[:12]
        raw_frpc_path = str(payload.get("frpc_path", default_frpc_path()))
        if raw_frpc_path in {"bin/frpc.exe", "bin/frpc"}:
            raw_frpc_path = default_frpc_path()
        return ProxyClientConfig(
            id=client_id,
            name=str(payload.get("name", f"客户端 {index + 1}")).strip() or f"客户端 {index + 1}",
            frpc_path=raw_frpc_path,
            run_args=str(payload.get("run_args", "")),
            config_text=str(payload.get("config_text", default_config_text())),
            env=self._safe_env(payload.get("env", {})),
            auto_start=bool(payload.get("auto_start", False)),
        )

    def _normalize_state(self, state: ClientState) -> ClientState:
        dedup: list[ProxyClientConfig] = []
        used_ids: set[str] = set()
        for idx, client in enumerate(state.clients):
            cid = self._safe_filename(client.id)
            if not cid:
                cid = f"client-{idx + 1}"
            if cid in used_ids:
                cid = f"{cid}-{uuid4().hex[:4]}"
            used_ids.add(cid)
            dedup.append(
                ProxyClientConfig(
                    id=cid,
                    name=client.name.strip() or f"客户端 {idx + 1}",
                    frpc_path=(client.frpc_path.strip() or default_frpc_path()),
                    run_args=client.run_args.strip(),
                    config_text=client.config_text or default_config_text(),
                    env=self._safe_env(client.env),
                    auto_start=bool(client.auto_start),
                )
            )
        if not dedup:
            dedup = [make_client(name="默认客户端", client_id="default")]
        active_id = state.active_client_id.strip()
        if not active_id or not any(item.id == active_id for item in dedup):
            active_id = dedup[0].id
        return ClientState(active_client_id=active_id, clients=dedup)

    def _state_from_payload(self, payload: dict[str, Any]) -> ClientState:
        clients: list[ProxyClientConfig] = []
        raw = payload.get("clients")
        if isinstance(raw, list):
            for idx, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                cid = self._safe_filename(str(item.get("id", ""))) or f"client-{idx + 1}"
                clients.append(
                    ProxyClientConfig(
                        id=cid,
                        name=str(item.get("name", f"客户端 {idx + 1}")),
                        frpc_path=str(item.get("frpc_path", default_frpc_path())),
                        run_args=str(item.get("run_args", "")),
                        config_text=str(item.get("config_text", default_config_text())),
                        env=self._safe_env(item.get("env", {})),
                        auto_start=bool(item.get("auto_start", False)),
                    )
                )
        active_id = str(payload.get("active_client_id", "")).strip()
        return self._normalize_state(ClientState(active_client_id=active_id, clients=clients))

    def _serialize_state(self, state: ClientState) -> dict[str, Any]:
        normalized = self._normalize_state(state)
        return {
            "active_client_id": normalized.active_client_id,
            "clients": [
                {
                    "id": item.id,
                    "name": item.name,
                    "frpc_path": item.frpc_path,
                    "run_args": item.run_args,
                    "config_text": item.config_text,
                    "env": item.env,
                    "auto_start": bool(item.auto_start),
                }
                for item in normalized.clients
            ],
        }

    def _normalize_store_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._state_from_payload(payload.get("state", {}))
        rules = payload.get("alert_rules", {})
        counters = payload.get("counters", {})
        runtime_events = payload.get("runtime_events", [])
        channels = payload.get("alert_channels", [])
        users = payload.get("users", [])
        sessions = payload.get("sessions", {})

        clean_events: list[dict[str, Any]] = []
        max_event_id = 0
        if isinstance(runtime_events, list):
            for row in runtime_events:
                if not isinstance(row, dict):
                    continue
                eid = int(row.get("id", 0))
                max_event_id = max(max_event_id, eid)
                pd = row.get("payload")
                if not isinstance(pd, dict):
                    pd = {}
                clean_events.append(
                    {
                        "id": eid,
                        "client_id": str(row.get("client_id", "")),
                        "event_type": str(row.get("event_type", "")),
                        "exit_type": (str(row.get("exit_type")) if row.get("exit_type") else None),
                        "message": str(row.get("message", "")),
                        "payload": pd,
                        "created_at": float(row.get("created_at", time.time())),
                    }
                )
        clean_events.sort(key=lambda item: int(item["id"]))

        clean_channels: list[dict[str, Any]] = []
        max_channel_id = 0
        if isinstance(channels, list):
            for row in channels:
                if not isinstance(row, dict):
                    continue
                cid = int(row.get("id", 0))
                max_channel_id = max(max_channel_id, cid)
                clean_channels.append(
                    {
                        "id": cid,
                        "name": str(row.get("name", "")),
                        "webhook_url": str(row.get("webhook_url", "")),
                        "timeout_sec": max(1, int(row.get("timeout_sec", 5))),
                        "enabled": bool(row.get("enabled", True)),
                        "created_at": float(row.get("created_at", time.time())),
                        "updated_at": float(row.get("updated_at", time.time())),
                    }
                )

        clean_users: list[dict[str, Any]] = []
        if isinstance(users, list):
            for row in users:
                if not isinstance(row, dict):
                    continue
                username = str(row.get("username", "")).strip()
                if not username:
                    continue
                clean_users.append(
                    {
                        "username": username,
                        "password_salt": str(row.get("password_salt", "")),
                        "password_hash": str(row.get("password_hash", "")),
                        "iterations": max(100000, int(row.get("iterations", 210000))),
                        "created_at": float(row.get("created_at", time.time())),
                        "updated_at": float(row.get("updated_at", time.time())),
                    }
                )

        clean_sessions: dict[str, dict[str, Any]] = {}
        if isinstance(sessions, dict):
            for token, row in sessions.items():
                if not isinstance(row, dict):
                    continue
                clean_sessions[str(token)] = {
                    "username": str(row.get("username", "")),
                    "expires_at": float(row.get("expires_at", 0)),
                    "created_at": float(row.get("created_at", time.time())),
                }

        return {
            "schema_version": 1,
            "state": self._serialize_state(state),
            "runtime_events": clean_events[-10000:],
            "alert_channels": clean_channels,
            "alert_rules": {
                "on_start_failure": bool(rules.get("on_start_failure", True)),
                "on_abnormal_exit": bool(rules.get("on_abnormal_exit", True)),
                "on_restart_threshold": bool(rules.get("on_restart_threshold", True)),
                "restart_threshold": max(1, int(rules.get("restart_threshold", 3))),
            },
            "users": clean_users,
            "sessions": clean_sessions,
            "counters": {
                "runtime_event_id": max(max_event_id, int(counters.get("runtime_event_id", 0))),
                "alert_channel_id": max(max_channel_id, int(counters.get("alert_channel_id", 0))),
            },
        }

    def _sync_config_files(self, payload: dict[str, Any]) -> None:
        state = self._state_from_payload(payload.get("state", {}))
        used: set[Path] = set()
        for client in state.clients:
            path = self.frpc_config_file(client.id, client.config_text)
            path.write_text(client.config_text, encoding="utf-8")
            used.add(path.resolve())
        for client in state.clients:
            prefix = f"{self._safe_filename(client.id)}."
            for item in self._configs_dir.glob(f"{prefix}*"):
                if item.resolve() not in used:
                    item.unlink(missing_ok=True)

    def _read_store(self) -> dict[str, Any]:
        if not self._store_file.exists():
            return self._build_initial_payload()
        try:
            raw = self._store_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                payload = {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        return self._normalize_store_payload(payload)

    def _write_store(self, payload: dict[str, Any]) -> None:
        normalized = self._normalize_store_payload(payload)
        tmp = self._store_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._store_file)

    def _safe_env(self, maybe_env: Any) -> dict[str, str]:
        if not isinstance(maybe_env, dict):
            return {}
        return {str(key): str(value) for key, value in maybe_env.items()}

    def _safe_filename(self, value: str) -> str:
        raw = str(value).strip()
        return "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def _backup_file(self, path: Path) -> None:
        if not path.exists():
            return
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            return
        path.replace(bak)
