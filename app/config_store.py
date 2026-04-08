from __future__ import annotations

import asyncio
import json
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


def default_frpc_path() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "bin/windows/frpc.exe"
    return "bin/linux/frpc"


def default_config_text() -> str:
    return (
        'serverAddr = "127.0.0.1"\n'
        "serverPort = 7000\n\n"
        "[[proxies]]\n"
        'name = "web"\n'
        'type = "tcp"\n'
        'localIP = "127.0.0.1"\n'
        "localPort = 80\n"
        "remotePort = 6000\n"
    )


@dataclass(slots=True)
class ProxyClientConfig:
    id: str
    name: str
    frpc_path: str = field(default_factory=default_frpc_path)
    run_args: str = ""
    config_text: str = field(default_factory=default_config_text)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ClientState:
    active_client_id: str
    clients: list[ProxyClientConfig] = field(default_factory=list)


def make_client(name: str = "新客户端", client_id: str | None = None) -> ProxyClientConfig:
    cid = client_id or uuid4().hex[:12]
    return ProxyClientConfig(id=cid, name=name)


class ConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_file = data_dir / "client_config.json"
        self._legacy_active_config_file = data_dir / "frpc.toml"
        self._configs_dir = data_dir / "configs"
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        await asyncio.to_thread(self._data_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._configs_dir.mkdir, parents=True, exist_ok=True)
        state = await self.load_state()
        if not state.clients:
            default_client = make_client(name="默认客户端", client_id="default")
            state = ClientState(active_client_id=default_client.id, clients=[default_client])
        await self.save_state(state)

    async def load_state(self) -> ClientState:
        async with self._lock:
            payload = await asyncio.to_thread(self._read_json)
        return self._state_from_payload(payload)

    async def save_state(self, state: ClientState) -> ClientState:
        normalized = self._normalize_state(state)
        async with self._lock:
            await asyncio.to_thread(self._persist_state, normalized)
        return normalized

    def frpc_config_file(self, client_id: str) -> Path:
        safe = self._safe_filename(client_id)
        return self._configs_dir / f"{safe}.toml"

    def _read_json(self) -> dict[str, Any]:
        if not self._data_file.exists():
            return {}
        text = self._data_file.read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
            if isinstance(raw, dict):
                return raw
            return {}
        except json.JSONDecodeError:
            return {}

    def _persist_state(self, state: ClientState) -> None:
        payload = asdict(state)
        self._data_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        expected_names: set[str] = set()
        for client in state.clients:
            cfg_path = self.frpc_config_file(client.id)
            cfg_path.write_text(client.config_text, encoding="utf-8")
            expected_names.add(cfg_path.name)

        for stale in self._configs_dir.glob("*.toml"):
            if stale.name not in expected_names:
                stale.unlink(missing_ok=True)

        active = self._find_client(state, state.active_client_id)
        if active is None and state.clients:
            active = state.clients[0]
        if active is not None:
            self._legacy_active_config_file.write_text(active.config_text, encoding="utf-8")

    def _state_from_payload(self, payload: dict[str, Any]) -> ClientState:
        # Migration path: old single-client shape.
        if "clients" not in payload:
            single = self._from_legacy_payload(payload)
            return self._normalize_state(
                ClientState(
                    active_client_id=single.id,
                    clients=[single],
                )
            )

        raw_clients = payload.get("clients")
        clients: list[ProxyClientConfig] = []
        if isinstance(raw_clients, list):
            for index, item in enumerate(raw_clients):
                if not isinstance(item, dict):
                    continue
                clients.append(self._from_client_payload(item, index=index))

        active_id = str(payload.get("active_client_id", "")).strip()
        return self._normalize_state(
            ClientState(active_client_id=active_id, clients=clients)
        )

    def _from_legacy_payload(self, payload: dict[str, Any]) -> ProxyClientConfig:
        raw_frpc_path = str(payload.get("frpc_path", default_frpc_path()))
        if raw_frpc_path in {"bin/frpc.exe", "bin/frpc"}:
            raw_frpc_path = default_frpc_path()
        return ProxyClientConfig(
            id="default",
            name=str(payload.get("name", "默认客户端")).strip() or "默认客户端",
            frpc_path=raw_frpc_path,
            run_args=str(payload.get("run_args", "")),
            config_text=str(payload.get("config_text", default_config_text())),
            env=self._safe_env(payload.get("env", {})),
        )

    def _from_client_payload(self, payload: dict[str, Any], index: int) -> ProxyClientConfig:
        raw_id = str(payload.get("id", "")).strip() or f"client-{index + 1}"
        client_id = self._safe_filename(raw_id)
        if not client_id:
            client_id = uuid4().hex[:12]

        raw_frpc_path = str(payload.get("frpc_path", default_frpc_path()))
        if raw_frpc_path in {"bin/frpc.exe", "bin/frpc"}:
            raw_frpc_path = default_frpc_path()

        return ProxyClientConfig(
            id=client_id,
            name=str(payload.get("name", f"客户端 {index + 1}")).strip() or f"客户端 {index + 1}",
            frpc_path=raw_frpc_path,
            run_args=str(payload.get("run_args", "")),
            config_text=str(payload.get("config_text", default_config_text())),
            env=self._safe_env(payload.get("env", {})),
        )

    def _normalize_state(self, state: ClientState) -> ClientState:
        dedup: list[ProxyClientConfig] = []
        used_ids: set[str] = set()

        for idx, client in enumerate(state.clients):
            cid = self._safe_filename(client.id)
            if not cid:
                cid = f"client-{idx + 1}"
            if cid in used_ids:
                cid = f"{cid}-{uuid4().hex[:4]}"
            used_ids.add(cid)

            dedup.append(
                ProxyClientConfig(
                    id=cid,
                    name=client.name.strip() or f"客户端 {idx + 1}",
                    frpc_path=(client.frpc_path.strip() or default_frpc_path()),
                    run_args=client.run_args.strip(),
                    config_text=client.config_text or default_config_text(),
                    env=self._safe_env(client.env),
                )
            )

        if not dedup:
            default_client = make_client(name="默认客户端", client_id="default")
            dedup = [default_client]

        active_id = state.active_client_id.strip()
        if not active_id or not any(client.id == active_id for client in dedup):
            active_id = dedup[0].id

        return ClientState(active_client_id=active_id, clients=dedup)

    def _find_client(self, state: ClientState, client_id: str) -> ProxyClientConfig | None:
        for client in state.clients:
            if client.id == client_id:
                return client
        return None

    def _safe_env(self, maybe_env: Any) -> dict[str, str]:
        if not isinstance(maybe_env, dict):
            return {}
        clean_env: dict[str, str] = {}
        for key, value in maybe_env.items():
            clean_env[str(key)] = str(value)
        return clean_env

    def _safe_filename(self, value: str) -> str:
        raw = str(value).strip()
        safe = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
        return safe

