from __future__ import annotations

import asyncio
import json
import platform
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


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
        self._db_file = data_dir / "app.db"
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

    def frpc_config_file(self, client_id: str) -> Path:
        safe = self._safe_filename(client_id)
        return self._configs_dir / f"{safe}.toml"

    def _init_sync(self) -> None:
        conn = self._connect()
        try:
            self._create_tables(conn)
            self._migrate_legacy_clients_if_needed(conn)
            self._ensure_alert_rule_defaults(conn)
            self._ensure_default_client(conn)
            self._sync_config_files_from_db(conn)
        finally:
            conn.close()

    def _load_state_sync(self) -> ClientState:
        conn = self._connect()
        try:
            return self._load_state_from_db(conn)
        finally:
            conn.close()

    def _save_state_sync(self, state: ClientState) -> None:
        conn = self._connect()
        try:
            now = time.time()
            with conn:
                conn.execute("DELETE FROM clients")
                conn.execute("DELETE FROM client_env")
                for client in state.clients:
                    conn.execute(
                        """
                        INSERT INTO clients (
                            id, name, frpc_path, run_args, config_text, is_active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            client.id,
                            client.name,
                            client.frpc_path,
                            client.run_args,
                            client.config_text,
                            1 if client.id == state.active_client_id else 0,
                            now,
                            now,
                        ),
                    )
                    for key, value in client.env.items():
                        conn.execute(
                            "INSERT INTO client_env (client_id, env_key, env_value) VALUES (?, ?, ?)",
                            (client.id, key, value),
                        )
            self._sync_config_files_from_db(conn)
        finally:
            conn.close()

    def _append_runtime_event_sync(
        self,
        client_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        exit_type: str | None,
    ) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO runtime_events (
                        client_id, event_type, exit_type, message, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_id,
                        event_type,
                        exit_type,
                        message,
                        json.dumps(payload, ensure_ascii=False),
                        time.time(),
                    ),
                )
        finally:
            conn.close()

    def _list_runtime_events_sync(self, client_id: str, limit: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, client_id, event_type, exit_type, message, payload_json, created_at
                FROM runtime_events
                WHERE client_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (client_id, limit),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                payload_raw = row["payload_json"] or "{}"
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    payload = {}
                items.append(
                    {
                        "id": int(row["id"]),
                        "client_id": str(row["client_id"]),
                        "event_type": str(row["event_type"]),
                        "exit_type": str(row["exit_type"]) if row["exit_type"] else None,
                        "message": str(row["message"]),
                        "payload": payload,
                        "created_at": float(row["created_at"]),
                    }
                )
            items.reverse()
            return items
        finally:
            conn.close()

    def _list_alert_channels_sync(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, name, webhook_url, timeout_sec, enabled, created_at, updated_at
                FROM alert_channels
                ORDER BY id ASC
                """
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "name": str(row["name"]),
                    "webhook_url": str(row["webhook_url"]),
                    "timeout_sec": int(row["timeout_sec"]),
                    "enabled": bool(row["enabled"]),
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _create_alert_channel_sync(
        self,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any]:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO alert_channels (name, webhook_url, timeout_sec, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (name, webhook_url, max(1, timeout_sec), 1 if enabled else 0, now, now),
                )
                channel_id = int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT id, name, webhook_url, timeout_sec, enabled, created_at, updated_at
                FROM alert_channels WHERE id = ?
                """,
                (channel_id,),
            ).fetchone()
            assert row is not None
            return {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "webhook_url": str(row["webhook_url"]),
                "timeout_sec": int(row["timeout_sec"]),
                "enabled": bool(row["enabled"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
        finally:
            conn.close()

    def _update_alert_channel_sync(
        self,
        channel_id: int,
        name: str,
        webhook_url: str,
        timeout_sec: int,
        enabled: bool,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE alert_channels
                    SET name = ?, webhook_url = ?, timeout_sec = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, webhook_url, max(1, timeout_sec), 1 if enabled else 0, now, channel_id),
                )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                """
                SELECT id, name, webhook_url, timeout_sec, enabled, created_at, updated_at
                FROM alert_channels WHERE id = ?
                """,
                (channel_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "webhook_url": str(row["webhook_url"]),
                "timeout_sec": int(row["timeout_sec"]),
                "enabled": bool(row["enabled"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
        finally:
            conn.close()

    def _delete_alert_channel_sync(self, channel_id: int) -> bool:
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM alert_channels WHERE id = ?", (channel_id,))
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _get_alert_rules_sync(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT rule_key, enabled, threshold FROM alert_rules ORDER BY rule_key ASC"
            ).fetchall()
            mapped: dict[str, dict[str, Any]] = {
                str(row["rule_key"]): {
                    "enabled": bool(row["enabled"]),
                    "threshold": int(row["threshold"]),
                }
                for row in rows
            }
            return {
                "on_start_failure": mapped.get("start_failure", {}).get("enabled", True),
                "on_abnormal_exit": mapped.get("abnormal_exit", {}).get("enabled", True),
                "on_restart_threshold": mapped.get("restart_threshold", {}).get("enabled", True),
                "restart_threshold": mapped.get("restart_threshold", {}).get("threshold", 3),
            }
        finally:
            conn.close()

    def _update_alert_rules_sync(
        self,
        on_start_failure: bool,
        on_abnormal_exit: bool,
        on_restart_threshold: bool,
        restart_threshold: int,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = ?",
                    (1 if on_start_failure else 0, 0, "start_failure"),
                )
                conn.execute(
                    "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = ?",
                    (1 if on_abnormal_exit else 0, 0, "abnormal_exit"),
                )
                conn.execute(
                    "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = ?",
                    (1 if on_restart_threshold else 0, max(1, restart_threshold), "restart_threshold"),
                )
            return self._get_alert_rules_sync()
        finally:
            conn.close()

    def _get_user_sync(self, username: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT username, password_salt, password_hash, iterations
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()
            if row is None:
                return None
            return {
                "username": str(row["username"]),
                "password_salt": str(row["password_salt"]),
                "password_hash": str(row["password_hash"]),
                "iterations": int(row["iterations"]),
            }
        finally:
            conn.close()

    def _upsert_user_sync(
        self,
        username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> None:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO users (username, password_salt, password_hash, iterations, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        password_salt = excluded.password_salt,
                        password_hash = excluded.password_hash,
                        iterations = excluded.iterations,
                        updated_at = excluded.updated_at
                    """,
                    (username, password_salt, password_hash, iterations, now, now),
                )
        finally:
            conn.close()

    def _count_users_sync(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            return int(row["n"] if row else 0)
        finally:
            conn.close()

    def _create_session_sync(self, token: str, username: str, expires_at: float) -> None:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (token, username, expires_at, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (token, username, expires_at, now),
                )
                conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        finally:
            conn.close()

    def _get_session_username_sync(self, token: str, now: float) -> str | None:
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    """
                    SELECT username, expires_at
                    FROM sessions
                    WHERE token = ?
                    """,
                    (token,),
                ).fetchone()
                if row is None:
                    return None
                if float(row["expires_at"]) < now:
                    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                    return None
                return str(row["username"])
        finally:
            conn.close()

    def _delete_session_sync(self, token: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        finally:
            conn.close()

    def _delete_sessions_by_username_sync(self, username: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        finally:
            conn.close()

    def _replace_user_sync(
        self,
        current_username: str,
        new_username: str,
        password_salt: str,
        password_hash: str,
        iterations: int,
    ) -> str:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                current_row = conn.execute(
                    """
                    SELECT username, created_at
                    FROM users
                    WHERE username = ?
                    """,
                    (current_username,),
                ).fetchone()
                if current_row is None:
                    return "not_found"

                if new_username != current_username:
                    conflict = conn.execute(
                        "SELECT 1 FROM users WHERE username = ?",
                        (new_username,),
                    ).fetchone()
                    if conflict is not None:
                        return "username_exists"

                    conn.execute(
                        """
                        INSERT INTO users (username, password_salt, password_hash, iterations, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_username,
                            password_salt,
                            password_hash,
                            iterations,
                            float(current_row["created_at"]),
                            now,
                        ),
                    )
                    conn.execute("DELETE FROM users WHERE username = ?", (current_username,))
                    return "ok"

                conn.execute(
                    """
                    UPDATE users
                    SET password_salt = ?, password_hash = ?, iterations = ?, updated_at = ?
                    WHERE username = ?
                    """,
                    (
                        password_salt,
                        password_hash,
                        iterations,
                        now,
                        current_username,
                    ),
                )
                return "ok"
        finally:
            conn.close()

    def _load_state_from_db(self, conn: sqlite3.Connection) -> ClientState:
        rows = conn.execute(
            """
            SELECT id, name, frpc_path, run_args, config_text, is_active
            FROM clients
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        clients: list[ProxyClientConfig] = []
        active_id = ""
        for row in rows:
            cid = str(row["id"])
            env_rows = conn.execute(
                "SELECT env_key, env_value FROM client_env WHERE client_id = ? ORDER BY env_key ASC",
                (cid,),
            ).fetchall()
            env = {str(item["env_key"]): str(item["env_value"]) for item in env_rows}
            clients.append(
                ProxyClientConfig(
                    id=cid,
                    name=str(row["name"]),
                    frpc_path=str(row["frpc_path"]),
                    run_args=str(row["run_args"]),
                    config_text=str(row["config_text"]),
                    env=env,
                )
            )
            if bool(row["is_active"]):
                active_id = cid

        state = ClientState(active_client_id=active_id, clients=clients)
        return self._normalize_state(state)

    def _sync_config_files_from_db(self, conn: sqlite3.Connection) -> None:
        state = self._load_state_from_db(conn)
        expected_names: set[str] = set()
        for client in state.clients:
            cfg_path = self.frpc_config_file(client.id)
            cfg_path.write_text(client.config_text, encoding="utf-8")
            expected_names.add(cfg_path.name)

        for stale in self._configs_dir.glob("*.toml"):
            if stale.name not in expected_names:
                stale.unlink(missing_ok=True)

        active = next((item for item in state.clients if item.id == state.active_client_id), None)
        if active is not None:
            self._legacy_active_config_file.write_text(active.config_text, encoding="utf-8")

    def _migrate_legacy_clients_if_needed(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        has_clients = bool(row and int(row["n"]) > 0)
        if has_clients:
            return

        payload = self._read_json_file(self._legacy_client_file)
        if payload:
            state = self._state_from_legacy_payload(payload)
            state = self._normalize_state(state)
            now = time.time()
            with conn:
                for client in state.clients:
                    conn.execute(
                        """
                        INSERT INTO clients (id, name, frpc_path, run_args, config_text, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            client.id,
                            client.name,
                            client.frpc_path,
                            client.run_args,
                            client.config_text,
                            1 if client.id == state.active_client_id else 0,
                            now,
                            now,
                        ),
                    )
                    for key, value in client.env.items():
                        conn.execute(
                            "INSERT INTO client_env (client_id, env_key, env_value) VALUES (?, ?, ?)",
                            (client.id, key, value),
                        )
            self._backup_file(self._legacy_client_file)
            self._backup_file(self._legacy_active_config_file)
            return

        default_client = make_client(name="默认客户端", client_id="default")
        now = time.time()
        with conn:
            conn.execute(
                """
                INSERT INTO clients (id, name, frpc_path, run_args, config_text, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    default_client.id,
                    default_client.name,
                    default_client.frpc_path,
                    default_client.run_args,
                    default_client.config_text,
                    1,
                    now,
                    now,
                ),
            )

    def _ensure_default_client(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        if row and int(row["n"]) > 0:
            return
        default_client = make_client(name="默认客户端", client_id="default")
        now = time.time()
        with conn:
            conn.execute(
                """
                INSERT INTO clients (id, name, frpc_path, run_args, config_text, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    default_client.id,
                    default_client.name,
                    default_client.frpc_path,
                    default_client.run_args,
                    default_client.config_text,
                    1,
                    now,
                    now,
                ),
            )

    def _ensure_alert_rule_defaults(self, conn: sqlite3.Connection) -> None:
        defaults = [
            ("start_failure", 1, 0),
            ("abnormal_exit", 1, 0),
            ("restart_threshold", 1, 3),
        ]
        with conn:
            for rule_key, enabled, threshold in defaults:
                conn.execute(
                    """
                    INSERT INTO alert_rules (rule_key, enabled, threshold)
                    VALUES (?, ?, ?)
                    ON CONFLICT(rule_key) DO NOTHING
                    """,
                    (rule_key, enabled, threshold),
                )

    def _state_from_legacy_payload(self, payload: dict[str, Any]) -> ClientState:
        if "clients" not in payload:
            single = self._legacy_single_client(payload)
            return ClientState(active_client_id=single.id, clients=[single])

        raw_clients = payload.get("clients")
        clients: list[ProxyClientConfig] = []
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
                )
            )

        if not dedup:
            dedup = [make_client(name="默认客户端", client_id="default")]

        active_id = state.active_client_id.strip()
        if not active_id or not any(item.id == active_id for item in dedup):
            active_id = dedup[0].id

        return ClientState(active_client_id=active_id, clients=dedup)

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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_file)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    iterations INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    frpc_path TEXT NOT NULL,
                    run_args TEXT NOT NULL,
                    config_text TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(is_active);

                CREATE TABLE IF NOT EXISTS client_env (
                    client_id TEXT NOT NULL,
                    env_key TEXT NOT NULL,
                    env_value TEXT NOT NULL,
                    PRIMARY KEY (client_id, env_key),
                    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    exit_type TEXT,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_events_client_id ON runtime_events(client_id, id DESC);

                CREATE TABLE IF NOT EXISTS alert_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    timeout_sec INTEGER NOT NULL DEFAULT 5,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_rules (
                    rule_key TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    threshold INTEGER NOT NULL DEFAULT 0
                );

                DROP TABLE IF EXISTS config_snapshots;
                DROP TABLE IF EXISTS config_templates;
                DROP TABLE IF EXISTS audit_logs;
                DROP TABLE IF EXISTS maintenance_state;
                """
            )

    def _dump_bundle_sync(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            state = self._load_state_from_db(conn)
            clients = []
            for client in state.clients:
                clients.append(
                    {
                        "id": client.id,
                        "name": client.name,
                        "frpc_path": client.frpc_path,
                        "run_args": client.run_args,
                        "config_text": client.config_text,
                        "env": client.env,
                    }
                )
            channels = self._list_alert_channels_sync_with_conn(conn)
            rules = self._get_alert_rules_sync_with_conn(conn)
            return {
                "schema_version": 1,
                "active_client_id": state.active_client_id,
                "clients": clients,
                "alert_channels": channels,
                "alert_rules": rules,
                "exported_at": time.time(),
            }
        finally:
            conn.close()

    def _apply_bundle_sync(self, bundle: dict[str, Any], mode: str) -> None:
        conn = self._connect()
        now = time.time()
        try:
            state = self._normalize_state(
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
                        )
                        for item in list(bundle.get("clients") or [])
                        if isinstance(item, dict)
                    ],
                )
            )

            if mode not in {"overwrite", "merge"}:
                raise ValueError(f"invalid mode: {mode}")

            with conn:
                if mode == "overwrite":
                    conn.execute("DELETE FROM clients")
                    conn.execute("DELETE FROM client_env")
                    conn.execute("DELETE FROM alert_channels")
                for client in state.clients:
                    if mode == "merge":
                        exists = conn.execute(
                            "SELECT 1 FROM clients WHERE id = ?",
                            (client.id,),
                        ).fetchone()
                        if exists:
                            continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO clients (id, name, frpc_path, run_args, config_text, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            client.id,
                            client.name,
                            client.frpc_path,
                            client.run_args,
                            client.config_text,
                            1 if client.id == state.active_client_id else 0,
                            now,
                            now,
                        ),
                    )
                    conn.execute("DELETE FROM client_env WHERE client_id = ?", (client.id,))
                    for key, value in client.env.items():
                        conn.execute(
                            "INSERT INTO client_env (client_id, env_key, env_value) VALUES (?, ?, ?)",
                            (client.id, key, value),
                        )

                for item in list(bundle.get("alert_channels") or []):
                    if not isinstance(item, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO alert_channels (name, webhook_url, timeout_sec, enabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(item.get("name", "")),
                            str(item.get("webhook_url", "")),
                            int(item.get("timeout_sec", 5)),
                            1 if bool(item.get("enabled", True)) else 0,
                            now,
                            now,
                        ),
                    )

                rules = bundle.get("alert_rules")
                if isinstance(rules, dict):
                    conn.execute(
                        "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = 'start_failure'",
                        (1 if bool(rules.get("on_start_failure", True)) else 0, 0),
                    )
                    conn.execute(
                        "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = 'abnormal_exit'",
                        (1 if bool(rules.get("on_abnormal_exit", True)) else 0, 0),
                    )
                    conn.execute(
                        "UPDATE alert_rules SET enabled = ?, threshold = ? WHERE rule_key = 'restart_threshold'",
                        (
                            1 if bool(rules.get("on_restart_threshold", True)) else 0,
                            max(1, int(rules.get("restart_threshold", 3))),
                        ),
                    )
            self._sync_config_files_from_db(conn)
        finally:
            conn.close()

    def _list_alert_channels_sync_with_conn(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, name, webhook_url, timeout_sec, enabled, created_at, updated_at
            FROM alert_channels
            ORDER BY id ASC
            """
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "webhook_url": str(row["webhook_url"]),
                "timeout_sec": int(row["timeout_sec"]),
                "enabled": bool(row["enabled"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def _get_alert_rules_sync_with_conn(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT rule_key, enabled, threshold FROM alert_rules ORDER BY rule_key ASC"
        ).fetchall()
        mapped: dict[str, dict[str, Any]] = {
            str(row["rule_key"]): {
                "enabled": bool(row["enabled"]),
                "threshold": int(row["threshold"]),
            }
            for row in rows
        }
        return {
            "on_start_failure": mapped.get("start_failure", {}).get("enabled", True),
            "on_abnormal_exit": mapped.get("abnormal_exit", {}).get("enabled", True),
            "on_restart_threshold": mapped.get("restart_threshold", {}).get("enabled", True),
            "restart_threshold": mapped.get("restart_threshold", {}).get("threshold", 3),
        }
