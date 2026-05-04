from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_compose_has_host_gateway_mapping():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "extra_hosts:" in text
    assert '"host.docker.internal:host-gateway"' in text


def test_readme_mentions_bridge_host_access():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "桥接网络访问宿主机说明" in text
    assert "host.docker.internal" in text
