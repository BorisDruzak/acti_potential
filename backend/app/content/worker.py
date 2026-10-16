"""Recoverable extraction leases and a bounded existing-file/index backlog."""

import re
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from app.content.indexing import index_metadata, queue_file
from app.content.models import ChunkRecord, ContentExtraction, DocumentBlock, DocumentIndexState
from app.platform.outbox import enqueue_event
from app.registry.models import Document, DocumentFile

MAX_ATTEMPTS = 3
MAX_BLOCKS = 5000
MAX_TEXT_BYTES = 2 * 1024 * 1024
PARSER_SECONDS = 15


def process_backlog(factory, limit=5):
    with factory.begin() as session:
        documents = list(
            session.scalars(
                select(Document.id)
                .outerjoin(DocumentIndexState, DocumentIndexState.document_id == Document.id)
                .where(
                    or_(
                        DocumentIndexState.document_id.is_(None),
                        DocumentIndexState.document_revision != Document.revision,
                        DocumentIndexState.index_version != 2,
                    )
                )
                .order_by(Document.created_at)
                .limit(limit)
            )
        )
        for identity in documents:
            index_metadata(session, identity)
        files = session.scalars(
            select(DocumentFile)
            .where(
                ~select(ContentExtraction.id)
                .where(ContentExtraction.file_id == DocumentFile.id)
                .exists()
            )
            .order_by(DocumentFile.created_at)
            .limit(limit)
        )
        for file in files:
            queue_file(session, file)
        from app.content.chunks import CHUNK_VERSION, index_chunks

        extractions = list(
            session.scalars(
                select(ContentExtraction.id)
                .where(
                    ContentExtraction.state == "completed",
                    ~select(ChunkRecord.id)
                    .where(
                        ChunkRecord.extraction_id == ContentExtraction.id,
                        ChunkRecord.algorithm_version == CHUNK_VERSION,
                    )
                    .exists(),
                    select(DocumentBlock.id)
                    .where(DocumentBlock.extraction_id == ContentExtraction.id)
                    .exists(),
                )
                .order_by(ContentExtraction.id)
                .limit(limit)
            )
        )
        for identity in extractions:
            index_chunks(session, identity)
    return len(documents)


def _claim(factory):
    now = datetime.now(UTC)
    with factory.begin() as session:
        # A crashed worker never leaves a permanent running state.
        abandoned = list(
            session.scalars(
                select(ContentExtraction)
                .where(ContentExtraction.state == "running", ContentExtraction.lease_until < now)
                .with_for_update(skip_locked=True)
                .limit(5)
            )
        )
        for row in abandoned:
            row.state = "pending" if row.attempts < MAX_ATTEMPTS else "failed"
            row.error_code = "worker_interrupted"
            row.lease_until = None
        row = session.scalar(
            select(ContentExtraction)
            .where(
                ContentExtraction.state == "pending",
                ContentExtraction.attempts < MAX_ATTEMPTS,
                ContentExtraction.next_attempt_at <= now,
            )
            .order_by(ContentExtraction.created_at, ContentExtraction.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.state, row.started_at, row.lease_until = "running", now, now + timedelta(seconds=60)
        row.attempts += 1
        file = session.get(DocumentFile, row.file_id)
        return {
            "id": row.id,
            "file_id": file.id,
            "storage_key": file.storage_key,
            "name": file.name,
            "mime": file.mime,
            "sha256": file.sha256,
            "attempt": row.attempts,
            "fingerprint": row.fingerprint,
            "parser": row.parser,
            "parser_version": row.parser_version,
        }


def _valid_result(result):
    if not isinstance(result, dict) or result.get("state") not in {
        "completed",
        "no_text",
        "unsupported",
        "encrypted",
        "failed",
    }:
        raise ValueError("Invalid parser result")
    full_text = result.get("full_text", "")
    blocks = result.get("blocks", [])
    if (
        not isinstance(full_text, str)
        or len(full_text.encode()) > MAX_TEXT_BYTES
        or not isinstance(blocks, list)
        or len(blocks) > MAX_BLOCKS
    ):
        raise ValueError("Parser result exceeds bounds")
    if result["state"] != "completed" and blocks:
        raise ValueError("Only complete extractions contain blocks")
    seen, consumed = set(), 0
    for block in blocks:
        ordinal, parent = block.get("ordinal"), block.get("parent_ordinal")
        if (
            type(ordinal) is not int
            or ordinal < 0
            or ordinal in seen
            or parent is not None
            and parent not in seen
        ):
            raise ValueError("Invalid block order")
        seen.add(ordinal)
        if (
            not isinstance(block.get("text"), str)
            or not isinstance(block.get("anchor"), str)
            or len(block["anchor"]) > 255
        ):
            raise ValueError("Invalid block text or anchor")
        consumed += len(block["text"].encode())
        if consumed > MAX_TEXT_BYTES:
            raise ValueError("Parser blocks exceed text limit")
        if block.get("kind") not in {"paragraph", "heading", "table", "table_row", "page", "point"}:
            raise ValueError("Unknown block kind")
        if block.get("page") is not None and (
            type(block["page"]) is not int or not 1 <= block["page"] <= 1000
        ):
            raise ValueError("Invalid block page")
    return full_text, blocks


def process_extractions(factory, storage_root, limit=1, *, parser=None):
    if parser is None:
        from app.content.sandbox import extract_file

        parser = extract_file
    completed = 0
    for _ in range(min(limit, 1)):
        claim = _claim(factory)
        if claim is None:
            break
        try:
            result = parser(
                storage_root,
                claim["storage_key"],
                claim["name"],
                claim["mime"],
                claim["sha256"],
                timeout_seconds=PARSER_SECONDS,
            )
            if (
                result.get("parser") != claim["parser"]
                or result.get("parser_version") != claim["parser_version"]
            ):
                result = {"state": "failed", "error_code": "parser_version_changed"}
            full_text, blocks = _valid_result(result)
        except Exception:
            result, full_text, blocks = {"state": "failed", "error_code": "parser_failed"}, "", []
        with factory.begin() as session:
            row = session.scalar(
                select(ContentExtraction)
                .where(ContentExtraction.id == claim["id"])
                .with_for_update()
            )
            if row.state != "running" or row.attempts != claim["attempt"]:
                continue
            row.full_text, row.state, row.completed_at, row.lease_until = (
                full_text,
                result["state"],
                datetime.now(UTC),
                None,
            )
            code = result.get("error_code")
            row.error_code = (
                code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_.-]{1,80}", code) else None
            )
            row.warnings = [
                value
                for value in result.get("warnings", [])
                if isinstance(value, str) and re.fullmatch(r"[a-z0-9_.-]{1,80}", value)
            ][:20]
            identities = {
                block["ordinal"]: uuid5(
                    NAMESPACE_URL,
                    f"acti:block:{row.fingerprint}:{block['ordinal']}:{block['anchor']}",
                )
                for block in blocks
            }
            for block in blocks:
                session.add(
                    DocumentBlock(
                        id=identities[block["ordinal"]],
                        extraction_id=row.id,
                        document_id=row.document_id,
                        kind=block["kind"],
                        ordinal=block["ordinal"],
                        parent_id=identities.get(block.get("parent_ordinal")),
                        page=block.get("page"),
                        anchor=block["anchor"],
                        text=block["text"],
                        table_data=block.get("table"),
                    )
                )
            if (
                row.state == "failed"
                and row.error_code
                in {"timeout", "parser_timeout", "storage_unavailable", "worker_interrupted"}
                and row.attempts < MAX_ATTEMPTS
            ):
                row.state = "pending"
                row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2**row.attempts)
            session.flush()
            if row.state == "completed":
                from app.content.chunks import index_chunks

                index_chunks(session, row.id)
            enqueue_event(
                session,
                "content.extraction.completed"
                if row.state == "completed"
                else "content.extraction.status",
                row.id,
                {"file_id": str(row.file_id)},
            )
            completed += 1
    return completed
