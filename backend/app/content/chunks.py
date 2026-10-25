"""Versioned structure-aware source maps; no duplicate document text storage."""

import hashlib
import json
from collections import defaultdict
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Integer, Text, Uuid, cast, column, select, true, values
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH, insert

from app.api.errors import fail
from app.content.models import ChunkRecord, ContentExtraction, DocumentBlock, MetadataFragment
from app.registry.models import Document, DocumentFile

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
        get_document(session, actor, fragment.document_id, mode=mode)
        return _metadata_output(fragment)
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
    return _file_output(row, extraction, file, blocks)


def _metadata_output(fragment):
    return {
        "document_id": fragment.document_id,
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
            "document_id": fragment.document_id,
            "field_key": fragment.field_key,
            "metadata_revision": fragment.document_revision,
            "url": f"/view?document={fragment.document_id}",
        },
    }


def _file_output(row, extraction, file, blocks):
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
    """Resolve a bounded page in batches while rechecking current source ACLs."""
    from app.content.authorization import download_clause, source_clauses
    from app.registry.policy import accessible_document_clause

    matches = [match for item in search_result["items"] for match in item["matches"]]
    if not matches:
        return []
    ids_by_match = {i: [m["source_id"]] for i, m in enumerate(matches) if m["source"] == "metadata"}
    file_matches = [
        (
            i,
            m["extraction_id"],
            [str(m["block_id"])],
            f'$[*] ? (@.block_id == "{m["block_id"]}" && @.start <= {m.get("block_offset", 0)} && @.end > {m.get("block_offset", 0)})',
        )
        for i, m in enumerate(matches)
        if m["source"] == "file"
    ]
    if file_matches:
        batch = (
            values(
                column("position", Integer),
                column("extraction_id", Uuid),
                column("block_ids", JSONB),
                column("path", Text),
            )
            .data(file_matches)
            .alias("match_page")
        )
        candidates = (
            select(ChunkRecord.id, ChunkRecord.ordinal)
            .where(
                ChunkRecord.extraction_id == batch.c.extraction_id,
                ChunkRecord.block_ids.contains(batch.c.block_ids),
                ChunkRecord.ranges.op("@?")(cast(batch.c.path, JSONPATH)),
                ChunkRecord.algorithm_version == CHUNK_VERSION,
            )
            .order_by(ChunkRecord.ordinal)
            .limit(3)
            .correlate(batch)
            .lateral()
        )
        for position, identity in session.execute(
            select(batch.c.position, candidates.c.id)
            .select_from(batch.join(candidates, true()))
            .order_by(batch.c.position, candidates.c.ordinal)
        ):
            ids_by_match.setdefault(position, []).append(identity)
    metadata_ids = [m["source_id"] for m in matches if m["source"] == "metadata"]
    file_ids = [
        identity
        for i, identities in ids_by_match.items()
        if matches[i]["source"] == "file"
        for identity in identities
    ]
    metadata, sources, blocks = {}, {}, defaultdict(dict)
    if metadata_ids and (actor.scoped_operations is None or "documents" in actor.scoped_operations):
        metadata = {
            row.id: row
            for row in session.scalars(
                select(MetadataFragment)
                .join(Document, Document.id == MetadataFragment.document_id)
                .where(
                    MetadataFragment.id.in_(metadata_ids),
                    MetadataFragment.document_revision == Document.revision,
                    accessible_document_clause(session, actor, mode),
                    source_clauses(session, actor, mode, MetadataFragment.field_key),
                )
            )
        }
    if file_ids and (actor.scoped_operations is None or "content" in actor.scoped_operations):
        sources = {
            row.id: (row, extraction, file)
            for row, extraction, file in session.execute(
                select(ChunkRecord, ContentExtraction, DocumentFile)
                .join(ContentExtraction, ContentExtraction.id == ChunkRecord.extraction_id)
                .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
                .join(Document, Document.id == ContentExtraction.document_id)
                .where(
                    ChunkRecord.id.in_(file_ids),
                    accessible_document_clause(session, actor, mode),
                    download_clause(actor),
                    source_clauses(session, actor, mode, DocumentFile.field_key, files=True),
                )
            )
        }
        block_ids = {UUID(identity) for row, _, _ in sources.values() for identity in row.block_ids}
        for block in session.scalars(select(DocumentBlock).where(DocumentBlock.id.in_(block_ids))):
            blocks[block.extraction_id][str(block.id)] = block
    output, seen, checksums = [], set(), set()
    for position, match in enumerate(matches):
        for identity in ids_by_match.get(position, []):
            if identity in seen:
                continue
            if match["source"] == "metadata":
                if identity not in metadata:
                    fail(404, "source_not_found", "Источник не найден")
                chunk = _metadata_output(metadata[identity])
            else:
                if identity not in sources:
                    fail(404, "source_not_found", "Источник не найден")
                row, extraction, file = sources[identity]
                chunk = _file_output(row, extraction, file, blocks[extraction.id])
            source_digest = (
                str(chunk["document_id"]),
                str(chunk.get("version_id")),
                chunk["checksum"],
            )
            seen.add(identity)
            if source_digest in checksums:
                continue
            chunk["score"] = match["rank"]
            output.append(chunk)
            checksums.add(source_digest)
    return output
