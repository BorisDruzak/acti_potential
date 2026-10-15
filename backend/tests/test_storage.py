from io import BytesIO

import pytest


def test_roundtrip_no_overwrite_and_delete_policy(tmp_path):
    from app.storage.local import LocalFileStorage

    store = LocalFileStorage(tmp_path)
    result = store.put("version-1", BytesIO(b"synthetic document"))
    assert result.size == 18
    assert store.get("version-1") == b"synthetic document"
    assert store.stat("version-1").sha256 == result.sha256
    with pytest.raises(FileExistsError):
        store.put("version-1", BytesIO(b"replacement"))
    assert store.get("version-1") == b"synthetic document"
    with pytest.raises(PermissionError):
        store.delete_if_allowed("version-1", allowed=False)
    store.delete_if_allowed("version-1", allowed=True)
    assert not store.exists("version-1")


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/b", "..", "a\\b"])
def test_invalid_storage_key_is_rejected(tmp_path, key):
    from app.storage.local import LocalFileStorage

    with pytest.raises(ValueError):
        LocalFileStorage(tmp_path).put(key, BytesIO(b"data"))


def test_symlink_escape_and_partial_write(tmp_path):
    from app.storage.local import LocalFileStorage

    root = tmp_path / "storage"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (root / "link").symlink_to(outside)
    store = LocalFileStorage(root)
    with pytest.raises(OSError):
        store.get("link")

    class BrokenSource:
        def read(self, size):
            raise RuntimeError("interrupted source")

    with pytest.raises(RuntimeError):
        store.put("partial", BrokenSource())
    assert not store.exists("partial")
    assert list(root.iterdir()) == [root / "link"]
    assert outside.read_bytes() == b"outside"
