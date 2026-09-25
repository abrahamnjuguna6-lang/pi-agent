"""T6.5: the Level 4/5 reflection gate — it blocks recovery until submitted (R8.5, R8.10, design §19.8)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

EMAIL = "kira@example.com"
MON = date(2026, 9, 21)
ANSWERS = {
    "obstacle": "I schedule the run at 07:00 but I am still working at midnight.",
    "easier": "Move it to lunchtime and keep my kit at the office.",
    "decision": "change",
}


def days(*offsets: int) -> list[date]:
    return [MON - timedelta(days=o) for o in offsets]


async def check_in(me: AuthedClient, action: m.DailyAction, status: str, note: str | None = None) -> None:
    body = {"new_status": status} | ({"note": note} if note else {})
    r = await me.post(f"/daily-actions/{action.id}/checkins", body)
    assert r.status_code == 201, r.text


async def escalation_of(me: AuthedClient) -> dict[str, Any]:
    r = await me.get("/accountability/escalations")
    assert r.status_code == 200, r.text
    (state,) = r.json()["data"]
    return dict(state)


async def at_level_4(api: Api, me: AuthedClient) -> dict[str, Any]:
    """Five skips inside the window, with a completion breaking the run (Level 4, not Level 5)."""
    await goal_with_habit(me, start_date="2026-09-01")
    await generate(api, me, *days(5, 4, 3, 2, 1, 0))
    statuses = ["Skipped"] * 3 + ["Completed"] + ["Skipped"] * 2
    for day, status in zip(days(5, 4, 3, 2, 1, 0), statuses, strict=True):
        for action in await actions(api, date=day, source_type="HABIT"):
            await check_in(me, action, status, "no time" if status == "Skipped" else None)
    state = await escalation_of(me)
    assert (state["level"], state["reflection_required"]) == (4, True)
    return state


async def complete_next_days(api: Api, me: AuthedClient, *dates: date) -> AuthedClient:
    """Generate and complete the habit on later dates (the recovery evidence, R8.10)."""
    for day in dates:
        api.clock.set(datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12))
        me = await api.relogin(EMAIL)
        await generate(api, me, day)
        for action in await actions(api, date=day, source_type="HABIT"):
            await check_in(me, action, "Completed")
    return me


async def test_reflection_gate_blocks_and_then_releases_recovery(api: Api) -> None:
    me = await api.as_user(EMAIL)
    state = await at_level_4(api, me)

    me = await complete_next_days(api, me, *[MON + timedelta(days=d) for d in (1, 2, 3)])
    blocked = await escalation_of(me)
    assert (blocked["level"], blocked["reflection_required"]) == (4, True)  # three completions, still gated

    r = await me.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["reflection"]["type"] == "accountability"
    assert body["reflection"]["escalation_episode_id"] == state["escalation_episode_id"]
    assert body["reflection"]["answers"] == ANSWERS
    assert body["reflection"]["goal_categories"] == ["Fitness"]
    # submitting releases the gate, and the same evaluation applies the pending recovery (§14.4)
    assert (body["escalation"]["level"], body["escalation"]["reflection_required"]) == (3, False)
    assert body["escalation"]["reflection_completed_at"] is not None
    assert body["escalation"]["recovery_window_anchor_date"] == (MON + timedelta(days=3)).isoformat()

    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        reflections = list(
            (await s.execute(select(m.Reflection).where(m.Reflection.user_id == uid))).scalars()
        )
        memories = list(
            (await s.execute(select(m.MemoryStoreEntry).where(m.MemoryStoreEntry.user_id == uid))).scalars()
        )
        state_row = (
            await s.execute(
                select(m.AccountabilityEscalationState).where(m.AccountabilityEscalationState.user_id == uid)
            )
        ).scalar_one()
    assert len(reflections) == 1
    assert state_row.reflection_id == reflections[0].id
    assert [(e.type, e.source, e.categories) for e in memories] == [
        ("Reflection", "User-stated", ["Fitness", "Reflections"])
    ]
    assert ANSWERS["obstacle"] in memories[0].content


async def test_the_same_completions_do_not_reduce_twice(api: Api) -> None:  # design §14.4
    me = await api.as_user(EMAIL)
    state = await at_level_4(api, me)
    me = await complete_next_days(api, me, *[MON + timedelta(days=d) for d in (1, 2, 3)])
    await me.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert (await escalation_of(me))["level"] == 3

    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        (again,) = await api.container.accountability.evaluate_user(s, uid)
    assert again.level == 3  # the anchor blocks a second reduction from the same completions

    me = await complete_next_days(api, me, *[MON + timedelta(days=d) for d in (4, 5, 6)])
    assert (await escalation_of(me))["level"] == 2  # new completions reduce by exactly one more level

    me = await complete_next_days(api, me, *[MON + timedelta(days=d) for d in (7, 8, 9)])
    r = await me.get("/accountability/escalations")
    assert r.json()["data"] == []  # back to baseline: the episode is over (R8.10)
    (ended,) = (await me.get("/accountability/escalations", include_resolved=True)).json()["data"]
    assert (ended["level"], ended["id"]) == (1, state["id"])
    assert ended["last_reduced_at"] is not None


async def test_reflection_requires_a_pending_gate(api: Api) -> None:
    me = await api.as_user(EMAIL)
    state = await at_level_4(api, me)
    first = await me.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert first.status_code == 201, first.text
    second = await me.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert (second.status_code, second.json()["error"]["code"]) == (409, "INVALID_TRANSITION")


async def test_reflection_of_another_user_is_not_found(api: Api) -> None:
    me = await api.as_user(EMAIL)
    state = await at_level_4(api, me)
    other = await api.as_user("mallory@example.com")
    r = await other.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert r.status_code == 404


async def test_escalating_to_level_5_needs_no_second_reflection(api: Api) -> None:  # design §14.3
    me = await api.as_user(EMAIL)
    state = await at_level_4(api, me)
    r = await me.post(f"/accountability/escalations/{state['id']}/reflection", ANSWERS)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["escalation"]["level"] == 4  # no completions yet, so no reduction

    # three more skipped occurrences make the trailing run five long → Level 5, with no new gate
    me2 = me
    for offset in (1, 2, 3):
        day = MON + timedelta(days=offset)
        api.clock.set(datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12))
        me2 = await api.relogin(EMAIL)
        await generate(api, me2, day)
        for action in await actions(api, date=day, source_type="HABIT"):
            await check_in(me2, action, "Skipped", "again")
    final = await escalation_of(me2)
    assert (final["level"], final["reflection_required"]) == (5, False)
    assert final["reflection_completed_at"] is not None
