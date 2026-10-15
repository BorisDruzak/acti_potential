"""Authorize individual sources before PostgreSQL matching, ranking or snippets."""

import heapq
import re
from difflib import SequenceMatcher

from pydantic import Field
from sqlalchemy import func, literal_column, or_, select

from app.api.errors import fail
from app.content.indexing import text_fields
from app.content.models import ContentExtraction, DocumentBlock, MetadataFragment
from app.registry.document_search import ExportParameters, SearchParameters, search_query
from app.registry.documents import document_summary, file_visible, get_schema, raw_values
from app.registry.models import DocumentFile, DocumentVersion
from app.registry.policy import document_permissions, redact_values

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


def _ranked(session, model, allowed, query, fuzzy, **extra):
    if not allowed:
        return []
    tsquery = func.websearch_to_tsquery(literal_column("'russian'::regconfig"), query)
    lexical = model.search_vector.op("@@")(tsquery)
    predicate = or_(lexical, model.text.op("%>")(query)) if fuzzy else lexical
    rank = func.ts_rank(model.search_vector, tsquery)
    if fuzzy:
        rank = func.greatest(rank, func.word_similarity(query, model.text) * 0.1)
    result = []
    for part in _chunks(allowed):
        authorization = (
            model.id.in_(part) if model is MetadataFragment else model.extraction_id.in_(part)
        )
        for row, score in session.execute(
            select(model, rank.label("rank"))
            .where(authorization, predicate)
            .order_by(rank.desc(), model.id)
            .limit(5)
        ):
            if model is MetadataFragment:
                match = {"source": "metadata", "field_key": row.field_key, "label": row.label}
            else:
                extraction, file, version = extra["files"][row.extraction_id]
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
            result.append(
                {**match, "snippet": context_snippet(row.text, query), "rank": float(score)}
            )
    return sorted(result, key=lambda item: -item["rank"])[:5]


def iter_matches(session, actor, params):
    if not params.text.strip():
        fail(422, "content_query_empty", "Введите слова для поиска по содержанию")
    session.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.45", True)))
    documents, _ = search_query(session, actor, params)
    with session.scalars(documents.execution_options(yield_per=100)) as rows:
        for index, document in enumerate(rows):
            if index >= MAX_SEARCH_DOCUMENTS:
                fail(
                    422,
                    "content_search_limit",
                    "Уточните реестр, тип или реквизиты: предел поиска 10000 документов",
                )
            schema, values = get_schema(session, document), None
            values = raw_values(document, schema)
            visible = redact_values(schema, values, actor, params.mode)
            keys = {key for key, _, _ in text_fields(schema, visible)}
            # Revision equality prevents old index values surviving metadata edits
            # while the asynchronous worker catches up.
            fragments = [
                identity
                for identity, key in session.execute(
                    select(MetadataFragment.id, MetadataFragment.field_key).where(
                        MetadataFragment.document_id == document.id,
                        MetadataFragment.document_revision == document.revision,
                    )
                )
                if key in keys
            ]
            matches = _ranked(session, MetadataFragment, fragments, params.text, params.fuzzy)
            files = {}
            if "download" in document_permissions(session, actor, document):
                for extraction, file, version in session.execute(
                    select(ContentExtraction, DocumentFile, DocumentVersion.version)
                    .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
                    .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
                    .where(
                        ContentExtraction.document_id == document.id,
                        ContentExtraction.state == "completed",
                    )
                ):
                    if file_visible(schema, actor, file, values, params.mode):
                        files[extraction.id] = extraction, file, version
            matches += _ranked(
                session, DocumentBlock, list(files), params.text, params.fuzzy, files=files
            )
            if matches:
                yield document, sorted(matches, key=lambda item: -item["rank"])[:5]


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
