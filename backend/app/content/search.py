"""Authorize individual sources before PostgreSQL matching, ranking or snippets."""

import heapq
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import islice

from pydantic import Field
from sqlalchemy import exists, func, literal_column, or_, select

from app.api.errors import fail
from app.content.indexing import text_fields
from app.content.models import ContentExtraction, DocumentBlock, MetadataFragment
from app.registry.document_search import ExportParameters, SearchParameters, search_query
from app.registry.documents import document_summary, file_visible, raw_values
from app.registry.models import (
    Document,
    DocumentACL,
    DocumentFile,
    DocumentVersion,
    FormSchema,
    RegistryAccess,
)
from app.registry.policy import accessible_document_clause, permissions_for, redact_values

MAX_SEARCH_DOCUMENTS = 10000


class ContentSearchParameters(SearchParameters):
    text: str = Field(min_length=1, max_length=255)
    fuzzy: bool = True


class ContentExportParameters(ExportParameters):
    text: str = Field(min_length=1, max_length=255)
    fuzzy: bool = True


def _chunks(values, size=500):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def context_snippet(value, query):
    folded, words = value.casefold(), re.findall(r"\w{3,}", query.casefold())
    position = folded.find(query.casefold())
    if position < 0:
        position = next(
            (
                folded.find(word[:-2] if len(word) > 5 else word)
                for word in words
                if folded.find(word[:-2] if len(word) > 5 else word) >= 0
            ),
            -1,
        )
    if position < 0 and words:
        for match in re.finditer(r"\w{3,}", folded):
            if any(SequenceMatcher(None, match.group(), word).ratio() >= 0.65 for word in words):
                position = match.start()
                break
    start = max(0, position - 100)
    return (
        ("…" if start else "")
        + value[start : start + 400]
        + ("…" if start + 400 < len(value) else "")
    )


def _matching_expression(model, query, fuzzy):
    tsquery = func.websearch_to_tsquery(literal_column("'russian'::regconfig"), query)
    lexical = model.search_vector.op("@@")(tsquery)
    predicate = or_(lexical, model.text.op("%>")(query)) if fuzzy else lexical
    rank = func.ts_rank(model.search_vector, tsquery)
    if fuzzy:
        rank = func.greatest(rank, func.word_similarity(query, model.text) * 0.1)
    return predicate, rank


def _ranked(session, model, allowed, query, fuzzy, *, files=None):
    """Rank only authorized fragments, taking five per document, not per batch."""
    result = defaultdict(list)
    predicate, rank = _matching_expression(model, query, fuzzy)
    for part in _chunks(allowed):
        authorization = (
            model.id.in_(part) if model is MetadataFragment else model.extraction_id.in_(part)
        )
        ranked = (
            select(
                model.id,
                rank.label("rank"),
                func.row_number()
                .over(partition_by=model.document_id, order_by=(rank.desc(), model.id))
                .label("position"),
            )
            .where(authorization, predicate)
            .subquery()
        )
        for row, score in session.execute(
            select(model, ranked.c.rank)
            .join(ranked, ranked.c.id == model.id)
            .where(ranked.c.position <= 5)
            .order_by(ranked.c.rank.desc(), model.id)
        ):
            if model is MetadataFragment:
                match = {"source": "metadata", "field_key": row.field_key, "label": row.label}
            else:
                extraction, file, version = files[row.extraction_id]
                match = {
                    "source": "file",
                    "file_id": file.id,
                    "version_id": file.version_id,
                    "version": version,
                    "extraction_id": extraction.id,
                    "block_id": row.id,
                    "block_ordinal": row.ordinal,
                    "label": file.name,
                }
            result[row.document_id].append(
                {**match, "snippet": context_snippet(row.text, query), "rank": float(score)}
            )
    return {
        identity: sorted(matches, key=lambda item: -item["rank"])[:5]
        for identity, matches in result.items()
    }


def _downloadable_documents(session, actor, documents):
    registry_ids = {document.registry_id for document in documents}
    # Keep these ORM rows alive for canonical permissions_for's identity-map lookup.
    grants = list(
        session.scalars(
            select(RegistryAccess).where(
                RegistryAccess.registry_id.in_(registry_ids), RegistryAccess.user_id == actor.id
            )
        )
    )
    allowed_registries = {
        grant.registry_id
        for grant in grants
        if "download" in permissions_for(session, actor, grant.registry_id)
    }
    if not allowed_registries:
        return []
    restricted = exists().where(DocumentACL.document_id == Document.id)
    individual = exists().where(
        DocumentACL.document_id == Document.id,
        DocumentACL.user_id == actor.id,
        DocumentACL.permissions.contains(["download"]),
    )
    return list(
        session.scalars(
            select(Document.id).where(
                Document.id.in_([document.id for document in documents]),
                Document.registry_id.in_(allowed_registries),
                accessible_document_clause(session, actor),
                or_(~restricted, individual),
            )
        )
    )


def _batch_matches(session, actor, params, documents):
    by_id = {document.id: document for document in documents}
    schemas = {
        schema.id: schema
        for schema in session.scalars(
            select(FormSchema).where(
                FormSchema.id.in_({document.schema_version_id for document in documents})
            )
        )
    }
    values, visible_keys = {}, {}
    for document in documents:
        schema = schemas.get(document.schema_version_id)
        if schema is None or schema.state != "published":
            fail(409, "schema_unavailable", "Схема документа недоступна")
        values[document.id] = raw_values(document, schema.schema_data)
        visible = redact_values(schema.schema_data, values[document.id], actor, params.mode)
        visible_keys[document.id] = {key for key, _, _ in text_fields(schema.schema_data, visible)}

    # Fetch identities only. Current field authorization and revision equality
    # precede all text matching, ranking, counts and snippets.
    fragments = [
        identity
        for identity, document_id, revision, key in session.execute(
            select(
                MetadataFragment.id,
                MetadataFragment.document_id,
                MetadataFragment.document_revision,
                MetadataFragment.field_key,
            ).where(MetadataFragment.document_id.in_(by_id))
        )
        if revision == by_id[document_id].revision and key in visible_keys[document_id]
    ]
    matches = _ranked(session, MetadataFragment, fragments, params.text, params.fuzzy)
    files = {}
    downloadable = _downloadable_documents(session, actor, documents)
    if downloadable:
        for extraction, file, version in session.execute(
            select(ContentExtraction, DocumentFile, DocumentVersion.version)
            .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
            .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
            .where(
                ContentExtraction.document_id.in_(downloadable),
                ContentExtraction.state == "completed",
            )
        ):
            document = by_id[extraction.document_id]
            schema = schemas[document.schema_version_id].schema_data
            if file_visible(schema, actor, file, values[document.id], params.mode):
                files[extraction.id] = extraction, file, version
    for identity, file_matches in _ranked(
        session, DocumentBlock, list(files), params.text, params.fuzzy, files=files
    ).items():
        matches.setdefault(identity, []).extend(file_matches)
    for document in documents:
        if document.id in matches:
            yield document, sorted(matches[document.id], key=lambda item: -item["rank"])[:5]


def iter_matches(session, actor, params):
    if not params.text.strip():
        fail(422, "content_query_empty", "Введите слова для поиска по содержанию")
    session.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.45", True)))
    documents, _ = search_query(session, actor, params)
    with session.scalars(documents.execution_options(yield_per=100)) as rows:
        processed = 0
        while batch := list(islice(rows, 100)):
            processed += len(batch)
            if processed > MAX_SEARCH_DOCUMENTS:
                fail(
                    422,
                    "content_search_limit",
                    "Уточните реестр, тип или реквизиты: предел поиска 10000 документов",
                )
            yield from _batch_matches(session, actor, params, batch)


def search_response(session, actor, params):
    maximum, total, best = params.page * params.page_size, 0, []
    for document, matches in iter_matches(session, actor, params):
        total += 1
        item = (matches[0]["rank"], -document.id.int, document, matches)
        if len(best) < maximum:
            heapq.heappush(best, item)
        elif item[:2] > best[0][:2]:
            heapq.heapreplace(best, item)
    ordered = sorted(best, key=lambda item: (-item[0], -item[1]))
    items = [
        {
            "document": document_summary(session, document, actor, mode=params.mode),
            "matches": matches,
        }
        for _, _, document, matches in ordered[
            (params.page - 1) * params.page_size : params.page * params.page_size
        ]
    ]
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


def matching_ids(session, actor, params):
    return [document.id for document, _ in iter_matches(session, actor, params)]
