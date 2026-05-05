from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import re
import shlex
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable

from .config_store import ProxyClientConfig

RuntimeEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class RuntimeState:
    process: subprocess.Popen[bytes] | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    wait_task: asyncio.Task[None] | None = None
    restart_task: asyncio.Task[None] | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    started_at: float | None = None
    last_exit_code: int | None = None
    restart_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    manual_stop_requested: bool = False
    latest_cfg: ProxyClientConfig | None = None
    latest_config_path: Path | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)


class FrpcManager:
    _ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
    _DECODINGS = ("utf-8", "utf-8-sig", "gb18030", "cp936")
    _TERM_WAIT_TIMEOUT_SEC = 5.0
    _KILL_WAIT_TIMEOUT_SEC = 3.0
    _WAIT_TASK_TIMEOUT_SEC = 3.0
    _TASK_CLEANUP_TIMEOUT_SEC = 1.5
    _EVENT_CALLBACK_TIMEOUT_SEC = 2.0

    def __init__(self, event_callback: RuntimeEventCallback | None = None) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._registry_lock = asyncio.Lock()
        self._event_callback = event_callback
        io_workers = max(8, int(os.getenv("FRP_PANEL_FRPC_IO_WORKERS", "16")))
        self._frpc_io_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=io_workers,
            thread_name_prefix="frpc-io",
        )

    async def start(
        self,
        client_id: str,
        cfg: ProxyClientConfig,
        config_path: Path,
    ) -> dict[str, int | bool | float | str | None]:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            if self._is_running(state):
                await self._emit_log(client_id, state, "frpc is already running.")
                return self.status(client_id)

            await self._cancel_restart_task(state)
            await self._cleanup_tasks(state)
            state.manual_stop_requested = False
            state.latest_cfg = cfg
            state.latest_config_path = config_path
            state.last_error = None
            spawned = await self._spawn_process(client_id, state, cfg, config_path)
            if not spawned:
                state.last_error = "failed to start frpc process"
                await self._emit_event(
                    client_id=client_id,
                    event_type="start_failure",
                    message="Failed to start frpc process.",
                    payload={"frpc_path": cfg.frpc_path},
                )
            return self.status(client_id)

    async def stop(self, client_id: str) -> dict[str, int | bool | float | str | None]:
        state = await self._get_or_create_state(client_id)
        process: subprocess.Popen[bytes] | None = None
        wait_task: asyncio.Task[None] | None = None
        async with state.lock:
            await self._cancel_restart_task(state)
            if not self._is_running(state):
                await self._emit_log(client_id, state, "frpc is not running.")
                return self.status(client_id)

            assert state.process is not None
            process = state.process
            wait_task = state.wait_task
            state.manual_stop_requested = True
            await self._emit_log(client_id, state, "> Stopping frpc...")

        assert process is not None
        process.terminate()
        try:
            await asyncio.wait_for(self._run_io(process.wait), timeout=self._TERM_WAIT_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            await self._emit_log(client_id, state, "> Terminate timeout, killing frpc.")
            process.kill()
            try:
                await asyncio.wait_for(
                    self._run_io(process.wait),
                    timeout=self._KILL_WAIT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                await self._emit_log(client_id, state, "> Kill timeout, force cleanup and continue.")

        if wait_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await asyncio.wait_for(wait_task, timeout=self._WAIT_TASK_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    wait_task.cancel()
                    await self._emit_log(client_id, state, "> Exit monitor timeout, cancelled.")

        async with state.lock:
            await self._cleanup_tasks(state)
            return self.status(client_id)

    async def shutdown(self) -> None:
        ids = list(self._states.keys())
        for client_id in ids:
            state = await self._get_or_create_state(client_id)
            async with state.lock:
                await self._cancel_restart_task(state)
            if self._is_running(state):
                await self.stop(client_id)
            else:
                async with state.lock:
                    await self._cleanup_tasks(state)
        self._frpc_io_pool.shutdown(wait=False, cancel_futures=True)

    async def drop_client(self, client_id: str) -> None:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            await self._cancel_restart_task(state)
        if self._is_running(state):
            await self.stop(client_id)
        async with self._registry_lock:
            self._states.pop(client_id, None)

    async def subscribe(self, client_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            state.subscribers.add(queue)
        await queue.put({"event": "status", "data": self.status(client_id)})

    async def unsubscribe(self, client_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            state.subscribers.discard(queue)

    def status(self, client_id: str) -> dict[str, int | bool | float | str | None]:
        state = self._states.get(client_id)
        if state is None:
            return {
                "running": False,
                "pid": None,
                "started_at": None,
                "uptime_sec": 0,
                "last_exit_code": None,
                "restart_count": 0,
                "last_error": None,
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
            "restart_count": state.restart_count,
            "last_error": state.last_error,
        }

    def logs(self, client_id: str, limit: int = 200) -> list[str]:
        limit = max(1, min(limit, 1000))
        state = self._states.get(client_id)
        if state is None:
            return []
        return list(state.logs)[-limit:]

    async def clear_logs(self, client_id: str) -> None:
        state = await self._get_or_create_state(client_id)
        async with state.lock:
            state.logs.clear()

    async def _spawn_process(
        self,
        client_id: str,
        state: RuntimeState,
        cfg: ProxyClientConfig,
        config_path: Path,
    ) -> bool:
        command = self._build_command(cfg, config_path)
        program = command[0]
        if not Path(program).exists() and not self._is_path_command(program):
            state.last_error = f"frpc executable not found: {program}"
            await self._emit_event(
                client_id=client_id,
                event_type="start_failure",
                message=state.last_error,
                payload={"program": program},
                exit_type="start_failure",
            )
            return False

        env = os.environ.copy()
        env.update(cfg.env)
        env.setdefault("NO_COLOR", "1")
        env.setdefault("CLICOLOR", "0")

        try:
            await self._emit_log(client_id, state, f"> Starting frpc: {' '.join(command)}")
            state.process = await self._run_io(
                subprocess.Popen,
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
            )
        except OSError as exc:
            state.last_error = f"spawn failed: {exc}"
            await self._emit_event(
                client_id=client_id,
                event_type="start_failure",
                message=state.last_error,
                payload={"program": program},
                exit_type="start_failure",
            )
            return False

        state.started_at = time.time()
        state.last_exit_code = None
        state.manual_stop_requested = False
        state.consecutive_failures = 0

        if state.process.stdout is not None:
            state.stdout_task = asyncio.create_task(
                self._drain_stream(client_id, state, state.process.stdout, "stdout")
            )
        if state.process.stderr is not None:
            state.stderr_task = asyncio.create_task(
                self._drain_stream(client_id, state, state.process.stderr, "stderr")
            )
        state.wait_task = asyncio.create_task(self._monitor_exit(client_id, state))
        await self._publish_status(client_id, state)
        return True

    async def _monitor_exit(self, client_id: str, state: RuntimeState) -> None:
        process = state.process
        if process is None:
            return
        code = await self._run_io(process.wait)
        restart_delay: float | None = None
        exit_type = "normal_exit" if code == 0 else "abnormal_exit"
        message = f"frpc exited with code {code}."
        payload: dict[str, Any] = {"exit_code": code}
        async with state.lock:
            if state.process is process:
                state.last_exit_code = code
                state.started_at = None
                state.process = None
                if state.manual_stop_requested:
                    exit_type = "manual_stop"
                    message = "frpc stopped by user."
                    state.manual_stop_requested = False
                elif code == 0:
                    state.consecutive_failures = 0
                else:
                    state.consecutive_failures += 1
                    state.last_error = f"exit code {code}"
                    delay = min(30, 2 ** (state.consecutive_failures - 1))
                    restart_delay = float(delay)
                    state.restart_count += 1
                    payload["restart_count"] = state.restart_count

        await self._emit_event(
            client_id=client_id,
            event_type=exit_type,
            message=message,
            payload=payload,
            exit_type=exit_type,
        )
        await self._publish_status(client_id, state)

        if restart_delay is not None:
            await self._emit_event(
                client_id=client_id,
                event_type="auto_restart",
                message=f"Scheduling restart in {restart_delay:.0f}s.",
                payload={
                    "delay_sec": restart_delay,
                    "restart_count": state.restart_count,
                },
                exit_type="abnormal_exit",
            )
            async with state.lock:
                await self._cancel_restart_task(state)
                state.restart_task = asyncio.create_task(
                    self._restart_after_delay(client_id, state, restart_delay)
                )

    async def _restart_after_delay(self, client_id: str, state: RuntimeState, delay_sec: float) -> None:
        await asyncio.sleep(delay_sec)
        async with state.lock:
            if state.process is not None:
                return
            if state.manual_stop_requested:
                return
            cfg = state.latest_cfg
            cfg_path = state.latest_config_path
            if cfg is None or cfg_path is None:
                return
            await self._spawn_process(client_id, state, cfg, cfg_path)

    async def _drain_stream(
        self,
        client_id: str,
        state: RuntimeState,
        stream: BinaryIO,
        source: str,
    ) -> None:
        while True:
            line = await self._run_io(stream.readline)
            if not line:
                break
            text = self._decode_line(line)
            if text:
                await self._emit_log(client_id, state, f"[{source}] {text}")

    async def _cleanup_tasks(self, state: RuntimeState) -> None:
        for task in (state.stdout_task, state.stderr_task):
            if task is None:
                continue
            with contextlib.suppress(asyncio.CancelledError):
                if not task.done():
                    task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=self._TASK_CLEANUP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    pass
        if state.wait_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                if not state.wait_task.done():
                    state.wait_task.cancel()
                try:
                    await asyncio.wait_for(state.wait_task, timeout=self._TASK_CLEANUP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    pass
        state.stdout_task = None
        state.stderr_task = None
        state.wait_task = None
        state.process = None
        state.started_at = None

    async def _cancel_restart_task(self, state: RuntimeState) -> None:
        if state.restart_task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            if not state.restart_task.done():
                state.restart_task.cancel()
            await state.restart_task
        state.restart_task = None

    async def _get_or_create_state(self, client_id: str) -> RuntimeState:
        async with self._registry_lock:
            if client_id not in self._states:
                self._states[client_id] = RuntimeState()
            return self._states[client_id]

    async def _emit_log(self, client_id: str, state: RuntimeState, text: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        message = f"{timestamp} {text}"
        state.logs.append(message)
        await self._publish(client_id, state, "log", {"line": message})

    async def _emit_event(
        self,
        client_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        exit_type: str | None = None,
    ) -> None:
        data = {
            "client_id": client_id,
            "event_type": event_type,
            "message": message,
            "payload": payload or {},
            "exit_type": exit_type,
            "created_at": time.time(),
        }
        state = await self._get_or_create_state(client_id)
        await self._publish(client_id, state, "event", data)
        if self._event_callback is not None:
            asyncio.create_task(self._run_event_callback(data))

    async def _run_event_callback(self, data: dict[str, Any]) -> None:
        if self._event_callback is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self._event_callback(data),
                timeout=self._EVENT_CALLBACK_TIMEOUT_SEC,
            )

    async def _run_io(self, func: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._frpc_io_pool, func, *args)

    async def _publish_status(self, client_id: str, state: RuntimeState) -> None:
        await self._publish(client_id, state, "status", self.status(client_id))

    async def _publish(self, client_id: str, state: RuntimeState, event: str, data: dict[str, Any]) -> None:
        _ = client_id
        dead_queues: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in state.subscribers:
            try:
                queue.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                dead_queues.append(queue)
        for queue in dead_queues:
            state.subscribers.discard(queue)

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
