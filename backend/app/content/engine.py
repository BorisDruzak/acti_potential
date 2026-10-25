"""Indexed candidate retrieval, current authorization, DB ranking and keyset paging."""

import base64
import hashlib
import hmac
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from functools import wraps
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Text,
    Uuid,
    and_,
    case,
    column,
    func,
    literal,
    literal_column,
    or_,
    select,
    union_all,
    values,
)
from sqlalchemy.exc import DBAPIError

from app.api.errors import fail
from app.content.authorization import download_clause, source_clauses
from app.content.models import ContentExtraction, DocumentBlock, MetadataFragment
from app.content.normalization import expand, fuzzy_allowed, normalize, number_key
from app.content.queries import latest_extraction_clause
from app.content.search import context_snippet
from app.registry.document_search import search_query
from app.registry.models import Document, DocumentFile, DocumentVersion, FormSchema
from app.registry.presentation import configuration_text

DEFAULT_WEIGHTS = {
    "number": 1.0,
    "title_exact": 0.95,
    "title_partial": 0.8,
    "metadata": 0.6,
    "content": 0.4,
    "fuzzy": 0.15,
}
DEFAULT_SYNONYMS = [["ЖКХ", "жилищно-коммунальное хозяйство"], ["НПА", "нормативный правовой акт"]]


def configure_search(session):
    session.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.45", True)))
    session.execute(select(func.set_config("statement_timeout", "8000", True)))


def search_errors(operation):
    @wraps(operation)
    def bounded(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "57014":
                fail(
                    422,
                    "search_query_too_broad",
                    "Поиск занял слишком много времени. Уточните запрос или добавьте фильтры.",
                )
            raise

    return bounded


def normalized_column(column):
    return func.replace(func.lower(column), "ё", "е")


def matching(model, query, fuzzy, synonyms):
    normalized = normalize(query)
    tsquery = func.websearch_to_tsquery(
        literal_column("'russian'::regconfig"), expand(query, synonyms)
    )
    lexical = model.search_vector.op("@@")(tsquery)
    # Quoted phrases are never broadened by substring or fuzzy branches.
    if '"' in normalized:
        predicate = lexical
    elif any(c.isdigit() for c in normalized):
        predicate = model.text.icontains(normalized, autoescape=True)
    else:
        predicate = lexical
        if len(normalized) >= 3:
            predicate = or_(predicate, model.text.icontains(normalized, autoescape=True))
    approximate = fuzzy and fuzzy_allowed(normalized)
    if approximate:
        predicate = or_(predicate, model.text.op("%>")(normalized))
    strength = func.ts_rank_cd(model.search_vector, tsquery, 32)
    if approximate:
        strength = func.greatest(strength, func.word_similarity(normalized, model.text) * 0.8)
    return predicate, lexical, strength, approximate


def candidate_query(session, actor, params, *, weights=None, synonyms=None, legacy=None):
    weights = DEFAULT_WEIGHTS | (weights or {})
    synonyms = DEFAULT_SYNONYMS if synonyms is None else synonyms
    base, fields = search_query(session, actor, legacy or params.legacy_filters())
    if params.registry_ids:
        base = base.where(Document.registry_id.in_(params.registry_ids))
    if params.document_type_ids:
        base = base.where(Document.document_type_id.in_(params.document_type_ids))
    clauses = tuple(base._where_criteria)
    if not params.query.strip():
        return base, None, fields
    branches = []
    schemas = {
        row.id: row.schema_data
        for row in session.scalars(select(FormSchema).where(FormSchema.state == "published"))
    }
    title_keys, number_keys = [], []
    for sid, schema in schemas.items():
        for section in schema.get("sections", []):
            for field in section.get("fields", []):
                target = (
                    title_keys
                    if field.get("system") == "title"
                    else number_keys
                    if field.get("system") == "reg_number"
                    else None
                )
                if target is not None:
                    target.append(
                        and_(
                            Document.schema_version_id == sid,
                            MetadataFragment.field_key == field["key"],
                        )
                    )
    title = or_(literal(False), *title_keys)
    number = or_(literal(False), *number_keys)
    if params.scope in {"all", "metadata"}:
        model = MetadataFragment
        predicate, lexical, strength, approximate = matching(
            model, params.query, params.fuzzy, synonyms
        )
        exact_number = and_(
            number,
            func.md5(
                func.regexp_replace(
                    func.replace(
                        func.lower(model.text), literal_column("'ё'"), literal_column("'е'")
                    ),
                    literal_column("'[^[:alnum:]]'"),
                    literal_column("''"),
                    literal_column("'g'"),
                )
            )
            == hashlib.md5(number_key(params.query).encode()).hexdigest(),
            func.regexp_replace(normalized_column(model.text), "[^[:alnum:]]", "", "g")
            == number_key(params.query),
        )
        predicate = or_(exact_number, and_(~number, predicate))
        normal = normalized_column(model.text)
        exact_title = and_(title, normal == normalize(params.query).strip('"'))
        partial_title = and_(
            title, normal.contains(normalize(params.query).strip('"'), autoescape=True)
        )
        score = (
            case(
                (exact_number, weights["number"]),
                (exact_title, weights["title_exact"]),
                (partial_title, weights["title_partial"]),
                (lexical, weights["metadata"]),
                else_=weights["fuzzy"],
            )
            + strength * 0.04
        )
        branches.append(
            select(
                model.document_id,
                model.id.label("source_id"),
                literal("metadata").label("source"),
                func.least(1.0, score).label("score"),
            )
            .join(Document, Document.id == model.document_id)
            .where(
                *clauses,
                model.document_revision == Document.revision,
                source_clauses(session, actor, params.mode, model.field_key),
                predicate,
            )
        )
    if params.scope in {"all", "content"} and (
        actor.scoped_operations is None or "content" in actor.scoped_operations
    ):
        model = DocumentBlock
        predicate, lexical, strength, approximate = matching(
            model, params.query, params.fuzzy, synonyms
        )
        score = case((lexical, weights["content"]), else_=weights["fuzzy"]) + strength * 0.04
        branch = (
            select(
                model.document_id,
                model.id.label("source_id"),
                literal("file").label("source"),
                func.least(1.0, score).label("score"),
            )
            .join(Document, Document.id == model.document_id)
            .join(ContentExtraction, ContentExtraction.id == model.extraction_id)
            .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
            .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
            .where(
                *clauses,
                ContentExtraction.state == "completed",
                latest_extraction_clause(),
                download_clause(actor),
                source_clauses(session, actor, params.mode, DocumentFile.field_key, files=True),
                predicate,
            )
        )
        if params.version_scope == "current":
            # Original identity, never filename, determines binary succession.
            # Independent legacy uploads with identical names remain independent.
            from app.registry.documents import current_binary_clause

            branch = branch.where(current_binary_clause())
        branches.append(branch)
    if not branches:
        # A documents-only service token can browse metadata but never file text.
        branches.append(
            select(
                Document.id.label("document_id"),
                Document.id.label("source_id"),
                literal("metadata").label("source"),
                literal(0.0).label("score"),
            ).where(False)
        )
    candidates = union_all(*branches).subquery("authorized_sources")
    ranked = (
        select(
            candidates.c.document_id,
            func.max(candidates.c.score).label("score"),
            func.count().label("match_count"),
        )
        .group_by(candidates.c.document_id)
        .subquery("ranked_documents")
    )
    query = base.join(ranked, ranked.c.document_id == Document.id)
    return query, (candidates, ranked), fields


def binding(actor, params, algorithm):
    payload = {
        "actor": str(actor.id),
        "roles": sorted(actor.roles),
        "registries": sorted(actor.scoped_registry_ids or []),
        "operations": sorted(actor.scoped_operations or []),
        "algorithm": algorithm,
        "params": params.model_dump(mode="json", exclude={"cursor"}),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def encode_cursor(data, secret):
    encoded = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def decode_cursor(value, secret, fingerprint):
    try:
        encoded, signature = value.split(".")
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(encoded))
        if data["binding"] != fingerprint or time.time() - data["time"] > 900:
            raise ValueError()
        UUID(data["id"])
        return data
    except (ValueError, KeyError, TypeError):
        fail(422, "invalid_search_cursor", "Курсор поиска недействителен. Повторите поиск.")


def source_matches(session, actor, params, candidates, identities, synonyms=None):
    ranked = (
        select(
            candidates,
            func.row_number()
            .over(
                partition_by=candidates.c.document_id,
                order_by=(candidates.c.score.desc(), candidates.c.source_id),
            )
            .label("position"),
        )
        .where(candidates.c.document_id.in_(identities))
        .subquery()
    )
    rows = list(
        session.execute(
            select(ranked).where(ranked.c.position <= 5).order_by(ranked.c.position)
        ).mappings()
    )
    metadata = {
        row.id: row
        for row in session.scalars(
            select(MetadataFragment).where(
                MetadataFragment.id.in_([r["source_id"] for r in rows if r["source"] == "metadata"])
            )
        )
    }
    blocks = {
        b.id: (b, e, f, v)
        for b, e, f, v in session.execute(
            select(DocumentBlock, ContentExtraction, DocumentFile, DocumentVersion)
            .join(ContentExtraction, ContentExtraction.id == DocumentBlock.extraction_id)
            .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
            .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
            .where(DocumentBlock.id.in_([r["source_id"] for r in rows if r["source"] == "file"]))
        )
    }
    output = defaultdict(list)
    snippets = {}
    for item in rows:
        row = (
            metadata[item["source_id"]]
            if item["source"] == "metadata"
            else blocks[item["source_id"]][0]
        )
        snippets[row.id] = context_snippet(
            row.text, expand(params.query, DEFAULT_SYNONYMS if synonyms is None else synonyms)
        )
    highlighted = {}
    if snippets:
        batch = (
            values(column("id", Uuid), column("snippet", Text))
            .data(list(snippets.items()))
            .alias("snippet_page")
        )
        query = func.websearch_to_tsquery(
            literal_column("'russian'::regconfig"),
            expand(params.query, DEFAULT_SYNONYMS if synonyms is None else synonyms),
        )
        highlighted = dict(
            session.execute(
                select(
                    batch.c.id,
                    func.ts_headline(
                        literal_column("'russian'::regconfig"),
                        batch.c.snippet,
                        query,
                        "StartSel=ACTI_MATCH_START, StopSel=ACTI_MATCH_END, HighlightAll=true",
                    ),
                )
            ).all()
        )
    for item in rows:
        row = (
            metadata[item["source_id"]]
            if item["source"] == "metadata"
            else blocks[item["source_id"]][0]
        )
        citation = {"document_id": row.document_id, "url": f"/view?document={row.document_id}"}
        parts, spans, plain, marking = (
            re.split(
                r"(ACTI_MATCH_START|ACTI_MATCH_END)", highlighted.get(row.id, snippets[row.id])
            ),
            [],
            "",
            False,
        )
        for part in parts:
            if part == "ACTI_MATCH_START":
                marking = True
            elif part == "ACTI_MATCH_END":
                marking = False
            else:
                if marking and part:
                    spans.append([len(plain), len(plain) + len(part)])
                plain += part
        if not spans and params.fuzzy and fuzzy_allowed(params.query):
            terms = re.findall(r"\w+", normalize(params.query))
            spans = [
                [word.start(), word.end()]
                for word in re.finditer(r"\w+", plain)
                if any(
                    SequenceMatcher(None, normalize(word.group()), term).ratio() >= 0.65
                    for term in terms
                )
            ]
        offset = max(0, row.text.find(snippets[row.id].strip("…")))
        if spans:
            start, end = spans[0]
            position = row.text.find(plain[start:end], offset)
            if position >= 0:
                offset = position
        match = {
            "source": item["source"],
            "source_id": row.id,
            "rank": float(item["score"]),
            "snippet": plain,
            "highlights": [
                [
                    len(plain[:start].encode("utf-16-le")) // 2,
                    len(plain[:end].encode("utf-16-le")) // 2,
                ]
                for start, end in spans
            ],
            "block_offset": offset,
        }
        if item["source"] == "metadata":
            match.update(field_key=row.field_key, label=configuration_text(row.label))
            citation["field_key"] = row.field_key
            citation["metadata_revision"] = row.document_revision
        else:
            block, extraction, file, version = blocks[row.id]
            match.update(
                file_id=file.id,
                version_id=version.id,
                version=version.version,
                extraction_id=extraction.id,
                block_id=block.id,
                block_ordinal=block.ordinal,
                page=block.page,
                anchor=block.anchor,
                label=file.name,
            )
            citation.update(
                file_id=file.id,
                version_id=version.id,
                extraction_id=extraction.id,
                block_ids=[block.id],
                page=block.page,
                anchor=block.anchor,
                url=f"/view?document={row.document_id}&tab=content&file={file.id}&block={block.id}&ordinal={block.ordinal}",
            )
        match["citation"] = citation
        output[row.document_id].append(match)
    return output


@search_errors
def execute_search(
    session,
    actor,
    params,
    *,
    secret,
    weights=None,
    synonyms=None,
    algorithm="lexical-2",
    offset=0,
    legacy=None,
):
    started = time.perf_counter()
    configure_search(session)
    base, sources, _ = candidate_query(
        session, actor, params, weights=weights, synonyms=synonyms, legacy=legacy
    )
    total = session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    score = sources[1].c.score if sources else literal(0.0)
    match_count = sources[1].c.match_count if sources else literal(0)
    use_rank = params.sort == "relevance" or (params.sort == "auto" and params.query.strip())
    order_column = score if use_rank else base._order_by_clauses[0].element.element
    # Preserve the canonical safe sort expression, including condition-hidden values.
    descending = True if use_rank else params.direction == "desc"
    query = base.order_by(None).add_columns(score, match_count, order_column.label("sort_value"))
    profile = hashlib.sha256(
        json.dumps(
            {
                "weights": DEFAULT_WEIGHTS | (weights or {}),
                "synonyms": DEFAULT_SYNONYMS if synonyms is None else synonyms,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    fingerprint = binding(actor, params, algorithm + profile)
    if params.cursor:
        cursor = decode_cursor(params.cursor, secret, fingerprint)
        value = cursor["value"]
        from sqlalchemy import Numeric

        if value is not None and isinstance(order_column.type, Numeric):
            value = Decimal(str(value))
        if isinstance(value, str) and isinstance(order_column.type, DateTime):
            value = datetime.fromisoformat(value)
        elif isinstance(value, str) and isinstance(order_column.type, Date):
            value = date.fromisoformat(value)
        if value is None:
            after = and_(order_column.is_(None), Document.id > UUID(cursor["id"]))
        else:
            after = or_(
                order_column.is_(None),
                order_column < value if descending else order_column > value,
                and_(order_column == value, Document.id > UUID(cursor["id"])),
            )
        query = query.where(after)
    order = order_column.desc() if descending else order_column.asc()
    rows = list(
        session.execute(
            query.order_by(order.nulls_last(), Document.id)
            .offset(offset)
            .limit(params.page_size + 1)
        )
    )
    selected, more = rows[: params.page_size], len(rows) > params.page_size
    matches = (
        source_matches(session, actor, params, sources[0], [r[0].id for r in selected], synonyms)
        if sources and selected
        else {}
    )
    from app.content.serialization import summaries

    cards = summaries(session, actor, [r[0] for r in selected], params.mode)
    items = [
        {
            "document": cards[document.id],
            "matches": matches.get(document.id, []),
            "score": float(rank),
            "match_count": count,
        }
        for document, rank, count, _ in selected
    ]
    next_cursor = None
    if more:
        last = selected[-1]
        value = (
            last[3].isoformat()
            if isinstance(last[3], (datetime, date))
            else str(last[3])
            if isinstance(last[3], Decimal)
            else last[3]
        )
        next_cursor = encode_cursor(
            {"id": str(last[0].id), "value": value, "binding": fingerprint, "time": time.time()},
            secret,
        )
    from app.content.operations import record_search

    record_search(session, actor, params, round((time.perf_counter() - started) * 1000))
    return {
        "items": items,
        "total": total,
        "next_cursor": next_cursor,
        "page_size": params.page_size,
        "algorithm_version": algorithm,
    }
