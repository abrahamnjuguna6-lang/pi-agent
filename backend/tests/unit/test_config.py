"""T0.1: settings fail fast on missing secrets and match the design defaults (§34, §35)."""

import pytest
from pydantic import ValidationError

from lifeos.config import Settings, get_settings

REQUIRED = ["DATABASE_URL", "REDIS_URL", "JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "HMAC_KEY", "ENCRYPTION_KEY"]


def build(**overrides: str) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_setting_raises(
    test_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError) as exc:
        build()
    assert missing.lower() in str(exc.value)


def test_defaults_match_design(test_env: dict[str, str]) -> None:
    s = build()
    # §34.3 token budgets
    assert s.context_token_budget == 4000
    assert s.history_token_budget == 6000
    assert s.tool_result_token_budget == 2000
    assert s.max_model_calls_per_turn == 8
    # §34.1 timeouts and embeddings
    assert s.classifier_timeout_s == 2.0
    assert s.embedding_dimensions == 1536
    # §23.4 memory search
    assert s.memory_similarity_threshold == 0.75
    assert s.memory_top_k == 8
    # §21.1 token lifetimes, R15.6 (<= 24 h)
    assert s.access_token_ttl_minutes == 60
    assert s.refresh_token_ttl_hours == 24
    # §6.6 / §27.1
    assert s.turn_timeout_s == 45.0
    assert s.realtime_ticket_ttl_s == 60
    # §29.4 / §29.6
    assert s.push_max_per_hour == 3
    assert s.stale_window_hours_l12 == 4
    # §30.2 production default
    assert s.trace_capture_mode == "full_redacted"


def test_refresh_ttl_cannot_exceed_24h(test_env: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        build(refresh_token_ttl_hours="25")


def test_provider_keys_optional_in_test_env(test_env: dict[str, str]) -> None:
    assert build().openai_api_key is None


@pytest.mark.parametrize("env", ["staging", "production"])
def test_provider_keys_required_in_deployed_envs(test_env: dict[str, str], env: str) -> None:
    with pytest.raises(ValidationError, match="openai_api_key"):
        build(environment=env)


def test_full_trace_capture_forbidden_in_production(test_env: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="trace_capture_mode"):
        build(
            environment="production",
            openai_api_key="sk-test",
            tavily_api_key="tv-test",
            trace_capture_mode="full",
        )


def test_secrets_not_exposed_in_repr(test_env: dict[str, str]) -> None:
    s = build()
    assert "test-hmac-key" not in repr(s)
    assert s.hmac_key.get_secret_value() == "test-hmac-key"


def test_get_settings_is_cached(test_env: dict[str, str]) -> None:
    assert get_settings() is get_settings()
