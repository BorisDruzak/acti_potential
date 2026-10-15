from uuid import UUID

import pytest
from sqlalchemy import text

from app.knowledge.models import KnowledgeUsage
from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task008_knowledge_acl import knowledge_sample, publish_case, publish_template


def grant_curator(factory, setup):
    with factory.begin() as db:
        row = db.get(RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id))
        row.permissions = [*row.permissions, "curate_knowledge"]


def test_curation_requires_explicit_grant_and_records_append_only_reasoned_history(client, factory):
    setup, headers = knowledge_sample(client, factory)
    case = publish_case(client, headers, setup)
    endpoint = f"/api/v1/knowledge/cases/{case['id']}/curate"
    assert not case["confirmed"] and not case["recommended"]
    assert (
        client.post(
            endpoint, headers=headers, json={"action": "confirm", "reason": "Synthetic checked"}
        ).status_code
        == 403
    )
    grant_curator(factory, setup)
    assert (
        client.post(
            endpoint,
            headers=headers,
            json={"action": "recommend", "reason": "Synthetic recommended"},
        ).status_code
        == 409
    )
    for action in ["confirm", "recommend", "unrecommend", "recommend"]:
        response = client.post(
            endpoint, headers=headers, json={"action": action, "reason": "Synthetic " + action}
        )
        assert response.status_code == 200, response.text
    assert response.json()["confirmed"] and response.json()["recommended"]
    history = client.get(f"/api/v1/knowledge/cases/{case['id']}/history").json()["items"]
    assert [row["action"] for row in history] == [
        "confirm",
        "recommend",
        "unrecommend",
        "recommend",
    ]
    assert all(row["reason"] and row["actor_name"] for row in history)
    with pytest.raises(Exception, match="immutable"):
        with factory.begin() as db:
            db.execute(
                text("UPDATE knowledge_curations SET reason='rewritten' WHERE case_id=:id"),
                {"id": UUID(case["id"])},
            )


def test_curation_requires_current_bases_but_unrecommend_survives_source_revocation(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    case = publish_case(client, headers, setup)
    grant_curator(factory, setup)
    endpoint = f"/api/v1/knowledge/cases/{case['id']}/curate"
    with factory.begin() as db:
        source = db.get(RegistryAccess, (setup["sample"]["registry"], setup["sample"]["editor"].id))
        saved = source.permissions
        source.permissions = []
    assert (
        client.post(
            endpoint, headers=headers, json={"action": "confirm", "reason": "Cannot see source"}
        ).status_code
        == 404
    )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["editor"].id)
        ).permissions = saved
    for action in ["confirm", "recommend"]:
        assert (
            client.post(
                endpoint, headers=headers, json={"action": action, "reason": "Synthetic checked"}
            ).status_code
            == 200
        )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["editor"].id)
        ).permissions = []
    response = client.post(
        endpoint,
        headers=headers,
        json={"action": "unrecommend", "reason": "Source is no longer available"},
    )
    assert response.status_code == 200 and response.json()["recommended"] is False


def test_published_case_content_and_links_are_immutable_but_access_and_lifecycle_can_change(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    case = publish_case(client, headers, setup)
    for statement in [
        "UPDATE practice_cases SET data='{}' WHERE id=:id",
        "UPDATE practice_cases SET text='rewritten' WHERE id=:id",
        "DELETE FROM practice_links WHERE case_id=:id",
    ]:
        with pytest.raises(Exception, match="immutable"):
            with factory.begin() as db:
                db.execute(text(statement), {"id": UUID(case["id"])})
    archived = client.patch(
        f"/api/v1/knowledge/cases/{case['id']}/access",
        headers=headers,
        json={"visibility": "registry", "lifecycle": "archived", "reason": "Synthetic retirement"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["description"] == case["description"]
    assert client.get("/api/v1/knowledge/cases").json()["total"] == 0
    assert client.get(f"/api/v1/knowledge/cases/{case['id']}").status_code == 200


def test_template_draft_revision_and_published_version_immutability(client, factory):
    setup, headers = knowledge_sample(client, factory)
    template, frozen = publish_template(client, headers, setup)
    endpoint = f"/api/v1/knowledge/template-versions/{frozen['id']}"
    assert (
        client.post(
            endpoint + "/publish", headers=headers, json={"revision": frozen["revision"]}
        ).status_code
        == 409
    )
    with pytest.raises(Exception, match="immutable"):
        with factory.begin() as db:
            db.execute(
                text("UPDATE knowledge_template_versions SET data='{}' WHERE id=:id"),
                {"id": UUID(frozen["id"])},
            )
    second = client.post(
        f"/api/v1/knowledge/templates/{template['id']}/versions", headers=headers, json={}
    ).json()
    assert second["version"] == 2 and second["state"] == "draft"
    body = {
        key: second[key]
        for key in [
            "name",
            "purpose",
            "category",
            "owner_id",
            "visibility",
            "document_type_id",
            "form_schema_id",
            "process_version_id",
            "step_key",
            "characteristics",
            "revision",
        ]
        if key in second
    }
    body["original"] = {
        key: second["original"][key] for key in ["document_id", "version_id", "file_id"]
    }
    body["name"] = "Synthetic v2 renamed"
    saved = client.put(
        f"/api/v1/knowledge/template-versions/{second['id']}", headers=headers, json=body
    )
    assert saved.status_code == 200, saved.text
    assert (
        client.put(
            f"/api/v1/knowledge/template-versions/{second['id']}", headers=headers, json=body
        ).status_code
        == 409
    )
    assert client.get(endpoint).json()["name"] == frozen["name"]


def test_usage_metrics_are_acl_scoped_aggregate_buckets_without_queries_or_actor_fields(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    template, version = publish_template(client, headers, setup)
    case = publish_case(client, headers, setup)
    for action, identity in [
        ("template_open", {"template_version_id": version["id"]}),
        ("case_open", {"case_id": case["id"]}),
    ]:
        assert (
            client.post(
                "/api/v1/knowledge/usage", headers=headers, json={"action": action, **identity}
            ).status_code
            == 204
        )
    assert (
        client.get(f"/api/v1/knowledge/template-versions/{version['id']}/download").status_code
        == 200
    )
    assert client.get(f"/api/v1/knowledge/cases/{case['id']}/basis").status_code == 200
    assert client.get("/api/v1/knowledge/templates", params={"text": "ALPHA"}).json()["total"] == 1
    endpoint = f"/api/v1/knowledge/metrics?registry_id={setup['process_registry']}"
    assert client.get(endpoint).status_code == 403
    grant_curator(factory, setup)
    result = client.get(endpoint)
    assert result.status_code == 200, result.text
    counts = {row["action"]: row["count"] for row in result.json()["items"]}
    assert all(
        counts.get(action, 0) >= 1
        for action in ["template_open", "case_open", "template_download", "basis_follow", "search"]
    )
    assert set(KnowledgeUsage.__table__.columns.keys()) == {
        "id",
        "day",
        "registry_id",
        "object_id",
        "action",
        "count",
    }


def test_universal_process_template_binding_pins_version_without_rewriting_old_process(
    client, factory
):
    from app.processes import service as processes
    from app.processes.models import ProcessVersion

    setup, editor_headers = knowledge_sample(client, factory)
    _, template = publish_template(client, editor_headers, setup)
    admin_headers = sign_in_as(client, factory, setup["sample"]["admin"])
    before = client.get(f"/api/v1/process-versions/{setup['version']['id']}").json()
    draft = client.post(
        f"/api/v1/processes/{setup['process']['id']}/versions", headers=admin_headers, json={}
    ).json()
    with factory.begin() as db:
        steps, transitions = processes.graph(db, db.get(ProcessVersion, UUID(draft["id"])))
    steps[0]["template_versions"] = [template["id"]]
    for edge in transitions:
        edge.pop("ordinal", None)
    saved = client.put(
        f"/api/v1/process-versions/{draft['id']}",
        headers=admin_headers,
        json={
            "revision": draft["revision"],
            "effective_from": "2026-01-01",
            "steps": steps,
            "transitions": transitions,
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=admin_headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    assert client.get(f"/api/v1/process-versions/{setup['version']['id']}").json() == before
    basis = client.get(f"/api/v1/process-versions/{draft['id']}/steps/start/templates")
    assert basis.status_code == 200, basis.text
    assert basis.json()["items"][-1]["template"]["template_version_id"] == template["id"]
    assert basis.json()["items"][-1]["files"][0]["id"] == setup["extraction"]["file_id"]


def test_related_uses_owned_category_type_and_pinned_process_filters_without_ai(client, factory):
    setup, headers = knowledge_sample(client, factory)
    case = publish_case(client, headers, setup)
    template, _ = publish_template(client, headers, setup)
    response = client.get(
        "/api/v1/knowledge/related", params={"document_id": setup["source"]["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["templates"][0]["id"] == template["id"]
    assert response.json()["cases"][0]["id"] == case["id"]
    assert "Совпадает тип документа" in response.json()["cases"][0]["reasons"]
    assert (
        client.get("/api/v1/knowledge/cases", params={"category": "different"}).json()["total"] == 0
    )


def test_closed_process_binding_is_not_a_filter_oracle_and_cannot_be_confirmed(client, factory):
    setup, headers = knowledge_sample(client, factory)
    metadata = {
        "registry_id": str(setup["sample"]["registry"]),
        "name": "Synthetic separate process binding",
        "process_version_id": setup["version"]["id"],
        "step_key": "start",
    }
    created = client.post(
        "/api/v1/knowledge/templates",
        headers=headers,
        json={
            **metadata,
            "original": {
                "document_id": setup["source"]["id"],
                "version_id": setup["extraction"]["version_id"],
                "file_id": setup["extraction"]["file_id"],
            },
        },
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert (
        client.post(
            f"/api/v1/knowledge/template-versions/{template['version']['id']}/publish",
            headers=headers,
            json={"revision": template["version"]["revision"]},
        ).status_code
        == 200
    )
    created = client.post(
        "/api/v1/knowledge/cases",
        headers=headers,
        json={**metadata, "description": "Synthetic history", "result": "Synthetic result"},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert (
        client.post(
            f"/api/v1/knowledge/cases/{case['id']}/publish",
            headers=headers,
            json={"revision": case["revision"]},
        ).status_code
        == 200
    )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    sign_in_as(client, factory, setup["sample"]["viewer"])
    for kind, identity in [("templates", template["id"]), ("cases", case["id"])]:
        response = client.get(f"/api/v1/knowledge/{kind}/{identity}")
        assert response.status_code == 200, response.text
        assert (
            setup["version"]["id"] not in response.text
            and '"step_key":"start"' not in response.text
        )
        for filters in [{"process_version_id": setup["version"]["id"]}, {"step_key": "start"}]:
            assert client.get(f"/api/v1/knowledge/{kind}", params=filters).json()["total"] == 0
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id)
        ).permissions = []
        source_grant = db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["editor"].id)
        )
        source_grant.permissions = [*source_grant.permissions, "curate_knowledge"]
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    assert (
        client.post(
            f"/api/v1/knowledge/cases/{case['id']}/curate",
            headers=headers,
            json={"action": "confirm", "reason": "Cannot confirm inaccessible process"},
        ).status_code
        == 404
    )
