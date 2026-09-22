"""T5.8: Completion Rate semantics (design §16.3, R3.12–3.13) — 100% branch coverage of the rules."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from lifeos.domain.analytics.completion import ActionSnapshot, compute, status_as_of

D1 = date(2026, 9, 21)
T = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
EVENING = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)


def snap(
    *statuses: str, day: date = D1, cancelled: datetime | None = None, created: datetime = T
) -> ActionSnapshot:
    chain = [None, "Planned", *statuses]
    checkins = [(created, None, "Planned")] + [
        (created + timedelta(hours=i + 1), chain[i + 1], s) for i, s in enumerate(statuses)
    ]
    return ActionSnapshot(day, created, cancelled, checkins)


def test_formula_and_breakdown() -> None:
    result = compute([snap("Completed"), snap("Skipped"), snap("Started"), snap()], D1, D1, EVENING)
    assert (result.completed, result.skipped, result.started, result.planned) == (1, 1, 1, 1)
    assert result.rate == Decimal("0.2500")
    assert result.percent == Decimal("25.00")
    assert result.total == 4


def test_started_counts_in_denominator_only() -> None:
    assert compute([snap("Started"), snap("Completed")], D1, D1, EVENING).rate == Decimal("0.5000")


def test_cancelled_actions_excluded() -> None:
    cancelled = snap(cancelled=T + timedelta(hours=1))
    assert compute([cancelled, snap("Completed")], D1, D1, EVENING).rate == Decimal("1.0000")


def test_cancellation_after_evaluation_instant_still_counts() -> None:
    late_cancel = snap(cancelled=EVENING + timedelta(hours=1))
    assert compute([late_cancel], D1, D1, EVENING).planned == 1


def test_overdue_action_without_update_counts_as_planned() -> None:  # R3.13
    assert compute([snap()], D1, D1, EVENING).rate == Decimal("0.0000")


def test_empty_period_has_no_rate() -> None:
    result = compute([], D1, D1, EVENING)
    assert result.rate is None
    assert result.percent is None


def test_only_actions_on_period_dates_count() -> None:  # rescheduled actions count on their destination date
    moved_away = snap("Completed", day=D1 + timedelta(days=1))
    assert compute([moved_away, snap()], D1, D1, EVENING).rate == Decimal("0.0000")
    assert compute([moved_away], D1 + timedelta(days=1), D1 + timedelta(days=1), EVENING).completed == 1


def test_historical_as_of_evaluation() -> None:
    action = snap("Started", "Completed")  # Started at 09:00, Completed at 10:00
    assert compute([action], D1, D1, T + timedelta(minutes=90)).started == 1
    assert compute([action], D1, D1, T + timedelta(hours=3)).completed == 1


def test_actions_created_after_evaluation_ignored() -> None:
    future = snap(created=EVENING + timedelta(minutes=1))
    assert compute([future], D1, D1, EVENING).rate is None


def test_status_as_of_defaults_to_planned() -> None:
    assert status_as_of([], EVENING) == "Planned"
    assert status_as_of([(EVENING + timedelta(seconds=1), "Planned", "Completed")], EVENING) == "Planned"
    assert (
        status_as_of([(T, None, "Planned"), (T + timedelta(hours=2), "Planned", "Skipped")], EVENING)
        == "Skipped"
    )


def test_same_instant_checkins_ordered_by_transition_chain() -> None:
    # Created and completed within one instant: timestamps tie, the chain decides (any input order).
    tied = [(T, "Planned", "Started"), (T, None, "Planned"), (T, "Started", "Completed")]
    assert status_as_of(tied, EVENING) == "Completed"
    assert status_as_of(list(reversed(tied)), EVENING) == "Completed"


def test_rounding() -> None:
    result = compute([snap("Completed"), snap(), snap()], D1, D1, EVENING)
    assert result.rate == Decimal("0.3333")
    assert result.percent == Decimal("33.33")
