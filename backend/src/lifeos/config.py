"""Typed application settings (design §34, §35, §36.4).

Secrets have no defaults: a missing value fails fast at startup. Provider API keys are
optional in development/test (fake models are used) and required in staging/production.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
TraceCaptureMode = Literal["metadata", "full_redacted", "full"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "development"

    # Infrastructure
    database_url: PostgresDsn
    redis_url: RedisDsn
    db_pool_size: int = Field(default=10, ge=1)
    checkpoint_pool_size: int = Field(default=10, ge=1)

    # Secrets (design §21, §33.5)
    jwt_private_key: SecretStr
    jwt_public_key: SecretStr
    jwt_key_id: str = "k1"
    hmac_key: SecretStr
    encryption_key: SecretStr

    # Provider credentials (required outside development/test)
    openai_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None

    # Models (design §34.1)
    model_classifier: str = "openai:gpt-4.1-mini"
    model_agent: str = "openai:gpt-4.1"
    model_generation: str = "openai:gpt-4.1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    classifier_timeout_s: float = 2.0
    agent_call_timeout_s: float = 20.0
    generation_timeout_s: float = 20.0
    embedding_timeout_s: float = 5.0

    # Token budgets (design §34.3, §34.5)
    context_token_budget: int = 4000
    history_token_budget: int = 6000
    tool_result_token_budget: int = 2000
    max_model_calls_per_turn: int = 8
    user_daily_token_budget: int = 200_000

    # Chat / realtime (design §6.6, §6.7, §27)
    turn_timeout_s: float = 45.0
    session_inactivity_minutes: int = 30
    session_max_messages: int = 200
    realtime_ticket_ttl_s: int = Field(default=60, le=60)
    confirmation_expiry_hours: int = 24

    # Auth (design §21)
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_hours: int = Field(default=24, le=24)
    email_verification_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 60
    login_lockout_threshold: int = 5
    login_lockout_window_minutes: int = 10
    login_lockout_duration_minutes: int = 15

    # Memory (design §23.4)
    memory_similarity_threshold: float = Field(default=0.75, ge=0, le=1)
    memory_top_k: int = Field(default=8, ge=1, le=20)
    memory_exact_search_max_entries: int = 20_000

    # Notifications (design §29)
    push_max_per_hour: int = 3
    stale_window_hours_l12: int = 4

    # Observability (design §30, §31)
    trace_capture_mode: TraceCaptureMode = "full_redacted"
    trace_retention_days: int = Field(default=180, ge=90)
    langsmith_export_enabled: bool = False

    @model_validator(mode="after")
    def _require_provider_keys_in_deployed_envs(self) -> "Settings":
        if self.environment in ("staging", "production"):
            missing = [name for name in ("openai_api_key", "tavily_api_key") if getattr(self, name) is None]
            if missing:
                raise ValueError(f"missing required settings for {self.environment}: {missing}")
            if self.trace_capture_mode == "full":
                raise ValueError("trace_capture_mode='full' is not allowed outside development/test")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
