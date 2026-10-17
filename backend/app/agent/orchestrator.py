"""Research orchestrator: a bounded, persisted state machine.

    queued -> planning -> running -> verifying -> completed | failed | cancelled

Statuses are only advanced when the corresponding work actually finished: a run
whose required tool call failed is reported ``failed``, never ``completed``.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.agent.method_selector import select_method
from app.agent.providers import (
    AIConfigurationError,
    BaseProvider,
    MockProvider,
    ToolCall,
    get_provider,
)
from app.agent.writer import compose_answer
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sanitize_payload, utcnow
from app.db.session import session_scope
from app.models.entities import AgentEvent, AgentSession, User
from app.services.security_limits import enforce_user_limits
from app.tools import REGISTRY, execute as execute_tool, validate_arguments
from app.tools.base import ToolContext

logger = get_logger("physicslab.agent")

STATUSES = ("queued", "planning", "running", "verifying", "completed", "failed", "cancelled")


class AgentCancelled(Exception):
    pass


@dataclass
class PlanStep:
    index: int
    tool: str
    description: str
    status: str = "pending"
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "description": self.description,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class OrchestratorResult:
    session_id: str
    status: str
    answer_markdown: str
    plan: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    provider: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


class ResearchOrchestrator:
    def __init__(self, provider: BaseProvider | None = None) -> None:
        self._provider_override = provider

    # --- event helpers -----------------------------------------------------
    def _emit(self, db, session: AgentSession, event_type: str, status: str, payload: dict[str, Any]) -> int:
        last = db.execute(
            select(AgentEvent.sequence)
            .where(AgentEvent.session_id == session.id)
            .order_by(AgentEvent.sequence.desc())
        ).scalars().first()
        sequence = int(last) + 1 if last is not None else 1
        db.add(
            AgentEvent(
                session_id=session.id,
                sequence=sequence,
                event_type=event_type,
                status=status,
                payload=sanitize_payload(payload, 6000),
            )
        )
        db.flush()
        return sequence

    def _set_status(self, db, session: AgentSession, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown agent status '{status}'")
        session.status = status
        db.flush()

    # --- main entry point --------------------------------------------------
    async def run(
        self, *, session_id: str, user_message: str, client_request_id: str | None = None
    ) -> OrchestratorResult:
        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            if session is None:
                raise LookupError("agent session not found")
            user = db.get(User, session.owner_id)
            if user is None:
                raise LookupError("session owner not found")
            project_id = session.project_id
            provider_name = session.provider
            self._emit(
                db, session, "user_message", "info",
                {"text": user_message[:4000], "client_request_id": client_request_id},
            )
            self._set_status(db, session, "queued")
            db.flush()

        limits = enforce_user_limits(user)  # reads User.limits
        context = ToolContext(user_id=user.id, session_id=session_id, project_id=project_id, limits=limits)
        selection = select_method(user_message)

        try:
            provider = self._provider_override or get_provider(provider_name)
        except AIConfigurationError as exc:
            with session_scope() as db:
                session = db.get(AgentSession, session_id)
                self._emit(db, session, "provider_error", "error", {"message": str(exc)})
                self._set_status(db, session, "failed")
            return OrchestratorResult(
                session_id=session_id,
                status="failed",
                answer_markdown=(
                    "### AI provider is not usable\n\n"
                    f"{exc}\n\nThe agent did not run and no computation was performed. "
                    "Set `PLAB_AI_PROVIDER=mock` for deterministic offline operation, or configure "
                    "a provider key server-side."
                ),
                provider={"provider": provider_name, "is_mock": False, "model": "unavailable"},
                error={"code": "provider_not_configured", "message": str(exc)},
            )

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        tool_results: list[dict[str, Any]] = []
        plan: list[PlanStep] = []
        started = time.perf_counter()
        provider_status = provider.status()
        usage_total = {"input_tokens": 0, "output_tokens": 0, "provider_calls": 0}
        transcript: list[dict[str, Any]] = []  # provider-shaped history (with tool_result blocks)
        final_text = ""
        status = "failed"
        error_payload: dict[str, Any] | None = None
        signature_seen: dict[str, int] = {}

        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            self._set_status(db, session, "planning")
            self._emit(
                db,
                session,
                "classification",
                "info",
                {
                    "category": selection.category,
                    "problem_type": selection.problem_type,
                    "proposed_method": selection.method,
                    "rationale": selection.rationale,
                    "assumptions": selection.assumptions,
                    "missing_inputs": selection.missing,
                    "extracted": sanitize_payload(selection.extracted, 2000),
                    "provider": provider_status,
                },
            )

        max_steps = int(limits["max_agent_steps"])
        max_tool_calls = int(limits["max_agent_tool_calls"])
        calls_used = 0
        step = 0

        try:
            while step < max_steps:
                step += 1
                _check_cancelled(session_id)
                turn = await self._provider_turn(provider, messages, plan, selection, session_id)
                usage_total["provider_calls"] += 1
                usage_total["input_tokens"] += int(turn.usage.get("input_tokens", 0))
                usage_total["output_tokens"] += int(turn.usage.get("output_tokens", 0))

                if turn.stop_reason == "clarification_required":
                    status = "completed"
                    break

                if not turn.tool_calls:
                    final_text = turn.text or final_text
                    if turn.stop_reason == "plan_complete" or not tool_results:
                        status = "completed" if tool_results or turn.text else "failed"
                        break
                    status = "completed"
                    break

                assistant_blocks: list[dict[str, Any]] = []
                if turn.text:
                    assistant_blocks.append({"type": "text", "text": turn.text})
                for call in turn.tool_calls:
                    assistant_blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    )
                transcript.append({"role": "assistant", "content": assistant_blocks})

                result_blocks: list[dict[str, Any]] = []
                for call in turn.tool_calls[:4]:
                    if calls_used >= max_tool_calls:
                        with session_scope() as db:
                            session = db.get(AgentSession, session_id)
                            self._emit(
                                db, session, "tool_budget_exhausted", "error",
                                {"limit": max_tool_calls, "pending_tool": call.name},
                            )
                        status = "failed"
                        error_payload = {
                            "code": "tool_budget_exhausted",
                            "message": f"the {max_tool_calls}-tool-call budget for this session was reached",
                        }
                        break
                    calls_used += 1
                    outcome = await self._execute_tool(call, context, session_id, plan)
                    tool_results.append(outcome)
                    result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": outcome.get("output", outcome.get("error")),
                            "meta": {
                                "tool": outcome["tool"],
                                "status": outcome.get("status"),
                                "ok": bool(outcome.get("ok")),
                                "output": _compact_for_model(outcome),
                            },
                        }
                    )
                    signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)[:400]}"
                    signature_seen[signature] = signature_seen.get(signature, 0) + 1
                    if signature_seen[signature] >= 2:
                        with session_scope() as db:
                            session = db.get(AgentSession, session_id)
                            self._emit(
                                db, session, "loop_guard", "warning",
                                {"message": f"repeated identical call to '{call.name}'; halting",
                                 "signature": signature[:200]},
                            )
                transcript.append({"role": "user", "content": result_blocks})
                messages = [
                    {"role": "user", "content": user_message},
                    *transcript,
                ]

                if error_payload:
                    break
                if all(not r.get("ok") for r in tool_results) and tool_results:
                    status = "failed"
                    break
        except AgentCancelled:
            status = "cancelled"
            with session_scope() as db:
                session = db.get(AgentSession, session_id)
                self._emit(db, session, "cancelled", "warning", {"at_step": step})
        except AIConfigurationError as exc:
            status = "failed"
            error_payload = {"code": "provider_error", "message": str(exc)}
            with session_scope() as db:
                session = db.get(AgentSession, session_id)
                self._emit(db, session, "provider_error", "error", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrator crashed")
            status = "failed"
            error_payload = {
                "code": "orchestrator_error",
                "message": f"{type(exc).__name__} while coordinating this task",
            }
            with session_scope() as db:
                session = db.get(AgentSession, session_id)
                self._emit(db, session, "orchestrator_error", "error",
                           {"type": type(exc).__name__})

        # --- verification stage (never skipped silently) --------------------
        verification_summary = _verification_from(tool_results)
        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            self._set_status(db, session, "verifying")
            self._emit(
                db,
                session,
                "verification",
                "info" if verification_summary.get("status") != "failed" else "warning",
                verification_summary,
            )

        if status == "completed" and tool_results and any(
            (r.get("status") == "error") for r in tool_results
        ) and not any(r.get("ok") for r in tool_results):
            status = "failed"

        answer = compose_answer(
            task=user_message,
            plan=[p.to_dict() for p in plan],
            tool_results=tool_results,
            provider_text=final_text,
            provider=provider_status,
            warnings=[f"agent status: {status}"] if status not in ("completed",) else None,
        )

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            self._set_status(db, session, status)
            session.plan = [p.to_dict() for p in plan]
            session.usage = {**usage_total, "tool_calls": calls_used, "duration_ms": duration_ms}
            self._emit(
                db,
                session,
                "assistant_message",
                "info" if status == "completed" else status,
                {
                    "text": answer[:12000],
                    "plan": [p.to_dict() for p in plan],
                    "verification": verification_summary,
                    "duration_ms": duration_ms,
                    "provider": provider_status,
                    "error": error_payload,
                },
            )
            db.commit()

        with session_scope() as db:
            events = db.execute(
                select(AgentEvent)
                .where(AgentEvent.session_id == session_id)
                .order_by(AgentEvent.sequence)
            ).scalars().all()
            payload_events = [
                {
                    "sequence": e.sequence,
                    "event_type": e.event_type,
                    "status": e.status,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "payload": e.payload,
                }
                for e in events
            ]

        return OrchestratorResult(
            session_id=session_id,
            status=status,
            answer_markdown=answer,
            plan=[p.to_dict() for p in plan],
            tool_results=tool_results,
            events=payload_events,
            usage={**usage_total, "tool_calls": calls_used, "duration_ms": duration_ms},
            provider=provider_status,
            error=error_payload,
        )

    # --- internals ---------------------------------------------------------
    async def _provider_turn(
        self,
        provider: BaseProvider,
        messages: list[dict[str, Any]],
        plan: list[PlanStep],
        selection: Any,
        session_id: str,
    ):
        tools = [spec.provider_definition(provider.name if provider.name != "mock" else "anthropic")
                 for spec in REGISTRY.values()]
        system = _system_prompt(provider, selection)
        timeout = max(10.0, settings.ai_timeout_seconds)
        attempts = 0
        last_error: Exception | None = None
        while attempts <= settings.max_provider_retries:
            attempts += 1
            try:
                return await asyncio.wait_for(
                    provider.complete(system=system, messages=messages, tools=tools), timeout=timeout
                )
            except asyncio.TimeoutError as exc:
                last_error = AIConfigurationError(
                    f"the AI provider did not answer within {timeout:g}s (attempt {attempts})"
                )
            except AIConfigurationError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider transport failure
                last_error = AIConfigurationError(
                    f"provider call failed: {type(exc).__name__} (attempt {attempts})"
                )
            if attempts > settings.max_provider_retries:
                break
            await asyncio.sleep(0.2 * attempts)
        assert last_error is not None
        raise last_error

    async def _execute_tool(
        self, call: ToolCall, context: ToolContext, session_id: str, plan: list[PlanStep]
    ) -> dict[str, Any]:
        step = PlanStep(index=len(plan) + 1, tool=call.name, description=_describe(call))
        plan.append(step)
        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            self._set_status(db, session, "running")
            self._emit(
                db,
                session,
                "tool_call",
                "running",
                {"tool": call.name, "arguments": call.arguments, "call_id": call.id},
            )
        # validation failure must be visible and must not execute anything
        try:
            validate_arguments(call.name, call.arguments)
        except Exception as exc:  # noqa: BLE001 - ToolError path returns structured error
            outcome = {
                "tool": call.name,
                "ok": False,
                "status": "error",
                "error": {"code": "tool_call_rejected_before_execution", "message": str(exc)},
            }
            step.status = "failed"
            step.detail = "arguments rejected by schema validation"
            with session_scope() as db:
                session = db.get(AgentSession, session_id)
                self._emit(db, session, "tool_validation", "error", outcome["error"])
            return outcome

        limit = float(min(settings.compute_timeout_seconds * 3, 120.0))
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(execute_tool, call.name, call.arguments, context), timeout=limit
            )
        except asyncio.TimeoutError:
            output = {
                "tool": call.name,
                "ok": False,
                "status": "error",
                "error": {"code": "tool_timeout", "message": f"'{call.name}' exceeded {limit:g}s"},
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool dispatch error")
            output = {
                "tool": call.name,
                "ok": False,
                "status": "error",
                "error": {"code": "tool_dispatch_error", "message": f"{type(exc).__name__}"},
            }

        ok = bool(output.get("ok"))
        step.status = "completed" if ok else "failed"
        step.detail = (output.get("message") or (output.get("error") or {}).get("code") or "")[:300]
        if ok and output.get("persisted"):
            step.detail = (step.detail + f" persisted as {output['persisted'].get('experiment_id')}").strip()
        with session_scope() as db:
            session = db.get(AgentSession, session_id)
            self._emit(
                db,
                session,
                "tool_result",
                "completed" if ok else "error",
                {
                    "tool": call.name,
                    "call_id": call.id,
                    "status": output.get("status"),
                    "ok": ok,
                    "execution_time_ms": output.get("execution_time_ms"),
                    "verification": output.get("verification"),
                    "error": output.get("error"),
                    "headline": _headline_output(output),
                },
            )
        return {"tool": call.name, "arguments": call.arguments, "output": output, **_flatten(output, ok)}


def _flatten(output: dict[str, Any], ok: bool) -> dict[str, Any]:
    keys = (
        "status", "handle", "method", "method_title", "problem_type", "result", "simulation",
        "title", "summary", "error_estimate", "tolerance", "convergence_achieved",
        "solver_status", "warnings", "message", "verification", "validation", "assumptions",
        # the convergence evidence must survive the flattening, or the answer can only
        # say "iterations used: unavailable" while the number sits one key away
        "iterations_used", "iteration_count", "iterations_truncated", "iteration_table_preview",
        "limitations", "assumptions_applied", "persisted", "external_research_performed",
        # which columns a defaulted fit actually used must stay visible in the response,
        # as must the parameter set a simulation actually ran with
        "fit_columns", "parameters_used", "initial_conditions", "downsampled", "notes",
        "sources", "local_references", "dataset", "regression", "quality_flags", "report_id",
        "artifacts", "experiments", "question", "missing_fields", "why_needed",
        "suggested_defaults", "plot_ready_data", "solver", "units", "governing_equations",
    )
    flat = {"ok": ok}
    for key in keys:
        if key in output:
            flat[key] = output[key]
    if not ok:
        flat["error"] = output.get("error")
    return flat


def _headline_output(output: dict[str, Any]) -> dict[str, Any]:
    result = output.get("result")
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool, type(None)))}
    if output.get("persisted"):
        return {"persisted": output["persisted"]}
    if output.get("report_id"):
        return {"report_id": output["report_id"]}
    return {}


def _compact_for_model(outcome: dict[str, Any]) -> dict[str, Any]:
    """Trim a tool result down to what the next model step needs."""
    output = outcome.get("output") or {}
    keep = (
        "status", "ok", "handle", "method", "convergence_achieved", "solver_status", "result",
        "error_estimate", "message", "warnings", "verification", "persisted", "report_id",
        "artifacts", "error", "question", "missing_fields", "external_research_performed",
        "local_references", "summary", "validation", "reason",
    )
    compact = {key: output.get(key) for key in keep if key in output}
    if isinstance(compact.get("result"), dict):
        compact["result"] = {
            k: v
            for k, v in compact["result"].items()
            if not isinstance(v, list) or len(v) < 30
        }
    return _finite(compact)


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value[:40]]
    return value


def _verification_from(results: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    worst = "not_applicable"
    rank = {"passed": 3, "inconclusive": 2, "not_recorded": 1, "failed": 0}
    for result in results:
        verification = result.get("verification")
        if isinstance(verification, dict) and verification.get("status"):
            status = verification["status"]
            checks.append(
                {
                    "tool": result.get("tool"),
                    "status": status,
                    "failed_checks": verification.get("failed_checks", []),
                    "check_count": verification.get("check_count"),
                }
            )
            if rank.get(status, 1) < rank.get(worst, 4) or worst == "not_applicable":
                worst = status if worst == "not_applicable" or rank[status] < rank[worst] else worst
    if not checks:
        return {"status": "not_applicable", "checks": [], "note": "no tool in this run produced a verification record"}
    return {"status": worst, "checks": checks}


def _describe(call: ToolCall) -> str:
    spec = REGISTRY.get(call.name)
    label = spec.category if spec else "general"
    args = call.arguments if isinstance(call.arguments, dict) else {}
    if call.name == "solve_numerical_problem":
        return f"Solve a {args.get('problem_type', 'numerical')} problem with {args.get('method', 'a selected method')} [{label}]"
    if call.name == "run_physics_simulation":
        return f"Integrate the {args.get('simulation', 'physics')} model [{label}]"
    if call.name == "analyze_experimental_data":
        return f"Profile and fit dataset {str(args.get('dataset_id'))[:16]} [{label}]"
    if call.name == "save_experiment":
        return f"Persist {args.get('result_handle', 'the result')} with reproduction metadata [{label}]"
    if call.name == "verify_result":
        return f"Independently re-check {args.get('run_id') or args.get('handle')} [{label}]"
    if call.name == "compare_experiments":
        return f"Compare {len(args.get('experiment_ids') or [])} stored experiments [{label}]"
    if call.name == "search_scientific_sources":
        return f"Search scientific sources for “{str(args.get('query', ''))[:60]}” [{label}]"
    if call.name == "generate_research_report":
        return f"Generate a report from run/experiment records [{label}]"
    if call.name == "request_clarification":
        return "Ask the user for the inputs that cannot be inferred safely"
    return f"Call {call.name} [{label}]"


def _system_prompt(provider: BaseProvider, selection: Any) -> str:
    mode = "MOCK (deterministic rule planner, no language model)" if isinstance(provider, MockProvider) else "LIVE"
    return (
        "You are the research coordinator inside PHYSICS LAB AI, a computational physics "
        "workstation. Python code in the backend performs every numerical result; you may only "
        "use the registered tools.\n\n"
        "Hard rules:\n"
        "1. Never state a number that was not returned by a tool call in this session. If a tool "
        "failed, say so and report the failure - do not substitute an estimate.\n"
        "2. Never claim to have executed, retrieved or verified something you did not. If "
        "search_scientific_sources reports that no external retrieval happened, say exactly that.\n"
        "3. If required physical inputs are missing (bracket, damping, medium, units), call "
        "request_clarification instead of assuming values.\n"
        "4. Separate computed facts from model assumptions and from interpretation; name the "
        "assumptions of the selected method or simulation.\n"
        "5. Prefer a verified stored result over a fresh computation when one exists.\n"
        "6. Report convergence honestly: max_iterations_reached or target_accuracy_not_met are "
        "failures of the stopping criterion, not successes.\n\n"
        f"Agent execution mode: {mode}.\n"
        f"Pre-classification from the backend selector (advisory): {selection.to_dict()}\n"
        "Tool catalog: " + ", ".join(sorted(REGISTRY)) + "."
    )


def _check_cancelled(session_id: str) -> None:
    with session_scope() as db:
        session = db.get(AgentSession, session_id)
        if session is not None and session.status == "cancelled":
            raise AgentCancelled()


def cancel_session(session_id: str) -> tuple[bool, str]:
    with session_scope() as db:
        session = db.get(AgentSession, session_id)
        if session is None:
            return False, "not_found"
        if session.status in ("completed", "failed", "cancelled"):
            return False, f"already_finished:{session.status}"
        session.status = "cancelled"
        return True, "cancellation_requested"


def session_events(session_id: str, after: int = 0) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(AgentEvent)
            .where(AgentEvent.session_id == session_id, AgentEvent.sequence > after)
            .order_by(AgentEvent.sequence)
        ).scalars().all()
        return [
            {
                "sequence": r.sequence,
                "event_type": r.event_type,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "payload": r.payload,
            }
            for r in rows
        ]
