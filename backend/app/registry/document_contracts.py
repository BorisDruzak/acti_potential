from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardPublicationInput(Input):
    @field_validator("publication_targets", check_fields=False)
    @classmethod
    def distinct_targets(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Publication targets must be unique")
        return value


class AttachmentCreate(Input):
    field_key: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(default="application/octet-stream", max_length=255)
    content_base64: str = Field(max_length=35000000, repr=False)


class DocumentCreate(CardPublicationInput):
    registry_id: UUID
    document_type_id: UUID
    schema_version_id: UUID | None = None
    values: dict[str, Any]
    files: list[AttachmentCreate] = Field(default_factory=list, max_length=100)
    card_state: Literal["draft", "published"] = "draft"
    publication_targets: list[Literal["internal", "shared"]] = Field(default_factory=lambda: ["internal"], max_length=2)


class DocumentUpdate(CardPublicationInput):
    revision: int = Field(ge=1)
    values: dict[str, Any]
    files: list[AttachmentCreate] = Field(default_factory=list, max_length=100)
    card_state: Literal["draft", "published"] | None = None
    publication_targets: list[Literal["internal", "shared"]] | None = Field(None, max_length=2)


class DocumentDelete(Input):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Укажите причину удаления")
        return value


class EditorIdentity(BaseModel):
    id: UUID
    display_name: str


class CollaborationHeartbeat(Input):
    client_id: UUID
    editing: bool = False


class CollaborationParticipant(EditorIdentity):
    editing: bool


class CollaborationOutput(BaseModel):
    revision: int
    updated_at: datetime
    updated_by: EditorIdentity | None
    participants: list[CollaborationParticipant]


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
    card_state: Literal["draft", "published"]
    publication_targets: list[Literal["internal", "shared"]]
    revision: int
    current_version: int
    created_at: datetime
    updated_at: datetime
    updated_by: EditorIdentity | None = None
    permissions: list[str]
    values: dict[str, Any] = Field(default_factory=dict)
    display_values: dict[str, Any] = Field(default_factory=dict)


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
    card_state: Literal["draft", "published"] | None = None
    publication_targets: list[Literal["internal", "shared"]] | None = None
    created_by: UUID
    created_by_name: str | None = None
    created_at: datetime


class ACLEntry(Input):
    user_id: UUID
    permissions: list[Literal["read", "create", "update", "upload", "download", "manage", "confirm_relations", "curate_knowledge"]]


class ACLInput(Input):
    entries: list[ACLEntry] = Field(max_length=1000)


class UserOption(BaseModel):
    id: UUID
    display_name: str
