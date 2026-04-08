from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

try:
    from .config_store import ProxyClientConfig
except ImportError:
    from config_store import ProxyClientConfig


@dataclass(slots=True)
class RuntimeState:
    process: subprocess.Popen[bytes] | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    wait_task: asyncio.Task[None] | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    started_at: float | None = None
    last_exit_code: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class FrpcManager:
    _ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
    _DECODINGS = ("utf-8", "utf-8-sig", "gb18030", "cp936")

    def __init__(self) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._registry_lock = asyncio.Lock()

    async def start(
        self,
        client_id: str,
        cfg: ProxyClientConfig,
        config_path: Path,
    ) -> dict[str, int | bool | float | None]:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            if self._is_running(state):
                self._append_log(state, "frpc is already running.")
                return self.status(client_id)

            await self._cleanup_tasks(state, clear_process=True)
            command = self._build_command(cfg, config_path)
            program = command[0]
            if not Path(program).exists() and not self._is_path_command(program):
                raise FileNotFoundError(f"frpc executable not found: {program}")

            env = os.environ.copy()
            env.update(cfg.env)
            env.setdefault("NO_COLOR", "1")
            env.setdefault("CLICOLOR", "0")

            self._append_log(state, f"> Starting frpc: {' '.join(command)}")
            state.process = await asyncio.to_thread(
                subprocess.Popen,
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
            )
            state.started_at = time.time()
            state.last_exit_code = None

            if state.process.stdout is not None:
                state.stdout_task = asyncio.create_task(
                    self._drain_stream(state, state.process.stdout, "stdout")
                )
            if state.process.stderr is not None:
                state.stderr_task = asyncio.create_task(
                    self._drain_stream(state, state.process.stderr, "stderr")
                )
            state.wait_task = asyncio.create_task(self._monitor_exit(client_id, state))
            return self.status(client_id)

    async def stop(self, client_id: str) -> dict[str, int | bool | float | None]:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            if not self._is_running(state):
                self._append_log(state, "frpc is not running.")
                return self.status(client_id)

            assert state.process is not None
            self._append_log(state, "> Stopping frpc...")
            state.process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(state.process.wait), timeout=5)
            except asyncio.TimeoutError:
                self._append_log(state, "> Terminate timeout, killing frpc.")
                state.process.kill()
                await asyncio.to_thread(state.process.wait)

            await self._cleanup_tasks(state, clear_process=True)
            self._append_log(state, "> frpc stopped.")
            return self.status(client_id)

    async def shutdown(self) -> None:
        ids = list(self._states.keys())
        for client_id in ids:
            state = await self._get_or_create_state(client_id)
            if self._is_running(state):
                await self.stop(client_id)
            else:
                async with state.lock:
                    await self._cleanup_tasks(state, clear_process=True)

    async def drop_client(self, client_id: str) -> None:
        state = await self._get_or_create_state(client_id)
        if self._is_running(state):
            await self.stop(client_id)
        async with self._registry_lock:
            self._states.pop(client_id, None)

    def status(self, client_id: str) -> dict[str, int | bool | float | None]:
        state = self._states.get(client_id)
        if state is None:
            return {
                "running": False,
                "pid": None,
                "started_at": None,
                "uptime_sec": 0,
                "last_exit_code": None,
            }

        running = self._is_running(state)
        now = time.time()
        uptime = (now - state.started_at) if (running and state.started_at) else 0
        pid = state.process.pid if state.process else None
        return {
            "running": running,
            "pid": pid,
            "started_at": state.started_at,
            "uptime_sec": round(uptime, 2),
            "last_exit_code": state.last_exit_code,
        }

    def logs(self, client_id: str, limit: int = 200) -> list[str]:
        limit = max(1, min(limit, 1000))
        state = self._states.get(client_id)
        if state is None:
            return []
        return list(state.logs)[-limit:]

    async def _monitor_exit(self, client_id: str, state: RuntimeState) -> None:
        process = state.process
        if process is None:
            return
        code = await asyncio.to_thread(process.wait)
        async with state.lock:
            if state.process is process:
                state.last_exit_code = code
                state.started_at = None
                self._append_log(state, f"> frpc exited with code {code}.")

    async def _drain_stream(
        self,
        state: RuntimeState,
        stream: BinaryIO,
        source: str,
    ) -> None:
        while True:
            line = await asyncio.to_thread(stream.readline)
            if not line:
                break
            text = self._decode_line(line)
            if text:
                self._append_log(state, f"[{source}] {text}")

    async def _cleanup_tasks(self, state: RuntimeState, clear_process: bool) -> None:
        for task in (state.stdout_task, state.stderr_task, state.wait_task):
            if task is None:
                continue
            with contextlib.suppress(asyncio.CancelledError):
                if not task.done():
                    task.cancel()
                await task
        state.stdout_task = None
        state.stderr_task = None
        state.wait_task = None
        if clear_process:
            state.process = None
            state.started_at = None

    async def _get_or_create_state(self, client_id: str) -> RuntimeState:
        async with self._registry_lock:
            if client_id not in self._states:
                self._states[client_id] = RuntimeState()
            return self._states[client_id]

    def _append_log(self, state: RuntimeState, text: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        state.logs.append(f"{timestamp} {text}")

    def _build_command(self, cfg: ProxyClientConfig, config_path: Path) -> list[str]:
        base = [cfg.frpc_path, "-c", str(config_path)]
        extra = shlex.split(cfg.run_args, posix=False) if cfg.run_args.strip() else []
        return [*base, *extra]

    def _is_running(self, state: RuntimeState) -> bool:
        return state.process is not None and state.process.poll() is None

    def _is_path_command(self, value: str) -> bool:
        return "\\" not in value and "/" not in value

    def _decode_line(self, raw: bytes) -> str:
        text = ""
        for encoding in self._DECODINGS:
            try:
                text = raw.decode(encoding, errors="strict")
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")

        text = text.rstrip("\r\n")
        text = self._ANSI_ESCAPE_RE.sub("", text)
        text = text.replace("\x1b", "")
        text = self._CONTROL_CHAR_RE.sub("", text)
        return text.strip()
