"""Runtime configuration. Everything is set through the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_ROOT / ".env"), extra="ignore", populate_by_name=True
    )

    model_api_key: str = Field(default="", alias="MODEL_API_KEY", repr=False)
    model_api_base_url: str = Field(default="https://api.meta.ai/v1", alias="MODEL_API_BASE_URL")

    # Development-only routes stay hidden unless this is set on purpose.
    env: str = Field(default="production", alias="QUACK_ENV")
    cors_origins: str = Field(default="http://localhost:3000", alias="QUACK_CORS_ORIGINS")
    rate_limit_per_minute: int = Field(default=60, alias="QUACK_RATE_LIMIT_PER_MINUTE")
    # Client addresses can be forged through a proxy, so this cap is what actually bounds spend rate.
    global_rate_limit_per_minute: int = Field(default=300, alias="QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE")

    # JSON object of per-purpose overrides, for example:
    # {"transcribe": {"model": "muse-wonder-1.0", "reasoning_effort": "medium"}}
    routes_json: str = Field(default="", alias="QUACK_ROUTES")

    # A would-be alert is read a second time, independently, and only fires if both readings agree.
    # Telling a student a mistake is fixed needs the same agreement.
    confirm_alerts: bool = Field(default=True, alias="QUACK_CONFIRM_ALERTS")
    # Start that second reading alongside the first instead of after it. An alert then takes as
    # long as the slower reading rather than both added together, and every check pays for two.
    confirm_in_parallel: bool = Field(default=True, alias="QUACK_CONFIRM_IN_PARALLEL")
    # How long the second reading can take before another goes alongside it. Most finish in 6 s, and
    # those that use up all their tokens take 19 s. See `AnalysisService._second_reading`.
    second_reading_patience_s: float = Field(default=14.0, alias="QUACK_SECOND_READING_PATIENCE_S")
    # Re-read when the fast reading was unclear. Off until evaluation shows it pays for itself.
    second_reading_on_uncertain: bool = Field(default=False, alias="QUACK_SECOND_READING_ON_UNCERTAIN")
    reading_cache_size: int = Field(default=256, alias="QUACK_READING_CACHE_SIZE")
    # Lines read and agreed on, across sessions, so unchanged lines needn't be read again.
    line_memory_size: int = Field(default=20_000, alias="QUACK_LINE_MEMORY_SIZE")
    verify_timeout_s: float = Field(default=4.0, alias="QUACK_VERIFY_TIMEOUT_S")

    # Credit guard rails. The project ceiling leaves US$15 of the US$150 allocation in reserve.
    budget_project_usd: float = Field(default=135.0, alias="QUACK_BUDGET_PROJECT_USD")
    budget_session_usd: float = Field(default=2.0, alias="QUACK_BUDGET_SESSION_USD")
    budget_request_usd: float = Field(default=0.50, alias="QUACK_BUDGET_REQUEST_USD")
    # A session id is chosen by the client, so per-session limits only stop a runaway tab.
    # This one stops anyone draining the credits quickly, whatever ids they use.
    budget_hourly_usd: float = Field(default=5.0, alias="QUACK_BUDGET_HOURLY_USD")
    ledger_path: Path = Field(default=API_ROOT / "var" / "usage.jsonl", alias="QUACK_LEDGER_PATH")
    ledger_backend: Literal["file", "postgres"] = Field(default="file", alias="QUACK_LEDGER_BACKEND")

    # Who a request is from. "off" has no accounts at all, as Quack was before Phase 2. "local" is a
    # development stand-in, refused outside development. Unset, it follows from what's configured.
    auth_mode_setting: str = Field(default="", alias="QUACK_AUTH_MODE")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    # Public by design, and handed to the browser. Row-level security is what protects the data.
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    # Only for projects still signing tokens with a shared secret. Newer ones publish their keys.
    supabase_jwt_secret: str = Field(default="", alias="SUPABASE_JWT_SECRET", repr=False)
    # Server only. Needed just to delete the sign-in itself when an account is deleted.
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY", repr=False)
    # Postgres, such as Supabase's. Unset, accounts live in the SQLite file below.
    database_url: str = Field(default="", alias="QUACK_DATABASE_URL", repr=False)
    sqlite_path: Path = Field(default=API_ROOT / "var" / "quack.db", alias="QUACK_SQLITE_PATH")

    # When a reading is shown the student's own calibration samples. See docs/HANDWRITING_CALIBRATION.md.
    # off: never. on_doubt: when looking again at a line that didn't settle, or asked directly.
    # always: every reading, which costs more and has no evidence behind it yet.
    personalization: Literal["off", "on_doubt", "always"] = Field(default="on_doubt", alias="QUACK_PERSONALIZATION")
    references_per_reading: int = Field(default=2, ge=1, le=6, alias="QUACK_REFERENCES_PER_READING")

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def auth_mode(self) -> Literal["off", "local", "supabase"]:
        mode = self.auth_mode_setting or (
            "supabase" if self.supabase_url else "local" if self.is_development else "off"
        )
        if mode not in ("off", "local", "supabase"):
            raise ValueError(f"unknown QUACK_AUTH_MODE {mode!r}")
        if mode == "local" and not self.is_development:
            raise ValueError("local accounts aren't secure, so they only run with QUACK_ENV=development")
        if mode == "supabase" and not self.supabase_url:
            raise ValueError("QUACK_AUTH_MODE=supabase needs SUPABASE_URL")
        return mode  # type: ignore[return-value]

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
