"""T4.1: notification preference defaults, merging and guards (design §24.11, R8.9, R14.3)."""

from datetime import time

import pytest

from lifeos.domain.errors import DomainError
from lifeos.domain.notification_prefs import CRITICAL_TYPES, merge_prefs, parse_prefs


def test_defaults_match_design() -> None:
    prefs = parse_prefs(None)
    assert prefs.do_not_disturb.enabled is True
    assert (prefs.do_not_disturb.start_time, prefs.do_not_disturb.end_time) == (time(22), time(7))
    assert len(prefs.types) == 13
    assert prefs.types["briefing"].push is True
    assert prefs.types["schedule_suggestion"].push is False  # in-app only by default
    assert prefs.types["lesson_proposal"].push is False
    assert all(c.in_app for c in prefs.types.values())


def test_each_accountability_level_is_independently_configurable() -> None:  # R8.9
    prefs = merge_prefs(
        None, {"types": {"accountability_l1": {"push": False}, "accountability_l3": {"in_app": False}}}
    )
    assert (prefs.types["accountability_l1"].push, prefs.types["accountability_l1"].in_app) == (False, True)
    assert (prefs.types["accountability_l3"].push, prefs.types["accountability_l3"].in_app) == (True, False)
    assert prefs.types["accountability_l2"].push is True


@pytest.mark.parametrize("critical", sorted(CRITICAL_TYPES))
def test_critical_types_cannot_lose_in_app(critical: str) -> None:
    with pytest.raises(DomainError) as exc:
        merge_prefs(None, {"types": {critical: {"push": False, "in_app": False}}})
    assert exc.value.code == "VALIDATION_ERROR"
    # push may be disabled as long as in-app remains
    assert merge_prefs(None, {"types": {critical: {"push": False}}}).types[critical].in_app is True


def test_partial_patch_merges_onto_current() -> None:
    current = merge_prefs(None, {"types": {"briefing": {"push": False}}}).model_dump(mode="json")
    updated = merge_prefs(current, {"do_not_disturb": {"enabled": False}})
    assert updated.types["briefing"].push is False  # preserved
    assert updated.do_not_disturb.enabled is False


def test_unknown_type_and_keys_rejected() -> None:
    with pytest.raises(DomainError):
        merge_prefs(None, {"types": {"marketing": {"push": True}}})
    with pytest.raises(DomainError):
        merge_prefs(None, {"sound": "loud"})


def test_empty_dnd_window_rejected() -> None:
    with pytest.raises(DomainError):
        merge_prefs(None, {"do_not_disturb": {"start_time": "08:00", "end_time": "08:00"}})


@pytest.mark.parametrize(
    ("start", "end", "probe", "inside"),
    [
        (time(22), time(7), time(23, 30), True),  # crosses midnight
        (time(22), time(7), time(3), True),
        (time(22), time(7), time(7), False),  # end is exclusive
        (time(22), time(7), time(12), False),
        (time(13), time(14), time(13, 30), True),  # same-day window
        (time(13), time(14), time(14, 30), False),
    ],
)
def test_dnd_window(start: time, end: time, probe: time, inside: bool) -> None:
    prefs = merge_prefs(
        None, {"do_not_disturb": {"start_time": start.isoformat(), "end_time": end.isoformat()}}
    )
    assert prefs.in_dnd(probe) is inside


def test_dnd_disabled_never_quiet() -> None:
    prefs = merge_prefs(None, {"do_not_disturb": {"enabled": False}})
    assert prefs.in_dnd(time(23)) is False
