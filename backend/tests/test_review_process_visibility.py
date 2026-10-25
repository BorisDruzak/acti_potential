"""Historical process reads must not fall back to unpublished live metadata."""

from datetime import date, timedelta

from tests.test_content_security import sign_in_as
from tests.test_task007_processes import setup_process, valid_graph


def test_reader_date_before_first_version_does_not_expose_live_draft_metadata(client, factory):
    sample, headers, process, draft = setup_process(client, factory)
    body = valid_graph(draft)
    saved = client.put(
        f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    private_name = "SYNTHETIC-UNPUBLISHED-PROCESS-METADATA"
    changed = client.patch(
        f"/api/v1/processes/{process['id']}",
        headers=headers,
        json={"name": private_name},
    )
    assert changed.status_code == 200, changed.text
    historical_url = (
        f"/api/v1/processes/{process['id']}?as_of={date.today() - timedelta(days=1)}"
    )
    configured = client.get(historical_url)
    assert configured.status_code == 200, configured.text
    assert configured.json()["name"] == private_name
    assert configured.json()["version"] is None
    sign_in_as(client, factory, sample["viewer"])
    current = client.get(f"/api/v1/processes/{process['id']}")
    assert current.status_code == 200, current.text
    assert current.json()["name"] == process["name"]
    response = client.get(historical_url)
    assert response.status_code == 404, response.text
    assert private_name not in response.text
