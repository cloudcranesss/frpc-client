from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import time
from typing import Awaitable, Callable

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .alert_manager import AlertManager
from .auth import AuthManager
from .config_store import (
    ClientState,
    ConfigStore,
    ProxyClientConfig,
    default_frpc_path,
    make_client,
)
from .frpc_manager import FrpcManager
from .frpc_config_parser import parse_proxy_config
from .maintenance import (
    build_export_zip,
    parse_import_zip,
    preflight_config,
    preview_import_bundle,
    redact_sensitive,
    system_diagnostics,
)
from .site_aggregator import SiteAggregator
from .schemas import (
    AlertChannelPayload,
    AlertChannelResponse,
    AlertChannelsResponse,
    AlertRulesPayload,
    AlertRulesResponse,
    AppConfigPayload,
    AutoFrpcPathResponse,
    AuthProfileResponse,
    AuthStatusResponse,
    BatchActionItem,
    BatchActionPayload,
    BatchActionResponse,
    ChangePasswordPayload,
    ClientCreatePayload,
    ClientListItem,
    ClientsResponse,
    FrpcPathCandidate,
    ImportPreviewResponse,
    JumpLinkItem,
    JumpLinksResponse,
    LoginPayload,
    LoginResponse,
    LogsResponse,
    PreflightResponse,
    RuntimeEventItem,
    RuntimeEventsResponse,
    SuccessfulSiteItem,
    SuccessfulSitesResponse,
    StatusResponse,
    UpdateAuthProfilePayload,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"
APP_VERSION = "0.3.0"
ASSET_VERSION = os.getenv("FRP_PANEL_ASSET_VERSION", APP_VERSION).strip() or APP_VERSION
try:
    BATCH_CONCURRENCY = max(1, min(8, int(os.getenv("FRP_PANEL_BATCH_CONCURRENCY", "6"))))
except ValueError:
    BATCH_CONCURRENCY = 6

config_store = ConfigStore(DATA_DIR)
alert_manager = AlertManager(config_store)
auth_manager = AuthManager(DATA_DIR, config_store)


class LoginRateLimiter:
    def __init__(self, window_sec: int = 600, max_attempts: int = 20) -> None:
        self._window_sec = window_sec
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()
        self._attempts: dict[str, list[float]] = {}

    async def allow(self, ip: str, username: str) -> bool:
        key = f"{ip}|{username}"
        now = time.time()
        async with self._lock:
            entries = self._attempts.get(key, [])
            fresh = [item for item in entries if now - item <= self._window_sec]
            if len(fresh) >= self._max_attempts:
                self._attempts[key] = fresh
                return False
            fresh.append(now)
            self._attempts[key] = fresh
            return True


login_limiter = LoginRateLimiter()


async def handle_runtime_event(event: dict[str, object]) -> None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    await config_store.append_runtime_event(
        client_id=str(event.get("client_id", "")),
        event_type=str(event.get("event_type", "")),
        message=str(event.get("message", "")),
        payload=payload,
        exit_type=(str(event["exit_type"]) if event.get("exit_type") else None),
    )
    await alert_manager.handle_runtime_event(event)


frpc_manager = FrpcManager(event_callback=handle_runtime_event)


async def _load_clients_for_sites() -> list[ProxyClientConfig]:
    state = await config_store.load_state()
    return state.clients


async def _record_site_probe_failure(
    client_id: str,
    message: str,
    payload: dict[str, object],
) -> None:
    await config_store.append_runtime_event(
        client_id=client_id,
        event_type="sites_probe_failure",
        message=message,
        payload=payload,
    )


site_aggregator: SiteAggregator


def _is_public_path(path: str) -> bool:
    if path.startswith("/web/"):
        return True
    if path in {"/login", "/health/live", "/health/ready"}:
        return True
    if path.startswith("/api/auth/"):
        return True
    return False


async def _authenticated_username(request: Request) -> str | None:
    token = request.cookies.get(auth_manager.cookie_name)
    return await auth_manager.get_session_username(token)


async def _ensure_writable_mode() -> None:
    return


async def _audit(action: str, target: str, detail: dict[str, object] | None = None) -> None:
    _ = (action, target, detail)
    return


async def _snapshot(reason: str) -> None:
    _ = reason
    return


def _check_data_volume_health() -> tuple[bool, str]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"data dir create failed: {exc}"
    probe = DATA_DIR / ".rw-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"data dir not writable: {exc}"
    min_free_mb = int(os.getenv("FRP_PANEL_MIN_FREE_MB", "50"))
    usage = shutil.disk_usage(DATA_DIR)
    free_mb = usage.free / (1024 * 1024)
    if free_mb < min_free_mb:
        return False, f"low disk free space: {free_mb:.2f}MB < {min_free_mb}MB"
    return True, "ok"


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
        auto_start=bool(client.auto_start),
    )


def _find_client(state: ClientState, client_id: str) -> ProxyClientConfig:
    for client in state.clients:
        if client.id == client_id:
            return client
    raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")


def _clients_response(state: ClientState) -> ClientsResponse:
    clients = []
    for client in state.clients:
        status = frpc_manager.status(client.id)
        clients.append(
            ClientListItem(
                id=client.id,
                name=client.name,
                running=bool(status["running"]),
                auto_start=bool(client.auto_start),
                pid=status["pid"],
                uptime_sec=float(status["uptime_sec"] or 0),
                last_exit_code=status["last_exit_code"],
                restart_count=int(status["restart_count"] or 0),
                last_error=(str(status["last_error"]) if status["last_error"] else None),
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
        _, parsed = parse_proxy_config(config_text)
    except ValueError:
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


site_aggregator = SiteAggregator(
    load_clients=_load_clients_for_sites,
    build_jump_links=_build_jump_links,
    on_probe_failure=_record_site_probe_failure,
)


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


async def _client_preflight(client_id: str) -> dict[str, object]:
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    normalized_path = normalize_frpc_path(client.frpc_path)
    payload = preflight_config(
        frpc_path=normalized_path,
        config_text=client.config_text,
        run_args=client.run_args,
    )
    return payload


def _normalize_batch_ids(client_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in client_ids:
        client_id = str(raw or "").strip()
        if not client_id or client_id in seen:
            continue
        seen.add(client_id)
        normalized.append(client_id)
    return normalized


async def _split_batch_ids(client_ids: list[str]) -> tuple[list[str], list[str]]:
    state = await config_store.load_state()
    known = {item.id for item in state.clients}
    valid: list[str] = []
    missing: list[str] = []
    for client_id in _normalize_batch_ids(client_ids):
        if client_id in known:
            valid.append(client_id)
        else:
            missing.append(client_id)
    return valid, missing


def _build_batch_response(items: list[BatchActionItem]) -> BatchActionResponse:
    success = sum(1 for item in items if item.ok)
    return BatchActionResponse(
        total=len(items),
        success=success,
        failed=len(items) - success,
        items=items,
    )


async def _run_batch(
    client_ids: list[str],
    worker: Callable[[str], Awaitable[BatchActionItem]],
) -> list[BatchActionItem]:
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    results: list[BatchActionItem | None] = [None] * len(client_ids)

    async def run_one(index: int, client_id: str) -> None:
        async with semaphore:
            results[index] = await worker(client_id)

    await asyncio.gather(*(run_one(index, client_id) for index, client_id in enumerate(client_ids)))
    return [item for item in results if item is not None]


async def _batch_preflight_item(client_id: str) -> BatchActionItem:
    try:
        payload = await _client_preflight(client_id)
        ok = bool(payload.get("ok"))
        if not ok:
            await config_store.append_runtime_event(
                client_id=client_id,
                event_type="preflight_failed",
                message="Preflight checks failed.",
                payload=payload,
            )
        await _audit(
            "preflight_client",
            client_id,
            {
                "ok": ok,
                "errors": len(payload.get("errors") or []),
                "warnings": len(payload.get("warnings") or []),
                "batch": True,
            },
        )
        return BatchActionItem(
            client_id=client_id,
            ok=ok,
            message="预检通过" if ok else "预检未通过",
            detail=payload,
        )
    except HTTPException as exc:
        return BatchActionItem(
            client_id=client_id,
            ok=False,
            message="预检失败",
            detail=exc.detail if isinstance(exc.detail, (dict, list, str)) else str(exc.detail),
        )
    except Exception as exc:
        return BatchActionItem(client_id=client_id, ok=False, message="预检失败", detail=str(exc))


async def _batch_start_item(
    client_id: str,
    *,
    force: bool,
    skip_failed_preflight: bool,
) -> BatchActionItem:
    try:
        preflight_failed = False
        preflight_payload: dict[str, object] | None = None
        started_forcefully = force

        if not force:
            preflight_payload = await _client_preflight(client_id)
            if not bool(preflight_payload.get("ok")):
                preflight_failed = True
                await config_store.append_runtime_event(
                    client_id=client_id,
                    event_type="preflight_failed",
                    message="Preflight checks failed.",
                    payload=preflight_payload,
                )
                if skip_failed_preflight:
                    await _audit(
                        "start_client",
                        client_id,
                        {"force": False, "batch": True, "skipped": True, "reason": "preflight_failed"},
                    )
                    return BatchActionItem(
                        client_id=client_id,
                        ok=False,
                        message="预检未通过，已跳过启动",
                        detail=preflight_payload,
                    )
                started_forcefully = True

        if started_forcefully:
            await config_store.append_runtime_event(
                client_id=client_id,
                event_type="preflight_forced",
                message="Start requested with force=true, preflight skipped.",
                payload={"force": True, "batch": True},
            )

        cfg = await _resolve_start_config(client_id)
        status_payload = await frpc_manager.start(
            client_id,
            cfg,
            config_store.frpc_config_file(client_id, cfg.config_text),
        )
        await _audit("start_client", client_id, {"force": started_forcefully, "batch": True})
        detail: dict[str, object] = {"status": status_payload}
        if preflight_payload:
            detail["preflight"] = preflight_payload
        message = "已启动" if not preflight_failed else "预检失败后已强制启动"
        return BatchActionItem(client_id=client_id, ok=True, message=message, detail=detail)
    except HTTPException as exc:
        return BatchActionItem(
            client_id=client_id,
            ok=False,
            message="启动失败",
            detail=exc.detail if isinstance(exc.detail, (dict, list, str)) else str(exc.detail),
        )
    except Exception as exc:
        return BatchActionItem(client_id=client_id, ok=False, message="启动失败", detail=str(exc))


async def _batch_stop_item(client_id: str) -> BatchActionItem:
    try:
        status_before = frpc_manager.status(client_id)
        status_payload = await frpc_manager.stop(client_id)
        await _audit("stop_client", client_id, {"batch": True})
        message = "已停止" if bool(status_before.get("running")) else "客户端未运行，无需停止"
        return BatchActionItem(client_id=client_id, ok=True, message=message, detail=status_payload)
    except HTTPException as exc:
        return BatchActionItem(
            client_id=client_id,
            ok=False,
            message="停止失败",
            detail=exc.detail if isinstance(exc.detail, (dict, list, str)) else str(exc.detail),
        )
    except Exception as exc:
        return BatchActionItem(client_id=client_id, ok=False, message="停止失败", detail=str(exc))


async def _auto_start_clients_on_boot() -> None:
    state = await config_store.load_state()
    targets = [item for item in state.clients if item.auto_start]
    for client in targets:
        try:
            preflight = await _client_preflight(client.id)
            if not bool(preflight.get("ok")):
                await config_store.append_runtime_event(
                    client_id=client.id,
                    event_type="auto_start_preflight_failed",
                    message="Auto-start preflight failed.",
                    payload=preflight,
                )
                continue
            cfg = await _resolve_start_config(client.id)
            await frpc_manager.start(
                client.id,
                cfg,
                config_store.frpc_config_file(client.id, cfg.config_text),
            )
            await config_store.append_runtime_event(
                client_id=client.id,
                event_type="auto_start_triggered",
                message="Auto-started on service boot.",
                payload={"auto_start": True},
            )
        except Exception as exc:
            await config_store.append_runtime_event(
                client_id=client.id,
                event_type="auto_start_failed",
                message="Auto-start failed on service boot.",
                payload={"error": str(exc)},
            )


def _sse_message(event: str, data: object) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _render_page(filename: str) -> HTMLResponse:
    content = (WEB_DIR / filename).read_text(encoding="utf-8")
    content = content.replace("__ASSET_VERSION__", ASSET_VERSION)
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    await config_store.init()
    await auth_manager.init()
    await alert_manager.start()
    await site_aggregator.start()
    await _auto_start_clients_on_boot()
    yield
    await site_aggregator.shutdown()
    await alert_manager.shutdown()
    await frpc_manager.shutdown()


app = FastAPI(
    title="FRP Web Client",
    version=APP_VERSION,
    lifespan=lifespan,
)

cors_origins_raw = os.getenv("FRP_PANEL_CORS_ORIGINS", "").strip()
if cors_origins_raw:
    origins = [item.strip() for item in cors_origins_raw.split(",") if item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if _is_public_path(path):
        response = await call_next(request)
        if path.startswith("/web/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    username = await _authenticated_username(request)
    if username:
        request.state.username = username
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "未登录或会话已失效。"})
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    try:
        await config_store.load_state()
    except Exception:
        raise HTTPException(status_code=503, detail="storage unavailable")
    ok, reason = _check_data_volume_health()
    if not ok:
        raise HTTPException(status_code=503, detail=reason)
    return {"status": "ok"}


@app.get("/")
async def home() -> HTMLResponse:
    return _render_page("index.html")


@app.get("/console")
async def console_page() -> HTMLResponse:
    return _render_page("console.html")


@app.get("/dashboard")
async def dashboard_compat() -> RedirectResponse:
    return RedirectResponse(url="/console", status_code=302)


@app.get("/api/sites/successful", response_model=SuccessfulSitesResponse)
async def successful_sites() -> SuccessfulSitesResponse:
    rows = await site_aggregator.get_successful_sites()
    return SuccessfulSitesResponse(items=[SuccessfulSiteItem(**item) for item in rows])


@app.get("/api/sites/stream")
async def sites_stream(request: Request) -> StreamingResponse:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=256)
    await site_aggregator.subscribe(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse_message("heartbeat", {})
                    continue
                event_name = str(item.get("event", "heartbeat"))
                data = item.get("data", {})
                yield _sse_message(event_name, data)
        finally:
            await site_aggregator.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/events")
async def events_page() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=302)


@app.get("/alerts")
async def alerts_page() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=302)


@app.get("/maintenance")
async def maintenance_page() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=302)


@app.get("/settings")
async def settings_page() -> HTMLResponse:
    return _render_page("settings.html")


@app.get("/login")
async def login_page(request: Request):
    username = await _authenticated_username(request)
    if username:
        return RedirectResponse(url="/", status_code=302)
    return _render_page("login.html")


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(payload: LoginPayload, request: Request, response: Response) -> LoginResponse:
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空。")
    client_ip = request.client.host if request.client else "unknown"
    allowed = await login_limiter.allow(client_ip, username)
    if not allowed:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试。")
    ok = await auth_manager.verify_login(username, password)
    if not ok:
        raise HTTPException(status_code=401, detail="用户名或密码错误。")

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


@app.post("/api/auth/login-form")
async def login_form(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    name = username.strip()
    if not name or not password:
        return RedirectResponse(url="/login?error=empty", status_code=303)
    client_ip = request.client.host if request.client else "unknown"
    allowed = await login_limiter.allow(client_ip, name)
    if not allowed:
        return RedirectResponse(url="/login?error=rate_limit", status_code=303)
    ok = await auth_manager.verify_login(name, password)
    if not ok:
        return RedirectResponse(url="/login?error=invalid", status_code=303)

    token = await auth_manager.create_session(name)
    secure_cookie = os.getenv("FRP_PANEL_SECURE_COOKIE", "false").lower() in {"1", "true", "yes"}
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(
        key=auth_manager.cookie_name,
        value=token,
        max_age=60 * 60 * 24,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    return redirect


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
        raise HTTPException(status_code=401, detail="未登录或会话已失效。")
    updated = await auth_manager.change_password(
        username=username,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    if not updated:
        raise HTTPException(status_code=400, detail=auth_manager.password_policy_text())
    token = request.cookies.get(auth_manager.cookie_name)
    await auth_manager.delete_session(token)
    return LoginResponse(success=True, username=username)


@app.get("/api/auth/profile", response_model=AuthProfileResponse)
async def auth_profile(request: Request) -> AuthProfileResponse:
    username = await _authenticated_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或会话已失效。")
    return AuthProfileResponse(
        username=username,
        password_policy=auth_manager.password_policy_text(),
    )


@app.put("/api/auth/profile", response_model=LoginResponse)
async def update_auth_profile(
    request: Request,
    response: Response,
    payload: UpdateAuthProfilePayload,
) -> LoginResponse:
    username = await _authenticated_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或会话已失效。")

    new_username = (payload.new_username or "").strip() or None
    current_password = payload.current_password
    new_password = (payload.new_password or "").strip() or None
    if new_username is None and new_password is None:
        await _audit("auth_profile_update_failed", username, {"reason": "no_changes"})
        raise HTTPException(status_code=400, detail="请至少修改用户名或密码中的一项。")

    if new_username is not None and not auth_manager.validate_username(new_username):
        await _audit("auth_profile_update_failed", username, {"reason": "invalid_username"})
        raise HTTPException(
            status_code=400,
            detail="用户名格式不合法（3-32位，仅允许字母、数字、下划线、连字符）。",
        )
    if new_password is not None and not auth_manager.validate_password_strength(new_password):
        await _audit("auth_profile_update_failed", username, {"reason": "weak_password"})
        raise HTTPException(status_code=400, detail=auth_manager.password_policy_text())

    ok, reason, target_username = await auth_manager.update_profile(
        current_username=username,
        current_password=current_password,
        new_username=new_username,
        new_password=new_password,
    )
    if not ok:
        await _audit("auth_profile_update_failed", username, {"reason": reason})
        if reason in {"invalid_current_password", "user_not_found"}:
            raise HTTPException(status_code=400, detail="当前密码不正确。")
        if reason == "invalid_username":
            raise HTTPException(
                status_code=400,
                detail="用户名格式不合法（3-32位，仅允许字母、数字、下划线、连字符）。",
            )
        if reason == "weak_password":
            raise HTTPException(status_code=400, detail=auth_manager.password_policy_text())
        if reason == "username_exists":
            raise HTTPException(status_code=409, detail="该用户名已存在。")
        raise HTTPException(status_code=500, detail="账号信息更新失败。")

    assert target_username is not None
    await _audit(
        "auth_profile_updated",
        target_username,
        {
            "old_username": username,
            "username_changed": target_username != username,
            "password_changed": bool(new_password),
        },
    )
    token = request.cookies.get(auth_manager.cookie_name)
    await auth_manager.delete_session(token)
    response.delete_cookie(auth_manager.cookie_name, path="/")
    return LoginResponse(success=True, username=target_username)


@app.get("/api/clients", response_model=ClientsResponse)
async def list_clients() -> ClientsResponse:
    state = await config_store.load_state()
    return _clients_response(state)


@app.post("/api/clients", response_model=ClientsResponse)
async def create_client(payload: ClientCreatePayload) -> ClientsResponse:
    await _ensure_writable_mode()
    state = await config_store.load_state()
    name = payload.name.strip() or f"客户端 {len(state.clients) + 1}"
    client = make_client(name=name)
    state.clients.append(client)
    state.active_client_id = client.id
    saved = await config_store.save_state(state)
    await _snapshot("create_client")
    await _audit("create_client", client.id, {"name": client.name})
    return _clients_response(saved)


@app.post("/api/clients/{client_id}/select", response_model=ClientsResponse)
async def select_client(client_id: str) -> ClientsResponse:
    await _ensure_writable_mode()
    state = await config_store.load_state()
    _find_client(state, client_id)
    state.active_client_id = client_id
    saved = await config_store.save_state(state)
    await _snapshot("select_client")
    await _audit("select_client", client_id)
    return _clients_response(saved)


@app.delete("/api/clients/{client_id}", response_model=ClientsResponse)
async def delete_client(client_id: str) -> ClientsResponse:
    await _ensure_writable_mode()
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
    await _snapshot("delete_client")
    await _audit("delete_client", client_id)
    return _clients_response(saved)


@app.get("/api/clients/{client_id}/config", response_model=AppConfigPayload)
async def get_client_config(client_id: str) -> AppConfigPayload:
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    return _client_to_payload(client)


@app.put("/api/clients/{client_id}/config", response_model=AppConfigPayload)
async def update_client_config(client_id: str, payload: AppConfigPayload) -> AppConfigPayload:
    await _ensure_writable_mode()
    state = await config_store.load_state()
    client = _find_client(state, client_id)
    client.name = payload.name.strip() or client.name
    client.frpc_path = payload.frpc_path.strip()
    client.run_args = payload.run_args.strip()
    client.config_text = payload.config_text
    client.env = payload.env
    client.auto_start = bool(payload.auto_start)
    saved = await config_store.save_state(state)
    updated = _find_client(saved, client_id)
    await _snapshot("update_client_config")
    await _audit("update_client_config", client_id)
    return _client_to_payload(updated)


@app.get("/api/clients/{client_id}/status", response_model=StatusResponse)
async def client_status(client_id: str) -> StatusResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    return StatusResponse(**frpc_manager.status(client_id))


@app.post("/api/clients/{client_id}/start", response_model=StatusResponse)
async def start_client(client_id: str, force: bool = Query(default=False)) -> StatusResponse:
    await _ensure_writable_mode()
    state = await config_store.load_state()
    _find_client(state, client_id)
    if not force:
        preflight = await _client_preflight(client_id)
        if not bool(preflight["ok"]):
            await config_store.append_runtime_event(
                client_id=client_id,
                event_type="preflight_failed",
                message="Preflight checks failed.",
                payload=preflight,
            )
            raise HTTPException(status_code=400, detail=preflight)
    else:
        await config_store.append_runtime_event(
            client_id=client_id,
            event_type="preflight_forced",
            message="Start requested with force=true, preflight skipped.",
            payload={"force": True},
        )
    cfg = await _resolve_start_config(client_id)
    status_payload = await frpc_manager.start(
        client_id,
        cfg,
        config_store.frpc_config_file(client_id, cfg.config_text),
    )
    await _audit("start_client", client_id, {"force": force})
    return StatusResponse(**status_payload)


@app.post("/api/clients/{client_id}/stop", response_model=StatusResponse)
async def stop_client(client_id: str) -> StatusResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    status_payload = await frpc_manager.stop(client_id)
    await _audit("stop_client", client_id)
    return StatusResponse(**status_payload)


@app.get("/api/clients/{client_id}/logs", response_model=LogsResponse)
async def client_logs(
    client_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> LogsResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    return LogsResponse(items=frpc_manager.logs(client_id, limit=limit))


@app.post("/api/clients/{client_id}/logs/clear")
async def clear_client_logs(client_id: str) -> dict[str, bool]:
    state = await config_store.load_state()
    _find_client(state, client_id)
    await frpc_manager.clear_logs(client_id)
    return {"success": True}


@app.get("/api/clients/{client_id}/events", response_model=RuntimeEventsResponse)
async def client_events(
    client_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> RuntimeEventsResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    rows = await config_store.list_runtime_events(client_id, limit=limit)
    return RuntimeEventsResponse(items=[RuntimeEventItem(**item) for item in rows])


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


@app.post("/api/clients/{client_id}/preflight", response_model=PreflightResponse)
async def client_preflight(client_id: str) -> PreflightResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    payload = await _client_preflight(client_id)
    if not bool(payload["ok"]):
        await config_store.append_runtime_event(
            client_id=client_id,
            event_type="preflight_failed",
            message="Preflight checks failed.",
            payload=payload,
        )
    await _audit(
        "preflight_client",
        client_id,
        {
            "ok": bool(payload["ok"]),
            "errors": len(payload.get("errors") or []),
            "warnings": len(payload.get("warnings") or []),
        },
    )
    return PreflightResponse(**payload)


@app.post("/api/clients/preflight-batch", response_model=BatchActionResponse)
async def clients_preflight_batch(payload: BatchActionPayload) -> BatchActionResponse:
    valid_ids, missing_ids = await _split_batch_ids(payload.client_ids)
    if not valid_ids and not missing_ids:
        raise HTTPException(status_code=400, detail="client_ids 不能为空。")

    items: list[BatchActionItem] = [
        BatchActionItem(client_id=client_id, ok=False, message="客户端不存在", detail="client not found")
        for client_id in missing_ids
    ]
    if valid_ids:
        items.extend(await _run_batch(valid_ids, _batch_preflight_item))
    return _build_batch_response(items)


@app.post("/api/clients/start-batch", response_model=BatchActionResponse)
async def clients_start_batch(payload: BatchActionPayload) -> BatchActionResponse:
    await _ensure_writable_mode()
    valid_ids, missing_ids = await _split_batch_ids(payload.client_ids)
    if not valid_ids and not missing_ids:
        raise HTTPException(status_code=400, detail="client_ids 不能为空。")

    items: list[BatchActionItem] = [
        BatchActionItem(client_id=client_id, ok=False, message="客户端不存在", detail="client not found")
        for client_id in missing_ids
    ]
    if valid_ids:
        async def worker(client_id: str) -> BatchActionItem:
            return await _batch_start_item(
                client_id,
                force=bool(payload.force),
                skip_failed_preflight=bool(payload.skip_failed_preflight),
            )

        items.extend(await _run_batch(valid_ids, worker))
    return _build_batch_response(items)


@app.post("/api/clients/stop-batch", response_model=BatchActionResponse)
async def clients_stop_batch(payload: BatchActionPayload) -> BatchActionResponse:
    await _ensure_writable_mode()
    valid_ids, missing_ids = await _split_batch_ids(payload.client_ids)
    if not valid_ids and not missing_ids:
        raise HTTPException(status_code=400, detail="client_ids 不能为空。")

    items: list[BatchActionItem] = [
        BatchActionItem(client_id=client_id, ok=False, message="客户端不存在", detail="client not found")
        for client_id in missing_ids
    ]
    if valid_ids:
        items.extend(await _run_batch(valid_ids, _batch_stop_item))
    return _build_batch_response(items)


@app.get("/api/clients/{client_id}/stream")
async def client_stream(client_id: str, request: Request) -> StreamingResponse:
    state = await config_store.load_state()
    _find_client(state, client_id)
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=512)
    await frpc_manager.subscribe(client_id, queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse_message("heartbeat", {})
                    continue
                event_name = str(item.get("event", "heartbeat"))
                data = item.get("data", {})
                yield _sse_message(event_name, data)
        finally:
            await frpc_manager.unsubscribe(client_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/alerts/channels", response_model=AlertChannelsResponse)
async def list_alert_channels() -> AlertChannelsResponse:
    rows = await config_store.list_alert_channels()
    return AlertChannelsResponse(items=[AlertChannelResponse(**item) for item in rows])


@app.post("/api/alerts/channels", response_model=AlertChannelResponse)
async def create_alert_channel(payload: AlertChannelPayload) -> AlertChannelResponse:
    await _ensure_writable_mode()
    row = await config_store.create_alert_channel(
        name=payload.name.strip(),
        webhook_url=str(payload.webhook_url),
        timeout_sec=payload.timeout_sec,
        enabled=payload.enabled,
    )
    await _snapshot("create_alert_channel")
    await _audit("create_alert_channel", str(row["id"]), {"name": payload.name})
    return AlertChannelResponse(**row)


@app.put("/api/alerts/channels/{channel_id}", response_model=AlertChannelResponse)
async def update_alert_channel(channel_id: int, payload: AlertChannelPayload) -> AlertChannelResponse:
    await _ensure_writable_mode()
    row = await config_store.update_alert_channel(
        channel_id=channel_id,
        name=payload.name.strip(),
        webhook_url=str(payload.webhook_url),
        timeout_sec=payload.timeout_sec,
        enabled=payload.enabled,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    await _snapshot("update_alert_channel")
    await _audit("update_alert_channel", str(channel_id))
    return AlertChannelResponse(**row)


@app.delete("/api/alerts/channels/{channel_id}")
async def delete_alert_channel(channel_id: int) -> dict[str, bool]:
    await _ensure_writable_mode()
    deleted = await config_store.delete_alert_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    await _snapshot("delete_alert_channel")
    await _audit("delete_alert_channel", str(channel_id))
    return {"success": True}


@app.get("/api/alerts/rules", response_model=AlertRulesResponse)
async def get_alert_rules() -> AlertRulesResponse:
    row = await config_store.get_alert_rules()
    return AlertRulesResponse(**row)


@app.put("/api/alerts/rules", response_model=AlertRulesResponse)
async def update_alert_rules(payload: AlertRulesPayload) -> AlertRulesResponse:
    await _ensure_writable_mode()
    row = await config_store.update_alert_rules(
        on_start_failure=payload.on_start_failure,
        on_abnormal_exit=payload.on_abnormal_exit,
        on_restart_threshold=payload.on_restart_threshold,
        restart_threshold=payload.restart_threshold,
    )
    await _snapshot("update_alert_rules")
    await _audit("update_alert_rules", "alert_rules")
    return AlertRulesResponse(**row)


@app.post("/api/maintenance/export")
async def maintenance_export() -> Response:
    bundle = await config_store.dump_bundle()
    redacted = redact_sensitive(bundle)
    zip_bytes = build_export_zip(redacted)
    await _audit("maintenance_export", "bundle")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=frp_bundle_export.zip"},
    )


@app.post("/api/maintenance/import/preview", response_model=ImportPreviewResponse)
async def maintenance_import_preview(file: UploadFile = File(...)) -> ImportPreviewResponse:
    content = await file.read()
    try:
        bundle = parse_import_zip(content)
    except ValueError as exc:
        await _audit(
            "maintenance_import_preview_failed",
            "preview",
            {"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = await config_store.load_state()
    existing_ids = {item.id for item in state.clients}
    preview = preview_import_bundle(bundle, existing_client_ids=existing_ids)
    return ImportPreviewResponse(**preview)


@app.post("/api/maintenance/import/apply")
async def maintenance_import_apply(
    file: UploadFile = File(...),
    mode: str | None = Query(default=None),
    mode_form: str | None = Form(default=None),
) -> dict[str, bool]:
    await _ensure_writable_mode()
    content = await file.read()
    try:
        bundle = parse_import_zip(content)
    except ValueError as exc:
        await _audit(
            "maintenance_import_apply_failed",
            "parse",
            {"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    final_mode = (mode_form or mode or "overwrite").strip().lower()
    if final_mode not in {"overwrite", "merge"}:
        await _audit(
            "maintenance_import_apply_failed",
            "mode",
            {"mode": final_mode},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": "mode must be overwrite or merge",
                "mode": final_mode,
                "allowed": ["overwrite", "merge"],
            },
        )
    await config_store.apply_bundle(bundle=bundle, mode=final_mode)
    await _snapshot("maintenance_import_apply")
    await _audit("maintenance_import_apply", final_mode)
    return {"success": True}


@app.get("/api/maintenance/diagnostics")
async def maintenance_diagnostics() -> Response:
    try:
        bundle = await config_store.dump_bundle()
        events: list[dict[str, object]] = []
        state = await config_store.load_state()
        for client in state.clients:
            rows = await config_store.list_runtime_events(client.id, limit=200)
            events.extend(rows)
        events = sorted(events, key=lambda item: float(item.get("created_at", 0)))[-500:]
        report = system_diagnostics(DATA_DIR, events, bundle)
        zip_bytes = build_export_zip(
            {
                "schema_version": 1,
                "clients": bundle.get("clients", []),
                "alert_rules": bundle.get("alert_rules", {}),
                "diagnostics": report,
            }
        )
        await _audit("maintenance_diagnostics", "bundle")
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=frp_diagnostics.zip"},
        )
    except Exception as exc:
        await _audit(
            "maintenance_diagnostics_failed",
            "bundle",
            {"error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="failed to build diagnostics") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
