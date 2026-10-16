from uuid import UUID

from app.registry.models import RegistryAccess
from tests.test_task010_service_acl import service_sample


def test_service_search_chunks_exact_source_neighbors_and_revocation(client, factory):
    setup, account, bearer = service_sample(client, factory)
    response = client.post(
        "/api/service/v1/search", headers=bearer, json={"query": "", "scope": "all"}
    )
    assert response.status_code == 200
    query = setup["extraction"]["blocks"][0]["text"].split()[0]
    result = client.post(
        "/api/service/v1/retrieval", headers=bearer, json={"query": query, "version_scope": "all"}
    )
    assert result.status_code == 200, result.text
    chunks = result.json()["items"]
    assert chunks
    chunk = next(c for c in chunks if c["source_type"] == "file")
    assert chunk["citation"]["file_id"] == setup["extraction"]["file_id"]
    assert chunk["block_ids"] and chunk["ranges"] and len(chunk["checksum"]) == 64
    exact = client.get(f"/api/service/v1/chunks/{chunk['chunk_id']}", headers=bearer)
    assert exact.status_code == 200 and exact.json()["text"] == chunk["text"]
    neighbors = client.get(f"/api/service/v1/chunks/{chunk['chunk_id']}/neighbors", headers=bearer)
    assert neighbors.status_code == 200
    assert chunk["chunk_id"] in [c["chunk_id"] for c in neighbors.json()["items"]]
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], UUID(account["user_id"]))
        ).permissions = ["read"]
    assert (
        client.get(f"/api/service/v1/chunks/{chunk['chunk_id']}", headers=bearer).status_code == 404
    )
    assert (
        client.get(
            f"/api/service/v1/chunks/{chunk['chunk_id']}/neighbors", headers=bearer
        ).status_code
        == 404
    )


def test_documents_only_service_search_never_gains_content_operation(client, factory):
    setup, _, bearer = service_sample(client, factory, operations=["documents"])
    result = client.post("/api/service/v1/search", headers=bearer, json={"query": ""})
    assert result.status_code == 200
    assert (
        client.post(
            "/api/service/v1/retrieval", headers=bearer, json={"query": "источник"}
        ).status_code
        == 403
    )


def test_chunks_preserve_ranges_pages_tables_and_repeatable_ids():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.content.chunks import split_blocks

    extraction = uuid4()
    blocks = [
        SimpleNamespace(
            id=uuid4(),
            extraction_id=extraction,
            page=page,
            kind=kind,
            ordinal=i,
            text=text,
            anchor=f"p{page}:{i}",
        )
        for i, (page, kind, text) in enumerate(
            [
                (1, "heading", "Название"),
                (1, "paragraph", "Содержание " * 200),
                (1, "paragraph", "Длинный блок " * 1500),
                (2, "table", "Ячейка\tЗначение"),
                (2, "table_row", "Ячейка\tЗначение"),
                (2, "paragraph", "После таблицы"),
            ]
        )
    ]
    chunks = split_blocks(blocks)
    assert [c["id"] for c in chunks] == [c["id"] for c in split_blocks(blocks)]
    assert len(chunks) > 5
    for chunk in chunks:
        assert len(chunk["text"]) <= 4000
        assert (
            len(
                {
                    next(b.page for b in blocks if str(b.id) == r["block_id"])
                    for r in chunk["ranges"]
                }
            )
            == 1
        )
        assert chunk["text"] == "\n".join(
            next(b.text for b in blocks if str(b.id) == r["block_id"])[r["start"] : r["end"]]
            for r in chunk["ranges"]
        )
    assert not any(
        str(blocks[3].id) in c["block_ids"] and str(blocks[5].id) in c["block_ids"] for c in chunks
    )
