from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config_store import ProxyClientConfig

JumpLinksBuilder = Callable[[ProxyClientConfig], list[Any]]
LoadClientsCallable = Callable[[], Awaitable[list[ProxyClientConfig]]]
ProbeFailureCallback = Callable[[str, str, dict[str, object]], Awaitable[None]]

_UNKNOWN_REGION_LABEL = "内网/未知"
_LOG = logging.getLogger(__name__)


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

        self._geo_api_base = os.getenv("FRP_PANEL_GEO_API_BASE", "https://api.ip.sb/geoip").strip() or "https://api.ip.sb/geoip"
        try:
            self._geo_api_timeout_sec = max(0.2, float(os.getenv("FRP_PANEL_GEO_API_TIMEOUT_SEC", "2")))
        except ValueError:
            self._geo_api_timeout_sec = 2.0
        try:
            geo_ttl = int(os.getenv("FRP_PANEL_GEOIP_CACHE_TTL_SEC", "1800"))
        except ValueError:
            geo_ttl = 1800
        self._geo_cache_ttl_sec = max(60, geo_ttl)
        try:
            self._geo_api_concurrency = max(1, int(os.getenv("FRP_PANEL_GEO_API_CONCURRENCY", "8")))
        except ValueError:
            self._geo_api_concurrency = 8
        self._geo_lookup_sem = asyncio.Semaphore(self._geo_api_concurrency)
        self._geo_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self._geo_lock = asyncio.Lock()
        self._deprecated_geo_warned = False

        deprecated_geo_db_path = os.getenv("FRP_PANEL_GEOIP_DB_PATH")
        if deprecated_geo_db_path:
            self._warn_deprecated_geo_db_path(deprecated_geo_db_path)

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
        successful.sort(
            key=lambda item: (
                str(item.get("region_label", _UNKNOWN_REGION_LABEL)),
                str(item["client_name"]),
                str(item["proxy_name"]),
            )
        )
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
                "server_addr": getattr(link, "server_addr", ""),
                "remote_port": getattr(link, "remote_port", None),
            }
        proxy_type = str(link_data.get("proxy_type", "")).strip().lower() or "tcp"
        server_addr = str(link_data.get("server_addr", "")).strip()
        remote_port = link_data.get("remote_port")
        try:
            remote_port_value = int(remote_port) if remote_port is not None else None
        except (TypeError, ValueError):
            return None
        if not server_addr or remote_port_value is None or remote_port_value <= 0:
            return None
        proxy_name = str(link_data.get("proxy_name", "")).strip() or "proxy"
        return {
            "client_id": client.id,
            "client_name": client.name,
            "proxy_name": proxy_name,
            "proxy_type": proxy_type,
            "url": f"http://{server_addr}:{remote_port_value}",
            "server_addr": server_addr,
            "remote_port": remote_port_value,
            "region_country": None,
            "region_province": None,
            "region_city": None,
            "region_label": _UNKNOWN_REGION_LABEL,
            "probe_ok": False,
            "http_status": None,
            "latency_ms": None,
            "last_checked_at": None,
            "error": None,
        }

    async def _probe_many(self, items: list[dict[str, object]]) -> list[dict[str, object]]:
        if not items:
            return []
        probe_sem = asyncio.Semaphore(12)

        async def worker(row: dict[str, object]) -> dict[str, object]:
            host = str(row.get("server_addr") or "")
            port = row.get("remote_port")

            async with probe_sem:
                result = await self._probe_tcp(host, int(port) if isinstance(port, int) else 0)
            region = await self._resolve_region(host)

            now = time.time()
            row["probe_ok"] = result.ok
            row["http_status"] = result.status
            row["latency_ms"] = result.latency_ms
            row["last_checked_at"] = now
            row["error"] = result.error
            row["region_country"] = region.get("region_country")
            row["region_province"] = region.get("region_province")
            row["region_city"] = region.get("region_city")
            row["region_label"] = region.get("region_label", _UNKNOWN_REGION_LABEL)
            return row

        tasks = [asyncio.create_task(worker(dict(item))) for item in items]
        return await asyncio.gather(*tasks)

    async def _probe_tcp(self, host: str, port: int) -> _ProbeResult:
        if not host or port <= 0:
            return _ProbeResult(ok=False, status=None, latency_ms=None, error="invalid tcp target")

        def _connect() -> int:
            start = time.perf_counter()
            with socket.create_connection((host, port), timeout=self._timeout_sec):
                pass
            latency = int((time.perf_counter() - start) * 1000)
            return max(1, latency)

        try:
            latency = await asyncio.to_thread(_connect)
            return _ProbeResult(ok=True, status=200, latency_ms=latency, error=None)
        except Exception as exc:
            return _ProbeResult(ok=False, status=None, latency_ms=None, error=str(exc))

    async def _resolve_region(self, host: str) -> dict[str, object]:
        if not host.strip():
            return self._unknown_region()

        ip_text = await self._resolve_ip_for_geo(host)
        if not ip_text:
            return self._unknown_region()

        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            return self._unknown_region()

        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return self._unknown_region()

        cache_key = str(ip_obj)
        now = time.time()
        async with self._geo_lock:
            cached = self._geo_cache.get(cache_key)
            if cached and cached[0] > now:
                return dict(cached[1])

        async with self._geo_lookup_sem:
            ip_sb = await self._lookup_ip_sb_region(cache_key)

        if ip_sb is None:
            region = self._unknown_region()
        else:
            country = self._pick_text(ip_sb, ["country", "country_name", "country_cn", "countryCode", "country_code"])
            province = self._pick_text(ip_sb, ["region", "region_name", "province", "province_name", "state", "state_name"])
            city = self._pick_text(ip_sb, ["city", "city_name"])
            parts = [item for item in [country, province, city] if item]
            label = "/".join(parts) if parts else _UNKNOWN_REGION_LABEL
            region = {
                "region_country": country,
                "region_province": province,
                "region_city": city,
                "region_label": label,
            }

        async with self._geo_lock:
            self._geo_cache[cache_key] = (now + self._geo_cache_ttl_sec, dict(region))
        return region

    async def _resolve_ip_for_geo(self, host: str) -> str | None:
        try:
            ip_obj = ipaddress.ip_address(host)
            return str(ip_obj)
        except ValueError:
            pass

        def _resolve() -> str | None:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            if not infos:
                return None
            for info in infos:
                addr = info[4][0] if info[4] else None
                if addr:
                    return str(addr)
            return None

        try:
            return await asyncio.to_thread(_resolve)
        except Exception:
            return None

    async def _lookup_ip_sb_region(self, ip_text: str) -> dict[str, object] | None:
        base = self._geo_api_base.rstrip("/")
        url = f"{base}/{ip_text}"

        def _fetch() -> dict[str, object] | None:
            req = urllib.request.Request(
                url=url,
                method="GET",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "frp-web-client/geo",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self._geo_api_timeout_sec) as resp:
                    status = int(resp.getcode())
                    if status < 200 or status >= 300:
                        return None
                    body = resp.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError, OSError):
                return None
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None

        return await asyncio.to_thread(_fetch)

    def _pick_text(self, payload: dict[str, object], keys: list[str]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _unknown_region(self) -> dict[str, object]:
        return {
            "region_country": None,
            "region_province": None,
            "region_city": None,
            "region_label": _UNKNOWN_REGION_LABEL,
        }

    def _warn_deprecated_geo_db_path(self, value: str) -> None:
        if self._deprecated_geo_warned:
            return
        self._deprecated_geo_warned = True
        _LOG.warning(
            "FRP_PANEL_GEOIP_DB_PATH is deprecated and ignored: %s. "
            "Use FRP_PANEL_GEO_API_BASE/FRP_PANEL_GEO_API_TIMEOUT_SEC instead.",
            value,
        )

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
