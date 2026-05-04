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

    async def is_read_only(self) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._is_read_only_sync)

    async def set_read_only(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._set_read_only_sync, enabled)

    async def get_maintenance_state(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._get_maintenance_state_sync)

    async def list_templates(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_templates_sync)

    async def create_template(
        self,
        name: str,
        tags: list[str],
        content: str,
        variables: list[str],
        source_client_id: str | None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_template_sync,
                name,
                tags,
                content,
                variables,
                source_client_id,
            )

    async def update_template(
        self,
        template_id: int,
        name: str,
        tags: list[str],
        content: str,
        variables: list[str],
    ) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_template_sync,
                template_id,
                name,
                tags,
                content,
                variables,
            )

    async def delete_template(self, template_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_template_sync, template_id)

    async def dump_bundle(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._dump_bundle_sync)

    async def apply_bundle(self, bundle: dict[str, Any], mode: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._apply_bundle_sync, bundle, mode)

    async def create_snapshot(self, reason: str) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._create_snapshot_sync, reason)

    async def list_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        async with self._lock:
            return await asyncio.to_thread(self._list_snapshots_sync, safe_limit)

    async def rollback_snapshot(self, snapshot_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._rollback_snapshot_sync, snapshot_id)

    async def list_audit_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 2000))
        async with self._lock:
            return await asyncio.to_thread(self._list_audit_logs_sync, safe_limit)

    async def add_audit_log(
        self,
        action: str,
        target: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._add_audit_log_sync, action, target, detail or {})

    def frpc_config_file(self, client_id: str) -> Path:
        safe = self._safe_filename(client_id)
        return self._configs_dir / f"{safe}.toml"

    def _init_sync(self) -> None:
        conn = self._connect()
        try:
            self._create_tables(conn)
            self._migrate_legacy_clients_if_needed(conn)
            self._ensure_alert_rule_defaults(conn)
            self._ensure_maintenance_defaults(conn)
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

                CREATE TABLE IF NOT EXISTS config_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    content TEXT NOT NULL,
                    variables_json TEXT NOT NULL,
                    source_client_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    last_used_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS maintenance_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _ensure_maintenance_defaults(self, conn: sqlite3.Connection) -> None:
        now = time.time()
        defaults = [
            ("read_only", "0"),
            ("snapshot_max_keep", "20"),
        ]
        with conn:
            for key, value in defaults:
                conn.execute(
                    """
                    INSERT INTO maintenance_state (state_key, state_value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(state_key) DO NOTHING
                    """,
                    (key, value, now),
                )

    def _is_read_only_sync(self) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_value FROM maintenance_state WHERE state_key = ?",
                ("read_only",),
            ).fetchone()
            return bool(row and str(row["state_value"]) == "1")
        finally:
            conn.close()

    def _set_read_only_sync(self, enabled: bool) -> dict[str, Any]:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO maintenance_state (state_key, state_value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value, updated_at = excluded.updated_at
                    """,
                    ("read_only", "1" if enabled else "0", now),
                )
            return self._get_maintenance_state_sync_with_conn(conn)
        finally:
            conn.close()

    def _get_maintenance_state_sync(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            return self._get_maintenance_state_sync_with_conn(conn)
        finally:
            conn.close()

    def _get_maintenance_state_sync_with_conn(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT state_key, state_value, updated_at FROM maintenance_state"
        ).fetchall()
        mapping = {str(row["state_key"]): str(row["state_value"]) for row in rows}
        return {
            "read_only": mapping.get("read_only", "0") == "1",
            "snapshot_max_keep": int(mapping.get("snapshot_max_keep", "20")),
        }

    def _list_templates_sync(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, name, tags_json, content, variables_json, source_client_id, version, last_used_at, created_at, updated_at
                FROM config_templates
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                items.append(
                    {
                        "id": int(row["id"]),
                        "name": str(row["name"]),
                        "tags": self._safe_json_list(row["tags_json"]),
                        "content": str(row["content"]),
                        "variables": self._safe_json_list(row["variables_json"]),
                        "source_client_id": str(row["source_client_id"]) if row["source_client_id"] else None,
                        "version": int(row["version"]),
                        "last_used_at": float(row["last_used_at"]) if row["last_used_at"] is not None else None,
                        "created_at": float(row["created_at"]),
                        "updated_at": float(row["updated_at"]),
                    }
                )
            return items
        finally:
            conn.close()

    def _create_template_sync(
        self,
        name: str,
        tags: list[str],
        content: str,
        variables: list[str],
        source_client_id: str | None,
    ) -> dict[str, Any]:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO config_templates (name, tags_json, content, variables_json, source_client_id, version, last_used_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?)
                    """,
                    (
                        name,
                        json.dumps(tags, ensure_ascii=False),
                        content,
                        json.dumps(variables, ensure_ascii=False),
                        source_client_id,
                        now,
                        now,
                    ),
                )
            template_id = int(cursor.lastrowid)
            return self._get_template_by_id_sync(conn, template_id)
        finally:
            conn.close()

    def _update_template_sync(
        self,
        template_id: int,
        name: str,
        tags: list[str],
        content: str,
        variables: list[str],
    ) -> dict[str, Any] | None:
        conn = self._connect()
        now = time.time()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE config_templates
                    SET name = ?, tags_json = ?, content = ?, variables_json = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        json.dumps(tags, ensure_ascii=False),
                        content,
                        json.dumps(variables, ensure_ascii=False),
                        now,
                        template_id,
                    ),
                )
            if cursor.rowcount == 0:
                return None
            return self._get_template_by_id_sync(conn, template_id)
        finally:
            conn.close()

    def _delete_template_sync(self, template_id: int) -> bool:
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM config_templates WHERE id = ?", (template_id,))
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _get_template_by_id_sync(self, conn: sqlite3.Connection, template_id: int) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT id, name, tags_json, content, variables_json, source_client_id, version, last_used_at, created_at, updated_at
            FROM config_templates
            WHERE id = ?
            """,
            (template_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"template not found: {template_id}")
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "tags": self._safe_json_list(row["tags_json"]),
            "content": str(row["content"]),
            "variables": self._safe_json_list(row["variables_json"]),
            "source_client_id": str(row["source_client_id"]) if row["source_client_id"] else None,
            "version": int(row["version"]),
            "last_used_at": float(row["last_used_at"]) if row["last_used_at"] is not None else None,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

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
            templates = self._list_templates_sync_with_conn(conn)
            maintenance = self._get_maintenance_state_sync_with_conn(conn)
            return {
                "schema_version": 1,
                "active_client_id": state.active_client_id,
                "clients": clients,
                "alert_channels": channels,
                "alert_rules": rules,
                "templates": templates,
                "maintenance_state": maintenance,
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
                    conn.execute("DELETE FROM config_templates")
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

                for item in list(bundle.get("templates") or []):
                    if not isinstance(item, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO config_templates (name, tags_json, content, variables_json, source_client_id, version, last_used_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(item.get("name", "")),
                            json.dumps(self._safe_str_list(item.get("tags")), ensure_ascii=False),
                            str(item.get("content", "")),
                            json.dumps(self._safe_str_list(item.get("variables")), ensure_ascii=False),
                            str(item.get("source_client_id")) if item.get("source_client_id") else None,
                            int(item.get("version", 1)),
                            float(item["last_used_at"]) if item.get("last_used_at") is not None else None,
                            now,
                            now,
                        ),
                    )

                maintenance = bundle.get("maintenance_state")
                if isinstance(maintenance, dict):
                    conn.execute(
                        """
                        INSERT INTO maintenance_state (state_key, state_value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value, updated_at = excluded.updated_at
                        """,
                        ("read_only", "1" if bool(maintenance.get("read_only", False)) else "0", now),
                    )
                    conn.execute(
                        """
                        INSERT INTO maintenance_state (state_key, state_value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value, updated_at = excluded.updated_at
                        """,
                        ("snapshot_max_keep", str(max(1, int(maintenance.get("snapshot_max_keep", 20)))), now),
                    )
            self._sync_config_files_from_db(conn)
        finally:
            conn.close()

    def _create_snapshot_sync(self, reason: str) -> dict[str, Any]:
        conn = self._connect()
        now = time.time()
        try:
            payload = self._dump_bundle_sync()
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO config_snapshots (reason, payload_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (reason, json.dumps(payload, ensure_ascii=False), now),
                )
                snapshot_id = int(cursor.lastrowid)
            self._prune_snapshots_sync(conn)
            return {"id": snapshot_id, "reason": reason, "created_at": now}
        finally:
            conn.close()

    def _list_snapshots_sync(self, limit: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, reason, created_at
                FROM config_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {"id": int(row["id"]), "reason": str(row["reason"]), "created_at": float(row["created_at"])}
                for row in rows
            ]
        finally:
            conn.close()

    def _rollback_snapshot_sync(self, snapshot_id: int) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM config_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                return False
            payload = json.loads(str(row["payload_json"]))
            self._apply_bundle_sync(payload, "overwrite")
            return True
        finally:
            conn.close()

    def _list_audit_logs_sync(self, limit: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, action, target, detail_json, created_at
                FROM audit_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    detail = json.loads(str(row["detail_json"]))
                except json.JSONDecodeError:
                    detail = {}
                items.append(
                    {
                        "id": int(row["id"]),
                        "action": str(row["action"]),
                        "target": str(row["target"]),
                        "detail": detail,
                        "created_at": float(row["created_at"]),
                    }
                )
            return items
        finally:
            conn.close()

    def _add_audit_log_sync(self, action: str, target: str, detail: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs (action, target, detail_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        action,
                        target,
                        json.dumps(detail, ensure_ascii=False),
                        time.time(),
                    ),
                )
        finally:
            conn.close()

    def _prune_snapshots_sync(self, conn: sqlite3.Connection) -> None:
        max_keep = self._get_maintenance_state_sync_with_conn(conn)["snapshot_max_keep"]
        rows = conn.execute(
            """
            SELECT id FROM config_snapshots ORDER BY id DESC
            """
        ).fetchall()
        if len(rows) <= max_keep:
            return
        stale_ids = [int(row["id"]) for row in rows[max_keep:]]
        with conn:
            conn.executemany("DELETE FROM config_snapshots WHERE id = ?", [(sid,) for sid in stale_ids])

    def _safe_json_list(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return []
        else:
            parsed = raw
        return self._safe_str_list(parsed)

    def _safe_str_list(self, raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

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

    def _list_templates_sync_with_conn(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, name, tags_json, content, variables_json, source_client_id, version, last_used_at, created_at, updated_at
            FROM config_templates
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": int(row["id"]),
                    "name": str(row["name"]),
                    "tags": self._safe_json_list(row["tags_json"]),
                    "content": str(row["content"]),
                    "variables": self._safe_json_list(row["variables_json"]),
                    "source_client_id": str(row["source_client_id"]) if row["source_client_id"] else None,
                    "version": int(row["version"]),
                    "last_used_at": float(row["last_used_at"]) if row["last_used_at"] is not None else None,
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
            )
        return items
