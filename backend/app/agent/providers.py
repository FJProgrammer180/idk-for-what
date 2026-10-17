"""Provider-independent AI service layer.

Three implementations share one contract:

``await provider.complete(system=..., messages=[...], tools=[...]) -> Turn``

* :class:`MockProvider` - deterministic, rule-based planner + no language model at
  all.  It is *explicitly labelled* as such in every response so nobody mistakes
  it for an LLM.
* :class:`AnthropicProvider` / :class:`OpenAIProvider` - real API calls with
  structured tool use, reading keys from **server-side** environment only.

Keys never appear in a response, log line, or the browser bundle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agent.method_selector import select_method
from app.core.config import settings
from app.core.logging import get_logger
from app.tools import tool_names

logger = get_logger("physicslab.ai")


class AIConfigurationError(RuntimeError):
    """Raised when the configured provider cannot be used."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    provider: str = "mock"
    model: str = "deterministic-mock"
    usage: dict[str, int] = field(default_factory=dict)


class BaseProvider:
    name = "base"
    is_mock = False
    supports_tools = True

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_label(self) -> str:
        return "unknown"

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:  # pragma: no cover - interface
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model_label,
            "configured": True,
            "supports_structured_tools": self.supports_tools,
            "is_mock": self.is_mock,
        }


# --- mock provider -----------------------------------------------------------


class MockProvider(BaseProvider):
    """Deterministic task planner. Not a language model, and never claims to be."""

    name = "mock"
    is_mock = True
    supports_tools = True

    @property
    def model_label(self) -> str:
        return "deterministic-rule-planner-v1"

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:
        self.calls += 1
        task = _last_user_text(messages)
        history = _tool_history(messages)
        return self.plan(task, history)

    # exposed for tests: pure function of (task, completed tool results)
    def plan(self, task: str, history: list[dict[str, Any]]) -> Turn:
        selection = select_method(task)
        available = set(tool_names())
        lower = task.lower()
        wants_persist = bool(re.search(r"\b(?:save|store|record|persist|log this)\b", lower))
        wants_verify = bool(re.search(r"\b(?:verify|validate|check|confirm)\b", lower))
        wants_report = bool(re.search(r"\b(?:report|write[- ]up|document)\b", lower))
        done = {h.get("tool") for h in history}

        if history:
            last = history[-1]
            if last.get("status") == "awaiting_user_input":
                return Turn(text="", stop_reason="clarification_required", provider=self.name,
                            model=self.model_label)
            if last.get("tool") in ("solve_numerical_problem", "run_physics_simulation") and last.get("ok"):
                handle = (last.get("output") or {}).get("handle")
                if wants_verify and "verify_result" in available and "verify_result" not in done and handle:
                    return _tool_turn("verify_result", {"handle": handle}, self)
                if wants_persist and "save_experiment" in available and "save_experiment" not in done and handle:
                    return _tool_turn(
                        "save_experiment",
                        {"result_handle": handle, "title": _title_from(task)},
                        self,
                    )
                if wants_report and "generate_research_report" in available and "generate_research_report" not in done:
                    persisted = (last.get("output") or {}).get("persisted") or {}
                    args = {}
                    if persisted.get("experiment_id"):
                        args["experiment_id"] = persisted["experiment_id"]
                    if persisted.get("run_id"):
                        args["run_id"] = persisted["run_id"]
                    if not args:
                        return Turn(
                            text=(
                                "A report needs persisted records. No experiment was saved for this "
                                "task, so I did not generate one."
                            ),
                            stop_reason="end_turn",
                            provider=self.name,
                            model=self.model_label,
                        )
                    return _tool_turn("generate_research_report", args, self)
                return Turn(text="", stop_reason="plan_complete", provider=self.name,
                            model=self.model_label)
            if last.get("tool") in ("verify_result", "save_experiment"):
                if (
                    wants_report
                    and "generate_research_report" in available
                    and "generate_research_report" not in done
                    and (last.get("output") or {}).get("experiment_id")
                ):
                    return _tool_turn(
                        "generate_research_report",
                        {"experiment_id": last["output"]["experiment_id"]},
                        self,
                    )
                return Turn(text="", stop_reason="plan_complete", provider=self.name,
                            model=self.model_label)
            return Turn(text="", stop_reason="plan_complete", provider=self.name,
                        model=self.model_label)

        # --- first step: classify and either call a tool or ask for input
        if selection.category == "unclassified":
            return _tool_turn(
                "request_clarification",
                {
                    "question": (
                        "I could not classify this as a calculation, simulation, data analysis, "
                        "comparison, report or research request. Which one should it be, and what "
                        "are the governing equation or dataset involved?"
                    ),
                    "missing_fields": selection.missing,
                    "why_needed": "Choosing a numerical method requires knowing the problem type; "
                                  "guessing would silently change the mathematics.",
                    "suggested_defaults": {},
                },
                self,
            )
        if selection.missing:
            return _tool_turn(
                "request_clarification",
                {
                    "question": _clarification_question(selection),
                    "missing_fields": selection.missing,
                    "why_needed": selection.assumptions or "the selected method's preconditions are unmet",
                    "suggested_defaults": _suggestions(selection),
                },
                self,
            )
        method = selection.method
        if method in available and method in ("compare_experiments", "generate_research_report",
                                             "verify_result", "analyze_experimental_data",
                                             "search_scientific_sources"):
            args: dict[str, Any] = {}
            if method == "compare_experiments":
                args["experiment_ids"] = selection.extracted.get("experiment_ids", [])[:8]
            elif method == "generate_research_report":
                if selection.extracted.get("experiment_id"):
                    args["experiment_id"] = selection.extracted["experiment_id"]
                if selection.extracted.get("run_id"):
                    args["run_id"] = selection.extracted["run_id"]
            elif method == "verify_result":
                if selection.extracted.get("run_id"):
                    args["run_id"] = selection.extracted["run_id"]
            elif method == "analyze_experimental_data":
                args["dataset_id"] = selection.extracted.get("dataset_id")
                for key in ("x_column", "y_column", "model"):
                    if selection.extracted.get(key):
                        args[key] = selection.extracted[key]
                if selection.extracted.get("degree"):
                    args["degree"] = selection.extracted["degree"]
                if wants_persist:
                    args["persist"] = True
            else:
                args["query"] = task[:380]
            return _tool_turn(method, args, self)
        if method in ("projectile_motion", "simple_harmonic_motion", "newton_cooling",
                      "heat_1d", "dynamical_system"):
            params = dict(selection.extracted.get("parameters") or {})
            return _tool_turn(
                "run_physics_simulation",
                {
                    "simulation": method,
                    "parameters": params,
                    "persist": wants_persist,
                    "title": _title_from(task),
                },
                self,
            )
        # numeric method call
        return _tool_turn(
            "solve_numerical_problem",
            {
                "problem_type": selection.problem_type,
                "method": method,
                "parameters": selection.extracted,
                "persist": wants_persist,
                "title": _title_from(task),
            },
            self,
        )


def _tool_turn(name: str, arguments: dict[str, Any], provider: BaseProvider) -> Turn:
    return Turn(
        text="",
        tool_calls=[ToolCall(id=f"call_{provider.calls + 1}_{name}", name=name, arguments=arguments)],
        stop_reason="tool_use",
        provider=provider.name,
        model=provider.model_label,
    )


def _title_from(task: str) -> str:
    cleaned = " ".join(task.split())
    cleaned = re.sub(r"^(please |can you |could you |i want to |let's )", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(".!?")
    return (cleaned[:120] or "Agent computation").capitalize()


def _clarification_question(selection: Any) -> str:
    field_list = ", ".join(selection.missing)
    return (
        f"To run {selection.method} for this {selection.problem_type} task I need: {field_list}. "
        "What values should I use?"
    )


def _suggestions(selection: Any) -> dict[str, Any]:
    if selection.method == "bisection":
        return {"a": 0.0, "b": 2.0, "note": "any interval with a sign change; defaults are only examples"}
    if selection.method in ("newton_raphson", "secant"):
        return {"x0": 1.0}
    if selection.method == "analyze_experimental_data":
        return {"dataset_id": "dat_<id shown in the Data Studio>"}
    if selection.problem_type == "comparison":
        return {"experiment_ids": ["exp_<id>", "exp_<id>"]}
    return {}


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return str(block.get("text", ""))
    return ""


def _tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    history.append(block.get("meta") or {"tool": block.get("tool")})
    return history


# --- real providers ----------------------------------------------------------


class _HttpProvider(BaseProvider):
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        super().__init__()
        if not api_key:
            raise AIConfigurationError(
                f"{self.name} was selected but no API key is configured. Set the corresponding "
                "server-side environment variable, or run with PLAB_AI_PROVIDER=mock."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model_label(self) -> str:
        return self._model


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def __init__(self) -> None:
        super().__init__(settings.anthropic_api_key, settings.anthropic_base_url, settings.anthropic_model)

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:
        payload = {
            "model": self._model,
            "max_tokens": settings.ai_max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise AIConfigurationError(
                f"provider returned HTTP {response.status_code}: {_short_error(response.text)}"
            )
        data = response.json()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"call_{len(calls) + 1}"),
                        name=str(block.get("name")),
                        arguments=block.get("input") or {},
                    )
                )
        usage = data.get("usage") or {}
        return Turn(
            text="\n".join(p for p in text_parts if p).strip(),
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            provider=self.name,
            model=self._model,
            usage={
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            },
        )


class OpenAIProvider(_HttpProvider):
    name = "openai"

    def __init__(self) -> None:
        super().__init__(settings.openai_api_key, settings.openai_base_url, settings.openai_model)

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:
        openai_messages = [{"role": "system", "content": system}] + _to_openai_messages(messages)
        payload = {
            "model": self._model,
            "messages": openai_messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["function"]["name"],
                        "description": tool["function"]["description"],
                        "parameters": tool["function"]["parameters"],
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise AIConfigurationError(
                f"provider returned HTTP {response.status_code}: {_short_error(response.text)}"
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for item in message.get("tool_calls") or []:
            raw = ((item.get("function") or {}).get("arguments")) or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call_{len(calls) + 1}"),
                    name=str((item.get("function") or {}).get("name")),
                    arguments=arguments,
                )
            )
        usage = data.get("usage") or {}
        return Turn(
            text=(message.get("content") or "").strip(),
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            provider=self.name,
            model=self._model,
            usage={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        )


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the internal Anthropic-shaped transcript to Chat Completions."""
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        pending_tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        text: list[str] = []
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                text.append(str(block.get("text", "")))
            elif kind == "tool_use":
                pending_tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
            elif kind == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id"),
                        "content": json.dumps(block.get("content"), default=str)[:12000],
                    }
                )
        if pending_tool_calls or text:
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text) if text else None,
            }
            if pending_tool_calls:
                entry["tool_calls"] = pending_tool_calls
            out.append(entry)
        out.extend(tool_results)
    return out


def _short_error(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:200] or "no response body"


def get_provider(name: str | None = None) -> BaseProvider:
    """Resolve the configured provider. Raises AIConfigurationError when unusable."""
    provider = (name or settings.ai_provider or "mock").strip().lower()
    if provider in ("", "mock", "offline", "demo"):
        return MockProvider()
    if provider == "anthropic":
        return AnthropicProvider()
    if provider in ("openai", "azure-openai"):
        return OpenAIProvider()
    raise AIConfigurationError(
        f"unknown PLAB_AI_PROVIDER '{provider}'. Supported: mock, anthropic, openai."
    )


def provider_status() -> dict[str, Any]:
    """Non-secret provider status for the Settings page."""
    try:
        provider = get_provider()
        status = provider.status()
        status["error"] = None
    except AIConfigurationError as exc:
        status = {
            "provider": settings.ai_provider,
            "model": "unavailable",
            "configured": False,
            "supports_structured_tools": False,
            "is_mock": False,
            "error": str(exc),
        }
    status["note"] = (
        "Mock mode is a deterministic rule-based planner, not a language model. "
        "It selects and calls real registered tools; numeric results always come from the backend."
        if status.get("is_mock")
        else "Live provider: the model only chooses tools and writes prose - all numbers come "
             "from validated backend tool execution."
    )
    status["research_integration_configured"] = bool(
        settings.research_search_base_url and settings.research_search_api_key
    )
    status["allowed_tools"] = tool_names()
    return status
