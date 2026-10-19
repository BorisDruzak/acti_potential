"""A predetermined, reproducible relevance set using real PDF/DOCX extraction."""

import base64

from app.content.worker import process_extractions
from app.jobs.worker import process_batch
from tests.test_content_security import sign_in_as
from tests.test_search_engine_v2 import corpus, search
from tests.test_task005_parsers import docx, pdf


def test_gold_recall_and_mrr_at_ten_with_real_files(client, factory, record_property):
    sample = corpus(factory)
    editor = sign_in_as(client, factory, sample["editor"])
    table = "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Бюджет озеленения</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    body = "<w:p><w:r><w:t>Порядок согласования муниципальных договоров</w:t></w:r></w:p>" + table
    response = client.put(
        f"/api/v1/documents/{sample['document']}",
        headers=editor,
        json={
            "revision": 1,
            "values": {},
            "files": [
                {
                    "name": "synthetic.pdf",
                    "content_base64": base64.b64encode(
                        pdf("Beginning. Unique middle digital phrase. Ending.")
                    ).decode(),
                },
                {"name": "synthetic.docx", "content_base64": base64.b64encode(docx(body)).decode()},
            ],
        },
    )
    assert response.status_code == 200
    process_batch(factory, 100)
    for _ in range(2):
        process_extractions(factory, client.app.state.settings.storage_root)
    viewer = sign_in_as(client, factory, sample["viewer"])
    source, other = str(sample["document"]), str(sample["other"])
    gold = [
        ("12 р 2026", {source}),
        ("содержание территорий", {source, other}),
        ("территория", {source, other}),
        ("содержанеи", {source, other}),
        ("ЖКХ", {source}),
        ('"middle digital phrase"', {source}),
        ("согласование муниципального договора", {source}),
        ("бюджет озеленения", {source}),
    ]
    recall, reciprocal = [], []
    for query, expected in gold:
        result = search(client, viewer, query=query, page_size=10)
        received = [item["document"]["id"] for item in result["items"]]
        recall.append(len(expected & set(received)) / len(expected))
        reciprocal.append(
            next((1 / (i + 1) for i, identity in enumerate(received) if identity in expected), 0)
        )
    record_property("gold_queries", len(gold))
    record_property("Recall_at_10", sum(recall) / len(recall))
    record_property("MRR_at_10", sum(reciprocal) / len(reciprocal))
    assert sum(recall) / len(recall) == 1.0
    assert sum(reciprocal) / len(reciprocal) == 1.0
    assert search(client, viewer, query="Скрытаяулика")["total"] == 0
    assert search(client, viewer, query="99 р 2026")["total"] == 0

    # A same-name upload is an independent attachment, never an inferred revision.
    editor = sign_in_as(client, factory, sample["editor"])
    current = client.get(f"/api/v1/documents/{source}").json()
    response = client.put(
        f"/api/v1/documents/{source}",
        headers=editor,
        json={
            "revision": current["revision"],
            "values": {},
            "files": [
                {
                    "name": "synthetic.pdf",
                    "content_base64": base64.b64encode(
                        docx("<w:p><w:r><w:t>Новая редакция</w:t></w:r></w:p>")
                    ).decode(),
                }
            ],
        },
    )
    assert response.status_code == 200
    process_batch(factory, 100)
    process_extractions(factory, client.app.state.settings.storage_root)
    viewer = sign_in_as(client, factory, sample["viewer"])
    assert (
        search(client, viewer, query='"middle digital phrase"', version_scope="current")["total"]
        == 1
    )
    assert (
        search(client, viewer, query='"middle digital phrase"', version_scope="all")["total"] == 1
    )
