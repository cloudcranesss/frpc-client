from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
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

        self._geo_db_path = Path(
            os.getenv("FRP_PANEL_GEOIP_DB_PATH", "/app/data/GeoLite2-City.mmdb").strip() or "/app/data/GeoLite2-City.mmdb"
        )
        try:
            geo_ttl = int(os.getenv("FRP_PANEL_GEOIP_CACHE_TTL_SEC", "1800"))
        except ValueError:
            geo_ttl = 1800
        self._geo_cache_ttl_sec = max(60, geo_ttl)
        self._geo_reader: object | None = None
        self._geo_reader_checked = False
        self._geo_lock = asyncio.Lock()
        self._geo_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self._geo_warned = False

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
        reader = self._geo_reader
        self._geo_reader = None
        if reader is not None:
            close = getattr(reader, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

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
        successful.sort(key=lambda item: (str(item.get("region_label", _UNKNOWN_REGION_LABEL)), str(item["client_name"]), str(item["proxy_name"])))
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
        sem = asyncio.Semaphore(12)

        async def worker(row: dict[str, object]) -> dict[str, object]:
            async with sem:
                host = str(row.get("server_addr") or "")
                port = row.get("remote_port")
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
        key = host.strip().lower()
        if not key:
            return {"region_country": None, "region_province": None, "region_city": None, "region_label": _UNKNOWN_REGION_LABEL}
        now = time.time()
        async with self._geo_lock:
            cached = self._geo_cache.get(key)
            if cached and cached[0] > now:
                return dict(cached[1])

        region = await self._resolve_region_uncached(key)
        async with self._geo_lock:
            self._geo_cache[key] = (now + self._geo_cache_ttl_sec, dict(region))
        return region

    async def _resolve_region_uncached(self, host: str) -> dict[str, object]:
        ip_text = await self._resolve_ip_for_geo(host)
        if not ip_text:
            return {"region_country": None, "region_province": None, "region_city": None, "region_label": _UNKNOWN_REGION_LABEL}

        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            return {"region_country": None, "region_province": None, "region_city": None, "region_label": _UNKNOWN_REGION_LABEL}

        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return {"region_country": None, "region_province": None, "region_city": None, "region_label": _UNKNOWN_REGION_LABEL}

        geo_row = await self._lookup_geo_city(ip_text)
        if geo_row is None:
            return {"region_country": None, "region_province": None, "region_city": None, "region_label": _UNKNOWN_REGION_LABEL}

        country = str(geo_row.get("country") or "").strip() or None
        province = str(geo_row.get("province") or "").strip() or None
        city = str(geo_row.get("city") or "").strip() or None
        parts = [item for item in [country, province, city] if item]
        label = "/".join(parts) if parts else _UNKNOWN_REGION_LABEL
        return {
            "region_country": country,
            "region_province": province,
            "region_city": city,
            "region_label": label,
        }

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

    async def _lookup_geo_city(self, ip_text: str) -> dict[str, str] | None:
        reader = await self._get_geo_reader()
        if reader is None:
            return None

        def _extract_name(names: dict[str, str] | None) -> str | None:
            if not names:
                return None
            return names.get("zh-CN") or names.get("en") or next(iter(names.values()), None)

        def _lookup() -> dict[str, str] | None:
            try:
                city_response = reader.city(ip_text)  # type: ignore[union-attr]
            except Exception:
                return None
            country = _extract_name(getattr(getattr(city_response, "country", None), "names", None))
            subdivision_name = None
            subdivisions = getattr(city_response, "subdivisions", None)
            if subdivisions:
                with contextlib.suppress(Exception):
                    subdivision_name = _extract_name(subdivisions[0].names)
            city_name = _extract_name(getattr(getattr(city_response, "city", None), "names", None))
            return {
                "country": country or "",
                "province": subdivision_name or "",
                "city": city_name or "",
            }

        return await asyncio.to_thread(_lookup)

    async def _get_geo_reader(self) -> object | None:
        async with self._geo_lock:
            if self._geo_reader_checked:
                return self._geo_reader
            self._geo_reader_checked = True
            try:
                from geoip2.database import Reader as GeoReader
            except Exception as exc:
                self._warn_geo_once(f"geoip2 unavailable: {exc}")
                self._geo_reader = None
                return None
            if not self._geo_db_path.exists():
                self._warn_geo_once(f"geo database not found: {self._geo_db_path}")
                self._geo_reader = None
                return None
            try:
                self._geo_reader = GeoReader(str(self._geo_db_path))
                return self._geo_reader
            except Exception as exc:
                self._warn_geo_once(f"geo database open failed: {exc}")
                self._geo_reader = None
                return None

    def _warn_geo_once(self, message: str) -> None:
        if self._geo_warned:
            return
        self._geo_warned = True
        _LOG.warning("GeoIP disabled, fallback to 内网/未知: %s", message)

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
