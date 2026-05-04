from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config_store import ConfigStore


@pytest.mark.asyncio
async def test_legacy_client_json_migration(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = data_dir / "client_config.json"
    legacy_file.write_text(
        json.dumps(
            {
                "name": "legacy-client",
                "frpc_path": "frpc",
                "run_args": "--log-level info",
                "config_text": 'serverAddr = "1.2.3.4"\nserverPort = 7000\n',
                "env": {"A": "1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = ConfigStore(data_dir)
    await store.init()

    state = await store.load_state()
    assert len(state.clients) == 1
    assert state.clients[0].name == "legacy-client"
    assert state.clients[0].env == {"A": "1"}
    assert (data_dir / "app.db").exists()
    assert (data_dir / "client_config.json.bak").exists()


@pytest.mark.asyncio
async def test_default_alert_rules_exist(tmp_path: Path):
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    await store.init()

    rules = await store.get_alert_rules()
    assert rules["on_start_failure"] is True
    assert rules["on_abnormal_exit"] is True
    assert rules["on_restart_threshold"] is True
    assert rules["restart_threshold"] >= 1
