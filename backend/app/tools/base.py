"""Tool registry: schema-driven validation + bounded execution.

Every agent-callable tool is declared here with a Pydantic input model, so the
same schema powers (a) the provider's structured tool definition, (b) request
validation before anything runs, and (c) the documentation surface.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.logging import get_logger
from app.core.security import sanitize_payload

logger = get_logger("physicslab.tools")


class ToolError(RuntimeError):
    """Structured tool failure that is safe to show to the model and the user."""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class ToolContext:
    user_id: str
    session_id: str | None = None
    project_id: str | None = None
    scratchpad: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)

    def remember(self, key: str, payload: Any) -> None:
        self.scratchpad[key] = payload


# The manifest is grouped by these labels in the UI and in /agent/tools, so a typo would
# silently create a category nothing renders.  Declaring them makes that a startup error.
CATEGORIES: tuple[str, ...] = (
    "computation",   # runs a numerical method
    "simulation",    # runs a registered physics model
    "analysis",      # profiles or fits stored data
    "verification",  # independent re-checks of a result
    "persistence",   # writes experiments or runs
    "reporting",     # builds documents from stored rows
    "research",      # source lookup (honest about whether retrieval happened)
    "interaction",   # asks the user for what is missing
    "records",       # reads stored experiments and runs without writing
)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]
    category: str = "general"
    requires_user: bool = True
    read_only: bool = False
    max_output_chars: int = 24_000

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("$defs", None)
        return schema

    def provider_definition(self, provider: str = "anthropic") -> dict[str, Any]:
        if provider == "openai":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.json_schema(),
                },
            }
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.json_schema(),
        }


REGISTRY: dict[str, ToolSpec] = {}


def register(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    category: str = "general",
    read_only: bool = False,
):
    def decorator(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        if read_only and "persist" in getattr(input_model, "model_fields", {}):
            # `read_only` is what the manifest promises the model and the UI; a tool that
            # can store an experiment and run is not read-only, whatever its default is
            raise ValueError(
                f"tool '{name}' accepts persist=True, so it must not be declared read_only"
            )
        if category not in CATEGORIES:
            raise ValueError(
                f"tool '{name}' declared category '{category}', which is not one of "
                f"{', '.join(CATEGORIES)}"
            )
        REGISTRY[name] = ToolSpec(
            name=name,
            description=" ".join(description.split()),
            input_model=input_model,
            handler=fn,
            category=category,
            read_only=read_only,
        )
        return fn

    return decorator


def tool_names() -> list[str]:
    return sorted(REGISTRY)


def build_model(name: str, arguments: Any) -> tuple[BaseModel, dict[str, Any]]:
    """Return ``(validated_model_instance, normalised_dict)``."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(
            "unknown_tool",
            f"tool '{name}' is not registered. Available: {', '.join(tool_names())}",
        )
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise ToolError(
                "malformed_arguments", "tool arguments were not valid JSON", details=arguments[:200]
            ) from None
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("malformed_arguments", "tool arguments must be a JSON object")
    try:
        model = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolError(
            "argument_validation_failed",
            f"arguments for '{name}' did not satisfy the tool schema",
            details=[
                {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                for err in exc.errors()[:8]
            ],
        ) from None
    return model, model.model_dump(exclude_none=True)


def validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    """Validate/normalise model-supplied arguments; raise ToolError on rejection."""
    return build_model(name, arguments)[1]


def execute(
    name: str, arguments: Any, context: ToolContext, *, timeout_seconds: float | None = None
) -> dict[str, Any]:
    """Validate, execute and envelope a single tool call."""
    started = time.perf_counter()
    spec = REGISTRY.get(name)
    try:
        if spec is None:
            raise ToolError(
                "unknown_tool",
                f"tool '{name}' is not registered; the agent may only call declared tools",
                details={"registered": tool_names()},
            )
        model, clean = build_model(name, arguments)
        result = spec.handler(model, context)
        if not isinstance(result, dict):
            raise ToolError("bad_tool_output", f"tool '{name}' did not return an object")
        result.setdefault("tool", name)
        elapsed = (time.perf_counter() - started) * 1000
        result.setdefault("execution_time_ms", round(elapsed, 3))
        result.setdefault("ok", str(result.get("status", "completed")) in ("completed", "ok"))
        return _bound(result, spec)
    except ToolError as exc:
        logger.info("tool rejected", extra={"event": "tool_error", "session_id": context.session_id})
        return {
            "tool": name,
            "ok": False,
            "status": "error",
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": sanitize_payload(exc.details, 1200),
            },
            "execution_time_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except ValidationError as exc:  # pragma: no cover - defensive
        return {
            "tool": name,
            "ok": False,
            "status": "error",
            "error": {"code": "argument_validation_failed", "message": str(exc)[:600]},
        }
    except Exception as exc:  # noqa: BLE001 - tool crash must not escape to the model raw
        logger.exception("tool crashed")
        return {
            "tool": name,
            "ok": False,
            "status": "error",
            "error": {
                "code": "tool_execution_error",
                "message": f"{type(exc).__name__} while executing '{name}'",
                "detail": sanitize_payload(f"{type(exc).__name__}: {exc}", 400),
            },
        }


def _bound(result: dict[str, Any], spec: ToolSpec) -> dict[str, Any]:
    """Keep a single tool output from flooding the model context."""
    encoded = json.dumps(result, default=str)
    if len(encoded) <= spec.max_output_chars:
        return result
    trimmed: dict[str, Any] = {
        "tool": spec.name,
        "ok": True,
        "status": "completed_truncated",
        "truncation": {
            "reason": f"output exceeded {spec.max_output_chars} characters",
            "original_chars": len(encoded),
        },
        "result": {
            key: _shrink(value)
            for key, value in result.items()
            if key not in {"iterations", "series", "samples", "rows"}
        },
    }
    for key in ("iterations", "series", "rows"):
        if isinstance(result.get(key), list):
            trimmed["preview"] = {key: [ _shrink(v) for v in result[key][:15]]}  # noqa: E231
            trimmed.setdefault("counts", {})[key] = len(result[key])
    return trimmed


def _shrink(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "…"
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _shrink(v, depth + 1) for k, v in list(value.items())[:24]}
    if isinstance(value, (list, tuple)):
        head = [_shrink(v, depth + 1) for v in list(value)[:12]]
        if len(value) > 12:
            head.append(f"… {len(value) - 12} more")
        return head
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


class ToolInput(BaseModel):
    """Base config: reject unknown fields so the model cannot smuggle extras."""

    model_config = ConfigDict(extra="forbid")
