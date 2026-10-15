from contextlib import contextmanager
from types import SimpleNamespace

from tests.conftest import login_admin


def test_login_commit_failure_cannot_return_success(client, factory):
    assert login_admin(client, factory).status_code == 200

    @contextmanager
    def failing_transaction():
        with factory.begin() as session:
            yield session
            raise RuntimeError("simulated commit failure")

    client.app.state.factory = SimpleNamespace(begin=failing_transaction)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "synthetic-password-for-tests"},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
