from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.frpc_manager import FrpcManager


class _DummyProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self._done = threading.Event()

    def terminate(self) -> None:
        self._done.set()

    def kill(self) -> None:
        self._done.set()

    def wait(self) -> int:
        self._done.wait(timeout=1.0)
        return 0

    def poll(self) -> int | None:
        return 0 if self._done.is_set() else None


@pytest.mark.asyncio
async def test_stop_does_not_deadlock_when_wait_task_needs_state_lock():
    manager = FrpcManager()
    client_id = "c1"
    state = await manager._get_or_create_state(client_id)
    process = _DummyProcess()

    async with state.lock:
        state.process = process
        state.started_at = time.time()
        state.manual_stop_requested = False
        state.wait_task = asyncio.create_task(manager._monitor_exit(client_id, state))

    status = await asyncio.wait_for(manager.stop(client_id), timeout=1.0)
    assert status["running"] is False
