from __future__ import annotations

from pathlib import Path

import pytest

from app.auth import AuthManager
from app.config_store import ConfigStore


@pytest.mark.asyncio
async def test_default_auth_and_session_flow(tmp_path: Path):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    await store.init()

    auth = AuthManager(data_dir, store)
    await auth.init()

    assert await auth.verify_login("admin", "admin123456")
    token = await auth.create_session("admin")
    assert await auth.get_session_username(token) == "admin"

    changed = await auth.change_password("admin", "admin123456", "new-pass-123")
    assert changed is True
    assert await auth.verify_login("admin", "new-pass-123")


@pytest.mark.asyncio
async def test_env_init_account_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    await store.init()

    monkeypatch.setenv("FRP_PANEL_INIT_USERNAME", "ops_admin")
    monkeypatch.setenv("FRP_PANEL_INIT_PASSWORD", "SafePass1234")

    auth = AuthManager(data_dir, store)
    await auth.init()

    assert await auth.verify_login("ops_admin", "SafePass1234")
    assert not await auth.verify_login("admin", "admin123456")


@pytest.mark.asyncio
async def test_update_profile_username_password_and_session_cleanup(tmp_path: Path):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    await store.init()

    auth = AuthManager(data_dir, store)
    await auth.init()

    old_token = await auth.create_session("admin")
    ok, reason, new_name = await auth.update_profile(
        current_username="admin",
        current_password="admin123456",
        new_username="ops_admin",
        new_password="SafePass1234",
    )
    assert ok is True
    assert reason == "ok"
    assert new_name == "ops_admin"
    assert await auth.get_session_username(old_token) is None
    assert await auth.verify_login("ops_admin", "SafePass1234")
    assert not await auth.verify_login("admin", "admin123456")

    token2 = await auth.create_session("ops_admin")
    ok2, reason2, new_name2 = await auth.update_profile(
        current_username="ops_admin",
        current_password="SafePass1234",
        new_username=None,
        new_password="NextPass5678",
    )
    assert ok2 is True
    assert reason2 == "ok"
    assert new_name2 == "ops_admin"
    assert await auth.get_session_username(token2) is None
    assert await auth.verify_login("ops_admin", "NextPass5678")


@pytest.mark.asyncio
async def test_update_profile_rejects_conflict_and_weak_password(tmp_path: Path):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    await store.init()

    auth = AuthManager(data_dir, store)
    await auth.init()
    extra = auth._build_user("other_user", "Another12345")
    await store.upsert_user(
        username=extra.username,
        password_salt=extra.password_salt,
        password_hash=extra.password_hash,
        iterations=extra.iterations,
    )

    ok_conflict, reason_conflict, _ = await auth.update_profile(
        current_username="admin",
        current_password="admin123456",
        new_username="other_user",
        new_password=None,
    )
    assert ok_conflict is False
    assert reason_conflict == "username_exists"

    ok_weak, reason_weak, _ = await auth.update_profile(
        current_username="admin",
        current_password="admin123456",
        new_username=None,
        new_password="short12",
    )
    assert ok_weak is False
    assert reason_weak == "weak_password"
