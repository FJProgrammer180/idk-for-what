"""Dataset ingestion + profiling: CSV validation, safe storage, analysis jobs."""

from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.security import safe_filename, sha256_bytes
from app.db.session import session_scope
from app.models.entities import Dataset
from app.numerical.common import NumericalInputError

ALLOWED_EXTENSIONS = ("csv", "tsv", "txt")
MAX_ROWS = 200_000
SNIFF_LIMIT_BYTES = 64 * 1024
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class UploadError(NumericalInputError):
    pass


def validate_upload(
    filename: str, content: bytes, declared_type: str | None
) -> tuple[str, bytes, str, bool]:
    """Validate an upload before it is stored. Returns (safe_filename, content)."""
    if not content:
        raise UploadError("uploaded file is empty")
    if len(content) > settings.max_upload_bytes:
        raise UploadError(
            f"uploaded file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB limit"
        )
    name = safe_filename(filename, fallback="dataset", allowed_extensions=ALLOWED_EXTENSIONS)
    if b"\x00" in content[:SNIFF_LIMIT_BYTES]:
        raise UploadError("file contains NUL bytes; it does not look like delimited text")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise UploadError("file is not valid UTF-8/latin-1 text") from exc
    if declared_type and declared_type.split(";")[0].strip().lower() not in (
        "text/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream", ""
    ):
        raise UploadError(f"declared media type '{declared_type}' is not accepted for tabular data")
    # structural checks with the stdlib parser (cheap, no pandas objects retained)
    sample = text[: SNIFF_LIMIT_BYTES]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error as exc:
        raise UploadError(f"could not detect a delimited-text structure: {exc}") from exc
    reader = csv.reader(io.StringIO(text), dialect)
    rows = 0
    widths: set[int] = set()
    for record in reader:
        rows += 1
        widths.add(len(record))
        if rows > MAX_ROWS:
            raise UploadError(f"file has more than {MAX_ROWS} rows")
        if len(widths) > 1 and max(widths) - min(widths) > 2:
            raise UploadError(
                f"delimited structure is inconsistent at row {rows} "
                f"(column counts vary between {min(widths)} and {max(widths)})"
            )
        if rows > 5000 and len(widths) == 1:
            break
    if rows < 2:
        raise UploadError("file needs a header row and at least one data row")
    return name, text.encode("utf-8"), dialect.delimiter, bool(has_header)  # type: ignore[return-value]


def store_dataset(
    *, owner_id: str, project_id: str | None, filename: str, content: bytes,
    declared_type: str | None,
) -> dict[str, Any]:
    safe_name, normalised, delimiter, has_header = validate_upload(filename, content, declared_type)
    digest = sha256_bytes(normalised)
    with session_scope() as db:
        existing = (
            db.query(Dataset)
            .filter(Dataset.owner_id == owner_id, Dataset.sha256 == digest)
            .first()
        )
        if existing is not None:
            return {
                "dataset_id": existing.id,
                "duplicate": True,
                "sha256": digest,
                "row_count": existing.row_count,
                "schema_profile": existing.schema_profile,
            }
        settings.ensure_dirs()
        stored_name = f"{owner_id}_{digest[:16]}.csv"
        path = Path(settings.uploads_dir) / stored_name
        path.write_bytes(normalised)
        try:
            profile = _profile(normalised, delimiter, has_header)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        row = Dataset(
            owner_id=owner_id,
            project_id=project_id,
            original_filename=safe_name,
            stored_filename=stored_name,
            storage_path=settings.relative_storage_path(path),
            size_bytes=len(normalised),
            sha256=digest,
            mime_type="text/csv",
            row_count=profile["row_count"],
            schema_profile=profile,
        )
        db.add(row)
        db.flush()
        return {
            "dataset_id": row.id,
            "duplicate": False,
            "sha256": digest,
            "row_count": profile["row_count"],
            "schema_profile": profile,
        }


def read_dataframe(dataset: Dataset) -> pd.DataFrame:
    path = Path(settings.data_dir) / dataset.storage_path
    if not path.exists() or not str(dataset.storage_path).startswith("uploads/"):
        raise NumericalInputError("stored dataset file is missing from the workspace")
    delimiter = dataset.schema_profile.get("delimiter", ",") if dataset.schema_profile else ","
    frame = pd.read_csv(path, sep=delimiter, engine="python", on_bad_lines="error")
    return frame


def _profile(text_bytes: bytes, delimiter: str, has_header: bool) -> dict[str, Any]:
    frame = pd.read_csv(
        io.StringIO(text_bytes.decode("utf-8")), sep=delimiter, engine="python",
        nrows=MAX_ROWS,
    )
    columns = []
    for name in frame.columns:
        series = frame[name]
        coerced = pd.to_numeric(series, errors="coerce")
        numeric_dtype = bool(pd.api.types.is_numeric_dtype(series))
        parseable = bool(
            not numeric_dtype
            and int(coerced.notna().sum()) >= max(1, int(0.8 * len(series)))
            and len(series) > 0
        )
        numeric = numeric_dtype or parseable
        entry: dict[str, Any] = {
            "name": str(name),
            "dtype": str(series.dtype),
            "numeric": bool(numeric),
            "missing": int(series.isna().sum()),
            "missing_fraction": round(float(series.isna().mean()), 6),
            "unique": int(series.nunique(dropna=True)),
        }
        if numeric:
            values = coerced.dropna()
            if values.size:
                entry.update(
                    {
                        "min": _num(values.min()),
                        "max": _num(values.max()),
                        "mean": _num(values.mean()),
                        "std": _num(values.std(ddof=1)) if values.size > 1 else None,
                        "median": _num(values.median()),
                        "count": int(values.size),
                    }
                )
        else:
            top = series.dropna().astype(str).value_counts().head(5)
            entry["top_values"] = {str(k): int(v) for k, v in top.items()}
        columns.append(entry)
    return {
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": columns,
        "delimiter": delimiter,
        "header_detected": bool(has_header),
        "memory_bytes": int(frame.memory_usage(deep=True).sum()),
        "duplicated_rows": int(frame.duplicated().sum()),
    }


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def profile_dataset(dataset_id: str, owner_id: str) -> dict[str, Any]:
    with session_scope() as db:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None or dataset.owner_id != owner_id:
            raise NumericalInputError("dataset not found in your account")
        return dict(dataset.schema_profile or {})


def describe(dataset: Dataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.id,
        "project_id": dataset.project_id,
        "original_filename": dataset.original_filename,
        "size_bytes": dataset.size_bytes,
        "sha256": dataset.sha256,
        "row_count": dataset.row_count,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "schema_profile": dataset.schema_profile,
    }


def tidy_export(frame: pd.DataFrame) -> bytes:
    """Export to CSV with spreadsheet formula injection neutralised."""

    def guard(value: Any) -> Any:
        if isinstance(value, str) and value and value[0] in FORMULA_PREFIXES:
            return "'" + value
        return value

    out = frame.map(guard) if hasattr(frame, "map") else frame
    buffer = io.StringIO()
    out.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue().encode("utf-8")


def numeric_columns(profile: dict[str, Any]) -> list[str]:
    return [c["name"] for c in profile.get("columns", []) if c.get("numeric")]


def column_array(frame: pd.DataFrame, name: str) -> np.ndarray:
    if name not in frame.columns:
        raise NumericalInputError(f"column '{name}' is not present in this dataset")
    values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
    if not np.any(np.isfinite(values)):
        raise NumericalInputError(f"column '{name}' contains no finite numeric values")
    return values
