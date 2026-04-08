from __future__ import annotations

from pydantic import BaseModel, Field


class AppConfigPayload(BaseModel):
    id: str = Field(default="")
    name: str = Field(default="")
    frpc_path: str = Field(default="")
    run_args: str = Field(default="")
    config_text: str = Field(default="")
    env: dict[str, str] = Field(default_factory=dict)


class ClientCreatePayload(BaseModel):
    name: str = Field(default="新客户端")


class ClientSelectPayload(BaseModel):
    client_id: str


class StatusResponse(BaseModel):
    running: bool
    pid: int | None = None
    started_at: float | None = None
    uptime_sec: float = 0
    last_exit_code: int | None = None


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
