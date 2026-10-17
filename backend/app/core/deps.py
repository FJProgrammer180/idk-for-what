"""FastAPI dependencies: authentication and ownership enforcement."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.security import read_session_token
from app.db.session import get_db  # noqa: F401 - re-exported for routers
from app.models.entities import Dataset, Experiment, Project, User

logger = get_logger("physicslab.auth")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def token_from_request(request: Request) -> tuple[str | None, str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip(), "bearer"
    cookie = request.cookies.get(settings.session_cookie_name)
    return (cookie, "cookie") if cookie else (None, "none")


def resolve_user(request: Request, db: Session) -> User | None:
    token, kind = token_from_request(request)
    if not token:
        return None
    try:
        user_id, token_version = read_session_token(token)
    except ValueError as exc:
        request.state.auth_failure = str(exc)
        return None
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        request.state.auth_failure = "unknown subject"
        return None
    if not user.is_active:
        request.state.auth_failure = "account disabled"
        return None
    if user.token_version != token_version:
        request.state.auth_failure = "session revoked"
        return None
    request.state.user_id = user.id
    request.state.auth_mode = kind
    return user


def current_user_or_none(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """For endpoints that adapt their output for guests (e.g. /auth/me)."""
    return resolve_user(request, db)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = resolve_user(request, db)
    if user is None:
        detail = getattr(request.state, "auth_failure", "no credentials supplied")
        raise AuthenticationError(
            "Authentication required. Sign in to run computations or access stored data.",
            details={"reason": detail},
        )
    if settings.rate_limit_enabled:  # per-account budget on top of the IP budget
        limiter("user", settings.rate_limit_default)(request)
    return user


def require_compute_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_user(request, db)
    if settings.rate_limit_enabled:
        limiter("compute", settings.rate_limit_compute)(request)
    return user


# --- ownership ---------------------------------------------------------------


def assert_project_owner(project: Project | None, user: User) -> Project:
    if project is None:
        # 404: the row does not exist for this caller at all.  Only a row that exists and
        # belongs to somebody else is a 403.
        raise NotFoundError("Project not found")
    if project.owner_id != user.id:
        raise ForbiddenError("You do not have access to this project.")
    return project


def assert_experiment_owner(experiment: Experiment | None, user: User) -> Experiment:
    if experiment is None:
        # 404: the row does not exist for this caller at all.  Only a row that exists and
        # belongs to somebody else is a 403.
        raise NotFoundError("Experiment not found")
    if experiment.owner_id != user.id:
        raise ForbiddenError("You do not have access to this experiment.")
    return experiment


def assert_dataset_owner(dataset: Dataset | None, user: User) -> Dataset:
    if dataset is None:
        # 404: the row does not exist for this caller at all.  Only a row that exists and
        # belongs to somebody else is a 403.
        raise NotFoundError("Dataset not found")
    if dataset.owner_id != user.id:
        raise ForbiddenError("You do not have access to this dataset.")
    return dataset


def get_owned_or_403(db: Session, model, resource_id: str, user: User, label: str):
    """Load a row by id and verify ownership; identical error for missing/foreign."""
    row = db.get(model, resource_id)
    if row is None or getattr(row, "owner_id", None) != user.id:
        raise ForbiddenError(f"{label} not found or not owned by this account.")
    return row
