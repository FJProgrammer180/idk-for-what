"""Authentication and account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import resolve_user
from app.core.errors import AuthenticationError, ConflictError
from app.core.rate_limit import limiter
from app.core.security import (
    create_session_token,
    hash_password,
    new_csrf_token,
    password_needs_rehash,
    utcnow,
    valid_email,
    verify_password,
)
from app.db.session import get_db
from app.models.entities import User
from app.schemas.common import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    SessionResponse,
    UserPublic,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- cookie handling ---------------------------------------------------------


def _issue_cookies(response: Response, user: User) -> str:
    token = create_session_token(user.id, user.token_version)
    csrf = new_csrf_token()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # readable by the SPA on purpose: double-submit CSRF pattern
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf


def _clear_cookies(response: Response) -> None:
    for key in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(key, path="/", samesite="lax")


def public_user(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_demo=user.is_demo,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def require_user_or_401(request: Request, db: Session = Depends(get_db)) -> User:
    user = resolve_user(request, db)
    if user is None:
        raise AuthenticationError("Authentication required")
    return user


# --- endpoints ---------------------------------------------------------------


@router.post(
    "/register",
    response_model=SessionResponse,
    status_code=201,
    summary="Create an account and start a session",
)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(limiter("auth", settings.rate_limit_auth)),
) -> SessionResponse:
    email = payload.email.strip().lower()
    if not valid_email(email):
        raise ConflictError("that email address is not valid")
    if " " in payload.password or payload.password.isdigit():
        raise ConflictError("password must not be all digits or contain spaces; use a longer passphrase")
    if db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none():
        raise ConflictError("an account with that email already exists")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=payload.display_name.strip() or "Researcher",
        role="researcher",
        last_login_at=utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    csrf = _issue_cookies(response, user)
    return SessionResponse(
        user=public_user(user),
        expires_in_seconds=settings.session_ttl_seconds,
        csrf_token=csrf,
        auth_mode="cookie",
    )


@router.post("/login", response_model=SessionResponse, summary="Start a session")
async def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(limiter("auth", settings.rate_limit_auth)),
) -> SessionResponse:
    email = payload.email.strip().lower()
    user = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
    # same error text for unknown email and wrong password: no account enumeration
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthenticationError("Email or password is incorrect")
    if not user.is_active:
        raise AuthenticationError("this account is disabled; contact the administrator")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_login_at = utcnow()
    db.commit()
    csrf = _issue_cookies(response, user)
    return SessionResponse(
        user=public_user(user),
        expires_in_seconds=settings.session_ttl_seconds,
        csrf_token=csrf,
        auth_mode="cookie",
    )


@router.post("/logout", summary="End the session and revoke issued tokens")
async def logout(
    response: Response,
    user: User = Depends(require_user_or_401),
    db: Session = Depends(get_db),
) -> dict:
    user.token_version = int(user.token_version) + 1
    db.commit()
    _clear_cookies(response)
    return {"status": "logged_out", "note": "all previously issued session tokens are revoked"}


@router.get("/me", response_model=SessionResponse, summary="Current session")
async def me(
    request: Request,
    response: Response,
    user: User = Depends(require_user_or_401),
) -> SessionResponse:
    csrf = request.cookies.get(settings.csrf_cookie_name)
    if not csrf:  # bearer-token clients get a CSRF cookie issued on demand
        csrf = _issue_cookies(response, user)
    return SessionResponse(
        user=public_user(user),
        expires_in_seconds=settings.session_ttl_seconds,
        csrf_token=csrf,
        auth_mode="cookie" if request.cookies.get(settings.session_cookie_name) else "bearer",
    )


@router.patch("/me", summary="Update the profile")
async def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(require_user_or_401),
    db: Session = Depends(get_db),
) -> dict:
    if payload.display_name:
        user.display_name = payload.display_name.strip()[:120]
    db.commit()
    return {"status": "updated", "user": public_user(user).model_dump()}


@router.post("/password", summary="Change password (revokes all sessions)")
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    user: User = Depends(require_user_or_401),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(payload.current_password, user.password_hash):
        raise AuthenticationError("current password is incorrect")
    if payload.current_password == payload.new_password:
        raise ConflictError("the new password must differ from the current one")
    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    user.token_version = int(user.token_version) + 1
    db.commit()
    _clear_cookies(response)
    return {"status": "password_updated", "note": "sign in again; all earlier sessions were revoked"}
