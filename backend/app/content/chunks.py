"""Versioned structure-aware source maps; no duplicate document text storage."""

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONPATH, insert

from app.api.errors import fail
from app.content.models import ChunkRecord, ContentExtraction, DocumentBlock, MetadataFragment
from app.registry.models import DocumentFile

CHUNK_VERSION = "structure-1"
TARGET_CHARS = 2600  # approximately 400–800 tokens, adaptive to paragraph boundaries
MAX_CHARS = 4000
OVERLAP_CHARS = 240


def split_blocks(blocks):
    result, pending = [], []

    def flush():
        if not pending:
            return
        ranges = [
            {"block_id": str(block.id), "start": start, "end": end} for block, start, end in pending
        ]
        text = "\n".join(block.text[start:end] for block, start, end in pending)
        identity = uuid5(
            NAMESPACE_URL,
            f"acti:chunk:{CHUNK_VERSION}:{pending[0][0].extraction_id}:"
            + json.dumps(ranges, sort_keys=True),
        )
        result.append(
            {
                "id": identity,
                "ordinal": len(result),
                "ranges": ranges,
                "block_ids": list(dict.fromkeys(r["block_id"] for r in ranges)),
                "text": text,
                "page": pending[0][0].page,
                "anchor": pending[0][0].anchor,
                "checksum": hashlib.sha256(text.encode()).hexdigest(),
                "algorithm_version": CHUNK_VERSION,
            }
        )
        pending.clear()

    for block in blocks:
        if not block.text:
            continue
        boundary = block.kind in {"table", "table_row", "heading", "page", "section"}
        if pending and (
            pending[0][0].extraction_id != block.extraction_id
            or pending[0][0].page != block.page
            or boundary
        ):
            flush()
        # Both table and row sources retain their own provenance. Only compact
        # ranges are stored; identical assembled contexts are deduplicated below.
        if block.kind in {"table", "table_row"}:
            flush()
        start = 0
        while start < len(block.text):
            end = min(len(block.text), start + MAX_CHARS)
            if end < len(block.text):
                split = block.text.rfind(
                    "\n" if block.kind == "table" else " ", start + TARGET_CHARS, end
                )
                if split < 0 and block.kind == "table":
                    split = block.text.rfind(" ", start + TARGET_CHARS, end)
                if split > start:
                    end = split
            size = sum(e - s + 1 for _, s, e in pending)
            if pending and size + end - start > MAX_CHARS:
                flush()
            pending.append((block, start, end))
            if end < len(block.text):
                flush()
                start = max(start + 1, end - OVERLAP_CHARS)
            else:
                start = end
        if (
            block.kind in {"table", "table_row"}
            or sum(e - s for _, s, e in pending) >= TARGET_CHARS
        ):
            flush()
    flush()
    return result


def index_chunks(session, extraction_id):
    extraction = session.get(ContentExtraction, extraction_id)
    if extraction is None or extraction.state != "completed":
        return
    blocks = list(
        session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.extraction_id == extraction_id)
            .order_by(DocumentBlock.ordinal)
        )
    )
    for chunk in split_blocks(blocks):
        chunk.pop("text")
        session.execute(
            insert(ChunkRecord)
            .values(document_id=extraction.document_id, extraction_id=extraction.id, **chunk)
            .on_conflict_do_nothing(index_elements=["id"])
        )


def read_chunk(session, actor, identity, mode=None):
    from app.content.api import authorize_file
    from app.content.authorization import source_clauses
    from app.registry.documents import get_document
    from app.registry.models import Document
    from app.registry.policy import accessible_document_clause

    row = session.get(ChunkRecord, identity)
    if row is None:
        if actor.scoped_operations is not None and "documents" not in actor.scoped_operations:
            fail(404, "source_not_found", "Источник не найден")
        fragment = session.scalar(
            select(MetadataFragment)
            .join(Document, Document.id == MetadataFragment.document_id)
            .where(
                MetadataFragment.id == identity,
                MetadataFragment.document_revision == Document.revision,
                accessible_document_clause(session, actor, mode),
                source_clauses(session, actor, mode, MetadataFragment.field_key),
            )
        )
        if fragment is None:
            fail(404, "source_not_found", "Источник не найден")
        document = get_document(session, actor, fragment.document_id, mode=mode)
        return {
            "document_id": document.id,
            "chunk_id": fragment.id,
            "source_type": "metadata",
            "version_id": None,
            "metadata_revision": fragment.document_revision,
            "block_ids": [],
            "ranges": [],
            "text": fragment.text,
            "score": 0.0,
            "checksum": hashlib.sha256(fragment.text.encode()).hexdigest(),
            "algorithm_version": "metadata-2",
            "citation": {
                "document_id": document.id,
                "field_key": fragment.field_key,
                "metadata_revision": fragment.document_revision,
                "url": f"/view?document={document.id}",
            },
        }
    if actor.scoped_operations is not None and "content" not in actor.scoped_operations:
        fail(404, "source_not_found", "Источник не найден")
    extraction = session.get(ContentExtraction, row.extraction_id)
    file = session.get(DocumentFile, extraction.file_id)
    try:
        authorize_file(session, actor, file, mode)
    except Exception as error:
        from fastapi import HTTPException

        if isinstance(error, HTTPException) and error.status_code in {403, 404}:
            fail(404, "source_not_found", "Источник не найден")
        raise
    blocks = {
        str(block.id): block
        for block in session.scalars(
            select(DocumentBlock).where(
                DocumentBlock.id.in_([UUID(b) for b in row.block_ids]),
                DocumentBlock.extraction_id == extraction.id,
            )
        )
    }
    text = "\n".join(blocks[r["block_id"]].text[r["start"] : r["end"]] for r in row.ranges)
    if hashlib.sha256(text.encode()).hexdigest() != row.checksum:
        fail(409, "source_integrity_error", "Источник временно недоступен")
    first = blocks[row.ranges[0]["block_id"]]
    citation = {
        "document_id": row.document_id,
        "version_id": extraction.version_id,
        "file_id": file.id,
        "extraction_id": extraction.id,
        "block_ids": row.block_ids,
        "page": row.page,
        "anchor": row.anchor,
        "url": f"/view?document={row.document_id}&tab=content&file={file.id}&block={first.id}&ordinal={first.ordinal}",
    }
    return {
        "document_id": row.document_id,
        "chunk_id": row.id,
        "source_type": "file",
        "version_id": extraction.version_id,
        "document_version_id": extraction.version_id,
        "file_id": file.id,
        "extraction_id": extraction.id,
        "block_ids": row.block_ids,
        "ranges": row.ranges,
        "page": row.page,
        "anchor": row.anchor,
        "text": text,
        "score": 0.0,
        "checksum": row.checksum,
        "algorithm_version": row.algorithm_version,
        "citation": citation,
    }


def retrieve(session, actor, search_result, mode=None):
    output, seen, checksums = [], set(), set()
    for item in search_result["items"]:
        for match in item["matches"]:
            if match["source"] == "metadata":
                ids = [match["source_id"]]
            else:
                ids = list(
                    session.scalars(
                        select(ChunkRecord.id)
                        .where(
                            ChunkRecord.extraction_id == match["extraction_id"],
                            ChunkRecord.block_ids.contains([str(match["block_id"])]),
                            ChunkRecord.ranges.op("@?")(
                                cast(
                                    f'$[*] ? (@.block_id == "{match["block_id"]}" && @.start <= {match.get("block_offset", 0)} && @.end > {match.get("block_offset", 0)})',
                                    JSONPATH,
                                )
                            ),
                            ChunkRecord.algorithm_version == CHUNK_VERSION,
                        )
                        .order_by(ChunkRecord.ordinal)
                        .limit(3)
                    )
                )
            for identity in ids:
                if identity in seen:
                    continue
                chunk = read_chunk(session, actor, identity, mode)
                source_digest = (
                    str(chunk["document_id"]),
                    str(chunk.get("version_id")),
                    chunk["checksum"],
                )
                if source_digest in checksums:
                    continue
                chunk["score"] = match["rank"]
                output.append(chunk)
                seen.add(identity)
                checksums.add(source_digest)
    return output
