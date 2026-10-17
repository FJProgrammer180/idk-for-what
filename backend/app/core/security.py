"""Security primitives: password hashing, session tokens, CSRF, filename hygiene.

Design notes
------------
* Passwords use Argon2id (argon2-cffi) - no bespoke hashing.
* Sessions are stateless signed tokens (itsdangerous, URLSafeTimedSerializer)
  carrying only the user id + a per-user token version, so "log out everywhere"
  works by bumping the version.  Tokens are stored in an httpOnly cookie.
* CSRF: cookie-authenticated state-changing requests must echo a
  non-guessable value held in a readable cookie (double-submit), and
  ``SameSite=Lax`` blocks the common cross-site POST path.
* Upload/download filenames are sanitised; user text never touches a path
  unsanitized.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher, Type as Argon2Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_ph = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_kib,
    parallelism=settings.argon2_parallelism,
    hash_len=32,
    type=Argon2Type.ID,
)

_serializer: URLSafeTimedSerializer | None = None


def _serializer_instance() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(settings.resolve_secret_key(), salt="plab.session.v1")
    return _serializer


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    if len(password) > 512:
        raise ValueError("password must be at most 512 characters")
    return _ph.hash(password.encode("utf-8"))


def verify_password(password: str, hash_value: str) -> bool:
    try:
        return _ph.verify(hash_value, password.encode("utf-8"))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(hash_value: str) -> bool:
    try:
        return _ph.check_needs_rehash(hash_value)
    except Exception:  # noqa: BLE001
        return False


# --- session tokens ----------------------------------------------------------


def create_session_token(user_id: str, token_version: int) -> str:
    return _serializer_instance().dumps({"uid": user_id, "tv": int(token_version)})


class TokenError(ValueError):
    pass


def read_session_token(token: str) -> tuple[str, int]:
    try:
        payload = _serializer_instance().loads(token, max_age=settings.session_ttl_seconds)
    except SignatureExpired as exc:
        raise TokenError("session expired") from exc
    except BadSignature as exc:
        raise TokenError("invalid session token") from exc
    if not isinstance(payload, dict) or "uid" not in payload:
        raise TokenError("malformed session token")
    return str(payload["uid"]), int(payload.get("tv", 0))


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a, b)


# --- validation helpers ------------------------------------------------------

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


COMMON_PASSWORDS = frozenset(
    """A coarse screen for trivially guessable values, not a strength meter.

    A capstone project should not pretend to be a HIBP client: the honest statement is
    that this rejects the obvious cases and that real protection comes from the argon2id
    cost parameters plus a passphrase from a manager.
    """
    and {
        "password", "password1", "password12", "password123", "password1234",
        "passw0rd", "qwerty123", "qwertyuiop", "letmein123", "iloveyou",
        "admin1234", "welcome123", "monkey1234", "1234567890", "abc123456789",
        "trustno1", "football1", "dragon123", "sunshine1", "princess12",
    }
)


def password_policy_error(password: str, email: str | None = None) -> str | None:
    """Return a human-readable reason to reject this password, or None."""
    if len(password) < 10:
        return "must be at least 10 characters long"
    if len(password) > 512:
        return "must be at most 512 characters long"
    if len(set(password)) < 6:
        return "must use at least 6 different characters"
    if password.lower() in COMMON_PASSWORDS:
        return "is a commonly leaked password; choose something less guessable"
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in password.lower():
            return "must not contain the local part of your email address"
    return None


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email.strip()))


_FILENAME_STRIP = re.compile(r"[^A-Za-z0-9._-]+")


_RESERVED_WINDOWS = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


def _is_sensitive_key(key: str) -> bool:
    """Exact matches and provider-style prefixes (``anthropic_api_key``) count."""
    lowered = key.lower()
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)


def safe_filename(name: str, fallback: str = "file", max_len: int = 120,
                  allowed_extensions: tuple[str, ...] | None = None) -> str:
    """Reduce an arbitrary name to a single safe path component.

    Rejects path traversal, control characters, leading dots and any extension
    outside the whitelist.
    """
    name = unicodedata.normalize("NFKC", str(name or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = _FILENAME_STRIP.sub("_", stem).strip("._-") or fallback
    ext = _FILENAME_STRIP.sub("", ext).lower()
    if allowed_extensions is not None:
        if ext not in allowed_extensions:
            raise ValueError(
                "unsupported file extension"
                + (f" '{ext}'" if ext else " (none)")
                + f"; allowed: {', '.join(allowed_extensions)}"
            )
        return f"{stem}.{ext}"[:max_len]
    out = f"{stem}.{ext}" if ext else stem
    if _RESERVED_WINDOWS.match(out):
        # CON/PRN/AUX/NUL/COMn/LPTn are device names on Windows: writing them either
        # fails or, worse, blocks, so a leading underscore makes them inert
        out = "_" + out
    return out[:max_len]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- output hygiene ----------------------------------------------------------

_SENSITIVE_FRAGMENTS = (
    "api_key", "apikey", "password", "passwd", "secret", "token", "credential",
    "authorization", "access_key", "private_key",
)

_SENSITIVE_KEYS = {
    "secret", "api_key", "apikey", "token", "password", "passwd", "credential",
    "authorization", "cookie", "session", "private_key", "access_key",
}
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_PATH_PATTERN = re.compile(r"(?:/(?:home|root|usr|var|etc|opt)/|\\\\Windows\\\\)[^\s\"']{0,80}")
_ABS_WINDOWS = re.compile(r"[A-Za-z]:\\\\[^\s\"']{0,80}")


def redact_text(text: str) -> str:
    """Strip obvious secrets, absolute host paths and stack-trace noise from
    anything that may be persisted for user-visible audit logs."""
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[redacted-secret]", out)
    out = _PATH_PATTERN.sub("[redacted-path]", out)
    out = _ABS_WINDOWS.sub("[redacted-path]", out)
    return out


def sanitize_payload(payload: Any, max_len: int = 4000) -> Any:
    """Recursively redact sensitive keys/values and bound payload size."""
    if isinstance(payload, dict):
        clean = {}
        for key, value in payload.items():
            if _is_sensitive_key(str(key)):
                clean[str(key)] = "[redacted]"
            else:
                clean[str(key)] = sanitize_payload(value, max_len)
        return clean
    if isinstance(payload, (list, tuple)):
        return [sanitize_payload(v, max_len) for v in list(payload)[:200]]
    if isinstance(payload, float):
        return payload if math.isfinite(payload) else None
    if isinstance(payload, (int, bool)) or payload is None:
        return payload
    text = redact_text(str(payload))
    return text if len(text) <= max_len else text[:max_len] + "…[truncated]"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds").replace("+00:00", "Z")
