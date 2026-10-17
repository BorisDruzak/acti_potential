"""Exact, bounded exports of an explicitly selected authorized document set."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, select

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.registry.document_export import export_documents_response
from app.registry.document_search import ExportParameters
from app.registry.models import Document
from app.registry.policy import accessible_document_clause

router = APIRouter()


class SelectionExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=1, max_length=1000)
    format: Literal["csv", "xlsx"]
    columns: list[str] | None = Field(None, max_length=100)
    mode: Literal["editor", "viewer"] | None = None

    @model_validator(mode="after")
    def unique(self):
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("Duplicate documents")
        if self.columns is not None and (
            not self.columns or len(set(self.columns)) != len(self.columns)
            or any(not key or len(key) > 100 or "," in key for key in self.columns)
        ):
            raise ValueError("Invalid columns")
        return self


@router.post("/documents/export-selection")
def export_selection(
    data: SelectionExport, request: Request,
    session: Annotated[object, Depends(db_session, scope="function")],
    actor: Annotated[object, Depends(current_actor)],
):
    query = select(Document).where(
        Document.id.in_(data.document_ids), accessible_document_clause(session, actor, data.mode)
    )
    available = set(session.scalars(query.with_only_columns(Document.id)))
    if available != set(data.document_ids):
        fail(404, "selection_unavailable", "Часть выделенных документов больше недоступна. Обновите список.")
    query = query.order_by(case({identity: index for index, identity in enumerate(data.document_ids)}, value=Document.id))
    params = ExportParameters(
        format=data.format, columns=",".join(data.columns) if data.columns is not None else None,
        mode=data.mode,
    )
    return export_documents_response(request, session, actor, params, document_query=query)
