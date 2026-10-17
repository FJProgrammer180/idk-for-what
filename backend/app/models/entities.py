"""Declarative base, ID/timestamp mixins and all ORM entities."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("usr_"))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="Researcher", nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="researcher", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # per-account compute budget (admin-adjustable, enforced in the compute router)
    max_iterations: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    compute_timeout_seconds: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = keep forever
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def limits(self) -> dict[str, Any]:
        """Per-account ceilings consumed by :func:`app.services.security_limits`."""
        return {
            "max_iterations": self.max_iterations,
            "compute_timeout_seconds": self.compute_timeout_seconds,
        }


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("prj_"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner: Mapped["User"] = relationship(back_populates="projects")
    experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Experiment(Base, TimestampMixin):
    """A reproducible, versioned computational task (numerical or simulation)."""

    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_owner_created", "owner_id", "created_at"),
        Index("ix_experiments_method", "method"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("exp_"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    problem_type: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="numerical", nullable=False)  # numerical|simulation|analysis
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    input_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="experiments")
    project: Mapped["Project | None"] = relationship(back_populates="experiments")
    runs: Mapped[list["ExperimentRun"]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperimentRun.created_at.desc()",
    )


class ExperimentRun(Base, TimestampMixin):
    """One execution of an experiment: bounded async job + verified output."""

    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "idempotency_key", name="uq_run_idempotency"),
        Index("ix_runs_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("run_"))
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    execution_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    structured_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    software_versions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    experiment: Mapped["Experiment"] = relationship(back_populates="runs")


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("ses_"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="Research session", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="idle", nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="deterministic-mock", nullable=False)
    plan: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentEvent.sequence",
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_agent_event_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("evt_"))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="info", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    session: Mapped["AgentSession"] = relationship(back_populates="events")


class ResearchSource(Base, TimestampMixin):
    __tablename__ = "research_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("src_"))
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), default="reference", nullable=False)
    provenance: Mapped[str] = mapped_column(
        String(40), default="local_curated_reference", nullable=False
    )
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    citation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("dat_"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="text/csv", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    schema_profile: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("owner_id", "sha256", name="uq_dataset_owner_hash"),)


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: new_id("rep_"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True, nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(16), default="markdown", nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    __table_args__ = (Index("ix_reports_owner_created", "owner_id", "created_at"),)
