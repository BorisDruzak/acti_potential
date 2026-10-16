from io import BytesIO
from zipfile import ZipFile


def synthetic_docx(text="Согласование муниципального договора"):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return output.getvalue()


def test_content_version_queue_is_idempotent(client, factory):
    import base64
    from uuid import UUID

    from sqlalchemy import select

    from app.content.models import ContentExtraction
    from app.jobs.worker import process_batch
    from tests.test_content_security import actors_and_document, sign_in_as

    sample = actors_and_document(
        factory, [{"key": "title", "label": "Title", "type": "text", "system": "title"}]
    )
    headers = sign_in_as(client, factory, sample["editor"])
    card = client.get(f"/api/v1/documents/{sample['document']}").json()
    attached = client.put(
        f"/api/v1/documents/{sample['document']}",
        headers=headers,
        json={
            "revision": card["revision"],
            "values": {},
            "files": [
                {
                    "name": "synthetic.docx",
                    "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "content_base64": base64.b64encode(synthetic_docx()).decode(),
                }
            ],
        },
    )
    assert attached.status_code == 200, attached.text
    file_id = attached.json()["files"][0]["id"]
    first = client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={})
    again = client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={})
    assert first.status_code == 202 and again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    process_batch(factory, 100)
    with factory() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(ContentExtraction).where(ContentExtraction.file_id == UUID(file_id))
                    )
                )
            )
            == 1
        )


def test_content_search_uses_current_metadata_revision_and_acl(client, factory):
    from app.content.indexing import index_metadata
    from tests.test_content_security import actors_and_document, sign_in_as

    sample = actors_and_document(
        factory,
        [
            {
                "key": "public",
                "label": "Public",
                "type": "text",
                "filterable": True,
                "exportable": True,
            },
            {"key": "secret", "label": "Secret", "type": "text", "read_roles": ["admin"]},
        ],
    )
    from app.registry.models import Document

    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.custom_data = {
            "public": "Согласование муниципального договора",
            "secret": "Засекреченный поисковый текст",
        }
        index_metadata(db, document.id)
    sign_in_as(client, factory, sample["viewer"])
    visible = client.get("/api/v1/content/search", params={"text": "муниципальный договор"})
    assert visible.status_code == 200, visible.text
    assert visible.json()["total"] == 1
    assert (
        client.get("/api/v1/content/search", params={"text": "Засекреченный"}).json()["total"] == 0
    )
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.custom_data = {"public": "Совершенно другой текст"}
        document.revision += 1
    assert (
        client.get("/api/v1/content/search", params={"text": "муниципальный"}).json()["total"] == 0
    )


def upload_sample(
    client, factory, *, data=None, name="synthetic.docx", fields=None, field_key=None
):
    import base64

    from tests.test_content_security import actors_and_document, sign_in_as

    sample = actors_and_document(
        factory,
        fields or [{"key": "public", "label": "Public", "type": "text", "exportable": True}],
    )
    headers = sign_in_as(client, factory, sample["editor"])
    files = [
        {
            "name": name,
            "mime": "application/octet-stream",
            "content_base64": base64.b64encode(data or synthetic_docx()).decode(),
        }
    ]
    if field_key:
        files[0]["field_key"] = field_key
    response = client.put(
        f"/api/v1/documents/{sample['document']}",
        headers=headers,
        json={"revision": 1, "values": {}, "files": files},
    )
    assert response.status_code == 200, response.text
    file_id = response.json()["files"][0]["id"]
    queued = client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={})
    assert queued.status_code == 202, queued.text
    return sample, headers, file_id, queued.json()["id"]


def test_real_docx_table_extraction_search_fuzzy_and_block_idempotency(client, factory):
    from app.content.worker import process_extractions
    from tests.test_task005_parsers import docx

    data = docx(
        "<w:p><w:r><w:t>Согласование муниципального договора</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Бюджет</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    sample, headers, file_id, extraction_id = upload_sample(client, factory, data=data)
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    endpoint = f"/api/v1/documents/{sample['document']}/content"
    content = client.get(endpoint)
    assert content.status_code == 200, content.text
    extracted = content.json()["items"][0]
    assert extracted["state"] == "completed" and extracted["block_count"] == 3
    assert [block["kind"] for block in extracted["blocks"]] == ["paragraph", "table", "table_row"]
    assert extracted["blocks"][2]["parent_id"] == extracted["blocks"][1]["id"]
    for query in ["муниципальный договор", "муниципальнго"]:
        result = client.get("/api/v1/content/search", params={"text": query, "fuzzy": "true"})
        assert result.status_code == 200 and result.json()["total"] == 1, result.text
        match = result.json()["items"][0]["matches"][0]
        assert match["file_id"] == file_id and match["extraction_id"] == extraction_id
        assert match["block_ordinal"] == 0 and match["version"] == 1
    assert (
        client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={}).json()["id"]
        == extraction_id
    )
    assert process_extractions(factory, client.app.state.settings.storage_root) == 0
    assert client.get(endpoint).json()["items"][0]["blocks"] == extracted["blocks"]
    page = client.get(
        f"/api/v1/content/extractions/{extraction_id}/blocks?page_size=1&page=3"
    ).json()
    assert page["total"] == 3 and page["items"][0]["kind"] == "table_row"


def test_file_condition_acl_and_row_acl_apply_before_search_snippets_and_blocks(client, factory):

    from app.content.worker import process_extractions
    from app.registry.models import Document, DocumentACL
    from tests.test_content_security import sign_in_as

    fields = [
        {"key": "gate", "label": "Gate", "type": "boolean"},
        {
            "key": "original",
            "label": "Original",
            "type": "file",
            "visible_when": {"field": "gate", "op": "eq", "value": True},
        },
    ]
    sample = None
    # Supply a visible initial file field through the fixture, then change only values.
    from tests.test_content_security import actors_and_document

    sample = actors_and_document(factory, fields)
    with factory.begin() as db:
        db.get(Document, sample["document"]).custom_data = {"gate": True}
    headers = sign_in_as(client, factory, sample["editor"])
    import base64

    attached = client.put(
        f"/api/v1/documents/{sample['document']}",
        headers=headers,
        json={
            "revision": 1,
            "values": {},
            "files": [
                {
                    "name": "visible.docx",
                    "field_key": "original",
                    "content_base64": base64.b64encode(synthetic_docx()).decode(),
                }
            ],
        },
    )
    assert attached.status_code == 200, attached.text
    file_id = attached.json()["files"][0]["id"]
    extraction_id = client.post(
        f"/api/v1/files/{file_id}/extract", headers=headers, json={}
    ).json()["id"]
    process_extractions(factory, client.app.state.settings.storage_root)
    sign_in_as(client, factory, sample["viewer"])
    assert (
        client.get("/api/v1/content/search", params={"text": "муниципального"}).json()["total"] == 1
    )
    with factory.begin() as db:
        doc = db.get(Document, sample["document"])
        doc.custom_data = {"gate": False}
        doc.revision += 1
    assert (
        client.get("/api/v1/content/search", params={"text": "муниципального"}).json()["total"] == 0
    )
    assert client.get(f"/api/v1/documents/{sample['document']}/content").json()["items"] == []
    assert client.get(f"/api/v1/content/extractions/{extraction_id}/blocks").status_code == 404
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=sample["document"], user_id=sample["editor"].id, permissions=["read"]
            )
        )
    assert client.get(f"/api/v1/documents/{sample['document']}/content").status_code == 404


def test_equal_rank_pagination_and_text_filtered_export(client, factory):
    from app.content.indexing import index_metadata
    from app.registry.models import Document
    from tests.test_content_security import actors_and_document, sign_in_as

    sample = actors_and_document(
        factory, [{"key": "public", "label": "Public", "type": "text", "exportable": True}]
    )
    with factory.begin() as db:
        original = db.get(Document, sample["document"])
        original.custom_data = {"public": "Муниципальный договор"}
        duplicate = Document(
            card_state="published",
            publication_targets=["internal", "shared"],
            registry_id=original.registry_id,
            document_type_id=original.document_type_id,
            schema_version_id=original.schema_version_id,
            created_by=original.created_by,
            custom_data={"public": "Муниципальный договор"},
        )
        excluded = Document(
            card_state="published",
            publication_targets=["internal", "shared"],
            registry_id=original.registry_id,
            document_type_id=original.document_type_id,
            schema_version_id=original.schema_version_id,
            created_by=original.created_by,
            custom_data={"public": "Не соответствующая поиску запись"},
        )
        db.add_all([duplicate, excluded])
        db.flush()
        for document in (original, duplicate, excluded):
            index_metadata(db, document.id)
    sign_in_as(client, factory, sample["viewer"])
    one = client.get(
        "/api/v1/content/search",
        params={"text": "Муниципальный договор", "page_size": 1, "page": 1},
    ).json()
    two = client.get(
        "/api/v1/content/search",
        params={"text": "Муниципальный договор", "page_size": 1, "page": 2},
    ).json()
    assert one["total"] == two["total"] == 2
    assert one["items"][0]["document"]["id"] != two["items"][0]["document"]["id"]
    exported = client.get(
        "/api/v1/content/search/export", params={"text": "Муниципальный договор", "format": "csv"}
    )
    assert exported.status_code == 200, exported.text
    assert "Не соответствующая" not in exported.content.decode("utf-8-sig")
    assert exported.content.decode("utf-8-sig").count("Муниципальный договор") == 2


def test_worker_lease_recovery_retry_limit_and_identity_mismatch(client, factory):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from app.content.models import ContentExtraction
    from app.content.parsers import empty_result
    from app.content.worker import process_extractions

    sample, headers, file_id, extraction_id = upload_sample(client, factory)
    with factory.begin() as db:
        row = db.get(ContentExtraction, UUID(extraction_id))
        row.state, row.attempts, row.lease_until = (
            "running",
            1,
            datetime.now(UTC) - timedelta(seconds=1),
        )

    def timeout(*args, **kwargs):
        return empty_result(args[2], args[3], "failed", "timeout")

    assert process_extractions(factory, client.app.state.settings.storage_root, parser=timeout) == 1
    with factory.begin() as db:
        row = db.get(ContentExtraction, UUID(extraction_id))
        assert row.state == "pending" and row.attempts == 2
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert process_extractions(factory, client.app.state.settings.storage_root, parser=timeout) == 1
    denied = client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={})
    assert denied.status_code == 409
    with factory() as db:
        row = db.get(ContentExtraction, UUID(extraction_id))
        assert row.state == "failed" and row.attempts == 3 and row.error_code == "timeout"


def test_parser_version_mismatch_is_honest_and_explicit_retry_preserves_fingerprint(
    client, factory
):
    from uuid import UUID

    from app.content.models import ContentExtraction
    from app.content.parsers import empty_result
    from app.content.worker import process_extractions

    sample, headers, file_id, extraction_id = upload_sample(client, factory)

    def wrong_version(*args, **kwargs):
        result = empty_result(args[2], args[3], "completed")
        result["parser_version"] = "not-the-queued-parser"
        return result

    process_extractions(factory, client.app.state.settings.storage_root, parser=wrong_version)
    with factory() as db:
        failed = db.get(ContentExtraction, UUID(extraction_id))
        assert failed.state == "failed" and failed.error_code == "parser_version_changed"
    queued = client.post(f"/api/v1/files/{file_id}/extract", headers=headers, json={})
    assert queued.status_code == 202 and queued.json()["id"] == extraction_id
    process_extractions(factory, client.app.state.settings.storage_root)
    content = client.get(f"/api/v1/documents/{sample['document']}/content").json()["items"][0]
    assert content["state"] == "completed" and content["attempts"] == 2


def test_operator_errors_are_current_acl_scoped_and_do_not_expose_storage(client, factory):
    from app.content.worker import process_extractions
    from app.registry.models import RegistryAccess
    from tests.test_content_security import sign_in_as

    sample, headers, file_id, extraction_id = upload_sample(
        client, factory, data=b"%PDF-corrupt", name="broken.pdf"
    )
    process_extractions(factory, client.app.state.settings.storage_root)
    content = client.get(f"/api/v1/documents/{sample['document']}/content").json()["items"][0]
    assert content["state"] == "failed" and content["blocks"] == []
    assert client.get("/api/v1/admin/content/errors").status_code == 403
    sign_in_as(client, factory, sample["admin"])
    denied = client.get("/api/v1/admin/content/errors")
    assert denied.status_code == 200 and denied.json()["total"] == 0
    with factory.begin() as db:
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "download"],
            )
        )
    permitted = client.get("/api/v1/admin/content/errors?page_size=1")
    assert permitted.status_code == 200 and permitted.json()["total"] == 1
    row = permitted.json()["items"][0]
    assert row["id"] == extraction_id and row["name"] == "broken.pdf"
    assert "storage_key" not in row and "full_text" not in row and "traceback" not in str(row)
