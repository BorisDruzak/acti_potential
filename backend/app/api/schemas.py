from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    request_id: str


ERROR_RESPONSES = {status: {"model": ErrorEnvelope} for status in (401, 403, 422, 429, 500, 503)}


class LoginResponse(BaseModel):
    status: Literal["authenticated"]


class CurrentUserResponse(BaseModel):
    id: UUID
    display_name: str
    roles: list[str]
    csrf_token: str
    must_change_password: bool = False


class LiveResponse(BaseModel):
    status: Literal["live"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, bool]


class PlatformResponse(BaseModel):
    version: str
    source_sha: str
    worker: Literal["available", "unavailable"]
