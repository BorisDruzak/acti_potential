import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.storage.base import StoredFile


class LocalFileStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        if self.root != self.root.resolve(strict=True) or not self.root.is_dir():
            raise ValueError("Storage root must be an existing directory without symlinks")

    @staticmethod
    def _key(key):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key):
            raise ValueError("Invalid opaque storage key")
        return key

    @contextmanager
    def _directory(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def open_stream(self, key):
        key = self._key(key)
        with self._directory() as directory:
            fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                import stat

                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Storage object is not a regular file")
                yield stream

    def get(self, key):
        with self.open_stream(key) as stream:
            return stream.read()

    def stat(self, key):
        with self.open_stream(key) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            return StoredFile(key, os.fstat(stream.fileno()).st_size, digest)

    def exists(self, key):
        try:
            with self.open_stream(key):
                return True
        except FileNotFoundError:
            return False

    def put(self, key, source):
        key = self._key(key)
        temporary = ".upload-" + uuid4().hex
        with self._directory() as directory:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o640,
                dir_fd=directory,
            )
            digest, size = hashlib.sha256(), 0
            try:
                with os.fdopen(fd, "wb") as stream:
                    while chunk := source.read(1024 * 1024):
                        stream.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(
                    temporary,
                    key,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
            finally:
                os.unlink(temporary, dir_fd=directory)
        return StoredFile(key, size, digest.hexdigest())

    def delete_if_allowed(self, key, *, allowed):
        if not allowed:
            raise PermissionError("Explicit domain authorization required")
        key = self._key(key)
        with self._directory() as directory:
            os.unlink(key, dir_fd=directory)
            os.fsync(directory)
