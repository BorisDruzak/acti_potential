from datetime import date
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

RelationType = Literal[
    "changes",
    "repeals",
    "replaces",
    "approves",
    "attachment",
    "reference",
    "in_execution",
    "previous_revision",
    "topic",
]
LABELS = {
    "changes": ("Изменяет", "Изменён на основании"),
    "repeals": ("Отменяет", "Утратил силу на основании"),
    "replaces": ("Заменяет", "Заменён на основании"),
    "approves": ("Утверждает", "Утверждён на основании"),
    "attachment": ("Является приложением к", "Имеет приложение"),
    "reference": ("Ссылается на", "На него ссылается"),
    "in_execution": ("Принят во исполнение", "Исполняется документом"),
    "previous_revision": ("Имеет предыдущую редакцию", "Имеет следующую редакцию"),
    "topic": ("Связан по предмету", "Связан по предмету"),
}
ACYCLIC_TYPES = frozenset({"changes", "repeals", "replaces", "previous_revision"})
EFFECT_TYPES = frozenset({"changes", "repeals", "replaces", "approves"})


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExternalTarget(Input):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    identifier: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    effective_date: date
    verified: StrictBool

    @model_validator(mode="after")
    def valid_attestation(self):
        if any(ord(char) <= 32 or ord(char) == 127 for char in self.url):
            raise ValueError("External URLs cannot contain whitespace or control characters")
        parsed = urlsplit(self.url)
        parsed.port  # Validate an explicitly supplied port without resolving the URL.
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or not self.verified
        ):
            raise ValueError("A verified HTTPS basis without embedded credentials is required")
        return self


class RelationInput(Input):
    source_document_id: UUID
    target_document_id: UUID | None = None
    source_version_id: UUID | None = None
    source_block_id: UUID | None = None
    target_version_id: UUID | None = None
    target_block_id: UUID | None = None
    relation_type: RelationType
    source: Literal["manual", "deterministic", "imported", "ai_suggested"] = "manual"
    description: str = Field(default="", max_length=5000)
    external_target: ExternalTarget | None = None

    @model_validator(mode="after")
    def exclusive_target(self):
        if (self.target_document_id is None) == (self.external_target is None):
            raise ValueError("Choose exactly one internal or external target")
        if self.external_target and (self.target_version_id or self.target_block_id):
            raise ValueError("External bases cannot reference internal version IDs")
        return self


class DecisionInput(Input):
    reason: str = Field(min_length=1, max_length=2000)
    effective_date: date
