import pytest


def test_correct_mounts(monkeypatch):
    from deploy.preflight import verify_mounts

    monkeypatch.setattr("deploy.preflight.read_mount_uuid", lambda path: "expected")
    verify_mounts({"/srv/acti/postgresql": "expected", "/srv/acti/documents": "expected"})


@pytest.mark.parametrize("actual", [None, "wrong"])
def test_wrong_uuid_or_missing_mount_has_no_fallback(monkeypatch, tmp_path, actual):
    from deploy.preflight import MountVerificationError, verify_mounts

    monkeypatch.setattr("deploy.preflight.read_mount_uuid", lambda path: actual)
    missing = tmp_path / "never-create"
    with pytest.raises(MountVerificationError):
        verify_mounts({str(missing): "expected"})
    assert not missing.exists()
