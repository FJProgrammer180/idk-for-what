"""Scientific writer: turns verified tool output into user-facing prose.

Rules enforced here:
* every number quoted comes from a tool output that actually ran in this session;
* computed facts, model assumptions and interpretation are labelled separately;
* unresolved verification is stated as unresolved;
* mock mode is always disclosed.
"""

from __future__ import annotations

import math
from typing import Any

FACT_KEYS = (
    "root",
    "integral",
    "derivative",
    "solution",
    "r_squared",
    "max_height",
    "range_at_first_impact",
    "final_temperature",
)


def _fmt(value: Any, digits: int = 8) -> str:
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
        return "[" + ", ".join(_fmt(v, 6) for v in list(value)[:6]) + (", …" if len(value) > 6 else "") + "]"
    if isinstance(value, dict):
        return "; ".join(f"{k}={_fmt(v, 6)}" for k, v in list(value.items())[:6])
    return str(value)


def compose_answer(
    *,
    task: str,
    plan: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    provider_text: str = "",
    provider: dict[str, Any],
    warnings: list[str] | None = None,
) -> str:
    """Return markdown for the assistant message."""
    lines: list[str] = []
    ok_results = [r for r in tool_results if r.get("ok") and r.get("status") not in ("error",)]
    errors = [r for r in tool_results if not r.get("ok") or r.get("status") == "error"]
    awaiting = [r for r in ok_results if r.get("status") == "awaiting_user_input"]

    if awaiting:
        question = awaiting[-1].get("question") or "Additional information is required."
        missing = awaiting[-1].get("missing_fields") or []
        lines.append("**Clarification needed before I can compute this.**")
        lines.append("")
        lines.append(question)
        if missing:
            lines.append("")
            lines.append("Missing: " + ", ".join(f"`{m}`" for m in missing))
        why = awaiting[-1].get("why_needed")
        if why:
            lines.append("")
            lines.append(f"Why this cannot be guessed: {why}")
        defaults = awaiting[-1].get("suggested_defaults") or {}
        if defaults:
            lines.append("")
            lines.append(
                "Defaults I could use if you confirm: "
                + ", ".join(f"`{k} = {_fmt(v)}`" for k, v in defaults.items() if k != "note")
            )
        lines.append("")
        lines.append("_No numerical result was produced, because inventing the missing values would change the physics._")
        return _append_mode(_join(lines), provider)

    if not ok_results and errors:
        lines.append("**No verified result could be produced.**")
        lines.append("")
        for error in errors[:3]:
            err = error.get("error") or {}
            lines.append(
                f"- `{error.get('tool', 'tool')}` → {err.get('code', 'error')}: {err.get('message', 'unknown error')}"
            )
        lines.append("")
        lines.append(
            "Nothing was reported as completed because the required computation did not run "
            "successfully. Adjust the inputs as described above and try again."
        )
        return _append_mode(_join(lines), provider)

    primary = _primary_result(ok_results)
    if primary is None:
        lines.append(provider_text or "The tools ran but returned no reportable result.")
        return _append_mode(_join(lines), provider)

    title = primary.get("title") or _headline_title(primary)
    lines.append(f"### {title}")
    lines.append("")
    if provider_text:
        lines.append(provider_text.strip())
        lines.append("")
    lines.append("**Computed result** (from the tool run, not estimated)")
    lines.append("")
    lines.append(_facts_table(primary))
    lines.append("")

    verification = primary.get("verification") or {}
    if verification:
        status = verification.get("status", "not_recorded")
        badge = {"passed": "passed", "failed": "**FAILED**", "inconclusive": "inconclusive"}.get(
            status, str(status)
        )
        lines.append(f"**Independent verification:** {badge}")
        checks = verification.get("checks") or []
        for check in checks[:4]:
            mark = "✓" if check.get("passed") else ("?" if check.get("passed") is None else "✗")
            lines.append(f"- {mark} `{check.get('name')}` - {_fmt(check.get('detail'))}")
        failed = verification.get("failed_checks") or []
        if failed:
            lines.append(f"- failing checks: {', '.join(f'`{c}`' for c in failed)}")
        lines.append("")

    if primary.get("tool") == "run_physics_simulation":
        assumptions = primary.get("assumptions") or []
        limitations = primary.get("limitations") or []
        if assumptions:
            lines.append("**Model assumptions (declared by the registered model)**")
            lines.extend(f"- {a}" for a in assumptions)
            lines.append("")
        if limitations:
            lines.append("**Not represented by this model**")
            lines.extend(f"- {limitation}" for limitation in limitations)
            lines.append("")

    if primary.get("assumptions_applied"):
        lines.append("**Method assumptions**")
        lines.append(f"- {primary['assumptions_applied']}")
        lines.append("")

    error_estimate = primary.get("error_estimate")
    tolerance = primary.get("tolerance")
    if error_estimate is not None:
        lines.append(
            f"**Error control:** method estimate {_fmt(error_estimate)} against tolerance "
            f"{_fmt(tolerance)}; convergence criterion "
            f"{'satisfied' if primary.get('convergence_achieved') else 'NOT satisfied'}"
            + (f" (solver status `{primary.get('solver_status')}`)" if primary.get("solver_status") else "")
            + "."
        )
        lines.append("")

    warnings_all = list(warnings or []) + list(primary.get("warnings") or [])
    if warnings_all:
        lines.append("**Warnings**")
        lines.extend(f"- {w}" for w in dict.fromkeys(warnings_all))
        lines.append("")

    extra = [r for r in ok_results if r is not primary and r.get("tool") != "search_scientific_sources"]
    if extra:
        lines.append("**Other steps in this run**")
        for item in extra[:5]:
            summary = item.get("summary") or item.get("note") or item.get("message") or "completed"
            lines.append(
                f"- `{item.get('tool')}` ({item.get('status')}): {_fmt(summary) if not isinstance(summary, str) else summary[:220]}"
            )
        lines.append("")

    research = [r for r in ok_results if r.get("tool") == "search_scientific_sources"]
    if research:
        latest = research[-1]
        if latest.get("external_research_performed"):
            sources = latest.get("sources") or []
            lines.append(f"**Sources retrieved ({len(sources)})**")
            for source in sources[:5]:
                lines.append(
                    f"- {source.get('title')} — {source.get('publisher') or 'publisher unknown'} "
                    f"({source.get('url')}), retrieved {source.get('retrieved_at')}"
                )
            lines.append("")
        else:
            local = latest.get("local_references") or []
            lines.append("**Literature: no external retrieval was performed.**")
            lines.append(latest.get("reason", "no retrieval integration configured"))
            if local:
                lines.append("")
                lines.append("Curated local references (bibliographic only, not verified online):")
                for entry in local[:4]:
                    lines.append(
                        f"- {entry.get('authors')} ({entry.get('year')}), *{entry.get('title')}*, {entry.get('publisher')}."
                    )
            lines.append("")

    if plan:
        lines.append("**Execution plan actually followed**")
        for step in plan:
            mark = {"completed": "✓", "failed": "✗", "skipped": "–", "running": "…", "pending": " "}.get(
                str(step.get("status", "pending")), " "
            )
            lines.append(f"- [{mark}] {step.get('description')} → `{step.get('tool')}` ({step.get('status')})")
        lines.append("")

    lines.append(
        "_Facts above are outputs of executed tools. Assumptions and limitations are the declared "
        "scope of the selected model. Any interpretation beyond that is not supported by this run._"
    )
    return _append_mode(_join(lines), provider)


def _primary_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    priority = {
        "solve_numerical_problem": 0,
        "run_physics_simulation": 0,
        "analyze_experimental_data": 1,
        "compare_experiments": 2,
        "verify_result": 3,
        "generate_research_report": 4,
        "save_experiment": 5,
        "inspect_experiment": 6,
    }
    candidates = [r for r in results if r.get("tool") in priority]
    if not candidates:
        return results[-1] if results else None
    return sorted(candidates, key=lambda r: priority.get(r.get("tool"), 99))[0]


def _headline_title(result: dict[str, Any]) -> str:
    tool = result.get("tool")
    if tool == "solve_numerical_problem":
        return f"{result.get('method_title') or result.get('method')} result"
    if tool == "run_physics_simulation":
        return f"{result.get('title') or result.get('simulation')} simulation"
    if tool == "analyze_experimental_data":
        return "Dataset analysis"
    if tool == "generate_research_report":
        return "Report generated"
    if tool == "compare_experiments":
        return "Experiment comparison"
    if tool == "verify_result":
        return "Verification"
    return "Result"


def _facts_table(result: dict[str, Any]) -> str:
    rows: list[tuple[str, str]] = []
    tool = result.get("tool")
    if tool == "solve_numerical_problem":
        for key, value in (result.get("result") or {}).items():
            if key in {"x", "y", "predictions", "snapshots"}:
                continue
            rows.append((key, _fmt(value)))
        iterations = result.get("iterations_used")
        if iterations is None:
            iterations = result.get("iteration_count")
        rows.append(("iterations used", _fmt(iterations)))
        if result.get("iterations_truncated"):
            rows.append(("iteration records", "truncated in this view; the count above is complete"))
    elif tool == "run_physics_simulation":
        # state the inputs the model actually used: if the task's wording was not parsed
        # into a parameter, the default is what ran, and the reader must be able to see it
        used = result.get("parameters_used") or {}
        if used:
            rows.append(("parameters used", ", ".join(f"{k}={_fmt(v)}" for k, v in used.items())))
        rows.extend((k, _fmt(v)) for k, v in (result.get("summary") or {}).items())
        rows.append(("solver", _fmt((result.get("solver") or {}).get("scheme"))))
        rows.append(("time points", _fmt((result.get("plot_ready_data") or {}).get("time_points"))))
    elif tool == "analyze_experimental_data":
        dataset = result.get("dataset") or {}
        rows.extend(
            [
                ("rows", _fmt(dataset.get("rows"))),
                ("columns", _fmt(dataset.get("columns"))),
                ("missing cells", _fmt(dataset.get("total_missing_cells"))),
                ("duplicate rows", _fmt(dataset.get("duplicate_rows"))),
            ]
        )
        regression = result.get("regression") or {}
        if regression:
            rows.append(("model", _fmt(regression.get("formula"))))
            for name, value in (regression.get("parameters") or {}).items():
                rows.append((f"param {name}", _fmt(value)))
            rows.append(("R²", _fmt(regression.get("r_squared"))))
            rows.append(
                ("residual RMSE", _fmt((regression.get("residual_summary") or {}).get("rmse")))
            )
    else:
        for key, value in result.items():
            if key in {"tool", "status", "ok", "execution_time_ms", "warnings", "plan"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                rows.append((key, _fmt(value)))
    body = "\n".join(f"| {name} | {value} |" for name, value in rows if rows)
    return "| quantity | value |\n| --- | --- |\n" + body


def _join(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out).strip()


def _append_mode(text: str, provider: dict[str, Any]) -> str:
    if provider.get("is_mock"):
        return (
            text
            + "\n\n---\n"
            + "⚙ **Agent mode: `mock`** — a deterministic rule-based planner, not a language model. "
            "The numbers are real backend computations; the wording is templated from tool output. "
            "Set `PLAB_AI_PROVIDER` with a valid key for LLM-authored prose."
        )
    return text + f"\n\n---\n_Agent mode: `{provider.get('provider')}` ({provider.get('model')})._"
