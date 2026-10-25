"""Independent review regressions on synthetic, isolated PostgreSQL data."""

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.content.chunks import index_chunks, read_chunk, retrieve
from app.content.contracts import SearchInput
from app.content.engine import execute_search
from app.content.models import ChunkRecord, ContentExtraction, DocumentBlock
from app.registry.models import DocumentFile, DocumentVersion
from tests.test_task011_search_performance import indexed_corpus


def file_corpus(factory, count):
    sample, identities = indexed_corpus(factory, count=count)
    files, extractions = [], []
    with factory.begin() as db:
        for identity in identities:
            version = DocumentVersion(
                document_id=identity, version=1, created_by=sample["editor"].id
            )
            db.add(version)
            db.flush()
            file = DocumentFile(
                document_id=identity,
                version_id=version.id,
                name="synthetic.docx",
                size=1,
                mime="application/octet-stream",
                sha256="0" * 64,
                storage_key=uuid4().hex,
                created_by=sample["editor"].id,
            )
            db.add(file)
            db.flush()
            extraction = ContentExtraction(
                document_id=identity,
                file_id=file.id,
                version_id=version.id,
                sha256=file.sha256,
                fingerprint=uuid4().hex,
                parser="docx",
                parser_version="synthetic-old",
                state="completed",
            )
            db.add(extraction)
            db.flush()
            for ordinal in range(5):
                db.add(
                    DocumentBlock(
                        id=uuid4(),
                        extraction_id=extraction.id,
                        document_id=identity,
                        kind="heading",
                        ordinal=ordinal,
                        anchor=f"synthetic:{ordinal}",
                        text=f"Синтетическийфрагмент раздел {ordinal}",
                    )
                )
            db.flush()
            index_chunks(db, extraction.id)
            files.append(file.id)
            extractions.append(extraction.id)
    return sample, identities, files, extractions


@pytest.mark.parametrize("count", [1, 10, 50])
def test_retrieval_sql_is_bounded_per_page(factory, count, record_property):
    sample, _, _, _ = file_corpus(factory, count)
    statements, sql_seconds = [], []
    starts = []
    engine = factory.kw["bind"]

    def before(_conn, _cursor, statement, _params, _context, _many):
        starts.append(time.perf_counter())
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def after(*_args):
        sql_seconds.append(time.perf_counter() - starts.pop())

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with factory() as db:
            found = execute_search(
                db,
                sample["viewer"],
                SearchInput(
                    query="синтетическийфрагмент", fuzzy=False, scope="content", page_size=count
                ),
                secret="synthetic-test-only",
            )
            statements.clear()
            sql_seconds.clear()
            start, cpu = time.perf_counter(), time.process_time()
            chunks = retrieve(db, sample["viewer"], found)
            measured = {
                "documents": count,
                "chunks": len(chunks),
                "selects": len(statements),
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "cpu_ms": round((time.process_time() - cpu) * 1000, 2),
                "sql_ms": round(sum(sql_seconds) * 1000, 2),
            }
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    print("REVIEW_RETRIEVAL " + json.dumps(measured))
    record_property("retrieval_measurement", json.dumps(measured))
    assert len(chunks) == count * 5
    assert measured["selects"] <= 100, measured


@pytest.mark.parametrize("version_scope", ["current", "all"])
def test_search_ignores_superseded_successful_parser_runs(factory, version_scope):
    sample, identities, files, old = file_corpus(factory, 1)
    with factory.begin() as db:
        previous = db.get(ContentExtraction, old[0])
        previous.created_at = datetime.now(UTC) - timedelta(days=1)
        newer = ContentExtraction(
            document_id=identities[0],
            file_id=files[0],
            version_id=previous.version_id,
            sha256=previous.sha256,
            fingerprint=uuid4().hex,
            parser="docx",
            parser_version="synthetic-new",
            state="completed",
        )
        db.add(newer)
        db.flush()
        db.add(
            DocumentBlock(
                id=uuid4(),
                extraction_id=newer.id,
                document_id=identities[0],
                kind="paragraph",
                ordinal=0,
                anchor="synthetic:new",
                text="Исправленный текст",
            )
        )
    with factory() as db:
        result = execute_search(
            db,
            sample["viewer"],
            SearchInput(
                query="синтетическийфрагмент",
                fuzzy=False,
                scope="content",
                version_scope=version_scope,
            ),
            secret="synthetic-test-only",
        )
    assert result["total"] == 0
    with factory() as db:
        pinned = db.scalar(select(ChunkRecord.id).where(ChunkRecord.extraction_id == old[0]))
        assert "Синтетическийфрагмент" in read_chunk(db, sample["viewer"], pinned)["text"]


def test_error_page_sql_does_not_scan_every_extraction(factory):
    from app.content.api import errors
    from app.registry.models import RegistryAccess

    sample, _, _, identities = file_corpus(factory, 30)
    with factory.begin() as db:
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "download"],
            )
        )
        for identity in identities:
            db.get(ContentExtraction, identity).state = "failed"
    statements = []
    engine = factory.kw["bind"]

    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with factory() as db:
            result = errors(db, sample["admin"], page=1, page_size=1)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    print("REVIEW_ERROR_PAGE " + json.dumps({"documents": 30, "selects": len(statements)}))
    assert result["total"] == 30 and len(result["items"]) == 1
    assert len(statements) <= 20


@pytest.mark.parametrize("source", ["content", "metadata"])
def test_batched_retrieval_rechecks_document_acl_after_search(factory, source):
    from fastapi import HTTPException

    from app.registry.models import DocumentACL

    sample, identities, _, _ = file_corpus(factory, 1)
    params = SearchInput(
        query="синтетическийфрагмент" if source == "content" else "пилот", scope=source, fuzzy=False
    )
    with factory() as db:
        result = execute_search(db, sample["viewer"], params, secret="synthetic-test-only")
    assert result["total"] == 1
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=identities[0],
                user_id=sample["editor"].id,
                permissions=["read", "download"],
            )
        )
    with factory() as db, pytest.raises(HTTPException) as failure:
        retrieve(db, sample["viewer"], result)
    assert failure.value.status_code == 404


def test_error_page_counts_only_authorized_sources_before_pagination(factory):
    from app.content.api import errors
    from app.registry.models import Document, DocumentACL, FormSchema, RegistryAccess

    sample, documents, files, extractions = file_corpus(factory, 5)
    with factory.begin() as db:
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "download"],
            )
        )
        document = db.get(Document, documents[0])
        schema = db.get(FormSchema, document.schema_version_id)
        newer_schema = FormSchema(
            document_type_id=schema.document_type_id,
            version=2,
            state="published",
            schema_data={
                "sections": [
                    {
                        "fields": [
                            {"key": "private", "type": "file", "read_roles": ["editor"]},
                        ]
                    }
                ]
            },
        )
        db.add(newer_schema)
        db.flush()
        for identity in documents:
            db.get(Document, identity).schema_version_id = newer_schema.id
        db.get(DocumentFile, files[0]).field_key = "private"
        db.add(
            DocumentACL(
                document_id=documents[1],
                user_id=sample["editor"].id,
                permissions=["read", "download"],
            )
        )
        db.get(Document, documents[2]).deleted_at = datetime.now(UTC)
        for identity in extractions:
            db.get(ContentExtraction, identity).state = "failed"
    with factory() as db:
        first = errors(db, sample["admin"], page=1, page_size=1)
        second = errors(db, sample["admin"], page=2, page_size=1)
    assert first["total"] == second["total"] == 2
    received = {page["items"][0]["file_id"] for page in (first, second)}
    assert received == set(files[3:])
