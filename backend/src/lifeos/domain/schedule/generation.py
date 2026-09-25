"""Daily Action generation from Routine Templates and Habits (design §12.1, §17.1, §20.2; R1.13, R2.3–2.4).

`plan_occurrences()` is pure (which sources apply on a local date, and when); `generate_for_user()`
persists the plan idempotently: each Daily Action and its initial `none → Planned` check-in
(`transition_source='System'`) are inserted together, and existing active occurrences are skipped via
the active-occurrence unique index (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.timeutil import block_instants, local_date, local_weekday_sunday0

# Arbiter predicate of `uq_daily_actions_occurrence`, written as literal SQL on purpose: PostgreSQL can
# only match a partial unique index when it can prove the ON CONFLICT predicate implies the index
# predicate, which it cannot do for bound parameters once psycopg prepares the statement server-side
# (it then fails with "no unique or exclusion constraint matching the ON CONFLICT specification").
OCCURRENCE_INDEX_WHERE = text(
    "source_type IN ('ROUTINE_ENTRY','HABIT','TASK') AND lifecycle_state = 'active'"
)

DEFAULT_HABIT_START = time(8, 0)
DEFAULT_HABIT_MINUTES = 30


@dataclass(frozen=True)
class TemplateSpec:
    id: uuid.UUID
    active_days: list[int]


@dataclass(frozen=True)
class EntrySpec:
    id: uuid.UUID
    template_id: uuid.UUID
    title: str
    start_time: time
    end_time: time
    habit_id: uuid.UUID | None


@dataclass(frozen=True)
class HabitSpec:
    id: uuid.UUID
    title: str
    recurrence_type: str
    recurrence_days: list[int] | None
    preferred_start: time | None
    duration_minutes: int | None
    start_date: date | None
    end_date: date | None
    created_on: date
    pauses: list[tuple[date, date | None]] = field(default_factory=list[tuple[date, date | None]])
    blocked: bool = False  # archived status / archived goal subtree


@dataclass(frozen=True)
class Planned:
    source_type: str
    source_id: uuid.UUID
    occurrence_date: date
    title: str
    start: datetime
    end: datetime
    template_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DayOverrides:
    """User edits that apply to generation on one local date."""

    removed_entries: frozenset[uuid.UUID] = frozenset()
    modified_entries: dict[uuid.UUID, tuple[datetime, datetime]] = field(
        default_factory=dict[uuid.UUID, tuple[datetime, datetime]]
    )
    removed_habits: frozenset[uuid.UUID] = frozenset()
    rescheduled_habits: dict[uuid.UUID, tuple[datetime, datetime]] = field(
        default_factory=dict[uuid.UUID, tuple[datetime, datetime]]
    )


def habit_scheduled_on(habit: HabitSpec, day: date) -> bool:
    if habit.blocked:
        return False
    first = habit.start_date or habit.created_on
    if day < first or (habit.end_date is not None and day > habit.end_date):
        return False
    if any(start <= day and (end is None or day <= end) for start, end in habit.pauses):
        return False
    if habit.recurrence_type == "daily":
        return True
    return local_weekday_sunday0(day) in (habit.recurrence_days or [])


def plan_occurrences(
    day: date,
    tz: str,
    templates: list[TemplateSpec],
    entries: list[EntrySpec],
    habits: list[HabitSpec],
    overrides: DayOverrides,
    wake_time: time | None = None,
) -> list[Planned]:
    weekday = local_weekday_sunday0(day)
    planned: list[Planned] = []
    active_templates = {t.id for t in templates if weekday in t.active_days}
    routine_habits: set[uuid.UUID] = set()

    for entry in entries:
        if entry.template_id not in active_templates or entry.id in overrides.removed_entries:
            continue
        if entry.habit_id is not None:
            routine_habits.add(entry.habit_id)
        if entry.id in overrides.modified_entries:
            start, end = overrides.modified_entries[entry.id]
        else:
            start, end = block_instants(day, entry.start_time, entry.end_time, tz)
        planned.append(Planned("ROUTINE_ENTRY", entry.id, day, entry.title, start, end, entry.template_id))

    for habit in habits:
        # A habit placed in today's routine is represented by that routine entry's action (§17.1).
        if habit.id in routine_habits or habit.id in overrides.removed_habits:
            continue
        if not habit_scheduled_on(habit, day):
            continue
        if habit.id in overrides.rescheduled_habits:
            start, end = overrides.rescheduled_habits[habit.id]
        else:
            begin = habit.preferred_start or wake_time or DEFAULT_HABIT_START
            finish = (
                datetime.combine(day, begin)
                + timedelta(minutes=habit.duration_minutes or DEFAULT_HABIT_MINUTES)
            ).time()
            start, end = block_instants(day, begin, finish, tz)
        planned.append(Planned("HABIT", habit.id, day, habit.title, start, end))
    return planned


@dataclass(frozen=True)
class GenerationResult:
    created: int
    days: list[date]


class GenerationService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def generate_for_user(
        self, s: AsyncSession, user_id: uuid.UUID, local_dates: list[date] | None = None
    ) -> GenerationResult:
        user = (await s.execute(select(m.User).where(m.User.id == user_id))).scalar_one()
        tz = user.timezone
        if local_dates is None:
            today = local_date(self._clock.now(), tz)
            local_dates = [today, today + timedelta(days=1)]
        templates, entries = await self._load_routines(s, user_id)
        habits = await self._load_habits(s, user_id, tz)
        created = 0
        for day in local_dates:
            overrides = await self._load_overrides(s, user_id, day)
            plan = plan_occurrences(day, tz, templates, entries, habits, overrides, user.wake_time)
            created += await self._persist(s, user_id, day, plan)
        return GenerationResult(created=created, days=list(local_dates))

    async def _load_routines(
        self, s: AsyncSession, user_id: uuid.UUID
    ) -> tuple[list[TemplateSpec], list[EntrySpec]]:
        templates = [
            TemplateSpec(t.id, list(t.active_days))
            for t in (
                await s.execute(
                    select(m.RoutineTemplate).where(
                        m.RoutineTemplate.user_id == user_id, m.RoutineTemplate.deleted_at.is_(None)
                    )
                )
            ).scalars()
        ]
        entries = [
            EntrySpec(e.id, e.routine_template_id, e.title, e.start_time, e.end_time, e.habit_id)
            for e in (
                await s.execute(
                    select(m.RoutineEntry).where(
                        m.RoutineEntry.user_id == user_id, m.RoutineEntry.deleted_at.is_(None)
                    )
                )
            ).scalars()
        ]
        return templates, entries

    async def _load_habits(self, s: AsyncSession, user_id: uuid.UUID, tz: str) -> list[HabitSpec]:
        archived_goals = set(
            (
                await s.execute(
                    select(m.Goal.id).where(m.Goal.user_id == user_id, m.Goal.status == "archived")
                )
            ).scalars()
        )
        project_goal: dict[uuid.UUID, uuid.UUID] = {
            project_id: goal_id
            for project_id, goal_id in (
                await s.execute(
                    select(m.Project.id, m.Objective.goal_id)
                    .join(m.Objective, m.Objective.id == m.Project.objective_id)
                    .where(m.Project.user_id == user_id)
                )
            ).tuples()
        }
        pauses: dict[uuid.UUID, list[tuple[date, date | None]]] = {}
        for p in (
            await s.execute(select(m.HabitPausePeriod).where(m.HabitPausePeriod.user_id == user_id))
        ).scalars():
            pauses.setdefault(p.habit_id, []).append((p.starts_on, p.ends_on))
        specs: list[HabitSpec] = []
        for h in (
            await s.execute(select(m.Habit).where(m.Habit.user_id == user_id, m.Habit.deleted_at.is_(None)))
        ).scalars():
            goal_id = h.goal_id or (project_goal.get(h.project_id) if h.project_id else None)
            specs.append(
                HabitSpec(
                    id=h.id,
                    title=h.title,
                    recurrence_type=h.recurrence_type,
                    recurrence_days=list(h.recurrence_days) if h.recurrence_days else None,
                    preferred_start=h.preferred_start,
                    duration_minutes=h.duration_minutes,
                    start_date=h.start_date,
                    end_date=h.end_date,
                    created_on=local_date(h.created_at, tz),
                    pauses=pauses.get(h.id, []),
                    blocked=h.status != "active" or goal_id in archived_goals,
                )
            )
        return specs

    async def _load_overrides(self, s: AsyncSession, user_id: uuid.UUID, day: date) -> DayOverrides:
        removed_entries: set[uuid.UUID] = set()
        modified: dict[uuid.UUID, tuple[datetime, datetime]] = {}
        for ex in (
            await s.execute(
                select(m.RoutineException)
                .where(m.RoutineException.user_id == user_id, m.RoutineException.date == day)
                .order_by(m.RoutineException.created_at, m.RoutineException.id)  # latest edit wins
            )
        ).scalars():
            if ex.routine_entry_id is None:
                continue
            if ex.exception_type == "removed":
                removed_entries.add(ex.routine_entry_id)
            elif ex.exception_type == "modified" and "new_start" in ex.exception_payload:
                modified[ex.routine_entry_id] = (
                    datetime.fromisoformat(ex.exception_payload["new_start"]),
                    datetime.fromisoformat(ex.exception_payload["new_end"]),
                )
        removed_habits: set[uuid.UUID] = set()
        rescheduled: dict[uuid.UUID, tuple[datetime, datetime]] = {}
        for ov in (
            await s.execute(
                select(m.HabitOccurrenceOverride).where(
                    m.HabitOccurrenceOverride.user_id == user_id,
                    m.HabitOccurrenceOverride.occurrence_date == day,
                )
            )
        ).scalars():
            if ov.override_type == "removed":
                removed_habits.add(ov.habit_id)
            elif ov.new_start is not None and ov.new_end is not None:
                rescheduled[ov.habit_id] = (ov.new_start, ov.new_end)
        return DayOverrides(frozenset(removed_entries), modified, frozenset(removed_habits), rescheduled)

    async def _persist(self, s: AsyncSession, user_id: uuid.UUID, day: date, plan: list[Planned]) -> int:
        now = self._clock.now()
        instances: dict[uuid.UUID, uuid.UUID] = {}
        for template_id in {p.template_id for p in plan if p.template_id is not None}:
            await s.execute(
                insert(m.RoutineInstance)
                .values(routine_template_id=template_id, user_id=user_id, date=day, created_at=now)
                .on_conflict_do_nothing(index_elements=["user_id", "routine_template_id", "date"])
            )
            instances[template_id] = (
                await s.execute(
                    select(m.RoutineInstance.id).where(
                        m.RoutineInstance.user_id == user_id,
                        m.RoutineInstance.routine_template_id == template_id,
                        m.RoutineInstance.date == day,
                    )
                )
            ).scalar_one()

        created = 0
        for p in plan:
            action_id = (
                await s.execute(
                    insert(m.DailyAction)
                    .values(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        title=p.title,
                        date=day,
                        occurrence_date=p.occurrence_date,
                        scheduled_start=p.start,
                        scheduled_end=p.end,
                        status="Planned",
                        source_type=p.source_type,
                        source_id=p.source_id,
                        routine_instance_id=instances.get(p.template_id) if p.template_id else None,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["user_id", "source_type", "source_id", "occurrence_date"],
                        index_where=OCCURRENCE_INDEX_WHERE,
                    )
                    .returning(m.DailyAction.id)
                )
            ).scalar_one_or_none()
            if action_id is None:
                continue  # already generated (idempotent)
            s.add(
                m.CheckinRecord(
                    daily_action_id=action_id,
                    user_id=user_id,
                    previous_status=None,
                    new_status="Planned",
                    transition_source="System",
                    created_at=now,
                )
            )
            created += 1
        await s.flush()
        return created
