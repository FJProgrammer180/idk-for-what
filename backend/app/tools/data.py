"""Agent tool: analyse an authorised uploaded dataset."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.numerical.common import NumericalInputError
from app.services import analysis
from app.tools.base import ToolContext, ToolError, ToolInput, register
from app.tools.numerics import next_handle


class AnalyzeExperimentalDataInput(ToolInput):
    dataset_id: str = Field(description="Dataset id owned by the authenticated user.")
    x_column: str | None = Field(default=None, description="Independent variable column.")
    y_column: str | None = Field(default=None, description="Dependent variable column.")
    model: str = Field(default="linear", description="'linear', 'polynomial' or a whitelisted nonlinear model.")
    degree: int = Field(default=1, ge=1, le=5)
    columns: list[str] = Field(default_factory=list, max_length=12,
                               description="Optional columns to preview as rows.")
    persist: bool = Field(default=False, description="Store the analysis as an experiment run.")
    title: str | None = Field(default=None, max_length=200)


@register(
    name="analyze_experimental_data",
    category="analysis",
    read_only=False,
    description=(
        "Profile a dataset (types, missing values, descriptive statistics, quality flags) "
        "and optionally fit a supported regression model between two of its columns, "
        "returning residual statistics, parameter standard errors and chart-ready arrays."
    ),
    input_model=AnalyzeExperimentalDataInput,
)
def analyze_experimental_data(args: AnalyzeExperimentalDataInput, context: ToolContext) -> dict[str, Any]:
    params = {
        "dataset_id": args.dataset_id,
        "x_column": args.x_column,
        "y_column": args.y_column,
        "model": args.model,
        "degree": args.degree,
        "columns": args.columns,
    }
    try:
        payload = analysis.run_analysis_job(params, owner_id=context.user_id)
    except NumericalInputError as exc:
        raise ToolError("analysis_failed", str(exc)) from None
    result = payload.get("result") or {}
    profile = result.get("profile") or {}
    fit = result.get("fit")
    defaulted: dict[str, Any] | None = None
    if fit is None and not args.x_column and not args.y_column:
        # An agent task usually says "fit my data" without naming columns.  Leaving
        # `regression: null` next to status "completed" would read as a failed fit, so
        # the first two numeric columns are tried and the substitution is recorded.
        numeric = [str(c) for c in (profile.get("numeric_columns") or [])]
        if len(numeric) >= 2:
            retry = {**params, "x_column": numeric[0], "y_column": numeric[1]}
            payload = analysis.run_analysis_job(retry, owner_id=context.user_id)
            result = payload.get("result") or {}
            profile = result.get("profile") or {}
            fit = result.get("fit")
            defaulted = {
                "x_column": numeric[0],
                "y_column": numeric[1],
                "numeric_columns": numeric,
                "reason": "the task did not name columns; the first two numeric ones were used",
            }
        else:
            payload.setdefault("warnings", []).append(
                "no regression attempted: the dataset has fewer than two numeric columns"
            )
    handle = next_handle(context)
    context.remember(handle, {"kind": "analysis", "method": "dataset_analysis",
                              "params": params, "payload": payload, "verification": None})
    out: dict[str, Any] = {
        "status": "completed",
        "handle": handle,
        "dataset": {
            "dataset_id": payload.get("inputs", {}).get("dataset_id"),
            "filename": profile.get("filename"),
            "rows": profile.get("rows"),
            "columns": profile.get("columns"),
            "numeric_columns": profile.get("numeric_columns"),
            "total_missing_cells": profile.get("total_missing_cells"),
            "duplicate_rows": profile.get("duplicate_rows"),
        },
        "descriptive_statistics": profile.get("descriptive_statistics"),
        "missing_values": profile.get("missing_values"),
        "quality_flags": profile.get("warnings"),
        "regression": None,
        "fit_columns": defaulted,
        "warnings": payload.get("warnings", []),
        "message": payload.get("message"),
    }
    if fit:
        fit_result = fit.get("result") or {}
        out["regression"] = {
            "model": fit_result.get("model"),
            "formula": fit_result.get("formula"),
            "parameters": fit_result.get("parameters"),
            "r_squared": fit_result.get("r_squared"),
            "residual_summary": {
                k: v
                for k, v in (fit_result.get("residuals") or {}).items()
                if k not in {"residuals"}
            },
            "convergence_achieved": fit.get("convergence_achieved"),
            "solver_status": fit.get("status"),
            "warnings": fit.get("warnings", []),
        }
    if args.columns:
        out["row_preview"] = result.get("sample_rows", [])[:20]
    if args.persist:
        from app.tools.numerics import _persist
        from app.services.verification import verify_result as verifier

        verification = verifier(payload, {"params": params})
        out["persisted"] = _persist(
            context,
            title=args.title or f"Dataset analysis - {profile.get('filename')}",
            problem_type="data_analysis",
            method="dataset_analysis",
            params=params,
            payload=payload,
            verification=verification,
            kind="analysis",
        )
    return out
