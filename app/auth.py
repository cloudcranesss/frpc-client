from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_store import ConfigStore


@dataclass(slots=True)
class AuthUser:
    username: str
    password_salt: str
    password_hash: str
    iterations: int = 210_000


class AuthManager:
    _USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

    def __init__(self, data_dir: Path, config_store: ConfigStore) -> None:
        self._data_dir = data_dir
        self._auth_file = data_dir / "auth.json"
        self._store = config_store
        self._session_ttl_sec = 60 * 60 * 24
        self._cookie_name = "frp_panel_session"

    @property
    def cookie_name(self) -> str:
        return self._cookie_name

    async def init(self) -> None:
        user_count = await self._store.count_users()
        if user_count > 0:
            return

        env_username = os.getenv("FRP_PANEL_INIT_USERNAME", "").strip()
        env_password = os.getenv("FRP_PANEL_INIT_PASSWORD", "")
        created: AuthUser | None = None
        if env_username and env_password:
            if self.validate_username(env_username) and self.validate_password_strength(env_password):
                created = self._build_user(env_username, env_password)

        legacy = created or self._read_legacy_user()
        if legacy is None:
            legacy = self._build_user("admin", "admin123456")
            print("[AUTH] Default account created: admin / admin123456")

        await self._store.upsert_user(
            username=legacy.username,
            password_salt=legacy.password_salt,
            password_hash=legacy.password_hash,
            iterations=legacy.iterations,
        )
        self._backup_legacy_auth_file()

    async def verify_login(self, username: str, password: str) -> bool:
        row = await self._store.get_user(username)
        if row is None:
            return False
        expected = self._hash_password(
            password=password,
            salt_hex=str(row["password_salt"]),
            iterations=int(row["iterations"]),
        )
        return hmac.compare_digest(expected, str(row["password_hash"]))

    async def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + self._session_ttl_sec
        await self._store.create_session(token, username, expires_at)
        return token

    async def get_session_username(self, token: str | None) -> str | None:
        return await self._store.get_session_username(token)

    async def delete_session(self, token: str | None) -> None:
        await self._store.delete_session(token)

    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        if not self.validate_password_strength(new_password.strip()):
            return False
        row = await self._store.get_user(username)
        if row is None:
            return False
        if not await self.verify_login(username, old_password):
            return False
        updated = self._build_user(username, new_password.strip())
        await self._store.upsert_user(
            username=updated.username,
            password_salt=updated.password_salt,
            password_hash=updated.password_hash,
            iterations=updated.iterations,
        )
        return True

    async def update_profile(
        self,
        current_username: str,
        current_password: str,
        new_username: str | None,
        new_password: str | None,
    ) -> tuple[bool, str, str | None]:
        if not await self.verify_login(current_username, current_password):
            return False, "invalid_current_password", None

        row = await self._store.get_user(current_username)
        if row is None:
            return False, "user_not_found", None

        target_username = (new_username or "").strip() or current_username
        if target_username != current_username and not self.validate_username(target_username):
            return False, "invalid_username", None

        password_value = (new_password or "").strip()
        if password_value:
            if not self.validate_password_strength(password_value):
                return False, "weak_password", None
            updated = self._build_user(target_username, password_value)
            salt = updated.password_salt
            hashed = updated.password_hash
            iterations = updated.iterations
        else:
            salt = str(row["password_salt"])
            hashed = str(row["password_hash"])
            iterations = int(row["iterations"])

        result = await self._store.replace_user(
            current_username=current_username,
            new_username=target_username,
            password_salt=salt,
            password_hash=hashed,
            iterations=iterations,
        )
        if result != "ok":
            return False, result, None

        await self._store.delete_sessions_by_username(current_username)
        if target_username != current_username:
            await self._store.delete_sessions_by_username(target_username)
        return True, "ok", target_username

    def password_policy_text(self) -> str:
        return "密码至少8位，且必须同时包含字母和数字。"

    def validate_username(self, username: str) -> bool:
        return bool(self._USERNAME_RE.match(username.strip()))

    def validate_password_strength(self, password: str) -> bool:
        value = password.strip()
        if len(value) < 8:
            return False
        has_alpha = any(char.isalpha() for char in value)
        has_digit = any(char.isdigit() for char in value)
        return has_alpha and has_digit

    def _read_legacy_user(self) -> AuthUser | None:
        if not self._auth_file.exists():
            return None
        try:
            payload = json.loads(self._auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return self._user_from_payload(payload if isinstance(payload, dict) else {})

    def _backup_legacy_auth_file(self) -> None:
        if not self._auth_file.exists():
            return
        backup = self._auth_file.with_suffix(".json.bak")
        if backup.exists():
            return
        self._auth_file.replace(backup)

    def _user_from_payload(self, payload: dict[str, Any]) -> AuthUser | None:
        username = str(payload.get("username", "")).strip()
        salt = str(payload.get("password_salt", "")).strip()
        hashed = str(payload.get("password_hash", "")).strip()
        iterations = int(payload.get("iterations", 210_000))
        if not username or not salt or not hashed:
            return None
        return AuthUser(
            username=username,
            password_salt=salt,
            password_hash=hashed,
            iterations=max(100_000, iterations),
        )

    def _build_user(self, username: str, password: str) -> AuthUser:
        salt = secrets.token_bytes(16).hex()
        iterations = 210_000
        return AuthUser(
            username=username,
            password_salt=salt,
            password_hash=self._hash_password(password, salt, iterations),
            iterations=iterations,
        )

    def _hash_password(self, password: str, salt_hex: str, iterations: int) -> str:
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return digest.hex()
