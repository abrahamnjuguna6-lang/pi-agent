"""T7.2: Memory entry rules — defaults, clamping and the inference flag (design §23.1, §23.5; R4.2–4.3)."""

from decimal import Decimal

import pytest

from lifeos.domain.errors import DomainError
from lifeos.domain.memory.service import (
    DEFAULTS,
    MemoryDraft,
    clamp_confidence,
    clamp_importance,
    validate_content,
)


@pytest.mark.parametrize(
    ("source", "importance", "confidence"),
    [
        ("User-stated", 7, Decimal("1.0")),
        ("System-derived", 5, Decimal("1.0")),
        ("AI-inferred", 5, Decimal("0.5")),
    ],
)
def test_defaults_per_source(source: str, importance: int, confidence: Decimal) -> None:
    assert DEFAULTS[source] == (importance, confidence)
    assert clamp_importance(None, source) == importance
    assert clamp_confidence(None, source) == confidence


@pytest.mark.parametrize(
    ("source", "proposed", "expected"),
    [
        ("AI-inferred", 9, 7),  # model-proposed values are clamped to 1–7
        ("AI-inferred", 0, 1),
        ("AI-inferred", 4, 4),
        ("User-stated", 10, 10),  # the User may use the whole range
        ("User-stated", 42, 10),
        ("User-stated", -3, 1),
    ],
)
def test_importance_clamping(source: str, proposed: int, expected: int) -> None:
    assert clamp_importance(proposed, source) == expected


@pytest.mark.parametrize(
    ("source", "proposed", "expected"),
    [
        ("AI-inferred", Decimal("0.95"), Decimal("0.8")),  # clamped to 0.3–0.8
        ("AI-inferred", Decimal("0.1"), Decimal("0.3")),
        ("AI-inferred", Decimal("0.6"), Decimal("0.6")),
        ("User-stated", Decimal("1.0"), Decimal("1.0")),
        ("System-derived", Decimal("2"), Decimal("1.0")),
        ("User-stated", Decimal("-1"), Decimal("0.0")),
    ],
)
def test_confidence_clamping(source: str, proposed: Decimal, expected: Decimal) -> None:
    assert clamp_confidence(proposed, source) == expected


def test_ai_inferred_drafts_are_inferences() -> None:  # R4.3, design §23.5
    draft = MemoryDraft(content="They seem to avoid mornings", type="Pattern", source="AI-inferred")
    assert draft.source == "AI-inferred"  # the flag itself is set by create(); see test_memory_api


@pytest.mark.parametrize("content", ["", "   ", "\n\t "])
def test_empty_content_is_rejected(content: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_content(content)
    assert (exc.value.code, exc.value.details) == ("VALIDATION_ERROR", {"field": "content"})


def test_content_is_trimmed_and_length_limited() -> None:
    assert validate_content("  I value deep work  ") == "I value deep work"
    with pytest.raises(DomainError):
        validate_content("x" * 4001)
