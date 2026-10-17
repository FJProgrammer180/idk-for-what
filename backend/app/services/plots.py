"""Deterministic scientific plot rendering (Matplotlib Agg) for reports/exports."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")  # headless, deterministic, no display required

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.core.config import settings  # noqa: E402

STYLE = {
    "figure.facecolor": "#101014",
    "axes.facecolor": "#16161c",
    "savefig.facecolor": "#101014",
    "axes.edgecolor": "#2a2a35",
    "axes.labelcolor": "#c9c9d6",
    "xtick.color": "#8b8b9a",
    "ytick.color": "#8b8b9a",
    "text.color": "#e6e6f0",
    "axes.grid": True,
    "grid.color": "#23232c",
    "grid.linewidth": 0.6,
    "lines.linewidth": 1.6,
    "font.family": "DejaVu Sans",
    "axes.prop_cycle": plt.cycler(
        color=["#a855f7", "#22d3ee", "#60a5fa", "#f472b6", "#facc15", "#34d399"]
    ),
}


def _finite(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    return np.where(np.isfinite(arr), arr, np.nan)


def render_series(
    series: list[dict[str, Any]],
    *,
    title: str = "",
    xlabel: str = "x",
    ylabel: str = "y",
    width: float = 7.2,
    height: float = 4.0,
    scatter: bool = False,
    marker_alpha: float = 0.85,
) -> bytes:
    """Render one or more {x, y, label} series to PNG bytes."""
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(width, height), dpi=140)
        plotted = 0
        for spec in series[:8]:
            x = _finite(spec.get("x") or [])
            y = _finite(spec.get("y") or [])
            n = min(len(x), len(y))
            if n < 2:
                continue
            x, y = x[:n], y[:n]
            label = str(spec.get("label") or f"series {plotted + 1}")
            if scatter:
                ax.scatter(x, y, s=14, alpha=marker_alpha, label=label, linewidths=0.5,
                           edgecolors="white")
            else:
                ax.plot(x, y, label=label)
            plotted += 1
        if plotted == 0:
            ax.text(0.5, 0.5, "no plottable finite data", ha="center", va="center",
                    transform=ax.transAxes)
        if title:
            ax.set_title(title, fontsize=10.5, pad=10)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.tick_params(labelsize=8)
        if plotted > 1:
            ax.legend(fontsize=8, frameon=False)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()


def render_residuals(fitted: Sequence[float], residuals: Sequence[float], *,
                     title: str = "Residuals vs fitted values") -> bytes:
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=140)
        f = _finite(fitted)
        r = _finite(residuals)
        n = min(len(f), len(r))
        if n >= 2:
            ax.scatter(f[:n], r[:n], s=16, color="#22d3ee", alpha=0.85, edgecolors="white",
                       linewidths=0.4, label="residual")
            ax.axhline(0.0, color="#a855f7", linewidth=1.0, linestyle="--")
            span = float(np.nanmax(np.abs(r[:n]))) if np.isfinite(r[:n]).any() else 1.0
            sigma = float(np.nanstd(r[:n])) if n > 1 else 0.0
            if sigma:
                for k, style in ((2, ":"), (-2, ":")):
                    ax.axhline(k * sigma, color="#60a5fa", linewidth=0.8, linestyle=style)
                ax.set_ylim(-3.0 * sigma - 1e-9, 3.0 * sigma + 1e-9)
            else:
                ax.set_ylim(-span - 1e-9, span + 1e-9)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("fitted value", fontsize=9)
        ax.set_ylabel("residual", fontsize=9)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()


def save_artifact(run_id: str, image_bytes: bytes, *, kind: str = "plot") -> dict[str, Any]:
    settings.ensure_dirs()
    safe_run = "".join(ch for ch in run_id if ch.isalnum() or ch in "_-") or "run"
    path = Path(settings.artifacts_dir) / f"{safe_run}_{kind}.png"
    path.write_bytes(image_bytes)
    return {
        "path": str(path.relative_to(Path(settings.data_dir))),
        "bytes": len(image_bytes),
        "sha256": __import__("hashlib").sha256(image_bytes).hexdigest(),
    }


def series_from_result(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    """Derive plottable series from a stored run output (no fabricated data)."""
    series: list[dict[str, Any]] = []
    xlabel, ylabel = "x", "y"
    result = payload.get("result") or {}
    if payload.get("series"):
        times = payload["series"].get("time") or []
        units = payload.get("units") or {}
        xlabel = f"t [{units.get('time', 's')}]"
        for name, values in payload["series"].items():
            if name == "time" or not isinstance(values, list):
                continue
            series.append({"x": times, "y": values, "label": name})
        ylabel = "state"
    elif payload.get("iterations"):
        xs = [r.get("index") for r in payload["iterations"]]
        err = [r.get("error") for r in payload["iterations"]]
        fx = [r.get("f_x") for r in payload["iterations"]]
        if any(v is not None for v in err):
            series.append({"x": xs, "y": err, "label": "error estimate"})
        if any(v is not None for v in fx):
            series.append({"x": xs, "y": fx, "label": "|f(x)|"})
        xlabel, ylabel = "iteration", "value"
        if result.get("root") is not None:
            series.append(
                {
                    "x": [r.get("x") for r in payload["iterations"]],
                    "y": [r.get("f_x") for r in payload["iterations"]],
                    "label": "iterate vs f(x)",
                }
            )
    elif result.get("chart_data"):
        cd = result["chart_data"]
        if cd.get("scatter"):
            series.append({**cd["scatter"], "label": "measurements"})
        if cd.get("fit_curve"):
            series.append({**cd["fit_curve"], "label": "fitted model"})
        if cd.get("x"):
            xlabel = cd.get("x_label", "x")
        ylabel = "value"
    elif result.get("x") is not None and result.get("y") is not None:
        series.append({"x": result["x"], "y": result["y"], "label": "data"})
    return [s for s in series if s.get("x") and s.get("y")], xlabel, ylabel


def is_finite_matrix_ok(values: Any) -> bool:
    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        return False
    return bool(np.all(np.isfinite(arr)))


def unused() -> None:  # keeps linters quiet about math import in some paths
    _ = math.inf
