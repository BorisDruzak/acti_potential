from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachmentCreate(Input):
    field_key: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(default="application/octet-stream", max_length=255)
    content_base64: str = Field(max_length=35000000, repr=False)


class DocumentCreate(Input):
    registry_id: UUID
    document_type_id: UUID
    schema_version_id: UUID | None = None
    values: dict[str, Any]
    files: list[AttachmentCreate] = Field(default_factory=list, max_length=100)


class DocumentUpdate(Input):
    revision: int = Field(ge=1)
    values: dict[str, Any]
    files: list[AttachmentCreate] = Field(default_factory=list, max_length=100)


class FileOutput(BaseModel):
    id: UUID
    version: int
    name: str
    size: int
    mime: str
    sha256: str
    field_key: str | None
    created_by: UUID
    created_at: datetime


class DocumentSummary(BaseModel):
    id: UUID
    registry_id: UUID
    document_type_id: UUID
    schema_version_id: UUID
    reg_number: str | None
    reg_date: date | None
    title: str | None
    status: str | None
    revision: int
    current_version: int
    created_at: datetime
    updated_at: datetime
    permissions: list[str]


class DocumentOutput(DocumentSummary):
    values: dict[str, Any]
    schema_content: dict[str, Any] = Field(alias="schema")
    files: list[FileOutput]


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    page: int
    page_size: int


class HistoryOutput(BaseModel):
    id: UUID
    revision: int
    values: dict[str, Any]
    created_by: UUID
    created_at: datetime


class ACLEntry(Input):
    user_id: UUID
    permissions: list[Literal["read", "create", "update", "upload", "download", "manage"]]


class ACLInput(Input):
    entries: list[ACLEntry] = Field(max_length=1000)


class UserOption(BaseModel):
    id: UUID
    display_name: str
