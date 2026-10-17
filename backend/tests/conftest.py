"""Pytest configuration.

The environment is written before ``app.core.config`` is imported so the settings
singleton picks up an isolated SQLite file and a disabled rate limiter.  Without the
limiter switch, a full API sweep would trip the 240/minute cap and the failures would
look like application bugs.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="physics-lab-tests-"))
os.environ.update(
    {
        "PLAB_ENVIRONMENT": "test",
        "PLAB_DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
        "PLAB_DATA_DIR": str(_TMP),
        "PLAB_UPLOADS_DIR": str(_TMP / "uploads"),
        "PLAB_ARTIFACTS_DIR": str(_TMP / "artifacts"),
        "PLAB_SECRET_KEY": "test-secret-do-not-use-in-production",
        "PLAB_RATE_LIMIT_ENABLED": "false",
        "PLAB_DEMO_ACCOUNT_ENABLED": "false",
        "PLAB_LOG_JSON": "false",
        "PLAB_LOG_LEVEL": "WARNING",
        "PLAB_DEBUG_PROPAGATE_ERRORS": "false",
        "PLAB_AI_PROVIDER": "mock",
        "PLAB_ARGON2_TIME_COST": "1",
        "PLAB_ARGON2_MEMORY_KIB": "8192",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import create_all  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    create_all()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


class AuthedClient(TestClient):
    """Mirrors the browser: every mutating request echoes the CSRF cookie.

    ``TestClient`` shares the cookie jar across calls, so a plain ``TestClient`` would
    silently send an authenticated session on requests written as unauthenticated.
    This class keeps that distinction honest: ``AuthedClient`` is signed in, the
    ``client`` fixture is not.
    """

    def request(self, method, url, **kwargs):  # type: ignore[override]
        headers = dict(kwargs.get("headers") or {})
        if method.upper() not in ("GET", "HEAD", "OPTIONS") and "X-CSRF-Token" not in headers:
            token = self.cookies.get("plab_csrf")
            if token:
                headers["X-CSRF-Token"] = token
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def authed() -> AuthedClient:
    """A signed-in client with its own account.

    The email is unique per test because the SQLite file is shared for the whole
    session: reusing one address would make the second registration return 409 and the
    failure would be a fixture artefact rather than an application one.
    """
    client = AuthedClient(app)
    with client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"user{uuid.uuid4().hex[:10]}@example.com",
                "password": "muddy-lattice-quantum-77",
            },
        )
        assert response.status_code == 201, response.text
        yield client


@pytest.fixture
def second_user_token(authed: AuthedClient) -> str:
    other = AuthedClient(app)
    with other:
        other.post(
            "/api/v1/auth/register",
            json={
                "email": f"visitor{uuid.uuid4().hex[:10]}@example.com",
                "password": "another-long-passphrase-9",
            },
        )
        yield other.cookies.get("plab_session") or ""
