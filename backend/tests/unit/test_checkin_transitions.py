"""T5.4: Daily Action transition matrix (design §16.2, §38.1) — 100% branch coverage of the rules."""

import pytest

from lifeos.domain.checkins import NOTE_MAX, TRANSITIONS, validate_transition
from lifeos.domain.errors import DomainError

STATUSES = ["Planned", "Started", "Completed", "Skipped"]
VALID = {
    ("Planned", "Started"),
    ("Planned", "Completed"),
    ("Planned", "Skipped"),
    ("Started", "Completed"),
    ("Started", "Skipped"),
}


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("new", STATUSES)
def test_full_transition_matrix(current: str, new: str) -> None:
    note = "a reason"
    if (current, new) in VALID:
        assert validate_transition(current, new, "active", note) == note
    else:
        with pytest.raises(DomainError) as exc:
            validate_transition(current, new, "active", note)
        assert exc.value.code == "INVALID_TRANSITION"
        assert exc.value.details == {"from": current, "to": new}


def test_table_matches_requirements() -> None:  # R3.3
    assert {(a, b) for a, targets in TRANSITIONS.items() for b in targets} == VALID


@pytest.mark.parametrize("note", [None, "", "   \n\t"])
def test_skip_requires_reason(note: str | None) -> None:  # R3.6
    with pytest.raises(DomainError) as exc:
        validate_transition("Planned", "Skipped", "active", note)
    assert exc.value.code == "SKIP_REASON_REQUIRED"


def test_note_trimmed_and_empty_becomes_none() -> None:
    assert validate_transition("Planned", "Skipped", "active", "  sick  ") == "sick"
    assert validate_transition("Planned", "Completed", "active", "   ") is None
    assert validate_transition("Planned", "Completed", "active", None) is None


def test_note_length_limit() -> None:
    with pytest.raises(DomainError) as exc:
        validate_transition("Planned", "Completed", "active", "x" * (NOTE_MAX + 1))
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("new", ["Started", "Completed", "Skipped"])
def test_cancelled_action_accepts_no_transition(new: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_transition("Planned", new, "cancelled", "reason")
    assert exc.value.code == "INVALID_TRANSITION"


def test_unknown_current_status_rejected() -> None:
    with pytest.raises(DomainError):
        validate_transition("Bogus", "Completed", "active", None)


def test_chain_order_breaks_same_instant_ties() -> None:
    import uuid
    from datetime import UTC, datetime

    from lifeos.db import models as m
    from lifeos.domain.checkins import chain_order

    t0, t1 = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)

    def rec(at: datetime, prev: str | None, new: str) -> m.CheckinRecord:
        return m.CheckinRecord(id=uuid.uuid4(), created_at=at, previous_status=prev, new_status=new)

    records = [
        rec(t0, "Started", "Completed"),
        rec(t1, "Completed", "Completed"),
        rec(t0, None, "Planned"),
        rec(t0, "Planned", "Started"),
    ]
    ordered = chain_order(records)
    assert [(r.previous_status, r.new_status) for r in ordered] == [
        (None, "Planned"),
        ("Planned", "Started"),
        ("Started", "Completed"),
        ("Completed", "Completed"),
    ]


def test_chain_order_tolerates_broken_chain() -> None:
    import uuid
    from datetime import UTC, datetime

    from lifeos.db import models as m
    from lifeos.domain.checkins import chain_order

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    orphan = m.CheckinRecord(id=uuid.uuid4(), created_at=t0, previous_status="Started", new_status="Skipped")
    assert chain_order([orphan]) == [orphan]  # no predecessor in the data: still returned, never dropped
