"""Data-analysis service: profiling, regression on dataset columns, exports.

Used by the Data Analysis Studio endpoints and by the agent's
``analyze_experimental_data`` tool, so both paths share one implementation.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.db.session import session_scope
from app.models.entities import Dataset
from app.numerical.common import NumericalInputError, software_versions
from app.numerical.regression import NONLINEAR_MODELS, least_squares_fit
from app.services import datasets as ds


def load_dataset(dataset_id: str, owner_id: str) -> Dataset:
    with session_scope() as db:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None or dataset.owner_id != owner_id:
            raise NumericalInputError("dataset not found in your account")
        db.expunge(dataset)
        return dataset


def profile(dataset: Dataset) -> dict[str, Any]:
    """Full profiling: dtypes, missingness, descriptive stats, quality flags."""
    frame = ds.read_dataframe(dataset)
    profile_data = dict(dataset.schema_profile or {})
    numeric_cols = ds.numeric_columns(profile_data)
    stats: dict[str, dict[str, float | None]] = {}
    for col in numeric_cols:
        values = pd.to_numeric(frame[col], errors="coerce").dropna().to_numpy(dtype=float)
        if values.size == 0:
            continue
        q1, q2, q3 = np.percentile(values, [25, 50, 75])
        iqr = float(q3 - q1)
        outliers = int(np.sum((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr))) if iqr > 0 else 0
        finite = values[np.isfinite(values)]
        stats[col] = {
            "count": int(finite.size),
            "mean": _f(finite.mean()) if finite.size else None,
            "std": _f(finite.std(ddof=1)) if finite.size > 1 else None,
            "min": _f(finite.min()) if finite.size else None,
            "max": _f(finite.max()) if finite.size else None,
            "q1": _f(q1),
            "median": _f(q2),
            "q3": _f(q3),
            "iqr": iqr,
            "skew": _f(pd.Series(finite).skew()) if finite.size > 2 else None,
            "kurtosis": _f(pd.Series(finite).kurtosis()) if finite.size > 3 else None,
            "cv": _f(abs(finite.std(ddof=1) / finite.mean()))
            if finite.size > 1 and abs(finite.mean()) > 1e-12
            else None,
            "outlier_count_1_5_iqr": outliers,
        }
    missing = {
        col: {"count": int(frame[col].isna().sum()), "fraction": round(float(frame[col].isna().mean()), 6)}
        for col in frame.columns
    }
    non_numeric = [c for c in frame.columns if c not in numeric_cols]
    categorical = {}
    for col in non_numeric:
        counts = frame[col].astype(str).value_counts().head(8)
        categorical[col] = {"values": {str(k): int(v) for k, v in counts.items()},
                            "unique": int(frame[col].nunique(dropna=True))}
    return {
        "dataset_id": dataset.id,
        "filename": dataset.original_filename,
        "sha256": dataset.sha256,
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "column_names": [str(c) for c in frame.columns],
        "numeric_columns": numeric_cols,
        "non_numeric_columns": non_numeric,
        "missing_values": missing,
        "total_missing_cells": int(frame.isna().sum().sum()),
        "duplicate_rows": int(frame.duplicated().sum()),
        "descriptive_statistics": stats,
        "categorical": categorical,
        "schema": profile_data.get("columns", []),
        "warnings": _quality_flags(frame, numeric_cols, missing),
        "software_versions": software_versions(),
    }


def _quality_flags(frame: pd.DataFrame, numeric_cols: list[str], missing: dict) -> list[str]:
    flags: list[str] = []
    if not numeric_cols:
        flags.append("no numeric columns detected - regression and charts are unavailable")
    if frame.empty:
        flags.append("dataset has zero rows")
    if any(m["count"] for m in missing.values()):
        flags.append("missing values present; numeric coercion drops them row-wise")
    for col in numeric_cols:
        values = pd.to_numeric(frame[col], errors="coerce").dropna().to_numpy(dtype=float)
        if values.size > 2:
            spread = float(np.max(values) - np.min(values))
            if spread == 0.0:
                flags.append(f"column '{col}' is constant")
            elif np.std(values) / (abs(np.mean(values)) + 1e-12) > 5:
                flags.append(f"column '{col}' has very high relative dispersion")
    return flags


def _f(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def run_analysis_job(params: dict[str, Any], *, owner_id: str | None = None) -> dict[str, Any]:
    """Profile a dataset and (optionally) fit a model between two of its columns."""
    if owner_id is None:
        raise NumericalInputError("an authenticated owner is required to analyse a dataset")
    dataset_id = params.get("dataset_id")
    if not dataset_id:
        raise NumericalInputError("'dataset_id' is required for dataset analysis")
    dataset = load_dataset(str(dataset_id), owner_id)
    frame = ds.read_dataframe(dataset)
    report = profile(dataset)

    warnings = list(report["warnings"])
    x_col = params.get("x_column")
    y_col = params.get("y_column")
    fit_payload: dict[str, Any] | None = None
    if x_col and y_col:
        x = ds.column_array(frame, str(x_col))
        y = ds.column_array(frame, str(y_col))
        mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[mask], y[mask]
        dropped = int(mask.size - mask.sum())
        if dropped:
            warnings.append(f"{dropped} row(s) dropped because x or y was missing/non-finite")
        model = str(params.get("model", "linear"))
        if model in ("linear", "poly1"):
            model, degree = "polynomial", 1
        degree = int(params.get("degree", 1))
        if model not in NONLINEAR_MODELS and model != "polynomial":
            raise NumericalInputError(
                f"model '{model}' is not supported. Use 'linear', 'polynomial' "
                f"or one of {sorted(NONLINEAR_MODELS)}"
            )
        result = least_squares_fit(x.tolist(), y.tolist(), model=model, degree=degree)
        fit_payload = result.to_dict(max_iterations=0)
        fit_payload["x_column"] = str(x_col)
        fit_payload["y_column"] = str(y_col)
        warnings.extend(fit_payload.get("warnings", []))
        report["scatter"] = {
            "x": [float(v) for v in x],
            "y": [float(v) for v in y],
        }
        preds = fit_payload["result"].get("predictions")
        if preds:
            order = np.argsort(x)
            report["fit_curve"] = {
                "x": [float(x[i]) for i in order],
                "y": [float(preds[i]) for i in order],
            }
            residuals = fit_payload["result"]["residuals"]["residuals"]
            report["residual_plot"] = {
                "fitted": [float(preds[i]) for i in order],
                "residuals": [float(residuals[i]) for i in order],
            }
    elif x_col or y_col:
        warnings.append("specify both x_column and y_column to run a regression")

    columns = params.get("columns") or []
    sample_rows = []
    if columns:
        subset = frame.loc[[c for c in columns if c in frame.columns]].head(50)
        sample_rows = subset.to_dict(orient="records")
    return {
        "status": "completed",
        "result": {
            "profile": {k: v for k, v in report.items()
                        if k not in {"scatter", "fit_curve", "residual_plot"}},
            "fit": fit_payload,
            "chart_data": {
                "scatter": report.get("scatter"),
                "fit_curve": report.get("fit_curve"),
                "residuals": report.get("residual_plot"),
            },
            "sample_rows": sample_rows,
        },
        "iterations": [],
        "error_estimate": (
            fit_payload["result"]["residuals"]["rmse"]
            if fit_payload and fit_payload.get("result") else None
        ),
        "convergence_achieved": bool(fit_payload is None or fit_payload.get("convergence_achieved")),
        "warnings": warnings,
        "message": (
            f"analysed {report['rows']} rows x {report['columns']} columns"
            + (", regression fitted" if fit_payload else "")
        ),
        "method": "dataset_analysis",
        "inputs": {"dataset_id": dataset_id, "x_column": x_col, "y_column": y_col,
                   "model": params.get("model", "linear")},
        "metadata": {"dataset_sha256": dataset.sha256},
    }


def cleaned_export(dataset_id: str, owner_id: str, *, drop_missing: bool = True,
                   numeric_only: bool = False) -> bytes:
    """Return a cleaned CSV (formula-safe) for download."""
    dataset = load_dataset(dataset_id, owner_id)
    frame = ds.read_dataframe(dataset)
    if numeric_only:
        keep = ds.numeric_columns(dict(dataset.schema_profile or {}))
        frame = frame[[c for c in frame.columns if c in keep]]
    if drop_missing:
        frame = frame.dropna(how="any")
    return ds.tidy_export(frame.reset_index(drop=True))
