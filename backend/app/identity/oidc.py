"""Bounded OIDC authorization-code flow. Tokens never become application credentials."""

import base64
import hashlib
import json
import secrets
import ssl
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

TTL = 300
BINDING_COOKIE = "acti_oidc_binding"
CALLBACK_PATH = "/api/v1/auth/oidc/callback"


class OIDCError(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configuration(settings):
    issuer = settings.oidc_issuer
    try:
        parsed = urlsplit(issuer)
    except ValueError as error:
        raise OIDCError() from error
    if not (
        settings.oidc_enabled and parsed.scheme == "https" and parsed.hostname
        and not parsed.username and not parsed.password and not parsed.query
        and not parsed.fragment and len(issuer) <= 2048
        and settings.oidc_client_id and settings.oidc_client_secret.get_secret_value()
        and settings.origin.startswith("https://") and settings.secure_cookie
    ):
        raise OIDCError()
    return issuer


def endpoint(settings, value):
    issuer = urlsplit(configuration(settings))
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
    except ValueError as error:
        raise OIDCError() from error
    if not parsed or (
        parsed.scheme != "https" or parsed.netloc != issuer.netloc
        or parsed.username or parsed.password or parsed.fragment or parsed.query
    ):
        raise OIDCError()
    return value


def fetch_json(settings, method, url, **kwargs):
    """Strict CA verification, no ambient proxy, redirects, or unbounded response bodies."""
    try:
        context = ssl.create_default_context(cafile=settings.oidc_ca_file or None)
        with httpx.Client(
            verify=context, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(3.0),
        ) as client, client.stream(method, endpoint(settings, url), **kwargs) as response:
            if response.status_code != 200:
                raise OIDCError()
            body = bytearray()
            deadline = time.monotonic() + 3.0
            for chunk in response.iter_bytes(chunk_size=65536):
                body.extend(chunk)
                if len(body) > 1024 * 1024 or time.monotonic() > deadline:
                    raise OIDCError()
            result = json.loads(body)
            if not isinstance(result, dict):
                raise OIDCError()
            return result
    except (httpx.HTTPError, ValueError, OSError) as error:
        raise OIDCError() from error


def metadata(settings):
    issuer = configuration(settings)
    data = fetch_json(settings, "GET", issuer.rstrip("/") + "/.well-known/openid-configuration")
    for name in ("response_types_supported", "id_token_signing_alg_values_supported",
                 "code_challenge_methods_supported", "token_endpoint_auth_methods_supported"):
        if not isinstance(data.get(name), list):
            raise OIDCError()
    if (
        data.get("issuer") != issuer
        or "code" not in data.get("response_types_supported", [])
        or "RS256" not in data.get("id_token_signing_alg_values_supported", [])
        or "S256" not in data.get("code_challenge_methods_supported", [])
        or "client_secret_basic" not in data.get("token_endpoint_auth_methods_supported", [])
    ):
        raise OIDCError()
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint(settings, data.get(name))
    return data


def begin(settings, redis):
    data = metadata(settings)
    state, nonce, verifier, binding = (secrets.token_urlsafe(32) for _ in range(4))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    transaction = {
        "nonce": nonce, "verifier": verifier, "binding": digest(binding),
        "issuer": settings.oidc_issuer, "client_id": settings.oidc_client_id,
        "issued_at": time.time(),
    }
    if not redis.set("oidc:" + digest(state), json.dumps(transaction), ex=TTL, nx=True):
        raise OIDCError()
    query = urlencode({
        "response_type": "code", "client_id": settings.oidc_client_id,
        "redirect_uri": settings.origin + CALLBACK_PATH, "scope": "openid profile",
        "state": state, "nonce": nonce, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return data["authorization_endpoint"] + "?" + query, binding


def validate_token(settings, token, jwks, nonce):
    if not isinstance(token, str) or len(token) > 32768:
        raise OIDCError()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or "crit" in header or not header.get("kid"):
            raise OIDCError()
        keys = jwks.get("keys", [])
        if not isinstance(keys, list) or len(keys) > 100:
            raise OIDCError()
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise OIDCError()
        key = matches[0]
        if (
            key.get("kty") != "RSA" or key.get("alg", "RS256") != "RS256"
            or key.get("use", "sig") != "sig"
            or ("key_ops" in key and (
                not isinstance(key["key_ops"], list) or "verify" not in key["key_ops"]
            ))
            or not isinstance(key.get("n"), str) or len(key["n"]) > 1400
            or not isinstance(key.get("e"), str) or len(key["e"]) > 12
        ):
            raise OIDCError()
        public_key = jwt.PyJWK.from_dict(key, algorithm="RS256").key
        if not 2048 <= public_key.key_size <= 8192:
            raise OIDCError()
        claims = jwt.decode(
            token, public_key, algorithms=["RS256"], issuer=settings.oidc_issuer,
            audience=settings.oidc_client_id, leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
        audience = claims["aud"]
        if (
            not isinstance(audience, (str, list))
            or (isinstance(audience, list) and (not audience or any(not isinstance(a, str) for a in audience)))
            or (isinstance(audience, list) and len(audience) > 1 and "azp" not in claims)
            or ("azp" in claims and claims["azp"] != settings.oidc_client_id)
            or not isinstance(claims["nonce"], str)
            or not secrets.compare_digest(claims["nonce"], nonce)
            or not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or time.time() - claims["iat"] > TTL + 30
            or claims["exp"] <= claims["iat"]
        ):
            raise OIDCError()
        return claims["sub"]
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise OIDCError() from error


def complete(settings, redis, *, state, code, binding):
    configuration(settings)
    if not isinstance(state, str) or len(state) != 43:
        raise OIDCError()
    # Atomic single use, including rejected callbacks; do not exchange a replayed code.
    raw = redis.getdel("oidc:" + digest(state))
    try:
        transaction = json.loads(raw) if raw else None
        if not (
            isinstance(transaction, dict) and isinstance(binding, str) and len(binding) == 43
            and secrets.compare_digest(transaction["binding"], digest(binding))
            and transaction["issuer"] == settings.oidc_issuer
            and transaction["client_id"] == settings.oidc_client_id
            and 0 <= time.time() - transaction["issued_at"] <= TTL
            and isinstance(code, str) and 1 <= len(code) <= 4096
        ):
            raise OIDCError()
    except (ValueError, KeyError, TypeError) as error:
        raise OIDCError() from error
    data = metadata(settings)
    tokens = fetch_json(
        settings, "POST", data["token_endpoint"],
        auth=httpx.BasicAuth(settings.oidc_client_id, settings.oidc_client_secret.get_secret_value()),
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": settings.origin + CALLBACK_PATH,
              "code_verifier": transaction["verifier"]},
    )
    keys = fetch_json(settings, "GET", data["jwks_uri"])
    return validate_token(settings, tokens.get("id_token"), keys, transaction["nonce"])
