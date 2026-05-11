from __future__ import annotations

import pytest

from app.config_store import ProxyClientConfig
from app.site_aggregator import SiteAggregator


@pytest.mark.asyncio
async def test_refresh_keeps_only_successful_sites_with_tcp_probe():
    client = ProxyClientConfig(id="c1", name="A")

    async def load_clients():
        return [client]

    def build_links(_: ProxyClientConfig):
        return [
            {"proxy_name": "web-ok", "proxy_type": "http", "server_addr": "ok.test", "remote_port": 80},
            {"proxy_name": "web-fail", "proxy_type": "https", "server_addr": "bad.test", "remote_port": 443},
            {
                "proxy_name": "tcp-ok",
                "proxy_type": "tcp",
                "server_addr": "127.0.0.1",
                "remote_port": 22022,
            },
            {"proxy_name": "tcp-skip", "proxy_type": "tcp", "server_addr": "", "remote_port": 9000},
        ]

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links)

    async def fake_probe_tcp(host: str, port: int):
        from app.site_aggregator import _ProbeResult
        if (host == "ok.test" and port == 80) or (host == "127.0.0.1" and port == 22022):
            return _ProbeResult(ok=True, status=200, latency_ms=4, error=None)
        return _ProbeResult(ok=False, status=None, latency_ms=None, error="connection failed")

    async def fake_region(_: str):
        return {"region_country": None, "region_province": None, "region_city": None, "region_label": "内网/未知"}

    aggregator._probe_tcp = fake_probe_tcp  # type: ignore[method-assign]
    aggregator._resolve_region = fake_region  # type: ignore[method-assign]
    await aggregator._refresh_once()
    rows = await aggregator.get_successful_sites()
    names = {item["proxy_name"] for item in rows}
    assert names == {"web-ok", "tcp-ok"}
    assert all(item["probe_ok"] is True for item in rows)
    row = next(item for item in rows if item["proxy_name"] == "tcp-ok")
    assert row["server_addr"] == "127.0.0.1"
    assert row["remote_port"] == 22022
    assert row["url"] == "http://127.0.0.1:22022"
    assert row["region_label"] == "内网/未知"


@pytest.mark.asyncio
async def test_only_server_addr_and_remote_port_are_used():
    async def load_clients():
        return [ProxyClientConfig(id="c1", name="A")]

    def build_links(_: ProxyClientConfig):
        return [
            {"proxy_name": "h1", "proxy_type": "http", "server_addr": "a.test", "remote_port": 80, "url": "https://ignored:9999"},
            {"proxy_name": "h2", "proxy_type": "https", "server_addr": "b.test", "remote_port": 443},
            {"proxy_name": "h3", "proxy_type": "tcp", "server_addr": "c.test", "remote_port": 8088},
            {"proxy_name": "bad-port", "proxy_type": "http", "server_addr": "d.test", "remote_port": 0},
            {"proxy_name": "bad-host", "proxy_type": "http", "server_addr": "", "remote_port": 1000},
        ]

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links, timeout_sec=1)

    seen: list[tuple[str, int]] = []

    async def fake_probe_tcp(host: str, port: int):
        from app.site_aggregator import _ProbeResult
        seen.append((host, port))
        return _ProbeResult(ok=True, status=200, latency_ms=2, error=None)

    async def fake_region(_: str):
        return {"region_country": "中国", "region_province": "北京", "region_city": "北京", "region_label": "中国/北京/北京"}

    aggregator._probe_tcp = fake_probe_tcp  # type: ignore[method-assign]
    aggregator._resolve_region = fake_region  # type: ignore[method-assign]
    await aggregator._refresh_once()
    assert ("a.test", 80) in seen
    assert ("b.test", 443) in seen
    assert ("c.test", 8088) in seen
    assert all(host != "d.test" for host, _ in seen)


@pytest.mark.asyncio
async def test_probe_tcp_invalid_and_exception_paths(monkeypatch: pytest.MonkeyPatch):
    async def load_clients():
        return []

    def build_links(_: ProxyClientConfig):
        return []

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links, timeout_sec=1)

    invalid = await aggregator._probe_tcp("", 0)
    assert invalid.ok is False
    assert invalid.status is None
    assert invalid.error == "invalid tcp target"

    def fake_connect(*args, **kwargs):
        _ = (args, kwargs)
        raise TimeoutError("timeout")

    monkeypatch.setattr("socket.create_connection", fake_connect)
    failed = await aggregator._probe_tcp("a.test", 80)
    assert failed.ok is False
    assert failed.status is None
    assert "timeout" in str(failed.error)


@pytest.mark.asyncio
async def test_region_private_ip_falls_back_to_unknown():
    async def load_clients():
        return []

    def build_links(_: ProxyClientConfig):
        return []

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links)

    async def fake_resolve_ip(_: str):
        return "192.168.1.9"

    aggregator._resolve_ip_for_geo = fake_resolve_ip  # type: ignore[method-assign]
    result = await aggregator._resolve_region("host.local")
    assert result["region_label"] == "内网/未知"
    assert result["region_country"] is None


@pytest.mark.asyncio
async def test_region_cache_hits_without_requery():
    async def load_clients():
        return []

    def build_links(_: ProxyClientConfig):
        return []

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links)
    aggregator._geo_cache_ttl_sec = 3600
    calls = {"lookup": 0}

    async def fake_resolve_ip(_: str):
        return "8.8.8.8"

    async def fake_lookup(_: str):
        calls["lookup"] += 1
        return {"country": "美国", "province": "加州", "city": "山景城"}

    aggregator._resolve_ip_for_geo = fake_resolve_ip  # type: ignore[method-assign]
    aggregator._lookup_geo_city = fake_lookup  # type: ignore[method-assign]

    first = await aggregator._resolve_region("google.test")
    second = await aggregator._resolve_region("google.test")
    assert first["region_label"] == "美国/加州/山景城"
    assert second["region_label"] == "美国/加州/山景城"
    assert calls["lookup"] == 1
