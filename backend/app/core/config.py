"""Application configuration.

All secrets are read server-side only (env vars / .env file).  Nothing in this
module is ever exposed through an API response.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLAB_",
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- general -----------------------------------------------------------
    app_name: str = "PHYSICS LAB AI"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True
    # when true, unexpected exceptions propagate to the ASGI caller instead of being
    # converted into a 500 envelope.  Only ever enabled in tests; never in production.
    debug_propagate_errors: bool = False

    # --- persistence -------------------------------------------------------
    # SQLite by default so the app runs with zero infrastructure; any
    # SQLAlchemy Postgres URL (postgresql+psycopg://...) works unchanged.
    database_url: str = Field(
        default=f"sqlite:///{(BACKEND_DIR / 'data' / 'physics_lab.db').as_posix()}"
    )
    db_pool_size: int = 10
    data_dir: Path = BACKEND_DIR / "data"
    uploads_dir: Path = BACKEND_DIR / "data" / "uploads"
    artifacts_dir: Path = BACKEND_DIR / "data" / "artifacts"

    # --- auth --------------------------------------------------------------
    secret_key: str = ""
    session_cookie_name: str = "plab_session"
    csrf_cookie_name: str = "plab_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_ttl_seconds: int = 60 * 60 * 12
    cookie_secure: bool = False  # set True behind HTTPS in production
    # argon2id time cost (number of passes over memory); the old name for this was
    # misleading because nothing here is bcrypt
    argon2_time_cost: int = 3  # argon2 time cost
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 2
    demo_account_enabled: bool = True
    demo_email: str = "demo@physicslab.local"
    demo_password: str = "demo-password-123"

    # --- limits ------------------------------------------------------------
    max_body_bytes: int = 2_000_000
    max_upload_bytes: int = 10_000_000
    max_expression_chars: int = 400
    max_iterations: int = 200
    default_tolerance: float = 1e-10
    compute_timeout_seconds: float = 20.0
    max_grid_points: int = 20000
    max_output_points: int = 200000
    max_agent_steps: int = 8
    max_agent_tool_calls: int = 12
    max_provider_retries: int = 1
    page_size: int = 20
    max_page_size: int = 100
    rate_limit_enabled: bool = True
    rate_limit_default: str = "240/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_compute: str = "40/minute"

    # --- AI provider (server-side only) -----------------------------------
    ai_provider: str = "mock"  # mock | anthropic | openai
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ai_timeout_seconds: float = 60.0
    ai_max_tokens: int = 2048

    # --- research retrieval -------------------------------------------------
    # A live retrieval integration must be explicitly configured; otherwise
    # the agent reports that external research was NOT performed.
    research_search_base_url: str = ""
    research_search_api_key: str = ""
    research_timeout_seconds: float = 15.0

    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("environment")
    @classmethod
    def _env_enum(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"development", "test", "staging", "production"}:
            raise ValueError("environment must be development|test|staging|production")
        return v

    @model_validator(mode="after")
    def _keep_storage_roots_consistent(self) -> "Settings":
        """Keep `uploads_dir` / `artifacts_dir` inside `data_dir`, which is the invariant readers rely on.

        Stored paths are recorded *relative to* `data_dir` (`Dataset.storage_path` is
        `uploads/<owner>_<digest>.csv`, and the report download handler resolves its artifact the
        same way). So the three settings have to move together:

        * Setting only ``PLAB_DATA_DIR`` re-derives the two child directories. Without this, a
          relocated data dir left uploads writing into the packaged default and every upload died
          on ``relative_to`` - with a message that quoted an absolute server path to the client.
        * Pointing either one *outside* the data dir explicitly is refused here, at startup,
          instead of surfacing later as a half-written file or a leaked path.
        """
        data_root = Path(self.data_dir).resolve()
        fields = type(self).model_fields
        for name, child in (("uploads_dir", "uploads"), ("artifacts_dir", "artifacts")):
            default = fields[name].default
            current = Path(getattr(self, name))
            if default is not None and current == Path(default):
                setattr(self, name, data_root / child)
                continue
            resolved = current.resolve()
            if resolved != data_root and data_root not in resolved.parents:
                raise ValueError(
                    f"`{name}` ({resolved}) must live inside `data_dir` ({data_root}): stored "
                    "artifact paths are recorded relative to the data directory"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def relative_storage_path(self, path: Path | str) -> str:
        """How an artifact on disk is recorded in the database: relative to `data_dir`.

        Total by construction (the validator above guarantees containment); it raises only if a
        caller mutated the settings at runtime, and says which root was expected rather than
        printing a traceback about path arithmetic.
        """
        resolved = Path(path).resolve()
        root = Path(self.data_dir).resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError(
                f"{resolved} is outside the configured storage root {root}; "
                "uploads_dir/artifacts_dir must live inside data_dir"
            )
        return resolved.relative_to(root).as_posix()

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.artifacts_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

    def resolve_secret_key(self) -> str:
        """Return the configured secret; in dev, generate + persist one."""
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError("PLAB_SECRET_KEY must be set in production")
        keyfile = Path(self.data_dir) / ".secret_key"
        if keyfile.exists():
            return keyfile.read_text().strip()
        self.ensure_dirs()
        generated = secrets.token_urlsafe(48)
        keyfile.write_text(generated)
        return generated


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
