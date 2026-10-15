from datetime import date, timedelta
from uuid import UUID

from app.processes.models import Process, ProcessVersion
from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task007_processes import setup_process


def test_process_catalog_pages_beyond_legacy_limit_and_searches_visible_snapshot(client, factory):
    sample, _, original, _ = setup_process(client, factory)
    with factory.begin() as db:
        for index in range(1001):
            process = Process(
                registry_id=sample["registry"],
                code=f"catalog-{index}",
                metadata_data={"name": f"PRIVATE-DRAFT-{index}"},
                created_by=sample["admin"].id,
            )
            db.add(process)
            db.flush()
            db.add(
                ProcessVersion(
                    process_id=process.id,
                    version=1,
                    state="published",
                    effective_from=date.today(),
                    metadata_snapshot={
                        "name": f"Published catalog {index}",
                        "code": process.code,
                        "registry_id": str(sample["registry"]),
                    },
                    created_by=sample["admin"].id,
                )
            )
    sign_in_as(client, factory, sample["viewer"])
    first = client.get("/api/v1/processes/catalog?page_size=100").json()
    last = client.get("/api/v1/processes/catalog?page=11&page_size=100").json()
    assert first["total"] == last["total"] == 1001
    assert len(first["items"]) == 100 and len(last["items"]) == 1
    assert not ({row["id"] for row in first["items"]} & {row["id"] for row in last["items"]})
    assert all(row["id"] != original["id"] for row in first["items"])
    assert "PRIVATE-DRAFT" not in str(first)
    assert client.get("/api/v1/processes/catalog?q=PRIVATE-DRAFT").json()["total"] == 0
    assert client.get("/api/v1/processes/catalog?q=Published%20catalog%201000").json()["total"] == 1
    assert client.get("/api/v1/processes/catalog?page_size=101").status_code == 422
    assert isinstance(client.get("/api/v1/processes").json(), list)
    with factory.begin() as db:
        db.get(RegistryAccess, (sample["registry"], sample["viewer"].id)).permissions = []
    assert client.get("/api/v1/processes/catalog").json()["total"] == 0


def test_process_version_catalog_filters_drafts_future_and_revocation_before_paging(
    client, factory
):
    sample, _, process, _ = setup_process(client, factory)
    with factory.begin() as db:
        for number, effective in ((2, date.today()), (3, date.today() + timedelta(days=5))):
            db.add(
                ProcessVersion(
                    process_id=UUID(process["id"]),
                    version=number,
                    state="published",
                    effective_from=effective,
                    metadata_snapshot={"name": "Published only"},
                    created_by=sample["admin"].id,
                )
            )
    path = f"/api/v1/processes/{process['id']}/version-catalog"
    admin = client.get(path + "?page_size=1").json()
    assert admin["total"] == 3 and admin["items"][0]["version"] == 3
    sign_in_as(client, factory, sample["viewer"])
    reader = client.get(path + "?page_size=1").json()
    assert reader["total"] == 1 and reader["items"][0]["version"] == 2
    assert client.get(path + "?page=2&page_size=1").json()["items"] == []
    assert isinstance(client.get(f"/api/v1/processes/{process['id']}/versions").json(), list)
    with factory.begin() as db:
        db.get(RegistryAccess, (sample["registry"], sample["viewer"].id)).permissions = []
    assert client.get(path).status_code == 404


def test_content_catalog_pages_after_file_field_visibility(client, factory):
    import base64

    from app.content.worker import process_extractions
    from tests.test_task005_content import synthetic_docx
    from tests.test_task007_sources_acl import source_process

    setup = source_process(client, factory)
    sample, source = setup["sample"], setup["source"]
    headers = sign_in_as(client, factory, sample["editor"])
    card = client.get(f"/api/v1/documents/{source['id']}").json()
    files = [
        {
            "field_key": key,
            "name": f"{key}.docx",
            "content_base64": base64.b64encode(
                synthetic_docx("Synthetic pagination original")
            ).decode(),
        }
        for key in ("private_file", "public_file")
    ]
    uploaded = client.put(
        f"/api/v1/documents/{source['id']}",
        headers=headers,
        json={"revision": card["revision"], "values": {}, "files": files},
    )
    assert uploaded.status_code == 200, uploaded.text
    existing_ids = {file["id"] for file in card["files"]}
    for file in uploaded.json()["files"]:
        if file["id"] not in existing_ids:
            assert (
                client.post(
                    f"/api/v1/files/{file['id']}/extract", headers=headers, json={}
                ).status_code
                == 202
            )
    assert (
        sum(process_extractions(factory, client.app.state.settings.storage_root) for _ in range(2))
        == 2
    )
    sign_in_as(client, factory, sample["viewer"])
    path = f"/api/v1/documents/{source['id']}/content"
    first = client.get(path + "?page_size=1").json()
    second = client.get(path + "?page=2&page_size=1").json()
    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert "private_file.docx" not in str(first) + str(second)
    exact_id = second["items"][0]["file_id"]
    exact = client.get(path, params={"file_id": exact_id, "page_size": 1}).json()
    assert exact["total"] == 1 and exact["items"][0]["file_id"] == exact_id
    private_id = next(
        file["id"] for file in uploaded.json()["files"] if file.get("field_key") == "private_file"
    )
    assert client.get(path, params={"file_id": private_id}).status_code == 404
    assert client.get(path + "?page=3&page_size=1").json()["items"] == []
    assert client.get(path + "?page_size=101").status_code == 422
