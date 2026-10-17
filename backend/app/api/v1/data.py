"""Datasets (upload, profile, analyse, export) and report endpoints."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_dataset_owner, require_compute_user, require_user
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from app.db.session import get_db
from app.models.entities import Dataset, Experiment, ExperimentRun, Report, ResearchSource, User
from app.numerical.common import NumericalInputError, software_versions
from app.schemas.common import DatasetAnalyzeRequest, ReportCreate, SourceSearchRequest
from app.services import analysis
from app.services import datasets as dataset_service
from app.services.pagination import page_params, paginate
from app.services.reporting import build_report, render_markdown_file, render_pdf
from app.core.security import safe_filename, sha256_bytes, utcnow_iso

datasets_router = APIRouter(prefix="/datasets", tags=["datasets"])
reports_router = APIRouter(prefix="/reports", tags=["reports"])
sources_router = APIRouter(prefix="/sources", tags=["research"])


# --- datasets ----------------------------------------------------------------


@datasets_router.post("", status_code=201, summary="Upload a CSV/TSV dataset")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    user: User = Depends(require_compute_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"upload exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB limit"
        )
    if project_id:
        from app.models.entities import Project

        project = db.get(Project, project_id)
        if project is None or project.owner_id != user.id:
            raise ForbiddenError("project not found in your account")
    try:
        stored = dataset_service.store_dataset(
            owner_id=user.id,
            project_id=project_id,
            filename=file.filename or "dataset.csv",
            content=content,
            declared_type=file.content_type,
        )
    except (NumericalInputError, ValueError) as exc:
        # The message reaches the browser, so a filesystem path or an over-long internal complaint
        # is replaced by the honest short version.
        detail = str(exc).strip()
        root = str(Path(settings.data_dir).resolve())
        if not detail or len(detail) > 300 or root in detail or "/" in detail.replace("\\", "/").split(" ")[0]:
            detail = "this deployment could not store the file (see the server log for the reason)"
        raise ConflictError(f"upload rejected: {detail}") from None
    dataset = db.get(Dataset, stored["dataset_id"])
    return {
        **dataset_service.describe(dataset),
        "duplicate": stored.get("duplicate", False),
        "note": (
            "an identical file already exists in your account; the stored dataset was reused"
            if stored.get("duplicate")
            else "stored as CSV in the controlled upload directory (never inside the database)"
        ),
    }


@datasets_router.get("", summary="List datasets")
async def list_datasets(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
) -> dict[str, Any]:
    stmt = (
        select(Dataset)
        .where(Dataset.owner_id == user.id)
        .order_by(Dataset.created_at.desc())
    )
    return paginate(
        stmt, db, page, mapper=lambda row: {
            "dataset_id": row.id,
            "original_filename": row.original_filename,
            "size_bytes": row.size_bytes,
            "row_count": row.row_count,
            "column_count": (row.schema_profile or {}).get("column_count"),
            "sha256": row.sha256,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "has_analysis": row.analysis is not None,
        }
    )


@datasets_router.get("/{dataset_id}", summary="Dataset metadata + profiling")
async def get_dataset(
    dataset_id: str,
    rows: int = Query(default=25, ge=0, le=500),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = assert_dataset_owner(db.get(Dataset, dataset_id), user)
    profile = analysis.profile(dataset)
    preview: list[dict[str, Any]] = []
    if rows:
        frame = dataset_service.read_dataframe(dataset).head(rows)
        preview = json.loads(frame.to_json(orient="records", default_handler=str))
    return {
        "dataset": dataset_service.describe(dataset),
        "profile": profile,
        "preview_rows": preview,
        "export_url": f"{settings.api_v1_prefix}/datasets/{dataset.id}/export",
    }


@datasets_router.post("/{dataset_id}/analyze", summary="Profile and optionally fit a model")
async def analyze_dataset(
    dataset_id: str,
    payload: DatasetAnalyzeRequest,
    user: User = Depends(require_compute_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = assert_dataset_owner(db.get(Dataset, dataset_id), user)
    params = payload.model_dump(exclude_none=True)
    params["dataset_id"] = dataset.id
    try:
        result = await asyncio_run_analysis(params, user.id)
    except NumericalInputError as exc:
        # the analysis raised because of what the request asked for (a missing column, a
        # degree with too few points); that is a 422 on the request, not a conflict
        columns = [c.get("name") for c in (dataset.schema_profile or {}).get("columns", [])]
        raise ValidationError(
            f"analysis could not run: {exc}",
            details={
                "valid_columns": columns,
                "requested": {"x_column": params.get("x_column"), "y_column": params.get("y_column")},
            },
        ) from None
    dataset.analysis = {
        "generated_at": utcnow_iso(),
        "inputs": {k: v for k, v in params.items() if k != "columns"},
        "summary": {
            "rows": (result.get("result") or {}).get("profile", {}).get("rows"),
            "r_squared": ((result.get("result") or {}).get("fit") or {}).get("result", {}).get("r_squared")
            if (result.get("result") or {}).get("fit") else None,
        },
        "software_versions": software_versions(),
    }
    db.commit()
    return {"dataset_id": dataset.id, **result}


async def asyncio_run_analysis(params: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Run the (CPU-bound, pandas-heavy) analysis off the event loop."""
    import functools

    import asyncio

    return await asyncio.to_thread(functools.partial(analysis.run_analysis_job, params, owner_id=owner_id))


@datasets_router.get("/{dataset_id}/export", summary="Download the cleaned CSV")
async def export_dataset(
    dataset_id: str,
    drop_missing: bool = Query(default=True),
    numeric_only: bool = Query(default=False),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    dataset = assert_dataset_owner(db.get(Dataset, dataset_id), user)
    data = analysis.cleaned_export(
        dataset.id, user.id, drop_missing=drop_missing, numeric_only=numeric_only
    )
    name = safe_filename(
        Path(dataset.original_filename).stem + "_cleaned.csv", fallback="dataset_cleaned.csv",
        allowed_extensions=("csv",),
    )
    return Response(
        content=data,
        media_type="text/csv",
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "x-content-provenance": f"derived from dataset {dataset.id} (sha256 {dataset.sha256[:16]})",
        },
    )


@datasets_router.delete("/{dataset_id}", status_code=204, summary="Delete a dataset and its file")
async def delete_dataset(
    dataset_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Response:
    dataset = assert_dataset_owner(db.get(Dataset, dataset_id), user)
    path = Path(settings.data_dir) / dataset.storage_path
    if str(path).startswith(str(Path(settings.data_dir))) and path.exists():
        path.unlink(missing_ok=True)
    db.delete(dataset)
    db.commit()
    return Response(status_code=204)


# --- reports ------------------------------------------------------------------


@reports_router.post("", status_code=201, summary="Generate a report from stored, verified records")
async def create_report(
    payload: ReportCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not payload.experiment_id and not payload.run_id:
        raise ConflictError("provide experiment_id or run_id")
    if payload.run_id:
        run = db.get(ExperimentRun, payload.run_id)
        if run is None or run.owner_id != user.id:
            raise NotFoundError("run not found in your account")
        experiment = db.get(Experiment, run.experiment_id)
    else:
        experiment = db.get(Experiment, payload.experiment_id)
        if experiment is None or experiment.owner_id != user.id:
            raise NotFoundError("experiment not found in your account")
        run = (experiment.runs or [None])[0]
    if run is None:
        raise ConflictError("this experiment has no runs yet; execute it first")
    if (run.status or "").startswith("queued") or run.status in ("running", "verifying"):
        raise ConflictError(
            f"the run is still '{run.status}'; reports are generated only from finished runs"
        )
    sources: list[dict[str, Any]] = []
    if payload.source_ids:
        rows = db.execute(
            select(ResearchSource).where(
                ResearchSource.id.in_(payload.source_ids), ResearchSource.owner_id == user.id
            )
        ).scalars().all()
        sources = [
            {
                "title": r.title,
                "url": r.url,
                "publisher": r.publisher,
                "citation": r.citation,
                "provenance": r.provenance,
                "source_type": r.source_type,
            }
            for r in rows
        ]
    doc = build_report(
        experiment=experiment,
        run=run,
        sources=sources,
        include_verification=payload.include_verification,
        include_iterations=payload.include_iterations,
        notes=payload.notes,
    )
    settings.ensure_dirs()
    stem = f"{run.id}_{safe_filename(doc['title'], fallback='report', max_len=60)}"
    artifacts: dict[str, Any] = {}
    md_path = Path(settings.artifacts_dir) / f"{stem}.md"
    markdown_bytes = render_markdown_file(doc)
    md_path.write_bytes(markdown_bytes)
    artifacts["markdown"] = {
        "path": settings.relative_storage_path(md_path),
        "bytes": len(markdown_bytes),
        "sha256": sha256_bytes(markdown_bytes),
    }
    if payload.format in ("pdf", "both"):
        try:
            pdf_bytes = render_pdf(doc)
        except Exception as exc:  # noqa: BLE001
            artifacts["pdf"] = {"error": f"PDF rendering failed: {type(exc).__name__}"}
        else:
            pdf_path = Path(settings.artifacts_dir) / f"{stem}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            artifacts["pdf"] = {
                "path": settings.relative_storage_path(pdf_path),
                "bytes": len(pdf_bytes),
                "pages": doc.get("page_count", 1),
                "sha256": sha256_bytes(pdf_bytes),
            }
    report = Report(
        owner_id=user.id,
        experiment_id=experiment.id if experiment else None,
        run_id=run.id,
        title=doc["title"][:200],
        format="markdown+pdf" if payload.format == "both" else payload.format,
        body_markdown=markdown_bytes.decode("utf-8"),
        artifact_path=artifacts["markdown"]["path"],
        artifact_size_bytes=len(markdown_bytes),
        checksum_sha256=artifacts["markdown"]["sha256"],
        verification_snapshot={
            "run_status": run.status,
            "run_verification_status": (run.verification or {}).get("status"),
            "pdf": artifacts.get("pdf"),
            "software_versions": run.software_versions,
            "generated_at": utcnow_iso(),
            "cited_sources": len(sources),
        },
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return {
        "report_id": report.id,
        "title": report.title,
        "experiment_id": report.experiment_id,
        "run_id": report.run_id,
        "format": report.format,
        "sections": [section["heading"] for section in doc["sections"]],
        "word_count": doc["word_count"],
        "warnings": doc["warnings"],
        "artifacts": artifacts,
        "download": {
            "markdown": f"{settings.api_v1_prefix}/reports/{report.id}/download?format=markdown",
            "pdf": f"{settings.api_v1_prefix}/reports/{report.id}/download?format=pdf",
        },
    }


@reports_router.get("", summary="List reports")
async def list_reports(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
) -> dict[str, Any]:
    stmt = (
        select(Report)
        .where(Report.owner_id == user.id)
        .order_by(Report.created_at.desc())
    )
    return paginate(stmt, db, page, mapper=lambda row: report_summary(row))


def _owned_report(db: Session, report_id: str, user: User) -> Report:
    """A report the caller may read or delete; other people's ids are simply not found."""
    report = db.get(Report, report_id)
    if report is None or report.owner_id != user.id:
        raise NotFoundError("report not found in your account")
    return report


@reports_router.get("/{report_id}", summary="Report with its markdown body")
async def get_report(
    report_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    report = _owned_report(db, report_id, user)
    return {**report_summary(report), "body_markdown": report.body_markdown}


@reports_router.delete("/{report_id}", status_code=204, summary="Delete a report and its artifacts")
async def delete_report(
    report_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Response:
    """Remove the stored document and the files it was rendered to.

    Only this report's own artifacts are touched: the run and experiment rows it was built
    from stay, because other reports and the dashboard still reference them.
    """
    report = _owned_report(db, report_id, user)
    data_root = Path(settings.data_dir).resolve()
    snapshot = report.verification_snapshot or {}
    pdf_artifact = snapshot.get("pdf") if isinstance(snapshot, dict) else None
    pdf_path = (pdf_artifact or {}).get("path") if isinstance(pdf_artifact, dict) else None
    for relative in (report.artifact_path, pdf_path):
        if not relative:
            continue
        candidate = (data_root / str(relative)).resolve()
        if str(candidate).startswith(str(data_root)) and candidate.is_file():
            candidate.unlink(missing_ok=True)
    db.delete(report)
    db.commit()
    return Response(status_code=204)



@reports_router.get("/{report_id}/download", summary="Download the stored Markdown or PDF artifact")
async def download_report(
    report_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|pdf)$"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    report = db.get(Report, report_id)
    if report is None or report.owner_id != user.id:
        raise NotFoundError("report not found in your account")
    if format == "markdown":
        return Response(
            content=report.body_markdown.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "content-disposition": f'attachment; filename="{_filename(report, "md")}"',
                "x-report-checksum": report.checksum_sha256 or "",
            },
        )
    snapshot = report.verification_snapshot or {}
    pdf_info = ((snapshot.get("pdf") or {}) if isinstance(snapshot, dict) else {})
    rel = pdf_info.get("path")
    if not rel:
        raise NotFoundError("no PDF artifact was produced for this report")
    path = (Path(settings.data_dir) / rel).resolve()
    root = Path(settings.data_dir).resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise NotFoundError("PDF artifact could not be read from storage")
    return FileResponse(path, media_type="application/pdf", filename=_filename(report, "pdf"))


def _filename(report: Report, ext: str) -> str:
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in report.title)[:60] or "report"
    return f"{stem}.{ext}"


def report_summary(report: Report) -> dict[str, Any]:
    snapshot = report.verification_snapshot or {}
    return {
        "report_id": report.id,
        "title": report.title,
        "experiment_id": report.experiment_id,
        "run_id": report.run_id,
        "format": report.format,
        "format": report.format,
        "experiment_id": report.experiment_id,
        "run_id": report.run_id,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "artifact_path": report.artifact_path,
        "artifact_size_bytes": report.artifact_size_bytes,
        "checksum_sha256": report.checksum_sha256,
        "verification_snapshot": snapshot,
        "has_pdf": bool((snapshot.get("pdf") or {}).get("path")) if isinstance(snapshot, dict) else False,
        "word_count_estimate": len((report.body_markdown or "").split()),
    }


# --- research sources --------------------------------------------------------


@sources_router.get("", summary="Stored research sources (provenance visible)")
async def list_sources(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
) -> dict[str, Any]:
    stmt = (
        select(ResearchSource)
        .where(ResearchSource.owner_id == user.id)
        .order_by(ResearchSource.created_at.desc())
    )
    return paginate(stmt, db, page, mapper=lambda row: {
        "id": row.id,
        "title": row.title,
        "url": row.url,
        "publisher": row.publisher,
        "source_type": row.source_type,
        "provenance": row.provenance,
        "citation": row.citation,
        "evidence": row.evidence[:800],
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


@sources_router.post("/search", summary="Search sources through the configured integration")
async def search_sources(
    payload: SourceSearchRequest, user: User = Depends(require_user)
) -> dict[str, Any]:
    """Direct (non-agent) access to the same retrieval tool the agent uses.

    The tool itself decides whether live retrieval is configured; when it is not, the
    response says so instead of returning invented citations.
    """
    from app.tools.base import ToolContext
    from app.tools import execute as execute_tool

    return execute_tool(
        "search_scientific_sources",
        {"query": payload.query.strip(), "max_results": payload.max_results},
        ToolContext(user_id=user.id),
    )
