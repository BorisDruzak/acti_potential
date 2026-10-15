"""Fail closed before allowing any container to write data."""
import os
import subprocess
from collections.abc import Mapping


class MountVerificationError(RuntimeError):
    pass


def read_mount_uuid(path: str) -> str | None:
    result = subprocess.run(["findmnt", "-rn", "-o", "UUID", "--mountpoint", path],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def verify_mounts(expected: Mapping[str, str]) -> None:
    if not expected or any(not uuid or read_mount_uuid(path) != uuid
                           for path, uuid in expected.items()):
        raise MountVerificationError("Required data mount identity verification failed")


if __name__ == "__main__":
    verify_mounts({"/srv/acti/postgresql": os.environ.get("ACTI_POSTGRES_UUID", ""),
                   "/srv/acti/documents": os.environ.get("ACTI_DOCUMENTS_UUID", "")})
    print("Required mounts verified")
