"""T5.9: recording habit occurrences through the state machine, and metrics via the API (R1.11–1.12)."""

from datetime import date, timedelta

from tests.integration.api_harness import Api
from tests.integration.sched_helpers import actions, generate, goal_with_habit

MON = date(2026, 9, 21)


async def test_partial_gives_completed_action_but_no_target_credit(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me)
    await generate(api, me, MON)
    r = await me.post(
        f"/habits/{habit['id']}/occurrences",
        {"date": "2026-09-21", "result": "partial", "completion_percent": 50},
    )
    assert r.status_code == 201, r.text
    assert (r.json()["data"]["result"], r.json()["data"]["completion_percent"]) == ("partial", 50)
    (action,) = await actions(api)
    assert action.status == "Completed"  # the block was executed (§17.2)
    history = (await me.get(f"/daily-actions/{action.id}/checkins")).json()["data"]
    assert history[-1]["note"] == "partial 50%"
    metrics = (await me.get(f"/habits/{habit['id']}/metrics")).json()["data"]
    assert (metrics["partials"], metrics["current_streak"], metrics["total_completions"]) == (1, 0, 0)


async def test_generic_checkin_writes_occurrence_record(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me)
    await generate(api, me, MON)
    (action,) = await actions(api)
    await me.post(f"/daily-actions/{action.id}/checkins", {"new_status": "Completed"})
    metrics = (await me.get(f"/habits/{habit['id']}/metrics")).json()["data"]
    assert (metrics["current_streak"], metrics["total_completions"]) == (1, 1)


async def test_skip_via_occurrence_requires_reason(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me)
    await generate(api, me, MON)
    r = await me.post(f"/habits/{habit['id']}/occurrences", {"date": "2026-09-21", "result": "skipped"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "SKIP_REASON_REQUIRED")
    r = await me.post(
        f"/habits/{habit['id']}/occurrences", {"date": "2026-09-21", "result": "skipped", "note": "rain"}
    )
    assert r.status_code == 201
    (action,) = await actions(api)
    assert action.status == "Skipped"


async def test_unscheduled_day_occurrence_and_future_rejected(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me, recurrence_type="weekly", recurrence_days=[3], frequency_target=1)
    r = await me.post(f"/habits/{habit['id']}/occurrences", {"date": "2026-09-21", "result": "completed"})
    assert r.status_code == 201  # Monday isn't scheduled; still counts toward the weekly target
    assert (await actions(api)) == []
    metrics = (await me.get(f"/habits/{habit['id']}/metrics")).json()["data"]
    assert metrics["period_kind"] == "week"
    assert metrics["periods"][-1]["met"] is True
    r = await me.post(f"/habits/{habit['id']}/occurrences", {"date": "2026-09-23", "result": "completed"})
    assert r.status_code == 422  # future


async def test_percent_rules(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me)
    r = await me.post(f"/habits/{habit['id']}/occurrences", {"date": "2026-09-21", "result": "partial"})
    assert r.status_code == 422
    r = await me.post(
        f"/habits/{habit['id']}/occurrences",
        {"date": "2026-09-21", "result": "completed", "completion_percent": 40},
    )
    assert r.status_code == 422


async def test_metrics_report_pause_periods(api: Api) -> None:
    me = await api.as_user("uma@example.com")
    _, habit = await goal_with_habit(me)
    await me.post(f"/habits/{habit['id']}/pause", {"starts_on": "2026-09-22", "reason": "travel"})
    api.clock.set(api.clock.now() + timedelta(days=3))
    me = await api.relogin("uma@example.com")
    metrics = (await me.get(f"/habits/{habit['id']}/metrics")).json()["data"]
    assert metrics["pause_periods"] == [{"starts_on": "2026-09-22", "ends_on": None, "reason": "travel"}]
    assert [p["excluded"] for p in metrics["periods"]][-3:] == [True, True, True]
