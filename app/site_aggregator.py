from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config_store import ProxyClientConfig

JumpLinksBuilder = Callable[[ProxyClientConfig], list[Any]]
LoadClientsCallable = Callable[[], Awaitable[list[ProxyClientConfig]]]
ProbeFailureCallback = Callable[[str, str, dict[str, object]], Awaitable[None]]


@dataclass(slots=True)
class _ProbeResult:
    ok: bool
    status: int | None
    latency_ms: int | None
    error: str | None


class SiteAggregator:
    def __init__(
        self,
        load_clients: LoadClientsCallable,
        build_jump_links: JumpLinksBuilder,
        on_probe_failure: ProbeFailureCallback | None = None,
        interval_sec: float = 15.0,
        timeout_sec: float = 2.0,
    ) -> None:
        self._load_clients = load_clients
        self._build_jump_links = build_jump_links
        self._on_probe_failure = on_probe_failure
        self._interval_sec = max(5.0, float(interval_sec))
        self._timeout_sec = max(0.5, float(timeout_sec))
        self._snapshot: list[dict[str, object]] = []
        self._snapshot_hash = ""
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._last_failure_emit_at: dict[str, float] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._refresh_once()
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        async with self._lock:
            self._subscribers.clear()

    async def subscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        snapshot = await self.get_successful_sites()
        async with self._lock:
            self._subscribers.add(queue)
        await queue.put({"event": "snapshot", "data": {"items": snapshot}})

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def get_successful_sites(self) -> list[dict[str, object]]:
        async with self._lock:
            return [dict(item) for item in self._snapshot]

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_sec)
            await self._refresh_once()

    async def _refresh_once(self) -> None:
        try:
            clients = await self._load_clients()
        except Exception:
            return

        candidates: list[dict[str, object]] = []
        for client in clients:
            for link in self._build_jump_links(client):
                row = self._normalize_link(client, link)
                if row is not None:
                    candidates.append(row)

        probed = await self._probe_many(candidates)
        successful = [item for item in probed if bool(item["probe_ok"])]
        successful.sort(key=lambda item: (str(item["client_name"]), str(item["proxy_name"]), str(item["url"])))
        await self._emit_probe_failures(probed)

        encoded = json.dumps(successful, ensure_ascii=False, sort_keys=True)
        changed = False
        async with self._lock:
            if encoded != self._snapshot_hash:
                self._snapshot_hash = encoded
                self._snapshot = successful
                changed = True
            subscribers = list(self._subscribers)
        if changed:
            payload = {"items": successful}
            dead: list[asyncio.Queue[dict[str, object]]] = []
            for queue in subscribers:
                try:
                    queue.put_nowait({"event": "update", "data": payload})
                except asyncio.QueueFull:
                    dead.append(queue)
            if dead:
                async with self._lock:
                    for queue in dead:
                        self._subscribers.discard(queue)

    def _normalize_link(self, client: ProxyClientConfig, link: Any) -> dict[str, object] | None:
        if isinstance(link, dict):
            link_data = link
        else:
            link_data = {
                "proxy_name": getattr(link, "proxy_name", ""),
                "proxy_type": getattr(link, "proxy_type", ""),
                "url": getattr(link, "url", ""),
            }
        proxy_type = str(link_data.get("proxy_type", "")).strip().lower()
        if proxy_type not in {"http", "https"}:
            return None
        url = str(link_data.get("url", "")).strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return None
        proxy_name = str(link_data.get("proxy_name", "")).strip() or "proxy"
        return {
            "client_id": client.id,
            "client_name": client.name,
            "proxy_name": proxy_name,
            "proxy_type": proxy_type,
            "url": url,
            "probe_ok": False,
            "http_status": None,
            "latency_ms": None,
            "last_checked_at": None,
            "error": None,
        }

    async def _probe_many(self, items: list[dict[str, object]]) -> list[dict[str, object]]:
        if not items:
            return []
        sem = asyncio.Semaphore(12)

        async def worker(row: dict[str, object]) -> dict[str, object]:
            async with sem:
                result = await self._probe_url(str(row["url"]))
            now = time.time()
            row["probe_ok"] = result.ok
            row["http_status"] = result.status
            row["latency_ms"] = result.latency_ms
            row["last_checked_at"] = now
            row["error"] = result.error
            return row

        tasks = [asyncio.create_task(worker(dict(item))) for item in items]
        return await asyncio.gather(*tasks)

    async def _probe_url(self, url: str) -> _ProbeResult:
        def _request(method: str) -> tuple[int, int]:
            start = time.perf_counter()
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:
                status = int(resp.getcode())
            latency = int((time.perf_counter() - start) * 1000)
            return status, max(1, latency)

        try:
            status, latency = await asyncio.to_thread(_request, "HEAD")
            if 200 <= status < 400:
                return _ProbeResult(ok=True, status=status, latency_ms=latency, error=None)
        except Exception:
            status = None

        try:
            status_get, latency_get = await asyncio.to_thread(_request, "GET")
            if 200 <= status_get < 400:
                return _ProbeResult(ok=True, status=status_get, latency_ms=latency_get, error=None)
            return _ProbeResult(
                ok=False,
                status=status_get,
                latency_ms=latency_get,
                error=f"http status {status_get}",
            )
        except urllib.error.HTTPError as exc:
            return _ProbeResult(
                ok=False,
                status=int(exc.code),
                latency_ms=None,
                error=f"http status {exc.code}",
            )
        except Exception as exc:
            return _ProbeResult(ok=False, status=None, latency_ms=None, error=str(exc))

    async def _emit_probe_failures(self, rows: list[dict[str, object]]) -> None:
        if self._on_probe_failure is None:
            return
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            if bool(row.get("probe_ok")):
                continue
            cid = str(row.get("client_id", ""))
            if not cid:
                continue
            grouped.setdefault(cid, []).append(row)
        if not grouped:
            return

        now = time.time()
        for client_id, failures in grouped.items():
            prev = self._last_failure_emit_at.get(client_id, 0.0)
            if now - prev < 60:
                continue
            self._last_failure_emit_at[client_id] = now
            sample = [
                {
                    "proxy_name": str(item.get("proxy_name", "")),
                    "url": str(item.get("url", "")),
                    "error": item.get("error"),
                    "http_status": item.get("http_status"),
                }
                for item in failures[:3]
            ]
            try:
                await self._on_probe_failure(
                    client_id,
                    f"站点探活失败 {len(failures)} 条。",
                    {"failed_count": len(failures), "samples": sample, "checked_at": now},
                )
            except Exception:
                continue
