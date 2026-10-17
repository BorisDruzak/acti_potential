"""Shared employee/service lexical retrieval contract."""

import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.registry.document_contracts import EditorIdentity
from app.registry.document_search import SearchParameters


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field("", max_length=255)
    scope: Literal["all", "metadata", "content"] = "all"
    registry_ids: list[UUID] = Field(default_factory=list, max_length=100)
    document_type_ids: list[UUID] = Field(default_factory=list, max_length=100)
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = Field(None, max_length=100)
    card_state: Literal["draft", "published"] | None = None
    reg_number: str | None = Field(None, max_length=255)
    filters: dict = Field(default_factory=dict, max_length=20)
    fuzzy: bool = True
    sort: str = Field("auto", max_length=110)
    direction: Literal["asc", "desc"] = "desc"
    version_scope: Literal["current", "all"] = "current"
    page_size: int = Field(20, ge=1, le=100)
    cursor: str | None = Field(None, max_length=2000)
    mode: Literal["editor", "viewer"] | None = None

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.filters, ensure_ascii=False)) > 8000:
            raise ValueError("Filters too large")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Invalid date range")
        return self

    def legacy_filters(self):
        return SearchParameters(
            registry_id=self.registry_ids[0] if len(self.registry_ids) == 1 else None,
            document_type_id=self.document_type_ids[0]
            if len(self.document_type_ids) == 1
            else None,
            date_from=self.date_from,
            date_to=self.date_to,
            status=self.status,
            card_state=self.card_state,
            reg_number=self.reg_number,
            filters=json.dumps(self.filters),
            mode=self.mode,
            sort="updated_at" if self.sort in {"auto", "relevance"} else self.sort,
            direction=self.direction,
            page_size=self.page_size,
        )


class SourceRange(BaseModel):
    block_id: UUID
    start: int
    end: int


class Citation(BaseModel):
    document_id: UUID
    version_id: UUID | None = None
    file_id: UUID | None = None
    extraction_id: UUID | None = None
    block_ids: list[UUID] = Field(default_factory=list)
    field_key: str | None = None
    metadata_revision: int | None = None
    page: int | None = None
    anchor: str | None = None
    url: str


class RetrievalChunk(BaseModel):
    document_id: UUID
    chunk_id: UUID
    source_type: Literal["metadata", "file"]
    version_id: UUID | None = None
    metadata_revision: int | None = None
    document_version_id: UUID | None = None
    file_id: UUID | None = None
    extraction_id: UUID | None = None
    block_ids: list[UUID]
    ranges: list[SourceRange]
    page: int | None = None
    anchor: str | None = None
    text: str
    score: float = 0
    checksum: str
    algorithm_version: str
    citation: Citation


class MatchRead(BaseModel):
    source: Literal["metadata", "file"]
    source_id: UUID
    field_key: str | None = None
    label: str
    snippet: str
    highlights: list[list[int]] = Field(default_factory=list)
    rank: float
    file_id: UUID | None = None
    version_id: UUID | None = None
    version: int | None = None
    extraction_id: UUID | None = None
    block_id: UUID | None = None
    block_ordinal: int | None = None
    page: int | None = None
    anchor: str | None = None
    citation: Citation


class SearchDocument(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    registry_id: UUID
    document_type_id: UUID
    schema_version_id: UUID
    revision: int
    current_version: int
    created_at: datetime
    updated_at: datetime
    updated_by: EditorIdentity | None = None
    title: str | None = None
    reg_number: str | None = None
    reg_date: date | None = None
    status: str | None = None
    card_state: Literal["draft", "published"]
    publication_targets: list[Literal["internal", "shared"]]
    values: dict
    display_values: dict
    permissions: list[str]


class SearchHit(BaseModel):
    document: SearchDocument
    matches: list[MatchRead]
    score: float
    match_count: int


class SearchRead(BaseModel):
    items: list[SearchHit]
    total: int
    next_cursor: str | None
    page_size: int
    algorithm_version: str
