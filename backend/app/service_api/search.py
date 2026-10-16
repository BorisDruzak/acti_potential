"""Lexical retrieval under existing service scopes; no additional RAG authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import db_session
from app.content.chunks import read_chunk, retrieve
from app.content.contracts import RetrievalChunk, SearchInput, SearchRead
from app.content.engine import execute_search
from app.content.models import ChunkRecord
from app.service_api.auth import ServiceContext, current_service, operation

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Context = Annotated[ServiceContext, Depends(current_service)]


class RetrievalRead(BaseModel):
    items: list[RetrievalChunk]
    document_total: int
    next_cursor: str | None
    algorithm_version: str


class ContextRead(BaseModel):
    items: list[RetrievalChunk]


def run(params, request, session, context):
    operation(context, "documents")
    settings = request.app.state.settings
    params = params.model_copy(update={"mode": "viewer"})
    # Binding to a token prevents cursor sharing between service credentials.
    secret = settings.session_secret.get_secret_value() + str(context.token.id)
    return execute_search(
        session,
        context.actor,
        params,
        secret=secret,
        weights=settings.search_weights,
        synonyms=settings.search_synonyms,
        algorithm=settings.search_algorithm_version,
    )


@router.post("/search", response_model=SearchRead)
def search(params: SearchInput, request: Request, session: Db, context: Context):
    return run(params, request, session, context)


@router.post("/retrieval", response_model=RetrievalRead)
def retrieval(params: SearchInput, request: Request, session: Db, context: Context):
    operation(context, "content")
    result = run(params, request, session, context)
    return {
        "items": retrieve(session, context.actor, result, "viewer"),
        "document_total": result["total"],
        "next_cursor": result["next_cursor"],
        "algorithm_version": result["algorithm_version"],
    }


@router.get("/chunks/{chunk_id}", response_model=RetrievalChunk)
def chunk(chunk_id: UUID, session: Db, context: Context):
    operation(context, "content")
    return read_chunk(session, context.actor, chunk_id, "viewer")


@router.get("/chunks/{chunk_id}/neighbors", response_model=ContextRead)
def neighbors(chunk_id: UUID, session: Db, context: Context, radius: int = Query(1, ge=0, le=2)):
    operation(context, "content")
    selected = read_chunk(session, context.actor, chunk_id, "viewer")
    row = session.get(ChunkRecord, chunk_id)
    if row is None:
        return {"items": [selected]}
    identities = session.scalars(
        select(ChunkRecord.id)
        .where(
            ChunkRecord.extraction_id == row.extraction_id,
            ChunkRecord.algorithm_version == row.algorithm_version,
            ChunkRecord.ordinal.between(row.ordinal - radius, row.ordinal + radius),
        )
        .order_by(ChunkRecord.ordinal)
    )
    return {
        "items": [read_chunk(session, context.actor, identity, "viewer") for identity in identities]
    }
