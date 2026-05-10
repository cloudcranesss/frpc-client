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
            {"proxy_name": "web-ok", "proxy_type": "http", "url": "http://ok.test"},
            {"proxy_name": "web-fail", "proxy_type": "https", "url": "https://bad.test"},
            {
                "proxy_name": "tcp-ok",
                "proxy_type": "tcp",
                "server_addr": "127.0.0.1",
                "remote_port": 22022,
                "url": "http://ignored.test",
            },
            {"proxy_name": "tcp-skip", "proxy_type": "tcp", "url": "http://skip.test"},
        ]

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links)

    async def fake_probe_tcp(host: str, port: int):
        from app.site_aggregator import _ProbeResult
        if (host == "ok.test" and port == 80) or (host == "127.0.0.1" and port == 22022):
            return _ProbeResult(ok=True, status=200, latency_ms=4, error=None)
        return _ProbeResult(ok=False, status=None, latency_ms=None, error="connection failed")

    aggregator._probe_tcp = fake_probe_tcp  # type: ignore[method-assign]
    await aggregator._refresh_once()
    rows = await aggregator.get_successful_sites()
    names = {item["proxy_name"] for item in rows}
    assert names == {"web-ok", "tcp-ok"}
    assert all(item["probe_ok"] is True for item in rows)


@pytest.mark.asyncio
async def test_http_url_port_parsing_for_tcp_probe():
    async def load_clients():
        return [ProxyClientConfig(id="c1", name="A")]

    def build_links(_: ProxyClientConfig):
        return [
            {"proxy_name": "h1", "proxy_type": "http", "url": "http://a.test"},
            {"proxy_name": "h2", "proxy_type": "https", "url": "https://b.test"},
            {"proxy_name": "h3", "proxy_type": "http", "url": "http://c.test:8088"},
            {"proxy_name": "bad", "proxy_type": "http", "url": "http://bad.test:abc"},
        ]

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links, timeout_sec=1)

    seen: list[tuple[str, int]] = []

    async def fake_probe_tcp(host: str, port: int):
        from app.site_aggregator import _ProbeResult
        seen.append((host, port))
        return _ProbeResult(ok=True, status=200, latency_ms=2, error=None)

    aggregator._probe_tcp = fake_probe_tcp  # type: ignore[method-assign]
    await aggregator._refresh_once()
    assert ("a.test", 80) in seen
    assert ("b.test", 443) in seen
    assert ("c.test", 8088) in seen
    assert all(host != "bad.test" for host, _ in seen)


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
