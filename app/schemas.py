from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class AppConfigPayload(BaseModel):
    id: str = Field(default="")
    name: str = Field(default="")
    frpc_path: str = Field(default="")
    run_args: str = Field(default="")
    config_text: str = Field(default="")
    env: dict[str, str] = Field(default_factory=dict)


class ClientCreatePayload(BaseModel):
    name: str = Field(default="新客户端")


class StatusResponse(BaseModel):
    running: bool
    pid: int | None = None
    started_at: float | None = None
    uptime_sec: float = 0
    last_exit_code: int | None = None
    restart_count: int = 0
    last_error: str | None = None


class LogsResponse(BaseModel):
    items: list[str]


class FrpcPathCandidate(BaseModel):
    path: str
    exists: bool
    resolved_path: str | None = None


class AutoFrpcPathResponse(BaseModel):
    selected_path: str
    selected_exists: bool
    candidates: list[FrpcPathCandidate]


class ClientListItem(BaseModel):
    id: str
    name: str
    running: bool
    pid: int | None = None
    uptime_sec: float = 0
    last_exit_code: int | None = None
    restart_count: int = 0
    last_error: str | None = None


class ClientsResponse(BaseModel):
    active_client_id: str
    clients: list[ClientListItem]


class LoginPayload(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    username: str | None = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


class AuthProfileResponse(BaseModel):
    username: str
    password_policy: str


class UpdateAuthProfilePayload(BaseModel):
    new_username: str | None = None
    current_password: str
    new_password: str | None = None


class JumpLinkItem(BaseModel):
    proxy_name: str
    proxy_type: str
    server_addr: str
    remote_port: int
    local_port: int | None = None
    url: str


class JumpLinksResponse(BaseModel):
    items: list[JumpLinkItem]


class RuntimeEventItem(BaseModel):
    id: int
    client_id: str
    event_type: str
    exit_type: str | None = None
    message: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: float


class RuntimeEventsResponse(BaseModel):
    items: list[RuntimeEventItem]


class AlertChannelPayload(BaseModel):
    name: str
    webhook_url: HttpUrl
    timeout_sec: int = Field(default=5, ge=1, le=60)
    enabled: bool = True


class AlertChannelResponse(BaseModel):
    id: int
    name: str
    webhook_url: str
    timeout_sec: int
    enabled: bool
    created_at: float
    updated_at: float


class AlertChannelsResponse(BaseModel):
    items: list[AlertChannelResponse]


class AlertRulesPayload(BaseModel):
    on_start_failure: bool = True
    on_abnormal_exit: bool = True
    on_restart_threshold: bool = True
    restart_threshold: int = Field(default=3, ge=1, le=100)


class AlertRulesResponse(BaseModel):
    on_start_failure: bool
    on_abnormal_exit: bool
    on_restart_threshold: bool
    restart_threshold: int


class PreflightResponse(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

class ImportPreviewResponse(BaseModel):
    schema_version: int
    import_client_count: int
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ready: bool
