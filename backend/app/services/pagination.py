"""Pagination helpers shared by every collection endpoint."""

from __future__ import annotations

import math
from typing import Any, Sequence

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings


def page_params(
    limit: int = Query(default=None, ge=1, le=1000, description="Items per page"),
    offset: int = Query(default=0, ge=0, description="Items to skip"),
) -> dict[str, int]:
    return {
        "limit": min(limit or settings.page_size, settings.max_page_size),
        "offset": max(offset or 0, 0),
    }


def paginate(stmt, db: Session, params: dict[str, int], mapper=None) -> dict[str, Any]:
    """Return {items, page, page_size, total, has_more} for a select statement."""
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int(db.execute(count_stmt).scalar_one())
    rows = db.execute(stmt.limit(params["limit"]).offset(params["offset"])).scalars().all()
    items = [mapper(row) for row in rows] if mapper else list(rows)
    page = params["offset"] // max(params["limit"], 1) + 1
    return {
        "items": items,
        "page": page,
        "page_size": params["limit"],
        "offset": params["offset"],
        "total": total,
        "total_pages": int(math.ceil(total / max(params["limit"], 1))) if total else 0,
        "has_more": total > params["offset"] + len(rows),
    }
