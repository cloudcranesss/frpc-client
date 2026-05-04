from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from .config_store import ConfigStore


class AlertManager:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._stopped.set()

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stopped.clear()
        self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        finally:
            self._worker_task = None
            self._stopped.set()

    async def handle_runtime_event(self, event: dict[str, Any]) -> None:
        rules = await self._store.get_alert_rules()
        event_type = str(event.get("event_type", ""))
        should_send = False
        if event_type == "start_failure" and bool(rules["on_start_failure"]):
            should_send = True
        elif event_type == "abnormal_exit" and bool(rules["on_abnormal_exit"]):
            should_send = True
        elif event_type == "auto_restart" and bool(rules["on_restart_threshold"]):
            restart_count = int(event.get("payload", {}).get("restart_count", 0))
            should_send = restart_count >= int(rules["restart_threshold"])

        if should_send:
            await self._queue.put(event)

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._send_alerts(event)
            finally:
                self._queue.task_done()

    async def _send_alerts(self, event: dict[str, Any]) -> None:
        channels = await self._store.list_alert_channels()
        if not channels:
            return
        payload = {
            "title": "frp-web-client alert",
            "client_id": event.get("client_id"),
            "event_type": event.get("event_type"),
            "exit_type": event.get("exit_type"),
            "message": event.get("message"),
            "payload": event.get("payload", {}),
            "created_at": event.get("created_at"),
        }
        for channel in channels:
            if not bool(channel["enabled"]):
                continue
            timeout = int(channel["timeout_sec"])
            ok, detail = await asyncio.to_thread(
                self._post_webhook,
                str(channel["webhook_url"]),
                payload,
                timeout,
            )
            result_type = "alert_sent" if ok else "alert_failed"
            await self._store.append_runtime_event(
                client_id=str(event.get("client_id", "")),
                event_type=result_type,
                message=f"Alert channel [{channel['name']}] {detail}",
                payload={
                    "channel_id": channel["id"],
                    "channel_name": channel["name"],
                    "original_event_type": event.get("event_type"),
                },
            )

    def _post_webhook(self, url: str, payload: dict[str, Any], timeout_sec: int) -> tuple[bool, str]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=max(1, timeout_sec)) as resp:
                status = int(resp.status)
                if 200 <= status < 300:
                    return True, f"sent ({status})"
                return False, f"non-2xx status ({status})"
        except Exception as exc:
            return False, f"failed: {exc}"
