from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_compose_uses_host_network_and_custom_port():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "network_mode: host" in text
    assert "FRP_PANEL_PORT=8000" in text
    assert "--port $${FRP_PANEL_PORT:-8000}" in text
    assert "127.0.0.1:$${FRP_PANEL_PORT:-8000}" in text


def test_readme_mentions_host_network_port_config():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Docker Compose（Host 网络 + 端口可配置）" in text
    assert "FRP_PANEL_PORT" in text
