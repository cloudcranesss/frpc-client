from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuthUser:
    username: str
    password_salt: str
    password_hash: str
    iterations: int = 210_000


class AuthManager:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._auth_file = data_dir / "auth.json"
        self._lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_ttl_sec = 60 * 60 * 24
        self._cookie_name = "frp_panel_session"

    @property
    def cookie_name(self) -> str:
        return self._cookie_name

    async def init(self) -> None:
        await asyncio.to_thread(self._data_dir.mkdir, parents=True, exist_ok=True)
        if not self._auth_file.exists():
            default = self._build_user("admin", "admin123456")
            await self._save_user(default)
            print("[AUTH] Default account created: admin / admin123456")
            return
        await self._load_user()

    async def verify_login(self, username: str, password: str) -> bool:
        user = await self._load_user()
        if user.username != username:
            return False
        expected = self._hash_password(
            password=password,
            salt_hex=user.password_salt,
            iterations=user.iterations,
        )
        return hmac.compare_digest(expected, user.password_hash)

    async def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + self._session_ttl_sec
        async with self._session_lock:
            self._sessions[token] = {"username": username, "expires_at": expires_at}
            self._cleanup_sessions_no_lock()
        return token

    async def get_session_username(self, token: str | None) -> str | None:
        if not token:
            return None
        async with self._session_lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if float(session["expires_at"]) < time.time():
                self._sessions.pop(token, None)
                return None
            return str(session["username"])

    async def delete_session(self, token: str | None) -> None:
        if not token:
            return
        async with self._session_lock:
            self._sessions.pop(token, None)

    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        if len(new_password.strip()) < 8:
            return False
        user = await self._load_user()
        if user.username != username:
            return False
        if not await self.verify_login(username, old_password):
            return False
        updated = self._build_user(username, new_password.strip())
        await self._save_user(updated)
        return True

    def _cleanup_sessions_no_lock(self) -> None:
        now = time.time()
        expired = [token for token, sess in self._sessions.items() if float(sess["expires_at"]) < now]
        for token in expired:
            self._sessions.pop(token, None)

    async def _load_user(self) -> AuthUser:
        async with self._lock:
            payload = await asyncio.to_thread(self._read_json)
        return self._user_from_payload(payload)

    async def _save_user(self, user: AuthUser) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._auth_file.write_text,
                json.dumps(asdict(user), ensure_ascii=False, indent=2),
                "utf-8",
            )

    def _read_json(self) -> dict[str, Any]:
        if not self._auth_file.exists():
            return {}
        text = self._auth_file.read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
            if isinstance(raw, dict):
                return raw
            return {}
        except json.JSONDecodeError:
            return {}

    def _user_from_payload(self, payload: dict[str, Any]) -> AuthUser:
        username = str(payload.get("username", "admin")).strip() or "admin"
        salt = str(payload.get("password_salt", "")).strip()
        hashed = str(payload.get("password_hash", "")).strip()
        iterations = int(payload.get("iterations", 210_000))
        if not salt or not hashed:
            return self._build_user("admin", "admin123456")
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

