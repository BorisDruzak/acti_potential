"""Bound SQL work per authorized batch without changing content visibility."""

from uuid import uuid4

from sqlalchemy import event

from app.content.indexing import index_metadata
from app.content.search import ContentSearchParameters, search_response
from app.identity.policy import Actor
from app.registry.models import Document
from tests.test_content_security import actors_and_document


def indexed_corpus(factory, count=80):
    sample = actors_and_document(
        factory,
        [
            {"key": "public", "label": "Public", "type": "text"},
            {
                "key": "secret",
                "label": "SYNTHETIC-HIDDEN-LABEL",
                "type": "text",
                "read_roles": ["admin"],
            },
        ],
    )
    with factory.begin() as db:
        original = db.get(Document, sample["document"])
        identities = []
        for _ in range(count):
            row = Document(
                id=uuid4(),
                registry_id=original.registry_id,
                document_type_id=original.document_type_id,
                schema_version_id=original.schema_version_id,
                created_by=sample["editor"].id,
                custom_data={
                    "public": "Синтетический пилот согласования договора",
                    "secret": "Синтетический пилот скрытого значения",
                },
            )
            db.add(row)
            db.flush()
            index_metadata(db, row.id)
            identities.append(row.id)
    return sample, identities


def measured_search(factory, actor, params):
    statements = []
    engine = factory.kw["bind"]

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with factory() as db:
            result = search_response(db, actor, params)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, len(statements)


def test_metadata_search_batches_sql_instead_of_holding_pool_with_per_document_queries(
    factory, record_property
):
    sample, identities = indexed_corpus(factory)
    viewer = Actor(sample["viewer"].id, "Synthetic viewer", frozenset({"viewer"}))
    result, queries = measured_search(
        factory, viewer, ContentSearchParameters(text="пилот", fuzzy=False, page_size=5)
    )
    record_property("select_count", queries)
    assert result["total"] == len(identities) and len(result["items"]) == 5
    assert all(
        match["field_key"] == "public" for item in result["items"] for match in item["matches"]
    )
    assert queries <= 40, (
        f"Expected batch-bounded SQL, observed {queries} SELECT statements for 80 matching cards"
    )


def test_batched_search_keeps_revision_and_dynamic_document_acl_before_count_and_pagination(
    factory,
):
    from app.registry.models import DocumentACL

    sample, identities = indexed_corpus(factory, count=8)
    viewer = Actor(sample["viewer"].id, "Synthetic viewer", frozenset({"viewer"}))
    with factory.begin() as db:
        changed = db.get(Document, identities[0])
        changed.custom_data = {"public": "Другой текст"}
        changed.revision += 1
        db.add(
            DocumentACL(
                document_id=identities[1], user_id=sample["editor"].id, permissions=["read"]
            )
        )
    page1, _ = measured_search(
        factory, viewer, ContentSearchParameters(text="пилот", fuzzy=False, page_size=3)
    )
    page2, _ = measured_search(
        factory, viewer, ContentSearchParameters(text="пилот", fuzzy=False, page_size=3, page=2)
    )
    assert page1["total"] == page2["total"] == 6
    received = {item["document"]["id"] for page in (page1, page2) for item in page["items"]}
    assert received == set(identities[2:])


def test_file_ranking_takes_five_per_document_and_rechecks_download_acl(factory):
    from app.content.models import ContentExtraction, DocumentBlock
    from app.registry.models import DocumentACL, DocumentFile, DocumentVersion

    sample, identities = indexed_corpus(factory, count=2)
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
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                sha256="0" * 64,
                storage_key=f"synthetic/{uuid4()}",
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
                parser_version="synthetic-test",
                state="completed",
            )
            db.add(extraction)
            db.flush()
            db.add_all(
                DocumentBlock(
                    id=uuid4(),
                    extraction_id=extraction.id,
                    document_id=identity,
                    kind="paragraph",
                    ordinal=ordinal,
                    anchor=f"synthetic:{ordinal}",
                    text="Фрагмент согласования договора",
                )
                for ordinal in range(7)
            )
    params = ContentSearchParameters(text="фрагмент", fuzzy=False, page_size=5)
    result, queries = measured_search(factory, sample["viewer"], params)
    assert result["total"] == 2 and queries <= 40
    assert all(len(item["matches"]) == 5 for item in result["items"])
    assert all(match["source"] == "file" for item in result["items"] for match in item["matches"])
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=identities[0], user_id=sample["viewer"].id, permissions=["read"]
            )
        )
    result, _ = measured_search(factory, sample["viewer"], params)
    assert result["total"] == 1 and result["items"][0]["document"]["id"] == identities[1]
