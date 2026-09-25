"""T6.1: Commitment rules (design §11.1–11.3, §38.1) — 100% branch coverage of the pure logic."""

from datetime import UTC, date, datetime, timedelta

import pytest

from lifeos.domain.commitments import (
    TRANSITIONS,
    condition_after_removal,
    condition_satisfied,
    deadline_action,
    ensure_linkable,
    ensure_transition,
    resolve_condition,
    validate_deferral,
    validate_explanation,
    window_open,
)
from lifeos.domain.errors import DomainError

STATUSES = ["Open", "Kept", "Broken", "Deferred", "Cancelled"]
VALID = {
    ("Open", "Kept"),
    ("Open", "Broken"),
    ("Open", "Deferred"),
    ("Open", "Cancelled"),
    ("Deferred", "Kept"),
    ("Deferred", "Broken"),
    ("Deferred", "Deferred"),  # re-defer (R9.10, design §44 item 7)
}
TODAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("new", STATUSES)
def test_full_transition_matrix(current: str, new: str) -> None:  # R9.4
    if (current, new) in VALID:
        ensure_transition(current, new)
        return
    with pytest.raises(DomainError) as exc:
        ensure_transition(current, new)
    assert exc.value.code == "INVALID_COMMITMENT_TRANSITION"
    assert exc.value.details == {"from": current, "to": new}


def test_table_matches_requirements() -> None:
    assert {(a, b) for a, targets in TRANSITIONS.items() for b in targets} == VALID


# --------------------------------------------------------------------------- completion conditions


@pytest.mark.parametrize(
    ("requested", "links", "expected"),
    [
        (None, 0, "explicit"),
        ("explicit", 0, "explicit"),
        (None, 1, "single"),
        ("single", 1, "single"),
        ("all", 3, "all"),
        ("any", 2, "any"),
    ],
)
def test_resolve_condition(requested: str | None, links: int, expected: str) -> None:  # R9.2, §11.2
    assert resolve_condition(requested, links) == expected


@pytest.mark.parametrize(
    ("requested", "links"),
    [("all", 0), ("single", 0), ("any", 1), ("explicit", 1), (None, 2), ("single", 2), ("explicit", 3)],
)
def test_resolve_condition_rejects_mismatches(requested: str | None, links: int) -> None:
    with pytest.raises(DomainError) as exc:
        resolve_condition(requested, links)
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("condition", "statuses", "expected"),
    [
        ("single", ["Completed"], True),  # one linked Daily Action completed → Kept
        ("single", ["Planned"], False),
        ("all", ["Completed", "Completed"], True),
        ("all", ["Completed", "Skipped"], False),
        ("any", ["Planned", "Completed"], True),
        ("any", ["Planned", "Skipped"], False),
        ("explicit", [], False),
        ("explicit", ["Completed"], False),  # never automatic (R9.5)
        ("all", [], False),  # every link removed → nothing to satisfy
        ("any", [], False),
    ],
)
def test_condition_satisfied(condition: str, statuses: list[str], expected: bool) -> None:
    assert condition_satisfied(condition, statuses) is expected


@pytest.mark.parametrize(
    ("condition", "remaining", "expected"),
    [("single", 0, "explicit"), ("all", 0, "explicit"), ("all", 1, "all"), ("any", 2, "any")],
)
def test_condition_after_removal(condition: str, remaining: int, expected: str) -> None:  # R9.9
    assert condition_after_removal(condition, remaining) == expected


@pytest.mark.parametrize(("status", "lifecycle"), [("Planned", "active"), ("Started", "active")])
def test_linkable_actions(status: str, lifecycle: str) -> None:
    ensure_linkable(status, lifecycle)


@pytest.mark.parametrize(
    ("status", "lifecycle"), [("Completed", "active"), ("Skipped", "active"), ("Planned", "cancelled")]
)
def test_unlinkable_actions(status: str, lifecycle: str) -> None:
    with pytest.raises(DomainError) as exc:
        ensure_linkable(status, lifecycle)
    assert exc.value.code == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- deadlines and the window


def test_window_open() -> None:
    assert window_open(None, NOW) is False
    assert window_open(NOW + timedelta(seconds=1), NOW) is True
    assert window_open(NOW, NOW) is False


@pytest.mark.parametrize(
    ("status", "due", "window", "expected"),
    [
        ("Open", TODAY - timedelta(days=1), None, "break"),  # due date passed (R9.7)
        ("Open", TODAY, None, None),  # the due date ends at the end of the local day
        ("Open", TODAY + timedelta(days=3), None, None),
        ("Deferred", TODAY - timedelta(days=1), None, "open_window"),  # 24 h explanation window (R9.10)
        ("Deferred", TODAY - timedelta(days=1), NOW + timedelta(hours=1), None),  # window still open
        ("Deferred", TODAY - timedelta(days=1), NOW, "break"),  # window expired
        ("Deferred", TODAY, None, None),
        ("Kept", TODAY - timedelta(days=5), None, None),
        ("Broken", TODAY - timedelta(days=5), None, None),
        ("Cancelled", TODAY - timedelta(days=5), None, None),
    ],
)
def test_deadline_action(status: str, due: date, window: datetime | None, expected: str | None) -> None:
    assert deadline_action(status, due, TODAY, window, NOW) == expected


# --------------------------------------------------------------------------- deferral and explanation


def test_first_deferral_needs_no_explanation() -> None:  # R9.10
    kind = validate_deferral(
        "Open",
        TODAY + timedelta(days=2),
        TODAY,
        TODAY,
        in_window=False,
        explained=False,
        acknowledged=False,
    )
    assert kind == "deferred"


def test_redeferral_needs_explanation_and_acknowledgment() -> None:
    kind = validate_deferral(
        "Deferred",
        TODAY + timedelta(days=2),
        TODAY - timedelta(days=1),
        TODAY,
        in_window=True,
        explained=True,
        acknowledged=True,
    )
    assert kind == "redeferred"


@pytest.mark.parametrize(
    ("in_window", "explained", "acknowledged", "code"),
    [
        (False, True, True, "INVALID_COMMITMENT_TRANSITION"),  # not overdue yet / window expired
        (True, False, True, "INVALID_COMMITMENT_TRANSITION"),  # no written explanation
        (True, True, False, "VALIDATION_ERROR"),  # explanation without acknowledgment
    ],
)
def test_redeferral_rejections(in_window: bool, explained: bool, acknowledged: bool, code: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_deferral(
            "Deferred",
            TODAY + timedelta(days=2),
            TODAY - timedelta(days=1),
            TODAY,
            in_window=in_window,
            explained=explained,
            acknowledged=acknowledged,
        )
    assert exc.value.code == code


@pytest.mark.parametrize("new_due", [TODAY, TODAY - timedelta(days=1)])
def test_deferral_requires_a_future_due_date(new_due: date) -> None:
    with pytest.raises(DomainError) as exc:
        validate_deferral("Open", new_due, TODAY, TODAY, in_window=False, explained=False, acknowledged=False)
    assert (exc.value.code, exc.value.details) == ("VALIDATION_ERROR", {"field": "new_due_date"})


def test_deferral_requires_a_later_due_date_than_the_current_one() -> None:
    with pytest.raises(DomainError) as exc:
        validate_deferral(
            "Open",
            TODAY + timedelta(days=1),
            TODAY + timedelta(days=5),
            TODAY,
            in_window=False,
            explained=False,
            acknowledged=False,
        )
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("status", ["Kept", "Broken", "Cancelled"])
def test_terminal_commitments_cannot_be_deferred(status: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_deferral(
            status,
            TODAY + timedelta(days=2),
            TODAY,
            TODAY,
            in_window=False,
            explained=False,
            acknowledged=False,
        )
    assert exc.value.code == "INVALID_COMMITMENT_TRANSITION"


def test_explanation_is_stored_trimmed() -> None:
    assert validate_explanation("Deferred", True, False, "  the client review ran over  ") == (
        "the client review ran over"
    )


@pytest.mark.parametrize(
    ("status", "in_window", "already", "text", "code"),
    [
        ("Open", False, False, "x" * 30, "INVALID_COMMITMENT_TRANSITION"),
        ("Deferred", False, False, "x" * 30, "INVALID_COMMITMENT_TRANSITION"),  # window expired/not open
        ("Deferred", True, True, "x" * 30, "INVALID_COMMITMENT_TRANSITION"),  # one explanation per window
        ("Deferred", True, False, "too short", "VALIDATION_ERROR"),
    ],
)
def test_explanation_rejections(status: str, in_window: bool, already: bool, text: str, code: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_explanation(status, in_window, already, text)
    assert exc.value.code == code
