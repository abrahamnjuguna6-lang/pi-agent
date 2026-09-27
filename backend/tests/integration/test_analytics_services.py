"""M8 services against real data: Now Mode, day analysis, correlations and the weekly summary."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

EMAIL = "wren@example.com"
MON = date(2026, 9, 21)  # the frozen clock is 2026-09-21 12:00 UTC


async def manual(me: AuthedClient, title: str, start: str, end: str, day: date = MON) -> dict[str, Any]:
    r = await me.post(
        "/daily-actions",
        {"title": title, "date": day.isoformat(), "start_time": start, "end_time": end},
    )
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


# --------------------------------------------------------------------------- Now Mode (§16.7)


async def test_now_mode_recommends_the_current_block_and_reports_lateness(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await manual(me, "Morning run", "07:00:00", "07:30:00")  # past its end, still Planned
    await manual(me, "Deep work", "11:30:00", "12:30:00")  # now 12:00 → the current block
    await manual(me, "Gym", "18:00:00", "19:00:00")

    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        plan = await api.container.now_mode.candidates(s, uid)
    assert plan.recommended is not None
    assert (plan.recommended.kind, plan.recommended.title) == ("current_block", "Deep work")
    assert [c.title for c in plan.candidates] == ["Deep work", "Gym"]
    assert [c.title for c in plan.behind_schedule] == ["Morning run"]  # R7.4


async def test_now_mode_falls_back_to_the_top_goals_next_task(api: Api) -> None:  # R7.3
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    low = (await me.post("/goals", {"title": "Someday", "category": "Personal", "priority": 1})).json()[
        "data"
    ]
    high = (await me.post("/goals", {"title": "Ship it", "category": "Career", "priority": 5})).json()["data"]
    await me.post("/tasks", {"title": "Ignore me", "goal_id": low["id"]})
    await me.post("/tasks", {"title": "Write the spec", "goal_id": high["id"]})

    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        plan = await api.container.now_mode.candidates(s, uid)
    assert plan.recommended is not None
    assert (plan.recommended.kind, plan.recommended.title) == ("buffer_task", "Write the spec")


async def test_now_mode_suggests_the_goal_when_it_has_no_open_task(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await me.post("/goals", {"title": "Learn to sail", "category": "Personal", "priority": 4})
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        plan = await api.container.now_mode.candidates(s, uid)
    assert plan.recommended is not None
    assert (plan.recommended.kind, plan.recommended.title) == ("buffer_goal", "Learn to sail")


async def test_goal_priority_orders_two_actions_in_the_same_block(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    low = (await me.post("/goals", {"title": "Chores", "category": "Personal", "priority": 1})).json()["data"]
    high = (await me.post("/goals", {"title": "Career", "category": "Career", "priority": 5})).json()["data"]
    for goal, title in ((low, "Tidy up"), (high, "Client proposal")):
        task = (await me.post("/tasks", {"title": title, "goal_id": goal["id"]})).json()["data"]
        await me.post(
            f"/tasks/{task['id']}/schedule",
            {"date": MON.isoformat(), "start_time": "11:30:00", "end_time": "12:30:00"},
        )
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        plan = await api.container.now_mode.candidates(s, uid)
    assert [c.title for c in plan.candidates] == ["Client proposal", "Tidy up"]


# --------------------------------------------------------------------------- day analysis (§16.8)


async def test_analyze_day_finds_conflicts_and_late_blocks(api: Api) -> None:  # R6.4
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await me.patch("/me", {"wake_time": "06:00:00", "sleep_time": "22:30:00"})  # set during onboarding
    await manual(me, "Standup", "09:00:00", "09:30:00")
    await manual(me, "Client call", "09:15:00", "10:00:00")
    await manual(me, "Late email", "23:00:00", "23:30:00")

    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        analysis = await api.container.schedule_analysis.analyze_day(s, uid, MON)
    assert len(analysis.conflicts) == 1
    assert analysis.conflicts[0].overlap_minutes == 15
    assert [b.title for b in analysis.outside_waking] == ["Late email"]
    assert analysis.has_findings is True


async def test_a_cancelled_action_is_not_a_conflict(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    first = await manual(me, "Standup", "09:00:00", "09:30:00")
    await manual(me, "Client call", "09:15:00", "10:00:00")
    assert (await me.post(f"/daily-actions/{first['id']}/cancel", {})).status_code == 200
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        analysis = await api.container.schedule_analysis.analyze_day(s, uid, MON)
    assert analysis.conflicts == []
    assert analysis.booked_minutes == 45


async def test_a_packed_morning_is_an_overloaded_window(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    for hour in (9, 10, 11):
        await manual(me, f"Block {hour}", f"{hour:02d}:00:00", f"{hour + 1:02d}:00:00")
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        analysis = await api.container.schedule_analysis.analyze_day(s, uid, MON)
    assert len(analysis.overloaded_windows) == 1
    assert analysis.overloaded_windows[0].largest_gap_minutes == 0


# --------------------------------------------------------------------------- correlations (§16.5)


async def add_reflection(api: Api, uid: Any, day: date, energy: int, mood: int) -> None:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        s.add(
            m.Reflection(
                user_id=uid,
                date=day,
                type="daily",
                answers={},
                content=f"Reflection for {day}",
                energy=energy,
                mood=mood,
            )
        )


async def seed_days(api: Api, me: AuthedClient, uid: Any, days: int, *, correlated: bool) -> None:
    """One scheduled action per day, completed on high-energy days when `correlated`."""
    for index in range(days):
        day = MON - timedelta(days=index + 1)
        action = await manual(me, f"Task {index}", "09:00:00", "10:00:00", day=day)
        energy = 1 + index % 5
        if (energy >= 3) if correlated else (index % 2 == 0):
            r = await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Completed"})
            assert r.status_code == 201, r.text
        await add_reflection(api, uid, day, energy=energy, mood=energy)


async def test_correlation_needs_twenty_observations(api: Api) -> None:  # R11.6
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await seed_days(api, me, uid, 10, correlated=True)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        results = await api.container.correlation.refresh(s, uid, MON)
    assert results["energy"].n == 10
    assert results["energy"].status == "preliminary"

    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        rows = list((await s.execute(select(m.AnalyticsResult).order_by(m.AnalyticsResult.kind))).scalars())
    assert [row.kind for row in rows] == ["energy_completion_corr", "mood_completion_corr"]
    assert rows[0].result["status"] == "preliminary"
    assert rows[0].result["n"] == 10


async def test_a_strong_relationship_over_twenty_days_is_significant(api: Api) -> None:  # R11.7–11.8
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await seed_days(api, me, uid, 25, correlated=True)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        results = await api.container.correlation.refresh(s, uid, MON)
    energy = results["energy"]
    assert (energy.n, energy.status, energy.method) == (25, "significant", "spearman")
    assert energy.coefficient is not None
    assert energy.coefficient > 0.5
    assert energy.surfaceable is True


async def test_days_without_a_scheduled_action_are_not_observations(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await add_reflection(api, uid, MON - timedelta(days=1), energy=4, mood=4)  # no actions that day
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        observations = await api.container.correlation.observations(s, uid, MON)
    assert observations == []


async def test_refreshing_twice_updates_the_stored_row(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    await seed_days(api, me, uid, 8, correlated=True)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        await api.container.correlation.refresh(s, uid, MON)
        await api.container.correlation.refresh(s, uid, MON)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        rows = list((await s.execute(select(m.AnalyticsResult))).scalars())
    assert len(rows) == 2  # one row per kind per date (design §24.9)


# --------------------------------------------------------------------------- weekly summary (§16.10)


async def test_weekly_summary_ranks_wins_gaps_and_categories(api: Api) -> None:  # R10.2
    """The week is lived with the clock inside it, because the Completion Rate is evaluated as of the
    end of the period: actions created afterwards did not exist yet (design §16.3)."""
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    last_week = MON - timedelta(days=7)  # Monday 2026-09-14

    api.clock.set(datetime(2026, 9, 14, 12, 0, tzinfo=UTC))
    me = await api.relogin(EMAIL)
    goal_id, _ = await goal_with_habit(me, start_date="2026-09-01")  # a Fitness goal + "Run" habit
    await me.patch(f"/goals/{goal_id}", {"priority": 5})
    career = (await me.post("/goals", {"title": "Career", "category": "Career", "priority": 2})).json()[
        "data"
    ]
    task = (await me.post("/tasks", {"title": "Write the spec", "goal_id": career["id"]})).json()["data"]

    await generate(api, me, *[last_week + timedelta(days=offset) for offset in range(7)])
    for index, action in enumerate(await actions(api, source_type="HABIT")):
        status = "Completed" if index < 4 else "Skipped"
        body: dict[str, Any] = {"new_status": status} | ({"note": "too busy"} if status == "Skipped" else {})
        assert (await me.post(f"/daily-actions/{action.id}/checkins", body)).status_code == 201

    scheduled = (
        await me.post(
            f"/tasks/{task['id']}/schedule",
            {
                "date": (last_week + timedelta(days=1)).isoformat(),
                "start_time": "09:00:00",
                "end_time": "10:00:00",
            },
        )
    ).json()["data"]
    await me.post(f"/daily-actions/{scheduled['id']}/checkins", {"new_status": "Completed"})

    commitment = (
        await me.post(
            "/commitments",
            {
                "title": "Ship the spec",
                "due_date": (last_week + timedelta(days=3)).isoformat(),
                "links": [{"entity_type": "Goal", "entity_id": career["id"]}],
            },
        )
    ).json()["data"]
    api.clock.set(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    me = await api.relogin(EMAIL)
    assert (await me.post(f"/commitments/{commitment['id']}/keep")).status_code == 200

    api.clock.set(datetime(2026, 9, 21, 12, 0, tzinfo=UTC))  # the following Monday: review time
    me = await api.relogin(EMAIL)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        summary = await api.container.weekly.summarize(s, uid)

    assert (summary.week_start, summary.week_end) == (last_week, last_week + timedelta(days=6))
    assert summary.completion_rate.completed == 5  # 4 habit runs + the scheduled task
    assert [(row.category, row.completed, row.total) for row in summary.by_category] == [
        ("Career", 1, 1),
        ("Fitness", 4, 7),
    ]
    wins = {(win.kind, win.title) for win in summary.wins}
    assert ("source", "Run") in wins  # the habit, ranked first by its priority-5 goal
    assert ("commitment", "Ship the spec") in wins
    assert summary.wins[0].title == "Run"
    assert [(gap.kind, gap.title) for gap in summary.gaps] == [("source", "Run")]
    assert summary.gaps[0].misses == 3
    assert (summary.kept_commitments, summary.broken_commitments) == (1, 0)
    assert summary.as_json()["week_start"] == last_week.isoformat()


async def test_unattributable_actions_are_reported_as_unlinked(api: Api) -> None:  # design §16.10
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    last_week = MON - timedelta(days=7)
    action = await manual(me, "Call the bank", "09:00:00", "09:30:00", day=last_week)
    await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Completed"})
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        summary = await api.container.weekly.summarize(s, uid)
    assert [(row.category, row.completed) for row in summary.by_category] == [("Unlinked", 1)]


async def test_the_integrity_trend_compares_with_the_week_before(api: Api) -> None:  # R10.2
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        s.add_all(
            [
                m.IntegrityScoreSnapshot(
                    user_id=uid,
                    local_date=date(2026, 9, 13),
                    score=Decimal("60.0"),
                    kept=3,
                    broken=2,
                    overdue_deferred=0,
                ),
                m.IntegrityScoreSnapshot(
                    user_id=uid,
                    local_date=date(2026, 9, 20),
                    score=Decimal("80.0"),
                    kept=4,
                    broken=1,
                    overdue_deferred=0,
                ),
            ]
        )
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        summary = await api.container.weekly.summarize(s, uid)
    assert (summary.integrity_score, summary.integrity_delta) == (Decimal("80.0"), Decimal("20.0"))


async def test_an_empty_week_summarizes_to_nothing(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        summary = await api.container.weekly.summarize(s, uid)
    assert (summary.wins, summary.gaps, summary.by_category) == ([], [], [])
    assert summary.completion_rate.rate is None
    assert (summary.integrity_score, summary.integrity_delta) == (None, None)
