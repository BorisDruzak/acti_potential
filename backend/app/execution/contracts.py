from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.processes.contracts import RevisionInput, Source, Strict


class DeadlineFields(Strict):
    deadline_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def aware(cls, value):
        if value and value.utcoffset() is None:
            raise ValueError("A UTC-aware deadline is required")
        return value.astimezone(UTC) if value else None


class StartInput(DeadlineFields):
    process_version_id: UUID
    name: str = Field(min_length=1, max_length=300)
    assignee_id: UUID | None = None
    deadline_reason: str = Field(default="", max_length=2000)


class AssignInput(RevisionInput):
    assignee_id: UUID


class ReasonInput(RevisionInput):
    reason: str = Field(min_length=1, max_length=2000)


class DeadlineInput(DeadlineFields):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class Check(Strict):
    id: UUID
    checked: bool


class ChecklistInput(RevisionInput):
    items: list[Check] = Field(max_length=300)


class Attachment(Source):
    file_id: UUID | None = None


class ResultDocument(Strict):
    document_id: UUID
    revision: int = Field(ge=1)
    version_id: UUID | None = None


class ResultInput(RevisionInput):
    values: dict = Field(default_factory=dict, max_length=300)
    comment: str = Field(default="", max_length=20000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=100)
    result_document: ResultDocument | None = None


class ListInput(Strict):
    registry_id: UUID | None = None
    mine: bool = False
    state: Literal["pending", "active", "suspended", "completed", "cancelled"] | None = None
    overdue: bool | None = None
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)
    mode: Literal["editor", "viewer"] | None = None
