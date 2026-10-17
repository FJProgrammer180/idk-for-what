"""Pydantic request/response models shared across routers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_more: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    time: str


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, Any]
    duration_ms: float


# --- auth --------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=512)
    display_name: str = Field(default="Researcher", min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        from app.core.security import valid_email

        if not valid_email(v):
            raise ValueError("must be a valid email address")
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _password(cls, v: str, info) -> str:
        from app.core.security import password_policy_error

        email = (info.data or {}).get("email")
        error = password_policy_error(v, email)
        if error:
            raise ValueError(error)
        return v


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_demo: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class SessionResponse(BaseModel):
    user: UserPublic
    expires_in_seconds: int
    csrf_token: str
    auth_mode: Literal["cookie", "bearer"] = "cookie"


class PasswordChangeRequest(BaseModel):
    # the current password is only length-checked, so a policy change can never lock
    # an existing account out of its own keyring
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)

    @field_validator("new_password")
    @classmethod
    def _new(cls, v: str, info) -> str:
        from app.core.security import password_policy_error

        error = password_policy_error(v)
        if error:
            raise ValueError(error)
        return v


class ProfileUpdateRequest(BaseModel):
    """Only the fields the account actually owns.

    ``max_iterations`` is a per-account compute ceiling; everything harder (agent step
    budgets, upload size) is server policy and is therefore not accepted here.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    retention_days: int | None = Field(
        default=None, ge=0, le=3650, description="0 keeps runs forever"
    )
    max_iterations: int | None = Field(
        default=None, ge=1, le=100000, description="per-account ceiling, clamped to server policy"
    )


# --- lab ---------------------------------------------------------------------

ProblemType = Literal[
    "root_finding", "linear_system", "integration", "derivative", "regression", "ode_ivp"
]
JobKind = Literal["numerical", "simulation", "analysis"]


class ComputeRequest(BaseModel):
    """Synchronous, non-persisting computation (explicitly labelled ephemeral)."""

    kind: JobKind = Field(default="numerical")
    method: str = Field(min_length=2, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    tolerance: float | None = Field(default=None, gt=0, le=1e-1)
    max_iterations: int | None = Field(default=None, ge=1, le=5000)
    generate_plot: bool = True

    @field_validator("method")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    is_archived: bool | None = None


class ProjectOut(ORM):
    id: str
    name: str
    description: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    experiment_count: int = 0


class ExperimentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    kind: JobKind = "numerical"
    problem_type: str = Field(default="root_finding", min_length=3, max_length=40)
    method: str = Field(min_length=2, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    notes: str = Field(default="", max_length=4000)
    template_key: str | None = Field(default=None, max_length=64)


class ExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    parameters: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    project_id: str | None = None


class TemplateInstantiateRequest(BaseModel):
    """Overrides applied on top of a bundled experiment template.

    Only the descriptive fields and the parameter values may be changed: the template's
    method and problem type stay fixed so the run remains comparable with the others
    built from the same template.
    """

    title: str | None = Field(default=None, min_length=3, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    project_id: str | None = Field(default=None, max_length=36)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    parameters: dict[str, Any] | None = Field(
        default=None, description="Override the stored inputs for this run only."
    )
    wait: bool = Field(default=True, description="Block until the job reaches a terminal state.")
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=80)
    generate_plot: bool = True


class RunOut(BaseModel):
    run_id: str
    experiment_id: str
    status: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    execution_time_ms: float | None = None
    execution_config: dict[str, Any] = Field(default_factory=dict)
    structured_output: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    software_versions: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    cancel_requested: bool = False


class ExperimentOut(BaseModel):
    id: str
    title: str
    kind: str
    problem_type: str
    method: str
    status: str
    project_id: str | None
    notes: str
    template_key: str | None
    input_parameters: dict[str, Any]
    result_metadata: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    run_count: int = 0
    latest_run: RunOut | None = None


# --- agent -------------------------------------------------------------------


class MethodRecommendRequest(BaseModel):
    """Ask the deterministic planner which registered method fits a task."""

    task: str = Field(min_length=3, max_length=6000)
    method: str | None = Field(
        default=None,
        max_length=64,
        description="Optional preference the planner honours when the method actually fits the task.",
    )


class ToolCallValidationRequest(BaseModel):
    """A candidate tool call, checked against its input schema without executing it."""

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SourceSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=400)
    max_results: int = Field(default=5, ge=1, le=20)


class SessionCreate(BaseModel):
    title: str = Field(default="Research session", min_length=3, max_length=200)
    project_id: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=3, max_length=6000)
    wait: bool = Field(default=True, description="Wait for the full agent run (recommended).")
    client_request_id: str | None = Field(default=None, max_length=64)


class AgentSessionOut(BaseModel):
    session_id: str
    title: str
    status: str
    provider: str
    model: str
    project_id: str | None
    plan: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    event_count: int = 0


class AgentEventOut(BaseModel):
    sequence: int
    event_type: str
    status: str
    created_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    session_id: str
    status: str
    answer_markdown: str
    plan: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    usage: dict[str, Any]
    provider: dict[str, Any]
    error: dict[str, Any] | None = None
    events: list[AgentEventOut] = Field(default_factory=list)


# --- data & reports ----------------------------------------------------------


class DatasetAnalyzeRequest(BaseModel):
    x_column: str | None = Field(default=None, max_length=80)
    y_column: str | None = Field(default=None, max_length=80)
    model: str = Field(default="linear", max_length=40)
    degree: int = Field(default=1, ge=1, le=5)
    columns: list[str] = Field(default_factory=list, max_length=12)


class ReportCreate(BaseModel):
    experiment_id: str | None = None
    run_id: str | None = None
    format: Literal["markdown", "pdf", "both"] = "both"
    include_verification: bool = True
    include_iterations: bool = True
    notes: str | None = Field(default=None, max_length=2000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class ReportOut(BaseModel):
    report_id: str
    title: str
    format: str
    experiment_id: str | None
    run_id: str | None
    created_at: datetime | None = None
    artifact_path: str | None
    artifact_size_bytes: int | None
    checksum_sha256: str | None
    verification_snapshot: dict[str, Any] = Field(default_factory=dict)
    body_markdown: str | None = None
    has_pdf: bool = False
