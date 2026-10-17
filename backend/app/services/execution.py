"""Bounded execution of registered scientific jobs.

This is the *only* place where computation happens, and it never executes
arbitrary code: a job is a (kind, method, params) triple resolved against the
registries in :mod:`app.numerical.engine` and :mod:`app.physics.engine`.
"""

from __future__ import annotations

import math
import time
from typing import Any

from app.core.config import settings
from app.numerical.common import NumericalInputError, software_versions
from app.numerical.engine import run_numerical_method
from app.physics.engine import run_simulation
from app.services import datasets as dataset_service
from app.services import analysis as regression_bridge

KINDS = ("numerical", "simulation", "analysis")


def _finite_guard(payload: Any, path: str = "$") -> list[str]:
    """Collect non-finite floats so they can be replaced explicitly, never hidden."""
    issues: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            issues += _finite_guard(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            issues += _finite_guard(value, f"{path}[{i}]")
    elif isinstance(payload, float) and not math.isfinite(payload):
        issues.append(f"{path} is {payload}")
    return issues


def sanitise_for_storage(payload: Any) -> Any:
    """NaN/Inf are not valid JSON - represent them explicitly as null + marker."""
    if isinstance(payload, dict):
        return {str(k): sanitise_for_storage(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [sanitise_for_storage(v) for v in payload]
    if isinstance(payload, float):
        if math.isnan(payload):
            return None
        if math.isinf(payload):
            return None
        return payload
    if isinstance(payload, (int, bool, str)) or payload is None:
        return payload
    return str(payload)


def perform_job(spec: dict[str, Any], *, owner_id: str | None = None) -> dict[str, Any]:
    """Run one job and return a normalised, storage-safe result payload."""
    kind = str(spec.get("kind", "numerical"))
    method = str(spec.get("method", ""))
    params = dict(spec.get("params") or {})
    options = dict(spec.get("options") or {})
    started = time.perf_counter()
    if kind not in KINDS:
        raise NumericalInputError(
            f"job kind '{kind}' is not supported (expected one of {', '.join(KINDS)})"
        )

    if kind == "numerical":
        from app.services.security_limits import account_iteration_cap

        # the account limit is a bound, not a suggestion: a stored spec asking for more
        # is reduced and the reduction is recorded in the payload
        from app.numerical.engine import accepted_parameters

        cap, cap_record = account_iteration_cap(owner_id, params.get("max_iterations"))
        if "max_iterations" in accepted_parameters(method):
            params["max_iterations"] = cap
        elif "max_iterations" in params:
            params.pop("max_iterations")  # a direct solver has no budget to bound
            cap_record["applies"] = False
            cap_record["note"] = f"'{method}' is a direct method: no iteration budget applies"
        result = run_numerical_method(method, params)
        payload = result.to_dict(max_iterations=int(options.get("max_iteration_records", 200)))
        payload["inputs"] = _redact_params(params)
        payload["method"] = method
        payload["limits_applied"] = cap_record
        if cap_record.get("clamped") and cap_record.get("applies", True):
            payload.setdefault("warnings", []).append(
                f"max_iterations was reduced from {cap_record['requested']} to {cap} "
                f"({cap_record['source']} ceiling); the reported state describes the bounded run"
            )
    elif kind == "simulation":
        result = run_simulation(method, params)
        payload = result
        raw_status = str(result.get("status", "completed"))
        integrated = raw_status in ("completed", "converged")
        payload["inputs"] = _redact_params(params)
        payload["method"] = method
        payload["status"] = "completed" if integrated else raw_status
        payload["iterations"] = []
        payload["error_estimate"] = None
        payload["convergence_achieved"] = integrated
        payload["metadata"] = {"simulation": method, "solver_reported_status": raw_status}
        summary = payload.get("summary") or {}
        payload["result"] = {
            "summary": summary,
            "validation": payload.get("validation"),
            "solver": payload.get("solver"),
            "units": payload.get("units"),
            "final_state": (payload.get("series") or {}).get("final_state"),
            "model_parameters": (payload.get("model") or {}).get("parameters"),
            "assumptions": (payload.get("model") or {}).get("assumptions"),
            "limitations": (payload.get("model") or {}).get("limitations"),
            "governing_equations": (payload.get("model") or {}).get("governing_equations"),
        }
    elif kind == "analysis":
        payload = regression_bridge.run_analysis_job(params, owner_id=owner_id)
        payload["inputs"] = _redact_params(params)
        payload["method"] = method or "dataset_analysis"
    else:  # pragma: no cover
        raise NumericalInputError("unreachable job kind")

    issues = _finite_guard(payload)
    payload = sanitise_for_storage(payload)
    payload["execution_time_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    payload["software_versions"] = software_versions()
    payload["non_finite_fields"] = issues[:20]
    payload["experiment_id"] = spec.get("experiment_id")
    payload["job_kind"] = kind
    if issues:
        payload.setdefault("warnings", []).append(
            f"{len(issues)} non-finite value(s) were detected and replaced with null: "
            + "; ".join(issues[:5])
        )
    return payload


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    """Store the inputs needed for reproduction; never store secrets."""
    safe: dict[str, Any] = {}
    for key, value in params.items():
        lowered = str(key).lower()
        if any(tok in lowered for tok in ("key", "token", "password", "secret")):
            safe[key] = "[redacted]"
            continue
        if isinstance(value, (list, tuple)):
            if len(value) > 60:
                safe[key] = {
                    "type": "array",
                    "length": len(value),
                    "note": "array omitted from stored inputs for size; see dataset reference",
                }
            else:
                safe[key] = sanitise_for_storage(list(value))
        elif isinstance(value, str) and len(value) > 400:
            safe[key] = value[:400] + "…"
        else:
            safe[key] = sanitise_for_storage(value)
    return safe


def dataset_preview(dataset_id: str, owner_id: str) -> dict[str, Any]:
    return dataset_service.profile_dataset(dataset_id, owner_id)
