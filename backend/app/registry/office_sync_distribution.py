"""Public, signed ACTI Sync releases. No identity or document state is consulted.

Only a pinned repository is contacted. GitHub data is untrusted until signature,
schema and release/asset consistency checks succeed. Cache failure never replaces
the last known good release or resets its bounded stale lifetime.
"""

import base64
import binascii
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

REPOSITORY = "BorisDruzak/acti_potential"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST_NAME = "acti-sync-release.json"
MAX_MANIFEST = 16 * 1024
MAX_METADATA = 256 * 1024
MAX_INSTALLER = 512 * 1024 * 1024
REFRESH_SECONDS = 600
FAILURE_SECONDS = 300
MAX_STALE_SECONDS = 24 * 3600
PUBLIC_KEY = Path(__file__).with_name("acti_sync_release_public_key.pem")
router = APIRouter(prefix="/office-sync/distribution")


class ReleaseUnavailable(ValueError):
    pass


def strict_json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseUnavailable("Duplicate JSON property")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError) as exc:
        raise ReleaseUnavailable("Invalid JSON") from exc


def verify_envelope(raw: bytes, public_key: bytes, now: datetime):
    if len(raw) > MAX_MANIFEST:
        raise ReleaseUnavailable("Manifest exceeds limit")
    envelope = strict_json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ReleaseUnavailable("Invalid envelope")
    try:
        if any(not isinstance(value, str) for value in envelope.values()):
            raise ValueError()
        payload_raw = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 3072:
            raise ValueError()
        key.verify(signature, payload_raw, padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError, InvalidSignature, binascii.Error) as exc:
        raise ReleaseUnavailable("Invalid signature") from exc
    payload = strict_json(payload_raw)
    fields = {"schema", "product", "version", "source_sha", "filename", "sha256", "size",
              "release_tag", "published_at"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ReleaseUnavailable("Invalid payload fields")
    if type(payload["schema"]) is not int or payload["schema"] != 1:
        raise ReleaseUnavailable("Unknown schema")
    if payload["product"] != "ACTISync":
        raise ReleaseUnavailable("Wrong product")
    if any(not isinstance(payload[field], str) for field in fields - {"schema", "size"}):
        raise ReleaseUnavailable("Invalid field type")
    version = payload["version"]
    if not re.fullmatch(r"1\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})", version):
        raise ReleaseUnavailable("Invalid version")
    version_tuple = tuple(map(int, version.split(".")))
    if version_tuple[1] > 255 or version_tuple[2] > 65535:
        raise ReleaseUnavailable("Version component exceeds limit")
    if not re.fullmatch(r"[0-9a-f]{40}", payload["source_sha"]):
        raise ReleaseUnavailable("Invalid source revision")
    if not re.fullmatch(r"[0-9a-f]{64}", payload["sha256"]):
        raise ReleaseUnavailable("Invalid digest")
    if type(payload["size"]) is not int or not 1 <= payload["size"] <= MAX_INSTALLER:
        raise ReleaseUnavailable("Invalid installer size")
    if payload["filename"] != f"ACTISync-{version}-win-x64.msi":
        raise ReleaseUnavailable("Invalid filename")
    if payload["release_tag"] != f"acti-sync-v{version}-{payload['source_sha'][:12]}":
        raise ReleaseUnavailable("Invalid release tag")
    try:
        published = datetime.fromisoformat(payload["published_at"].replace("Z", "+00:00"))
        if published.utcoffset() != timedelta(0) or published > now + timedelta(minutes=10):
            raise ValueError()
    except ValueError as exc:
        raise ReleaseUnavailable("Invalid publication time") from exc
    return envelope, payload, version_tuple


def asset_url(tag: str, filename: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"


def bounded_get(client, url: str, limit: int, *, redirects=False) -> bytes:
    deadline = time.monotonic() + 20
    for _ in range(4):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ReleaseUnavailable("Invalid release URL") from exc
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or port not in (None, 443) or parsed.fragment
                or parsed.hostname not in {"api.github.com", "github.com",
                                           "release-assets.githubusercontent.com"}):
            raise ReleaseUnavailable("Invalid release origin")
        with client.stream("GET", url) as response:
            if response.is_redirect:
                target = response.headers.get("location", "")
                # Only the known GitHub asset delivery redirect is permitted.
                if (not redirects or parsed.hostname != "github.com"
                        or urlsplit(target).hostname != "release-assets.githubusercontent.com"):
                    raise ReleaseUnavailable("Invalid release redirect")
                url = target
                continue
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and (not length.isdecimal() or int(length) > limit):
                raise ReleaseUnavailable("Response exceeds limit")
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if len(body) + len(chunk) > limit or time.monotonic() > deadline:
                    raise ReleaseUnavailable("Response exceeds bounds")
                body.extend(chunk)
            return bytes(body)
    raise ReleaseUnavailable("Too many release redirects")


def fetch_release():
    with httpx.Client(
        timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ACTI-Sync-release-feed",
                 "X-GitHub-Api-Version": "2022-11-28"},
    ) as client:
        release = strict_json(bounded_get(client, LATEST_URL, MAX_METADATA))
        if (not isinstance(release, dict) or release.get("draft") is not False
                or release.get("prerelease") is not False
                or not isinstance(release.get("tag_name"), str)
                or not re.fullmatch(r"acti-sync-v1\.\d{1,5}\.\d{1,5}-[0-9a-f]{12}",
                                    release["tag_name"])):
            raise ReleaseUnavailable("Invalid GitHub release")
        tag = release["tag_name"]
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise ReleaseUnavailable("Missing assets")
        manifests = [a for a in assets if isinstance(a, dict) and a.get("name") == MANIFEST_NAME]
        if len(manifests) != 1 or manifests[0].get("browser_download_url") != asset_url(tag, MANIFEST_NAME):
            raise ReleaseUnavailable("Invalid manifest asset")
        raw = bounded_get(client, asset_url(tag, MANIFEST_NAME), MAX_MANIFEST, redirects=True)
        envelope, payload, version = verify_envelope(raw, PUBLIC_KEY.read_bytes(), datetime.now(UTC))
        installers = [a for a in assets if isinstance(a, dict) and a.get("name") == payload["filename"]]
        if (payload["release_tag"] != tag or len(installers) != 1
                or installers[0].get("size") != payload["size"]
                or installers[0].get("browser_download_url") != asset_url(tag, payload["filename"])):
            raise ReleaseUnavailable("Installer metadata mismatch")
        return envelope, payload, version


class ReleaseCache:
    def __init__(self, fetch=fetch_release, clock=time.monotonic):
        self.fetch = fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.next_check = 0.0
        self.good_at = 0.0
        self.good = None

    def get(self):
        with self.lock:
            now = self.clock()
            if now >= self.next_check:
                self.next_check = now + FAILURE_SECONDS
                try:
                    candidate = self.fetch()
                    if self.good:
                        if candidate[2] < self.good[2]:
                            raise ReleaseUnavailable("Release rollback")
                        if candidate[2] == self.good[2] and candidate[1] != self.good[1]:
                            raise ReleaseUnavailable("Immutable version changed")
                    self.good = candidate
                    self.good_at = self.clock()
                    self.next_check = self.good_at + REFRESH_SECONDS
                except (ReleaseUnavailable, httpx.HTTPError, OSError):
                    pass
            if self.good is None or self.clock() - self.good_at > MAX_STALE_SECONDS:
                raise ReleaseUnavailable("No verified current release available")
            return self.good[0]


cache = ReleaseCache()


@router.get("/latest")
def latest(request: Request):
    try:
        return JSONResponse(cache.get(), headers={"Cache-Control": "no-store"})
    except ReleaseUnavailable:
        # Return directly because the application's generic exception handler
        # intentionally drops exception headers, including Retry-After.
        return JSONResponse(
            {"error": {"code": "release_unavailable",
                       "message": "ACTI Sync release temporarily unavailable", "details": None},
             "request_id": getattr(request.state, "request_id", None)},
            status_code=503,
            headers={"Retry-After": str(FAILURE_SECONDS), "Cache-Control": "no-store"},
        )
