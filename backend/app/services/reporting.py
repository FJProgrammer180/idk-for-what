"""Report generation from persisted, verified records (Markdown + PDF)."""

from __future__ import annotations

import json
import math
import re
import textwrap
from pathlib import Path
from typing import Any

from app.core.config import settings

MAX_TABLE_ROWS = 25
LINE_WIDTH = 96  # characters used for both markdown wrapping and PDF layout

FORBIDDEN_PATTERNS = [re.compile(r"sk-[A-Za-z0-9]{8,}"), re.compile(r"/(?:home|etc|var|root|usr)/")]


def _clean(value: Any) -> str:
    text = str(value)
    for pattern in FORBIDDEN_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _fmt(value: Any, digits: int = 10) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "non-finite"
        if value == 0 or 1e-4 <= abs(value) < 1e6:
            return f"{value:.{digits}g}"
        return f"{value:.{digits}e}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v, 6) for v in list(value)[:8]) + (
            ", …" if len(value) > 8 else ""
        ) + "]"
    if isinstance(value, dict):
        return ", ".join(f"{k}={_fmt(v, 6)}" for k, v in list(value.items())[:8])
    return _clean(value)


def _table(headers: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"type": "table", "headers": headers, "rows": [[_fmt(c) for c in row] for row in rows]}


def _rendered_tokens(value: Any) -> list[str]:
    """Every string or number a reader would actually see in a block, at any depth.

    Report blocks come in four shapes (text, code, list, table) and tables nest rows of
    cells, so counting is done by walking the structure instead of by special-casing each
    type - a new block shape is then counted correctly by construction.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [repr(value)]
    if isinstance(value, dict):
        return [token for key, item in value.items() if key != "type" for token in _rendered_tokens(item)]
    if isinstance(value, (list, tuple)):
        return [token for item in value for token in _rendered_tokens(item)]
    return []


def _block_words(block: dict[str, Any]) -> int:
    return sum(len(re.findall(r"\S+", token)) for token in _rendered_tokens(block))


def build_report(
    *,
    experiment: Any,
    run: Any,
    sources: list[dict[str, Any]] | None = None,
    include_verification: bool = True,
    include_iterations: bool = True,
    notes: str | None = None,
) -> dict[str, Any]:
    """Compose the report document from ORM rows (never from model prose)."""
    payload: dict[str, Any] = dict(run.structured_output or {})
    config: dict[str, Any] = dict(run.execution_config or {})
    params: dict[str, Any] = dict(config.get("params") or experiment.input_parameters or {})
    model_block = payload.get("model") or {}
    warnings = list(payload.get("warnings") or [])
    verification = dict(run.verification or {})
    report_warnings: list[str] = []

    status = payload.get("status", run.status)
    if run.status in ("failed", "cancelled"):
        report_warnings.append(
            f"The reported run ended in status '{run.status}'; no validated numerical "
            "conclusion can be drawn from it."
        )
    elif status not in ("converged", "completed"):
        report_warnings.append(
            f"The stopping criterion was not satisfied (solver status: {status}); the "
            "numbers below are the best available iterates, not converged results."
        )
    if verification and verification.get("status") == "failed":
        report_warnings.append(
            "Independent verification FAILED for: "
            + ", ".join(verification.get("failed_checks", []))
        )
    if not sources:
        report_warnings.append(
            "No external sources were retrieved or verified for this report; the References "
            "section lists only locally curated bibliographic entries and none was checked online."
        )

    sections: list[dict[str, Any]] = []

    sections.append(
        {
            "heading": "1. Title and experiment identifier",
            "blocks": [
                {"type": "text", "text": experiment.title},
                _table(
                    ["field", "value"],
                    [
                        ["experiment_id", experiment.id],
                        ["run_id", run.id],
                        ["kind / problem_type", f"{experiment.kind} / {experiment.problem_type}"],
                        ["method", payload.get("method", experiment.method)],
                        ["run status", run.status],
                        ["generated (UTC)", run.finished_at.strftime("%Y-%m-%d %H:%M:%S")
                         if run.finished_at else "n/a"],
                    ],
                ),
            ],
        }
    )

    sections.append(
        {
            "heading": "2. Research question",
            "blocks": [
                {"type": "text",
                 "text": _research_question(experiment, payload, params)},
            ],
        }
    )

    assumptions = model_block.get("assumptions") or _method_assumptions(payload, config)
    sections.append(
        {
            "heading": "3. Model and assumptions",
            "blocks": [
                {"type": "list", "items": assumptions or ["No additional assumptions recorded."]},
                {"type": "text",
                 "text": "Model limitations (effects NOT represented):"},
                {"type": "list",
                 "items": model_block.get("limitations") or [
                     "This is a numerical method result; no physical model limitations apply beyond "
                     "the mathematical assumptions listed above."
                 ]},
            ],
        }
    )

    equations = model_block.get("governing_equations") or []
    sections.append(
        {
            "heading": "4. Governing equations",
            "blocks": ([{"type": "equation", "latex": eq} for eq in equations] or
                       [{"type": "text",
                         "text": f"Target function/expression: `{_fmt(params.get('expression'))}`"
                         if params.get("expression")
                         else "No closed governing equation is associated with this method; "
                              "the discrete algorithm is defined in section 6."}]),
        }
    )

    param_rows = [[k, _fmt(v), _units_for(params, k, config)] for k, v in sorted(params.items())
                  if not k.startswith("_")]
    sections.append(
        {
            "heading": "5. Input parameters and units",
            "blocks": [_table(["parameter", "value", "unit"], param_rows or [["-", "none", ""]])],
        }
    )

    sections.append(
        {
            "heading": "6. Numerical method",
            "blocks": [
                {"type": "text",
                 "text": _method_description(payload, config)},
                _table(
                    ["control", "value"],
                    [
                        ["tolerance", _fmt(payload.get("tolerance"))],
                        ["iteration cap", _fmt(params.get("max_iterations", "default"))],
                        ["iterations used", _fmt(payload.get("iteration_count"))],
                        ["solver", _fmt((payload.get("solver") or {}).get("scheme"))],
                        ["convergence achieved", _fmt(payload.get("convergence_achieved"))],
                    ],
                ),
            ],
        }
    )

    versions: dict[str, Any] = dict(run.software_versions or {})
    sections.append(
        {
            "heading": "7. Computational environment",
            "blocks": [
                _table(["component", "version"], [[k, _fmt(v)] for k, v in sorted(versions.items())]
                       or [["unavailable", "not recorded at run time"]]),
                {"type": "text",
                 "text": f"Wall-clock execution time: {_fmt(payload.get('execution_time_ms'))} ms "
                         f"(run record id {run.id})."},
            ],
        }
    )

    result_rows = [[k, _fmt(v)] for k, v in (payload.get("result") or {}).items()
                   if k not in {"x", "y", "predictions", "residuals", "iterations", "time", "series"}]
    result_blocks: list[dict[str, Any]] = [
        _table(["quantity", "value"], result_rows or [["no scalar result recorded", ""]])
    ]
    run_error = dict(getattr(run, "error", None) or {})
    if run_error:
        # a run that produced nothing still has to explain itself: the recorded error is
        # the finding of this report, so it is quoted rather than dropped as a status word
        detail = run_error.get("detail")
        result_blocks.append(
            _table(
                ["run error", "recorded value"],
                [
                    ["code", _fmt(run_error.get("code"))],
                    ["message", _fmt(run_error.get("message"))],
                    ["retryable", _fmt(run_error.get("retryable"))],
                    *([["detail", _fmt(detail)]] if detail else []),
                ],
            )
        )
        result_blocks.append({
            "type": "text",
            "text": (
                "No numerical result is reported because execution stopped before a value "
                "existed. The message above is the run's own recorded error, unedited."
            ),
        })
        report_warnings.append(
            f"the run's recorded error is reproduced in full: {str(run_error.get('message'))[:200]}"
        )
    residuals = ((payload.get("result") or {}).get("residuals") or {})
    if isinstance(residuals, dict) and residuals.get("count"):
        result_blocks.append(
            _table(
                ["residual statistic", "value"],
                [
                    ["count", residuals.get("count")],
                    ["RMSE", residuals.get("rmse")],
                    ["mean", residuals.get("mean_residual")],
                    ["std", residuals.get("std_residual")],
                    ["max |r|", residuals.get("max_abs_residual")],
                    ["Durbin-Watson", residuals.get("durbin_watson")],
                ],
            )
        )
    if include_iterations and payload.get("iterations"):
        records = payload["iterations"][:MAX_TABLE_ROWS]
        keys = [k for k in ("index", "a", "b", "x", "f_x", "step", "error") if any(k in r for r in records)]
        result_blocks.append(
            _table(
                keys,
                [[r.get(k) for k in keys] for r in records],
            )
        )
        if len(payload["iterations"]) > MAX_TABLE_ROWS:
            result_blocks.append(
                {"type": "text",
                 "text": f"Iteration table truncated to {MAX_TABLE_ROWS} of "
                         f"{len(payload['iterations'])} rows; full record is in the run JSON."}
            )
    if run.runtime_metadata and isinstance(run.runtime_metadata, dict) and run.runtime_metadata.get("artifact"):
        artifact = run.runtime_metadata["artifact"]
        if isinstance(artifact, dict) and artifact.get("path"):
            result_blocks.append({"type": "figure", "path": str(Path(settings.data_dir) / artifact["path"]),
                                  "caption": "Generated by Matplotlib from the stored run output."})
    sections.append({"heading": "8. Results and visualisation", "blocks": result_blocks})

    validation_blocks: list[dict[str, Any]] = []
    if payload.get("validation"):
        val = payload["validation"]
        if isinstance(val, dict):
            if val.get("checks"):
                validation_blocks.append(
                    _table(
                        ["check", "passed", "detail"],
                        [
                            [c.get("name"), "yes" if c.get("passed") else ("no" if c.get("passed") is not None else "n/a"),
                             _fmt({k: v for k, v in c.items() if k not in {"name", "passed"}})]
                            for c in val["checks"]
                        ],
                    )
                )
            if val.get("notes"):
                validation_blocks.append({"type": "list", "items": val["notes"]})
    if include_verification and verification:
        validation_blocks.append(
            _table(
                ["independent check", "result"],
                [
                    ["verifier status", verification.get("status")],
                    ["checks run", verification.get("check_count")],
                    ["failed checks", _fmt(verification.get("failed_checks"))],
                    ["inconclusive", _fmt(verification.get("inconclusive_checks"))],
                ],
            )
        )
        validation_blocks.append(
            _table(
                ["check", "passed", "detail"],
                [
                    [c.get("name"), "yes" if c.get("passed") else ("no" if c.get("passed") is not None else "n/a"),
                     _fmt({k: v for k, v in c.items() if k not in {"name", "passed", "checks"}})]
                    for c in (verification.get("checks") or [])[:10]
                ],
            )
        )
    if payload.get("error_estimate") is not None:
        validation_blocks.append(
            {"type": "text",
             "text": f"Method-reported error estimate: {_fmt(payload.get('error_estimate'))} "
                     f"against tolerance {_fmt(payload.get('tolerance'))}."}
        )
    sections.append(
        {"heading": "9. Validation and error analysis",
         "blocks": validation_blocks or [{"type": "text", "text": "No validation data was recorded."}]}
    )

    discussion = [
        _fmt(payload.get("message")),
    ]
    if notes:
        discussion.append(f"Analyst notes: {notes}")
    sections.append(
        {
            "heading": "10. Discussion",
            "blocks": [{"type": "text", "text": _wrap(" ".join(d for d in discussion if d))},
                       {"type": "text", "text": _interpretation(payload, params)}],
        }
    )

    sections.append(
        {
            "heading": "11. Limitations",
            "blocks": [
                {"type": "list",
                 "items": (model_block.get("limitations") or []) + warnings + report_warnings or [
                     "No limitations were recorded for this computation."
                 ]},
            ],
        }
    )

    reference_items = []
    for source in sources or []:
        provenance = source.get("provenance", "unknown")
        bits = [source.get("citation") or source.get("title")]
        if source.get("url"):
            bits.append(source["url"])
        bits.append(f"(provenance: {provenance})")
        reference_items.append(_fmt(" — ".join(str(b) for b in bits if b)))
    if not reference_items:
        reference_items = [
            "No sources were retrieved or verified for this report; citations must not be inferred here."
        ]
    sections.append({"heading": "12. References", "blocks": [{"type": "list", "items": reference_items}]})

    sections.append(
        {
            "heading": "13. Reproducibility",
            "blocks": [
                {"type": "code", "text": _reproduce_command(experiment, run, config)},
                _table(
                    ["item", "value"],
                    [
                        ["inputs hash", _fmt(hashlib_sha(json.dumps(params, sort_keys=True, default=str)))],
                        ["output sha256", _fmt(hashlib_sha(json.dumps(payload, sort_keys=True, default=str)))],
                        ["stored run status", run.status],
                        ["experiment status", experiment.status],
                        ["notes", _fmt(experiment.notes or "none")],
                    ],
                ),
            ],
        }
    )

    # counting only prose blocks understated the document by an order of magnitude,
    # because most of a lab report is tables and lists
    word_count = sum(_block_words(block) for section in sections for block in section["blocks"])
    # section headings are rendered too, so they belong in the count
    word_count += sum(len(re.findall(r"\S+", section["heading"])) for section in sections)
    return {
        "title": experiment.title,
        "subtitle": f"{payload.get('method', experiment.method)} · run {run.id}",
        "experiment_id": experiment.id,
        "run_id": run.id,
        "run_status": run.status,
        "verification_status": verification.get("status", "not_recorded"),
        "sections": sections,
        "warnings": report_warnings,
        "word_count": word_count,
    }


def _research_question(experiment: Any, payload: dict[str, Any], params: dict[str, Any]) -> str:
    method = payload.get("method", experiment.method)
    if experiment.kind == "simulation":
        return (
            f"Evolve the registered model '{method}' from the stated initial conditions and "
            "characterise its trajectory and summary metrics under the assumptions listed in "
            "section 3, then test the numerical solution against an available analytical reference."
        )
    if method in ("bisection", "newton_raphson", "secant"):
        return (
            f"Locate a zero of f(x) = {_fmt(params.get('expression'))} "
            + (f"on [{_fmt(params.get('a'))}, {_fmt(params.get('b'))}] " if params.get("a") is not None else "")
            + f"to a tolerance of {_fmt(payload.get('tolerance'))}, reporting the iteration history."
        )
    if method in ("trapezoidal", "simpson"):
        return (
            f"Evaluate ∫ f(x) dx over [{_fmt(params.get('a'))}, {_fmt(params.get('b'))}] for "
            f"f(x) = {_fmt(params.get('expression'))} with a verified error estimate."
        )
    if method in ("gaussian_elimination", "lu_decomposition", "jacobi", "gauss_seidel"):
        return "Solve the linear system Ax = b and quantify the residual of the computed solution."
    if method == "least_squares":
        return "Estimate the model parameters that minimise the sum of squared residuals and characterise the fit quality."
    return experiment.notes or "Perform and document the recorded computation."


def _method_assumptions(payload: dict[str, Any], config: dict[str, Any]) -> list[str]:
    from app.numerical.engine import METHODS

    method = str(payload.get("method") or config.get("method") or "")
    spec = METHODS.get(method)
    if not spec:
        return []
    return [
        f"Method: {spec['title']} ({spec['order']})",
        f"Mathematical assumptions: {spec['assumptions']}",
        "Expression evaluation uses a restricted parser; no user code is executed.",
    ]


def _method_description(payload: dict[str, Any], config: dict[str, Any]) -> str:
    from app.numerical.engine import METHODS

    method = str(payload.get("method") or config.get("method") or "")
    spec = METHODS.get(method)
    if spec:
        return (
            f"{spec['title']}: {spec['order']}. Requires {', '.join(spec['requires'])}; "
            f"accepts {', '.join(spec['optional']) or 'no further options'}. "
            f"Assumptions: {spec['assumptions']}."
        )
    solver = (payload.get("solver") or {})
    return f"Solver: {_fmt(solver.get('scheme'))} — {_fmt(solver.get('description'))}"


def _interpretation(payload: dict[str, Any], params: dict[str, Any]) -> str:
    result = payload.get("result") or {}
    if "root" in result and result.get("root") is not None:
        return (
            f"The computed root is {_fmt(result.get('root'))} with residual "
            f"{_fmt(result.get('f_root'))}. Facts: the iteration history and residual above. "
            "Interpretation: physical meaning depends on what f models, which is outside the "
            "scope of the numerical result."
        )
    if "integral" in result:
        return (
            f"The integral estimate is {_fmt(result.get('integral'))} with Richardson error estimate "
            f"{_fmt(payload.get('error_estimate'))}."
        )
    if "series" in payload:
        summary = payload.get("summary") or {}
        return "Key trajectory metrics: " + ", ".join(f"{k} = {_fmt(v)}" for k, v in list(summary.items())[:8]) + "."
    if (result.get("parameters")):
        return (
            "Fitted parameters with standard errors: "
            + ", ".join(
                f"{k} = {_fmt(v.get('value'))} ± {_fmt(v.get('standard_error'))}"
                for k, v in result["parameters"].items()
            )
            + f". R² = {_fmt(result.get('r_squared'))}."
        )
    return "See the result table above; no additional interpretation is supported by the stored data."


def _units_for(params: dict[str, Any], key: str, config: dict[str, Any] | None = None) -> str:
    """Resolve a unit from the registry metadata of the method actually executed.

    A simulation's parameters are looked up in that simulation's own spec (so a
    bisection interval end is never labelled with the pendulum's damping unit);
    numerical methods get their structural units, and anything unlabelled is
    reported as ``not declared`` rather than guessed.
    """
    from app.physics.engine import SIM_SPECS

    config = config or {}
    if str(config.get("kind", "")).lower() == "simulation":
        spec = SIM_SPECS.get(str(config.get("method", "")))
        meta = (getattr(spec, "parameters", None) or {}).get(key) if spec else None
        if isinstance(meta, dict):
            if meta.get("unit"):
                return meta["unit"]
            return "n/a (option)" if isinstance(params.get(key), str) else "dimensionless"
        return "not declared"
    units = {
        "tolerance": "-", "max_iterations": "count", "panels": "count", "n": "count",
        "n_points": "count", "iterations": "count", "a": "x-units", "b": "x-units",
        "x": "x-units", "x0": "x-units", "h": "x-units", "order": "count",
        "expression": "-", "matrix": "-", "vector": "-", "y0": "state-units",
    }
    return units.get(key, "not declared")


def _reproduce_command(experiment: Any, run: Any, config: dict[str, Any]) -> str:
    body = {
        "kind": config.get("kind", "numerical"),
        "method": config.get("method"),
        "params": {k: v for k, v in (config.get("params") or {}).items() if not k.startswith("_")},
    }
    return (
        f"curl -s -X POST \"$PLAB_API{settings.api_v1_prefix}/experiments/{experiment.id}/runs\" \\\n"
        "     -H 'Content-Type: application/json' --data @-\n\n"
        f"# request body (idempotency-key optional):\n{json.dumps(body, indent=2, default=str)}"
    )


def hashlib_sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _wrap(text: str) -> str:
    return "\n".join(textwrap.wrap(_clean(text), width=LINE_WIDTH)) or ""


def render_markdown_file(doc: dict[str, Any]) -> bytes:
    lines: list[str] = [f"# {_clean(doc['title'])}", "", f"*{_clean(doc['subtitle'])}*", ""]
    lines.append(f"Generated by PHYSICS LAB AI from stored run `{doc['run_id']}` "
                 f"(experiment `{doc['experiment_id']}`).")
    lines.append("")
    if doc.get("warnings"):
        lines.append("> **Data-quality notice**")
        for warning in doc["warnings"]:
            lines.append(f"> - {_clean(warning)}")
        lines.append("")
    for section in doc["sections"]:
        lines.append(f"## {_clean(section['heading'])}")
        lines.append("")
        for block in section["blocks"]:
            kind = block.get("type")
            if kind == "text":
                lines.append(_clean(block.get("text", "")))
                lines.append("")
            elif kind == "list":
                for item in block.get("items", []):
                    lines.append(f"- {_clean(item)}")
                lines.append("")
            elif kind == "equation":
                lines.append(f"$$\n{block.get('latex', '')}\n$$")
                lines.append("")
            elif kind == "code":
                lines.append("```bash")
                lines.append(_clean(block.get("text", "")))
                lines.append("```")
                lines.append("")
            elif kind == "figure":
                path = Path(block.get("path", ""))
                if path.exists():
                    rel = path.name
                    lines.append(f"![figure](./{rel})")
                    lines.append("")
                    lines.append(f"*{_clean(block.get('caption', ''))}*")
                    lines.append("")
                else:
                    lines.append("*(figure artifact unavailable)*")
                    lines.append("")
            elif kind == "table":
                headers = block.get("headers") or []
                rows = block.get("rows") or []
                if headers:
                    lines.append("| " + " | ".join(_clean(str(h)) for h in headers) + " |")
                    lines.append("| " + " | ".join("---" for _ in headers) + " |")
                    for row in rows:
                        cells = [_clean(str(c)).replace("|", "\\|") for c in row]
                        lines.append("| " + " | ".join(cells) + " |")
                    lines.append("")
    return "\n".join(lines).encode("utf-8")


# --- PDF rendering ------------------------------------------------------------

CHARS_PER_LINE = 104
LINES_PER_PAGE = 46


def render_pdf(doc: dict[str, Any]) -> bytes:
    """Render the report to a real, multi-page PDF using Matplotlib's PDF backend."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    import io

    pages: list[list[tuple[str, str]]] = []  # list of pages, each a list of (style, text)
    current: list[tuple[str, str]] = []

    def new_page() -> None:
        nonlocal current
        if current:
            pages.append(current)
        current = []

    def emit(style: str, text: str) -> None:
        nonlocal current
        if len(current) >= LINES_PER_PAGE:
            new_page()
        current.append((style, text))

    emit("title", _clean(doc["title"])[:120])
    emit("muted", _clean(doc["subtitle"])[:120])
    emit("muted", f"PHYSICS LAB AI report - run {doc['run_id']}")
    emit("muted", f"Verification status: {doc.get('verification_status')} | run status: {doc.get('run_status')}")
    for warning in doc.get("warnings", []):
        emit("warn", "! " + _clean(warning)[:220])
    for section in doc["sections"]:
        emit("space", "")
        emit("heading", _clean(section["heading"])[:120])
        for block in section["blocks"]:
            kind = block.get("type")
            if kind == "text":
                for line in textwrap.wrap(_clean(block.get("text", "")), width=CHARS_PER_LINE):
                    emit("body", line)
            elif kind == "list":
                for item in block.get("items", []):
                    wrapped = textwrap.wrap(_clean(str(item)), width=CHARS_PER_LINE - 4) or [""]
                    emit("body", "• " + wrapped[0])
                    for extra in wrapped[1:]:
                        emit("body", "  " + extra)
            elif kind == "equation":
                emit("mono", "$$ " + _clean(block.get("latex", ""))[:CHARS_PER_LINE])
            elif kind == "code":
                for line in _clean(block.get("text", "")).splitlines():
                    emit("mono", line[:CHARS_PER_LINE])
            elif kind == "table":
                headers = [str(h)[:22] for h in (block.get("headers") or [])]
                rows = [[str(c)[:26] for c in row] for row in (block.get("rows") or [])[:18]]
                if headers:
                    emit("table", " | ".join(headers))
                    emit("table", "-" * min(len(" | ".join(headers)), CHARS_PER_LINE))
                    for row in rows:
                        emit("table", " | ".join(row))
                remaining = (block.get("rows") or [])
                if len(remaining) > 18:
                    emit("muted", f"… {len(remaining) - 18} further row(s) omitted from the PDF; see Markdown export")
            elif kind == "figure":
                path = Path(block.get("path", ""))
                if path.exists():
                    emit("space", "")
                    emit("figure_path", str(path))
                    emit("muted", "[figure embedded above] " + _clean(block.get("caption", ""))[:80])
                    emit("space", "")
                    new_page()
    new_page()

    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        for page_number, page in enumerate(pages, start=1):
            fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
            fig.patch.set_facecolor("white")
            top, bottom, left = 0.955, 0.055, 0.075
            usable = top - bottom
            step = usable / LINES_PER_PAGE
            y = top
            for style, text in page:
                if style == "title":
                    fig.text(left, y, text, fontsize=17, fontweight="bold", color="#111118")
                    y -= step * 1.6
                elif style == "heading":
                    y -= step * 0.5
                    fig.text(left, y, text, fontsize=11.5, fontweight="bold", color="#4c1d95")
                    y -= step
                elif style == "space":
                    y -= step * 0.55
                elif style == "muted":
                    fig.text(left, y, text, fontsize=7.4, color="#555566")
                    y -= step * 0.85
                elif style == "warn":
                    fig.text(left, y, text, fontsize=8.2, color="#8f1d1d")
                    y -= step * 0.95
                elif style == "table":
                    fig.text(left, y, text, fontsize=7.0, family="DejaVu Sans Mono", color="#1b1b24")
                    y -= step * 0.8
                elif style == "mono":
                    fig.text(left, y, text, fontsize=7.2, family="DejaVu Sans Mono", color="#232332")
                    y -= step * 0.9
                elif style == "figure_path":
                    try:
                        image = mpimg.imread(text)
                        axes = fig.add_axes([left, y - 0.30, 1 - 2 * left, 0.30])
                        axes.imshow(image)
                        axes.axis("off")
                        y -= 0.315
                    except Exception:  # noqa: BLE001 - a missing figure must not kill the export
                        fig.text(left, y, "[figure could not be embedded]", fontsize=8, color="#8f1d1d")
                        y -= step
                else:
                    fig.text(left, y, text, fontsize=8.6, color="#181820")
                    y -= step
            fig.text(
                0.5,
                0.028,
                f"PHYSICS LAB AI - generated from verified run data - "
                f"page {page_number} of {len(pages)}",
                fontsize=6.5,
                ha="center",
                color="#777788",
            )
            pdf.savefig(fig)
            plt.close(fig)
        info = pdf.infodict()
        info["Title"] = _clean(doc["title"])[:180]
        info["Author"] = "PHYSICS LAB AI"
        info["Subject"] = f"Experiment {doc['experiment_id']} / run {doc['run_id']}"
    data = buffer.getvalue()
    doc["page_count"] = len(pages)
    if not data.startswith(b"%PDF"):
        raise RuntimeError("PDF renderer produced an invalid document header")
    return data
