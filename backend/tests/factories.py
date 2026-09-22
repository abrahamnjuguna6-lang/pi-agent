# pyright: reportPrivateImportUsage=false, reportIncompatibleVariableOverride=false
# factory-boy ships no precise stubs; its declarative Meta/attribute API is dynamic.
"""Test data factories (T1.4).

factory-boy builds ORM instances with valid defaults (`build` strategy only); `persist()` and
the `make_*` helpers insert them through an AsyncSession. Helpers build complete aggregates
(goal tree, routine, habit, daily action with its initial check-in, commitment with links,
memory entry) so later modules start from realistic data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from decimal import Decimal
from typing import Any, TypeVar

import factory
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m

T = TypeVar("T")
UTC = dt.UTC


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class _Base(factory.Factory):  # type: ignore[misc]
    class Meta:
        abstract = True
        strategy = factory.BUILD_STRATEGY


class UserFactory(_Base):
    class Meta:
        model = m.User

    id = factory.LazyFunction(uuid.uuid4)
    email_ciphertext = factory.Sequence(lambda n: f"enc-user{n}@example.test".encode())
    email_lookup_hash = factory.Sequence(lambda n: _hash(f"user{n}@example.test"))
    email_verified = True
    password_hash = "$argon2id$test"
    timezone = "UTC"
    wake_time = dt.time(6, 0)
    sleep_time = dt.time(22, 30)


class GoalFactory(_Base):
    class Meta:
        model = m.Goal

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Goal {n}")
    category = "Career"
    priority = 3


class ObjectiveFactory(_Base):
    class Meta:
        model = m.Objective

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Objective {n}")
    metric_direction = "higher_is_better"
    current_value = Decimal("0")
    target_value = Decimal("12")
    unit = "books"
    target_date = dt.date(2026, 12, 31)


class ProjectFactory(_Base):
    class Meta:
        model = m.Project

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Project {n}")


class TaskFactory(_Base):
    class Meta:
        model = m.Task

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Task {n}")


class HabitFactory(_Base):
    class Meta:
        model = m.Habit

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Habit {n}")
    recurrence_type = "daily"
    frequency_target = 1
    preferred_start = dt.time(7, 0)
    duration_minutes = 30


class RoutineTemplateFactory(_Base):
    class Meta:
        model = m.RoutineTemplate

    id = factory.LazyFunction(uuid.uuid4)
    title = "Weekday routine"
    active_days = factory.LazyFunction(lambda: [1, 2, 3, 4, 5])


class RoutineEntryFactory(_Base):
    class Meta:
        model = m.RoutineEntry

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Block {n}")
    start_time = dt.time(9, 0)
    end_time = dt.time(10, 0)


class DailyActionFactory(_Base):
    class Meta:
        model = m.DailyAction

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Action {n}")
    date = dt.date(2026, 9, 21)
    occurrence_date = factory.SelfAttribute("date")
    scheduled_start = dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    scheduled_end = dt.datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    status = "Planned"
    source_type = "MANUAL"
    source_id = None


class CommitmentFactory(_Base):
    class Meta:
        model = m.Commitment

    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Commitment {n}")
    source = "User-stated"
    due_date = dt.date(2026, 9, 30)
    current_due_date = factory.SelfAttribute("due_date")
    completion_condition = "explicit"


class MemoryEntryFactory(_Base):
    class Meta:
        model = m.MemoryStoreEntry

    id = factory.LazyFunction(uuid.uuid4)
    content = factory.Sequence(lambda n: f"I value deep work #{n}")
    type = "Value"
    source = "User-stated"
    importance = 7
    confidence = Decimal("1.00")


async def persist(session: AsyncSession, *objs: Any) -> None:
    session.add_all(objs)
    await session.flush()


async def make_user(session: AsyncSession, **kw: Any) -> m.User:
    user = UserFactory(**kw)
    await persist(session, user)
    return user


async def make_goal_tree(session: AsyncSession, user: m.User) -> dict[str, Any]:
    """Goal → Objective → Project → Task, plus a Habit under the Goal."""
    goal = GoalFactory(user_id=user.id)
    await persist(session, goal)
    objective = ObjectiveFactory(user_id=user.id, goal_id=goal.id)
    await persist(session, objective)
    project = ProjectFactory(user_id=user.id, objective_id=objective.id)
    await persist(session, project)
    task = TaskFactory(user_id=user.id, project_id=project.id, goal_id=goal.id)
    habit = HabitFactory(user_id=user.id, goal_id=goal.id)
    await persist(session, task, habit)
    return {"goal": goal, "objective": objective, "project": project, "task": task, "habit": habit}


async def make_routine(session: AsyncSession, user: m.User, entries: int = 2) -> dict[str, Any]:
    template = RoutineTemplateFactory(user_id=user.id)
    await persist(session, template)
    made = [
        RoutineEntryFactory(
            user_id=user.id,
            routine_template_id=template.id,
            sort_order=i,
            start_time=dt.time(9 + i, 0),
            end_time=dt.time(10 + i, 0),
        )
        for i in range(entries)
    ]
    await persist(session, *made)
    return {"template": template, "entries": made}


async def make_daily_action(session: AsyncSession, user: m.User, **kw: Any) -> m.DailyAction:
    """Daily Action plus its initial `none → Planned` Check-in Record (design §16.2)."""
    action = DailyActionFactory(user_id=user.id, **kw)
    await persist(session, action)
    checkin = m.CheckinRecord(
        daily_action_id=action.id,
        user_id=user.id,
        previous_status=None,
        new_status="Planned",
        transition_source="System",
    )
    await persist(session, checkin)
    return action


async def make_commitment(
    session: AsyncSession, user: m.User, daily_actions: list[m.DailyAction] | None = None, **kw: Any
) -> m.Commitment:
    actions = daily_actions or []
    condition = {0: "explicit", 1: "single"}.get(len(actions), kw.pop("completion_condition", "all"))
    commitment = CommitmentFactory(user_id=user.id, completion_condition=condition, **kw)
    await persist(session, commitment)
    links = [
        m.CommitmentLink(
            commitment_id=commitment.id, user_id=user.id, entity_type="DailyAction", entity_id=a.id
        )
        for a in actions
    ]
    if links:
        await persist(session, *links)
    return commitment


async def make_memory_entry(session: AsyncSession, user: m.User, **kw: Any) -> m.MemoryStoreEntry:
    entry = MemoryEntryFactory(user_id=user.id, **kw)
    await persist(session, entry)
    return entry
