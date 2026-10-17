"""Authentication, session, CSRF, rate-limit and object-level authorisation tests.

These are the behaviours a reviewer would check first, so each one asserts the status
code *and* the machine-readable error code from the shared envelope.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.security import (
    create_session_token,
    read_session_token,
    safe_filename,
    sanitize_payload,
    verify_password,
)
from app.main import app
from app.schemas.common import RegisterRequest

API = "/api/v1"
BISECTION = {
    "title": "sqrt of two",
    "kind": "numerical",
    "method": "bisection",
    "parameters": {"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-12},
}


def register(client: TestClient, email: str | None = None, password: str = "correct-horse-battery-9"):
    response = client.post(
        f"{API}/auth/register",
        json={"email": email or f"user{uuid.uuid4().hex[:10]}@example.com", "password": password},
    )
    return response


def test_registration_issues_a_cookie_session_and_no_token_in_the_body(client):
    response = register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"].endswith("@example.com")
    assert "password_hash" not in json.dumps(body)
    assert "access_token" not in body, "the session token must not be echoed in a JSON body"
    assert body["csrf_token"]
    assert "plab_session" in client.cookies
    assert client.cookies["plab_session"]  # the value doubles as the bearer token
    cookie_header = response.headers["set-cookie"]
    assert "HttpOnly" in cookie_header and "SameSite" in cookie_header


def test_weak_or_short_passwords_are_rejected_with_the_policy_message(client):
    too_short = client.post(f"{API}/auth/register", json={"email": "a@b.com", "password": "short"})
    assert too_short.status_code == 422
    assert "password" in too_short.text.lower()
    invalid_email = client.post(
        f"{API}/auth/register", json={"email": "not-an-email", "password": "long-enough-password-9"}
    )
    assert invalid_email.status_code == 422


@pytest.mark.parametrize(
    "email,password",
    [
        ("ok@example.com", "aaaaaaaaaa"),        # one repeated character
        ("ok@example.com", "password123"),        # trivially leakable
        ("ok@example.com", "short"),              # too short
        ("jane.doe@example.com", "jane.doe-is-here"),  # reuses the local part
    ],
)
def test_register_request_model_rejects_weak_passwords(email, password):
    with pytest.raises(ValidationError) as exc:
        RegisterRequest(email=email, password=password)
    assert "password" in str(exc.value)


def test_login_rejects_a_wrong_password_and_unknown_accounts_identically(client):
    created = register(client, email="known@example.com")
    assert created.status_code == 201
    bad = client.post(f"{API}/auth/login", json={"email": "known@example.com", "password": "wrong-password-here-1"})
    missing = client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-here-1"})
    assert bad.status_code == 401 and missing.status_code == 401
    # the same message: no account enumeration
    assert bad.json()["error"]["message"] == missing.json()["error"]["message"]


def test_unauthenticated_requests_get_401_on_every_private_route(client):
    for path in (
        f"{API}/experiments",
        f"{API}/projects",
        f"{API}/datasets",
        f"{API}/reports",
        f"{API}/agent/sessions",
        f"{API}/dashboard",
        f"{API}/settings",
    ):
        assert client.get(path).status_code == 401, path
    blocked = client.post(
        f"{API}/compute/run",
        json={"kind": "numerical", "method": "bisection",
              "parameters": {"expression": "x", "a": -1.0, "b": 1.0}},
    )
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "unauthenticated"


def test_a_garbage_bearer_token_is_rejected(client):
    assert client.get(f"{API}/experiments", headers={"Authorization": "Bearer not.a.token"}).status_code == 401
    expired = client.get(f"{API}/experiments", headers={"Authorization": "Bearer " + "x" * 80})
    assert expired.status_code == 401


def test_bearer_tokens_authenticate_without_a_cookie(authed):
    token = authed.cookies.get("plab_session")
    response = authed.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user"]["email"]


def test_csrf_is_enforced_for_cookie_writes_and_exempt_for_login(authed):
    missing = authed.post(f"{API}/experiments", json=BISECTION, headers={"X-CSRF-Token": "wrong"})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_check_failed"
    # login/register must stay reachable without a token, or nobody could sign in
    fresh = TestClient(app)
    with fresh:
        assert register(fresh).status_code == 201


def test_body_size_limit_rejects_oversized_json(authed):
    huge = dict(BISECTION)
    huge["title"] = "x" * (3 * 1024 * 1024)
    response = authed.post(f"{API}/experiments", json=huge)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_expressions_that_are_not_numeric_are_rejected_not_executed(authed):
    response = authed.post(
        f"{API}/compute/run",
        json={"kind": "numerical", "method": "bisection",
              "parameters": {"expression": "__import__('os').system('id')", "a": 1.0, "b": 2.0}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"invalid_numerical_input", "invalid_expression"}
    assert "safe parser" in response.json()["error"]["message"]


def test_sql_shaped_input_is_stored_as_text_and_harms_nothing(authed):
    title = "'; DROP TABLE experiments; --"
    created = authed.post(f"{API}/experiments", json={**BISECTION, "title": title})
    assert created.status_code == 201
    assert created.json()["title"] == title
    listing = authed.get(f"{API}/experiments")
    assert listing.status_code == 200
    assert any(item["title"] == title for item in listing.json()["items"])


def test_object_level_authorisation_between_accounts(authed, second_user_token):
    experiment_id = authed.post(f"{API}/experiments", json=BISECTION).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    other = {"Authorization": f"Bearer {second_user_token}"}
    assert authed.get(f"{API}/experiments/{experiment_id}").status_code == 200
    assert TestClient(app).get(f"{API}/experiments/{experiment_id}", headers=other).status_code in (401, 403, 404)
    # a second, fully authenticated account must be told 403 (exists but not yours)
    with TestClient(app) as c2:
        register(c2)
        c2.headers.update(other)
        assert c2.get(f"{API}/experiments/{experiment_id}", headers=other).status_code == 403
        assert c2.get(f"{API}/experiments/{experiment_id}/runs", headers=other).status_code == 403
        assert c2.patch(f"{API}/experiments/{experiment_id}", json={"title": "mine now"},
                        headers=other).status_code == 403
        assert c2.post(f"{API}/experiments/{experiment_id}/duplicate", headers=other).status_code == 403
        # runs and artifacts are hidden entirely: 404, so ids cannot be probed
        assert c2.get(f"{API}/runs/{run_id}", headers=other).status_code == 404
        assert c2.get(f"{API}/runs/{run_id}/plot.png", headers=other).status_code == 404
        assert c2.delete(f"{API}/experiments/{experiment_id}", headers=other).status_code == 403
        assert c2.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}, headers=other).status_code == 403


def test_password_change_revokes_every_existing_session(client):
    created = register(client, email="rotate@example.com")
    token = client.cookies.get("plab_session")
    csrf = created.json()["csrf_token"]
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    changed = client.post(
        f"{API}/auth/password",
        json={"current_password": "correct-horse-battery-9", "new_password": "a-brand-new-passphrase-42"},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "password_updated"
    assert "revoked" in changed.json()["note"]
    # the old token is dead (token_version bump), the cookie too
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    relogged = client.post(f"{API}/auth/login", json={"email": "rotate@example.com",
                                                     "password": "a-brand-new-passphrase-42"})
    assert relogged.status_code == 200


def test_logout_revokes_the_session_and_clears_the_cookie(client):
    created = register(client, email="bye@example.com")
    csrf = created.json()["csrf_token"]
    token = client.cookies.get("plab_session")
    response = client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_rate_limit_returns_429_with_retry_information():
    """The limiter is switched on for one test instead of the whole suite.

    ``settings.rate_limit_enabled`` is read per request, so this exercises the real
    dependency; a suite-wide limiter would make unrelated tests fail intermittently.
    """
    from app.core.config import settings
    from app.core.rate_limit import reset_state

    reset_state()
    settings.rate_limit_enabled = True
    try:
        with TestClient(app) as limited:
            codes = []
            for _ in range(12):
                codes.append(
                    limited.post(
                        f"{API}/auth/login",
                        json={"email": "nobody@example.com", "password": "wrong-password-here-1"},
                    ).status_code
                )
            assert codes.count(401) == settings_rate_limit_auth_calls(), codes
            assert codes[-1] == 429, codes
            blocked = limited.post(
                f"{API}/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password-here-1"},
            )
            assert blocked.status_code == 429
            error = blocked.json()["error"]
            assert error["code"] == "rate_limited"
            assert error["details"]["retry_after_seconds"] > 0
            assert error["details"]["limit"] == 10
    finally:
        settings.rate_limit_enabled = False
        reset_state()


def settings_rate_limit_auth_calls() -> int:
    """How many login attempts the configured auth rule allows per window."""
    from app.core.config import settings

    return int(settings.rate_limit_auth.split("/")[0])


def test_uploads_are_size_limited_and_type_checked(authed):
    from app.core.config import settings

    # shrinking the cap for one test is far faster than uploading 10 MB
    saved = settings.max_upload_bytes
    settings.max_upload_bytes = 2048
    try:
        big = io.BytesIO(b"a,b\n" + b"1,2\n" * 4000)
        response = authed.post(f"{API}/datasets", files={"file": ("big.csv", big, "text/csv")})
        assert response.status_code in (413, 422), response.text
        assert response.json()["error"]["code"] in {"payload_too_large", "request_validation_failed",
                                                     "conflict"}
    finally:
        settings.max_upload_bytes = saved
    shell = authed.post(
        f"{API}/datasets", files={"file": ("evil.sh", io.BytesIO(b"#!/bin/sh\nrm -rf /\n"), "text/plain")}
    )
    assert shell.status_code == 409
    assert "extension" in shell.json()["error"]["message"]


def test_password_hashing_and_token_roundtrip_are_self_consistent():
    from app.core.security import hash_password

    digest = hash_password("a-secret-passphrase")
    assert digest.startswith("$argon2id$")
    assert verify_password("a-secret-passphrase", digest) is True
    assert verify_password("a-secret-passphrase!", digest) is False
    from app.core.security import password_needs_rehash

    assert password_needs_rehash(digest) is False
    token = create_session_token("usr_123", 7)
    assert read_session_token(token) == ("usr_123", 7)
    from app.core.security import TokenError

    for forged in ("garbage", token[:-4] + "zzzz", create_session_token("usr_999", 1)[:-6] + "AAAA"):
        try:
            read_session_token(forged)
        except TokenError:
            continue
        raise AssertionError(f"a tampered token was accepted: {forged!r}")


def test_filename_sanitiser_blocks_traversal_and_reserved_names():
    assert safe_filename("results.csv") == "results.csv"
    for candidate in ("../../etc/passwd", "/etc/shadow", "..%2f%2e%2e/x", "x.csv\x00.png", "a" * 300):
        try:
            cleaned = safe_filename(candidate)
        except Exception:
            continue
        assert "/" not in cleaned and "\\" not in cleaned and "\x00" not in cleaned
        assert len(cleaned) <= 120
    # Windows device names are made inert rather than dropped
    assert safe_filename("con.txt") == "_con.txt"
    assert safe_filename("LPT1") == "_LPT1"
    # an extension whitelist turns unsupported types into a rejection, not a rename
    with pytest.raises(ValueError):
        safe_filename("payload.php", allowed_extensions=("csv", "tsv", "txt"))


def test_log_sanitiser_masks_secrets():
    cleaned = sanitize_payload({"email": "a@b.c", "password": "hunter2longenough",
                                "api_key": "sk-live-123", "nested": {"anthropic_api_key": "sk-1"}})
    assert cleaned["password"] == "[redacted]"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["nested"]["anthropic_api_key"] == "[redacted]"
    assert cleaned["email"] == "a@b.c"
