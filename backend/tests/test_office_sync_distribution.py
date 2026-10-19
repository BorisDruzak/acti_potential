"""Offline release trust, network bounds and polling tests; never contact GitHub."""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.registry import office_sync_distribution as feed

NOW = datetime(2026, 10, 19, tzinfo=UTC)


@pytest.fixture(scope="module")
def signing():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, pem


def payload(version="1.3.0", **changes):
    sha = "a" * 40
    result = dict(schema=1, product="ACTISync", version=version, source_sha=sha,
                  filename=f"ACTISync-{version}-win-x64.msi", sha256="b" * 64, size=1234,
                  release_tag=f"acti-sync-v{version}-{sha[:12]}",
                  published_at="2026-10-19T00:00:00Z")
    result.update(changes)
    return result


def signed(signing, data):
    raw = json.dumps(data, separators=(",", ":")).encode()
    signature = signing[0].sign(raw, padding.PKCS1v15(), hashes.SHA256())
    return json.dumps(dict(payload=base64.b64encode(raw).decode(),
                           signature=base64.b64encode(signature).decode())).encode()


def candidate(signing, version="1.3.0", **changes):
    return feed.verify_envelope(signed(signing, payload(version, **changes)), signing[1], NOW)


def test_verifies_exact_signed_raw_bytes(signing):
    envelope, data, version = candidate(signing)
    assert version == (1, 3, 0)
    assert data["filename"] == "ACTISync-1.3.0-win-x64.msi"
    assert set(envelope) == {"payload", "signature"}


@pytest.mark.parametrize("changes", [
    {"schema": True}, {"schema": 2}, {"product": "Other"}, {"version": "2.0.0"},
    {"version": "1.03.0"}, {"version": "1.256.0"}, {"version": "1.0.65536"},
    {"size": True}, {"size": 0},
    {"size": feed.MAX_INSTALLER + 1}, {"source_sha": "x" * 40}, {"sha256": "b" * 63},
    {"filename": "../package.msi"}, {"release_tag": "latest"},
    {"published_at": "2099-01-01T00:00:00Z"}, {"published_at": "2026-10-19"},
    {"download_url": "https://evil.example/file"},
])
def test_rejects_signed_invalid_contract(signing, changes):
    with pytest.raises(feed.ReleaseUnavailable):
        candidate(signing, **changes)


def test_tamper_duplicate_properties_bad_encoding_oversize(signing):
    envelope = json.loads(signed(signing, payload()))
    envelope["payload"] = base64.b64encode(json.dumps(payload(size=2)).encode()).decode()
    for raw in [json.dumps(envelope).encode(), b'{"payload":"x","payload":"y","signature":""}',
                b'{"payload":"%%%","signature":""}', b"x" * (feed.MAX_MANIFEST + 1)]:
        with pytest.raises(feed.ReleaseUnavailable):
            feed.verify_envelope(raw, signing[1], NOW)


@pytest.mark.parametrize("route", ["latest", "download"])
def test_failure_cold_backoff_and_public_route(monkeypatch, route):
    calls = []
    clock = [1.0]

    def fail():
        calls.append(1)
        raise httpx.ConnectError("synthetic")

    cache = feed.ReleaseCache(fail, lambda: clock[0])
    monkeypatch.setattr(feed, "cache", cache)
    app = FastAPI()
    app.include_router(feed.router, prefix="/api/v1")
    with TestClient(app) as client:
        for _ in range(3):
            response = client.get(f"/api/v1/office-sync/distribution/{route}")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "300"
            assert "synthetic" not in response.text
    assert len(calls) == 1
    clock[0] += feed.FAILURE_SECONDS
    with pytest.raises(feed.ReleaseUnavailable):
        cache.get()
    assert len(calls) == 2


def test_last_good_rollback_immutable_version_and_stale_expiry(signing):
    clock = [1.0]
    current = [candidate(signing)]
    calls = []

    def fetch():
        calls.append(1)
        if isinstance(current[0], Exception):
            raise current[0]
        return current[0]

    cache = feed.ReleaseCache(fetch, lambda: clock[0])
    good = cache.get()
    assert cache.get() == good
    assert len(calls) == 1
    for rejected in [candidate(signing, "1.2.9"), candidate(signing, sha256="c" * 64),
                     httpx.ReadTimeout("synthetic")]:
        current[0] = rejected
        clock[0] += feed.REFRESH_SECONDS
        assert cache.get() == good
        assert cache.good_at == 1.0
    clock[0] = feed.MAX_STALE_SECONDS + 2.0
    with pytest.raises(feed.ReleaseUnavailable):
        cache.get()
    current[0] = candidate(signing, "1.3.1")
    clock[0] += feed.FAILURE_SECONDS
    assert cache.get() == current[0][0]


def test_public_success_returns_only_envelope(signing, monkeypatch):
    expected = candidate(signing)
    monkeypatch.setattr(feed, "cache", feed.ReleaseCache(lambda: expected))
    app = FastAPI()
    app.include_router(feed.router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.get("/api/v1/office-sync/distribution/latest")
    assert response.status_code == 200
    assert response.json() == expected[0]
    assert "set-cookie" not in response.headers


def test_download_tracks_verified_current_release_without_login_or_target_override(signing, monkeypatch):
    clock = [1.0]
    current = [candidate(signing, "1.3.5")]
    monkeypatch.setattr(feed, "cache", feed.ReleaseCache(lambda: current[0], lambda: clock[0]))
    app = FastAPI()
    app.include_router(feed.router, prefix="/api/v1")
    with TestClient(app, follow_redirects=False) as client:
        for version in ("1.3.5", "1.3.6"):
            current[0] = candidate(signing, version)
            clock[0] += feed.REFRESH_SECONDS
            response = client.get("/api/v1/office-sync/distribution/download?url=https://evil.example/")
            assert response.status_code == 302
            assert response.headers["location"] == feed.asset_url(
                current[0][1]["release_tag"], f"ACTISync-{version}-win-x64.msi",
            )
            assert response.headers["cache-control"] == "no-store"
            assert "set-cookie" not in response.headers


@pytest.mark.parametrize("target", ["http://github.com/file", "https://evil.example/file",
                                      "https://user@github.com/file", "https://github.com:444/file",
                                      "https://github.com:bad/file"])
def test_rejects_untrusted_url_before_network(target):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("network"))) as http:
        with pytest.raises(feed.ReleaseUnavailable):
            feed.bounded_get(http, target, 10)


def test_stream_size_redirect_bounds():
    def handle(request):
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/file"})
        return httpx.Response(200, content=b"x" * 50)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        for path in ["/redirect", "/large"]:
            with pytest.raises(feed.ReleaseUnavailable):
                feed.bounded_get(http, "https://github.com" + path, 10, redirects=True)


def test_canonical_github_release_and_asset_consistency(signing, monkeypatch, tmp_path):
    data = payload()
    manifest = signed(signing, data)
    public_key = tmp_path / "key.pem"
    public_key.write_bytes(signing[1])
    monkeypatch.setattr(feed, "PUBLIC_KEY", public_key)
    metadata = dict(tag_name=data["release_tag"], draft=False, prerelease=False, assets=[
        dict(name=feed.MANIFEST_NAME,
             browser_download_url=feed.asset_url(data["release_tag"], feed.MANIFEST_NAME)),
        dict(name=data["filename"], size=data["size"],
             browser_download_url=feed.asset_url(data["release_tag"], data["filename"])),
    ])
    urls = []

    def handle(request):
        urls.append(str(request.url))
        if str(request.url) == feed.LATEST_URL:
            return httpx.Response(200, json=metadata)
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "https://release-assets.githubusercontent.com/file"})
        return httpx.Response(200, content=manifest)

    client_class = httpx.Client
    monkeypatch.setattr(feed.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    assert feed.fetch_release()[1] == data
    assert urls[0] == feed.LATEST_URL
    metadata["assets"][1]["size"] = data["size"] + 1
    with pytest.raises(feed.ReleaseUnavailable):
        feed.fetch_release()
    metadata["assets"][0]["browser_download_url"] = "https://evil.example/manifest"
    with pytest.raises(feed.ReleaseUnavailable):
        feed.fetch_release()
