from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
import platform
import shutil
import tomllib

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from .auth import AuthManager
    from .config_store import (
        ClientState,
        ConfigStore,
        ProxyClientConfig,
        default_frpc_path,
        make_client,
    )
    from .frpc_manager import FrpcManager
    from .schemas import (
        AppConfigPayload,
        AutoFrpcPathResponse,
        AuthStatusResponse,
        ChangePasswordPayload,
        ClientCreatePayload,
        ClientListItem,
        ClientsResponse,
        FrpcPathCandidate,
        LoginPayload,
        LoginResponse,
        LogsResponse,
        JumpLinkItem,
        JumpLinksResponse,
        StatusResponse,
    )
except ImportError:
    from auth import AuthManager
    from config_store import (
        ClientState,
        ConfigStore,
        ProxyClientConfig,
        default_frpc_path,
        make_client,
    )
    from frpc_manager import FrpcManager
    from schemas import (
        AppConfigPayload,
        AutoFrpcPathResponse,
        AuthStatusResponse,
        ChangePasswordPayload,
        ClientCreatePayload,
        ClientListItem,
        ClientsResponse,
        FrpcPathCandidate,
        LoginPayload,
        LoginResponse,
        LogsResponse,
        JumpLinkItem,
        JumpLinksResponse,
        StatusResponse,
    )

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"

config_store = ConfigStore(DATA_DIR)
frpc_manager = FrpcManager()
auth_manager = AuthManager(DATA_DIR)


def _is_public_path(path: str) -> bool:
    if path.startswith("/web/"):
        return True
    if path in {"/login"}:
        return True
    if path.startswith("/api/auth/"):
        return True
    return False


async def _authenticated_username(request: Request) -> str | None:
    token = request.cookies.get(auth_manager.cookie_name)
    return await auth_manager.get_session_username(token)


def normalize_frpc_path(path_value: str) -> str:
    value = path_value.strip()
    if value in {"bin/frpc.exe", "bin/frpc"}:
        value = default_frpc_path()
    if ("/" in value or "\\" in value) and not Path(value).is_absolute():
        return str((BASE_DIR / value).resolve())
    return value


def detect_frpc_path() -> AutoFrpcPathResponse:
    is_windows = platform.system().lower() == "windows"
    preferred = "bin/windows/frpc.exe" if is_windows else "bin/linux/frpc"
    secondary = "bin/linux/frpc" if is_windows else "bin/windows/frpc.exe"
    legacy = "bin/frpc.exe"
    ordered = [preferred, secondary, legacy]

    candidates: list[FrpcPathCandidate] = []
    for rel in ordered:
        resolved = (BASE_DIR / rel).resolve()
        exists = resolved.exists() and resolved.is_file()
        candidates.append(
            FrpcPathCandidate(
                path=rel,
                exists=exists,
                resolved_path=str(resolved),
            )
        )

    in_path = shutil.which("frpc") or shutil.which("frpc.exe")
    candidates.append(
        FrpcPathCandidate(
            path="frpc",
            exists=in_path is not None,
            resolved_path=in_path,
        )
    )

    selected = next((item for item in candidates if item.exists), candidates[0])
    return AutoFrpcPathResponse(
        selected_path=selected.path,
        selected_exists=selected.exists,
        candidates=candidates,
    )


def _client_to_payload(client: ProxyClientConfig) -> AppConfigPayload:
    return AppConfigPayload(
        id=client.id,
        name=client.name,
        frpc_path=client.frpc_path,
        run_args=client.run_args,
        config_text=client.config_text,
        env=client.env,
    )


def _find_client(state: ClientState, client_id: str) -> ProxyClientConfig:
    for client in state.clients:
        if client.id == client_id:
            return client
    raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")


def _active_client(state: ClientState) -> ProxyClientConfig:
    for client in state.clients:
        if client.id == state.active_client_id:
            return client
    if state.clients:
        return state.clients[0]
    raise HTTPException(status_code=404, detail="No client exists.")


def _clients_response(state: ClientState) -> ClientsResponse:
    clients = []
    for client in state.clients:
        status = frpc_manager.status(client.id)
        clients.append(
            ClientListItem(
                id=client.id,
                name=client.name,
                running=bool(status["running"]),
                pid=status["pid"],
                uptime_sec=float(status["uptime_sec"] or 0),
                last_exit_code=status["last_exit_code"],
            )
        )
    return ClientsResponse(active_client_id=state.active_client_id, clients=clients)


def _guess_link_scheme(proxy_type: str, remote_port: int, local_port: int | None) -> str:
    proxy_type = proxy_type.lower()
    if proxy_type == "https":
        return "https"
    if proxy_type == "http":
        return "http"
    if remote_port == 443 or local_port == 443:
        return "https"
    return "http"


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return None


def _build_jump_links(client: ProxyClientConfig) -> list[JumpLinkItem]:
    config_text = client.config_text.strip()
    if not config_text:
        return []
    try:
        parsed = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return []

    server_addr = str(parsed.get("serverAddr", "")).strip()
    if not server_addr:
        return []

    proxies = parsed.get("proxies", [])
    if not isinstance(proxies, list):
        return []

    items: list[JumpLinkItem] = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        remote_port = _safe_int(proxy.get("remotePort"))
        if remote_port is None:
            continue
        local_port = _safe_int(proxy.get("localPort"))
        proxy_name = str(proxy.get("name", f"proxy-{len(items) + 1}")).strip() or f"proxy-{len(items) + 1}"
        proxy_type = str(proxy.get("type", "tcp")).strip().lower() or "tcp"
        scheme = _guess_link_scheme(proxy_type, remote_port, local_port)
        items.append(
            JumpLinkItem(
                proxy_name=proxy_name,
                proxy_type=proxy_type,
                server_addr=server_addr,
                remote_port=remote_port,
                local_port=local_port,
                url=f"{scheme}://{server_addr}:{remote_port}",
            )
        )
    return items


async def _resolve_start_config(client_id: str) -> ProxyClientConfig:
    state = await config_store.load_state()
    client = _find_client(state, client_id)

    changed = False
    if not client.frpc_path.strip():
        auto = detect_frpc_path()
        if not auto.selected_exists:
            raise HTTPException(
                status_code=400,
                detail=(
                    "frpc_path is empty and no executable was auto-detected. "
                    "Run `python scripts/fetch_frpc.py` or fill frpc_path manually."
                ),
            )
        client.frpc_path = auto.selected_path
        changed = True

    if not client.config_text.strip():
        raise HTTPException(status_code=400, detail="config_text cannot be empty.")

    client.frpc_path = normalize_frpc_path(client.frpc_path)
    if ("/" in client.frpc_path or "\\" in client.frpc_path) and not Path(client.frpc_path).exists():
        auto = detect_frpc_path()
        if not auto.selected_exists:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"frpc executable not found: {client.frpc_path}. "
                    "Run `python scripts/fetch_frpc.py` or fill a valid path."
                ),
            )
        client.frpc_path = normalize_frpc_path(auto.selected_path)
        changed = True

    if changed:
        await config_store.save_state(state)

    return client


@asynccontextmanager
async def lifespan(_: FastAPI):
    await auth_manager.init()
    await config_store.init()
    yield
    await frpc_manager.shutdown()


app = FastAPI(
    title="FRP Web Client",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if _is_public_path(path):
        return await call_next(request)

    username = await _authenticated_username(request)
    if username:
        request.state.username = username
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return RedirectResponse(url="/login", status_code=302)


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/login")
async def login_page(request: Request):
    username = await _authenticated_username(request)
    if username:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(WEB_DIR / "login.html")


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(payload: LoginPayload, response: Response) -> LoginResponse:
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    ok = await auth_manager.verify_login(username, password)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = await auth_manager.create_session(username)
    secure_cookie = os.getenv("FRP_PANEL_SECURE_COOKIE", "false").lower() in {"1", "true", "yes"}
    response.set_cookie(
        key=auth_manager.cookie_name,
        value=token,
        max_age=60 * 60 * 24,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    return LoginResponse(success=True, username=username)


@app.post("/api/auth/logout", response_model=LoginResponse)
async def logout(request: Request, response: Response) -> LoginResponse:
    token = request.cookies.get(auth_manager.cookie_name)
    await auth_manager.delete_session(token)
    response.delete_cookie(auth_manager.cookie_name, path="/")
    return LoginResponse(success=True, username=None)


@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    username = await _authenticated_username(request)
    return AuthStatusResponse(authenticated=bool(username), username=username)


@app.post("/api/auth/change-password", response_model=LoginResponse)
async def change_password(request: Request, payload: ChangePasswordPayload) -> LoginResponse:
    username = await _authenticated_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    updated = await auth_manager.change_password(
        username=username,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Password update failed.")
    token = request.cookies.get(auth_manager.cookie_name)
    await auth_manager.delete_session(token)
    return LoginResponse(success=True, username=username)


@app.get("/api/clients", response_model=ClientsResponse)
async def list_clients() -> ClientsResponse:
    state = await config_store.load_state()
    return _clients_response(state)


@app.post("/api/clients", response_model=ClientsResponse)
async def create_client(payload: ClientCreatePayload) -> ClientsResponse:
    state = await config_store.load_state()
    name = payload.name.strip() or f"客户端 {len(state.clients) + 1}"
    client = make_client(name=name)
    state.clients.append(client)
    state.active_client_id = client.id
    saved = await config_store.save_state(state)
    return _clients_response(saved)


@app.post("/api/clients/{client_id}/select", response_model=ClientsResponse)
async def select_client(client_id: str) -> ClientsResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    state.active_client_id = client_id
    saved = await config_store.save_state(state)
    return _clients_response(saved)


@app.delete("/api/clients/{client_id}", response_model=ClientsResponse)
async def delete_client(client_id: str) -> ClientsResponse:
    state = await config_store.load_state()
    if len(state.clients) <= 1:
        raise HTTPException(status_code=400, detail="At least one client must remain.")

    kept: list[ProxyClientConfig] = []
    deleted = False
    for client in state.clients:
        if client.id == client_id:
            deleted = True
            continue
        kept.append(client)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

    state.clients = kept
    if state.active_client_id == client_id:
        state.active_client_id = kept[0].id
    saved = await config_store.save_state(state)
    await frpc_manager.drop_client(client_id)
    return _clients_response(saved)


@app.get("/api/clients/{client_id}/config", response_model=AppConfigPayload)
async def get_client_config(client_id: str) -> AppConfigPayload:
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    return _client_to_payload(client)


@app.put("/api/clients/{client_id}/config", response_model=AppConfigPayload)
async def update_client_config(client_id: str, payload: AppConfigPayload) -> AppConfigPayload:
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    client.name = payload.name.strip() or client.name
    client.frpc_path = payload.frpc_path.strip()
    client.run_args = payload.run_args.strip()
    client.config_text = payload.config_text
    client.env = payload.env
    saved = await config_store.save_state(state)
    updated = _find_client(saved, client_id)
    return _client_to_payload(updated)


@app.get("/api/clients/{client_id}/status", response_model=StatusResponse)
async def client_status(client_id: str) -> StatusResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    return StatusResponse(**frpc_manager.status(client_id))


@app.post("/api/clients/{client_id}/start", response_model=StatusResponse)
async def start_client(client_id: str) -> StatusResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    cfg = await _resolve_start_config(client_id)
    try:
        status_payload = await frpc_manager.start(
            client_id,
            cfg,
            config_store.frpc_config_file(client_id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start frpc: {exc}") from exc
    return StatusResponse(**status_payload)


@app.post("/api/clients/{client_id}/stop", response_model=StatusResponse)
async def stop_client(client_id: str) -> StatusResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    status_payload = await frpc_manager.stop(client_id)
    return StatusResponse(**status_payload)


@app.get("/api/clients/{client_id}/logs", response_model=LogsResponse)
async def client_logs(
    client_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> LogsResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    return LogsResponse(items=frpc_manager.logs(client_id, limit=limit))


@app.get("/api/clients/{client_id}/jump-links", response_model=JumpLinksResponse)
async def client_jump_links(client_id: str) -> JumpLinksResponse:
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    return JumpLinksResponse(items=_build_jump_links(client))


@app.get("/api/clients/{client_id}/frpc-path/auto", response_model=AutoFrpcPathResponse)
async def auto_frpc_path_for_client(client_id: str) -> AutoFrpcPathResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    return detect_frpc_path()


# Legacy endpoints for compatibility with old single-client UI.
@app.get("/api/config", response_model=AppConfigPayload)
async def get_config() -> AppConfigPayload:
    state = await config_store.load_state()
    return _client_to_payload(_active_client(state))


@app.put("/api/config", response_model=AppConfigPayload)
async def update_config(payload: AppConfigPayload) -> AppConfigPayload:
    state = await config_store.load_state()
    client = _active_client(state)
    client.name = payload.name.strip() or client.name
    client.frpc_path = payload.frpc_path.strip()
    client.run_args = payload.run_args.strip()
    client.config_text = payload.config_text
    client.env = payload.env
    saved = await config_store.save_state(state)
    return _client_to_payload(_active_client(saved))


@app.get("/api/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    state = await config_store.load_state()
    active = _active_client(state)
    return StatusResponse(**frpc_manager.status(active.id))


@app.post("/api/start", response_model=StatusResponse)
async def start() -> StatusResponse:
    state = await config_store.load_state()
    active = _active_client(state)
    cfg = await _resolve_start_config(active.id)
    try:
        status_payload = await frpc_manager.start(
            active.id,
            cfg,
            config_store.frpc_config_file(active.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start frpc: {exc}") from exc
    return StatusResponse(**status_payload)


@app.post("/api/stop", response_model=StatusResponse)
async def stop() -> StatusResponse:
    state = await config_store.load_state()
    active = _active_client(state)
    status_payload = await frpc_manager.stop(active.id)
    return StatusResponse(**status_payload)


@app.get("/api/logs", response_model=LogsResponse)
async def logs(
    limit: int = Query(default=200, ge=1, le=1000),
) -> LogsResponse:
    state = await config_store.load_state()
    active = _active_client(state)
    return LogsResponse(items=frpc_manager.logs(active.id, limit=limit))


@app.get("/api/jump-links", response_model=JumpLinksResponse)
async def jump_links() -> JumpLinksResponse:
    state = await config_store.load_state()
    active = _active_client(state)
    return JumpLinksResponse(items=_build_jump_links(active))


@app.get("/api/frpc-path/auto", response_model=AutoFrpcPathResponse)
async def auto_frpc_path() -> AutoFrpcPathResponse:
    return detect_frpc_path()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
