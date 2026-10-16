"""Compatibility adapter for Task 005; all retrieval uses the unified SQL engine."""

import json
import re
from difflib import SequenceMatcher

from pydantic import Field

from app.registry.document_search import ExportParameters, SearchParameters


class ContentSearchParameters(SearchParameters):
    text: str = Field(min_length=1, max_length=255)
    fuzzy: bool = True


class ContentExportParameters(ExportParameters):
    text: str = Field(min_length=1, max_length=255)
    fuzzy: bool = True


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


def unified_parameters(params):
    from app.api.errors import fail
    from app.content.contracts import SearchInput

    if not params.text.strip():
        fail(422, "content_query_empty", "Введите поисковый запрос")
    try:
        return SearchInput(
            query=params.text,
            scope="all",
            version_scope="all",
            registry_ids=[params.registry_id] if params.registry_id else [],
            document_type_ids=[params.document_type_id] if params.document_type_id else [],
            date_from=params.date_from,
            date_to=params.date_to,
            status=params.status,
            card_state=params.card_state,
            reg_number=params.reg_number,
            filters=json.loads(params.filters or "{}"),
            fuzzy=params.fuzzy,
            sort="relevance",
            mode=params.mode,
            page_size=params.page_size,
        )
    except (ValueError, RecursionError):
        fail(422, "invalid_filter", "Неверные условия поиска")


def search_response(session, actor, params):
    from app.content.engine import execute_search

    result = execute_search(
        session,
        actor,
        unified_parameters(params),
        secret="legacy-no-public-cursor",
        offset=(params.page - 1) * params.page_size,
        legacy=params,
    )
    return {**result, "page": params.page}


def matching_ids(session, actor, params):
    from app.content.engine import candidate_query, configure_search
    from app.registry.models import Document

    configure_search(session)
    query, _, _ = candidate_query(session, actor, unified_parameters(params), legacy=params)
    return query.order_by(None).with_only_columns(Document.id)
