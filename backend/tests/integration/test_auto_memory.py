"""T7.4: automatic Memory creation from Check-in notes and Reflections (design §10.7; R4.4, R11.5)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select, update

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

EMAIL = "tom@example.com"
MON = date(2026, 9, 21)


async def manual(me: AuthedClient, title: str = "Write the report") -> str:
    r = await me.post(
        "/daily-actions",
        {"title": title, "date": "2026-09-21", "start_time": "14:00:00", "end_time": "15:00:00"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["data"]["id"])


async def check_in(me: AuthedClient, action_id: str, status: str, note: str | None = None) -> None:
    body: dict[str, Any] = {"new_status": status} | ({"note": note} if note else {})
    r = await me.post(f"/daily-actions/{action_id}/checkins", body)
    assert r.status_code == 201, r.text


async def memories(api: Api) -> list[m.MemoryStoreEntry]:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        return list(
            (
                await s.execute(
                    select(m.MemoryStoreEntry).order_by(m.MemoryStoreEntry.created_at, m.MemoryStoreEntry.id)
                )
            ).scalars()
        )


async def test_a_skip_reason_becomes_one_fact(api: Api) -> None:  # R4.4
    me = await api.as_user(EMAIL)
    action = await manual(me)
    await check_in(me, action, "Skipped", "Migraine all afternoon")
    assert await memories(api) == []  # nothing until the outbox is drained

    await api.container.outbox.drain()
    (entry,) = await memories(api)
    assert (entry.type, entry.source, entry.is_inference) == ("Fact", "User-stated", False)
    assert entry.source_ref_type == "checkin"
    # the note is stored with its Daily Action title and local date, so it embeds into something
    # meaningful on its own (design §23.3)
    assert entry.content == "Write the report on 2026-09-21 — skipped: Migraine all afternoon"
    assert entry.embedding_status == "pending"


async def test_reprocessing_the_event_does_not_duplicate_the_entry(api: Api) -> None:
    me = await api.as_user(EMAIL)
    action = await manual(me)
    await check_in(me, action, "Skipped", "Migraine all afternoon")
    await api.container.outbox.drain()
    async with system_session(sessionmaker=api.container.sessionmaker) as s:  # replay the event
        await s.execute(
            update(m.DomainEvent)
            .where(m.DomainEvent.event_type == "daily_action.status_changed")
            .values(processed_at=None, attempts=0)
        )
    await api.container.outbox.drain()
    assert len(await memories(api)) == 1


async def test_a_transition_without_a_note_creates_nothing(api: Api) -> None:
    me = await api.as_user(EMAIL)
    action = await manual(me)
    await check_in(me, action, "Started")
    await check_in(me, action, "Completed")
    await api.container.outbox.drain()
    assert await memories(api) == []


async def test_categories_come_from_the_source_goal(api: Api) -> None:  # design §10.7
    me = await api.as_user(EMAIL)
    await goal_with_habit(me, start_date="2026-09-01")  # a Fitness goal
    await generate(api, me, MON)
    (habit_action,) = await actions(api, date=MON, source_type="HABIT")
    await check_in(me, str(habit_action.id), "Skipped", "Knee pain")
    await api.container.outbox.drain()
    (entry,) = await memories(api)
    assert entry.categories == ["Fitness"]
    assert entry.content.startswith("Run on 2026-09-21 — skipped: Knee pain")


async def test_completion_notes_are_stored_too(api: Api) -> None:
    me = await api.as_user(EMAIL)
    action = await manual(me)
    await check_in(me, action, "Completed", "Finally shipped the draft")
    await api.container.outbox.drain()
    (entry,) = await memories(api)
    assert entry.content.endswith("completed: Finally shipped the draft")


async def test_the_accountability_reflection_is_not_stored_twice(api: Api) -> None:
    """The submission writes its Memory entry in the same transaction (design §19.8), so the
    `reflection.submitted` handler must skip it."""
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        reflection = m.Reflection(
            user_id=uid,
            date=MON,
            type="accountability",
            answers={"obstacle": "late nights"},
            content="Accountability reflection on Run.",
        )
        s.add(reflection)
        await s.flush()
        from lifeos.events.outbox import publish

        await publish(
            s,
            "reflection.submitted",
            uid,
            {"reflection_id": str(reflection.id), "type": "accountability"},
            at=api.clock.now(),
        )
    await api.container.outbox.drain()
    assert await memories(api) == []


async def test_a_daily_reflection_becomes_a_memory_entry(api: Api) -> None:  # R11.5 (flow lands in T13.5)
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        reflection = m.Reflection(
            user_id=uid,
            date=MON,
            type="daily",
            answers={"accomplished": "shipped the draft"},
            content="Shipped the draft and felt clear afterwards.",
            goal_categories=["Career"],
        )
        s.add(reflection)
        await s.flush()
        from lifeos.events.outbox import publish

        await publish(
            s,
            "reflection.submitted",
            uid,
            {"reflection_id": str(reflection.id), "type": "daily"},
            at=api.clock.now(),
        )
    await api.container.outbox.drain()
    (entry,) = await memories(api)
    assert (entry.type, entry.source) == ("Reflection", "User-stated")
    assert entry.categories == ["Career", "Reflections"]
    assert entry.source_ref_type == "reflection"
    await api.container.outbox.drain()
    assert len(await memories(api)) == 1
