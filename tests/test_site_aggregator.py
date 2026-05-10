from __future__ import annotations

import urllib.error

import pytest

from app.config_store import ProxyClientConfig
from app.site_aggregator import SiteAggregator


@pytest.mark.asyncio
async def test_refresh_keeps_only_successful_http_sites():
    client = ProxyClientConfig(id="c1", name="A")

    async def load_clients():
        return [client]

    def build_links(_: ProxyClientConfig):
        return [
            {"proxy_name": "web-ok", "proxy_type": "http", "url": "http://ok.test"},
            {"proxy_name": "web-fail", "proxy_type": "https", "url": "https://bad.test"},
            {"proxy_name": "tcp-skip", "proxy_type": "tcp", "url": "http://skip.test"},
        ]

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links)

    async def fake_probe(url: str):
        if "ok.test" in url:
            from app.site_aggregator import _ProbeResult

            return _ProbeResult(ok=True, status=200, latency_ms=8, error=None)
        from app.site_aggregator import _ProbeResult

        return _ProbeResult(ok=False, status=502, latency_ms=10, error="bad gateway")

    aggregator._probe_url = fake_probe  # type: ignore[method-assign]
    await aggregator._refresh_once()
    rows = await aggregator.get_successful_sites()
    assert len(rows) == 1
    assert rows[0]["proxy_name"] == "web-ok"
    assert rows[0]["probe_ok"] is True


@pytest.mark.asyncio
async def test_probe_head_fallback_to_get(monkeypatch: pytest.MonkeyPatch):
    async def load_clients():
        return []

    def build_links(_: ProxyClientConfig):
        return []

    aggregator = SiteAggregator(load_clients=load_clients, build_jump_links=build_links, timeout_sec=1)

    class _Resp:
        def __init__(self, code: int) -> None:
            self._code = code

        def getcode(self) -> int:
            return self._code

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    calls: list[str] = []

    def fake_urlopen(req, timeout=1):
        _ = timeout
        method = req.get_method()
        calls.append(method)
        if method == "HEAD":
            raise urllib.error.HTTPError(req.full_url, 405, "method not allowed", None, None)
        return _Resp(302)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = await aggregator._probe_url("http://example.test")
    assert calls == ["HEAD", "GET"]
    assert result.ok is True
    assert result.status == 302
