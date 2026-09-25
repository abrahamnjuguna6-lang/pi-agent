"""Accountability Engine: deterministic escalation levels (design §14.2–14.4, §19.8; R8.1–8.8, R8.10).

Occurrence levels (every source, no stored state):
    1 Reminder — the action reached its scheduled start and is still Planned
    2 Nudge    — the action passed its scheduled end and is still Planned or Started
Source levels (HABIT / ROUTINE_ENTRY, tracked per stable source identity, never by title):
    3 Challenge  — Skipped on >= 3 distinct local dates in the last 7 local days
    4 Reflection — Skipped on >= 5 distinct local dates in the last 7 local days (reflection required)
    5 Pattern    — the last >= 5 resolved scheduled occurrences were all Skipped, the latest within
                   the last 7 local days
The highest applicable level wins. Only resolved occurrences (Completed / Skipped) form a consecutive
run, so days without a scheduled occurrence never break it, and pause days are excluded entirely.

Episodes start at Level >= 3 and end when the level returns to 1. Reaching Level 4 or 5 requires one
written reflection per episode; while it is pending the level cannot be reduced. A reduction needs
Completed occurrences on >= 3 distinct dates in the 7-day window, all later than the recovery anchor;
it lowers the level by exactly 1 and moves the anchor to the latest completion date used. Only skips
after the anchor count toward re-escalation, so the dates already "paid for" by a recovery cannot
immediately escalate the source again. Evaluation is a pure function of the check-in state, so running
it again with no new check-ins changes nothing (idempotent).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.checkins import TransitionContext
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.lineage import goal_category
from lifeos.domain.memory.service import MemoryDraft, MemoryService
from lifeos.domain.proactive import raise_flag, resolve_flags
from lifeos.domain.schedule.common import goal_of_source, user_timezone
from lifeos.domain.timeutil import local_date
from lifeos.events.outbox import publish

SourceType = Literal["HABIT", "ROUTINE_ENTRY"]
Outcome = Literal["Completed", "Skipped", "Open"]
Decision = Literal["keep", "change", "drop"]
SOURCE_TYPES: tuple[str, ...] = ("HABIT", "ROUTINE_ENTRY")
WINDOW_DAYS = 7
LOOKBACK_DAYS = 60  # consecutive runs of a sparse (e.g. weekly) source span several weeks
CHALLENGE_SKIPS = 3
REFLECTION_SKIPS = 5
PATTERN_RUN = 5
RECOVERY_DAYS = 3


# --------------------------------------------------------------------------- pure rules


def occurrence_level(status: str, lifecycle_state: str, start: datetime, end: datetime, now: datetime) -> int:
    """Level 1/2 for one Daily Action at `now` (0 = nothing due)."""
    if lifecycle_state != "active":
        return 0
    if now >= end and status in ("Planned", "Started"):
        return 2
    if now >= start and status == "Planned":
        return 1
    return 0


def in_pause(day: date, pauses: Sequence[tuple[date, date | None]]) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in pauses)


def day_outcomes(
    occurrences: Sequence[tuple[date, str]], pauses: Sequence[tuple[date, date | None]]
) -> dict[date, Outcome]:
    """One outcome per scheduled local date: Completed beats Skipped beats unresolved (Open)."""
    outcomes: dict[date, Outcome] = {}
    for day, status in occurrences:
        if in_pause(day, pauses):
            continue
        current = outcomes.get(day, "Open")
        if status == "Completed" or current == "Completed":
            outcomes[day] = "Completed"
        elif status == "Skipped" or current == "Skipped":
            outcomes[day] = "Skipped"
        else:
            outcomes[day] = "Open"
    return outcomes


def trailing_skip_run(outcomes: dict[date, Outcome]) -> tuple[int, date | None]:
    """Length of the run of Skipped resolved occurrences ending at the latest resolved one, and the
    date of that latest skip."""
    resolved = sorted((d for d, o in outcomes.items() if o != "Open"), reverse=True)
    run = 0
    for day in resolved:
        if outcomes[day] != "Skipped":
            break
        run += 1
    return run, resolved[0] if run else None


def pattern_level(outcomes: dict[date, Outcome], today: date, since: date | None) -> int:
    """The source level implied by skips on or before `today` and after `since` (the recovery anchor)."""
    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    considered: dict[date, Outcome] = {
        d: o for d, o in outcomes.items() if d <= today and (since is None or d > since)
    }
    skip_days = sum(1 for d, o in considered.items() if o == "Skipped" and d >= window_start)
    run, latest = trailing_skip_run(considered)
    if run >= PATTERN_RUN and latest is not None and latest >= window_start:
        return 5
    if skip_days >= REFLECTION_SKIPS:
        return 4
    if skip_days >= CHALLENGE_SKIPS:
        return 3
    return 1


def recovery_dates(outcomes: dict[date, Outcome], today: date, since: date | None) -> list[date]:
    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    return sorted(
        d
        for d, o in outcomes.items()
        if o == "Completed" and window_start <= d <= today and (since is None or d > since)
    )


@dataclass(frozen=True)
class SourceState:
    level: int
    reflection_required: bool
    reflection_done: bool  # a reflection was submitted in the current episode
    anchor: date | None


BASELINE = SourceState(level=1, reflection_required=False, reflection_done=False, anchor=None)


@dataclass(frozen=True)
class Evaluation:
    level: int
    reflection_required: bool
    anchor: date | None
    new_episode: bool = False
    reduced: bool = False


def evaluate(state: SourceState, outcomes: dict[date, Outcome], today: date) -> Evaluation:
    """Next source state (design §14.2–14.4). Escalation wins over recovery; a pending reflection
    blocks recovery; recovery lowers the level by exactly one."""
    pattern = pattern_level(outcomes, today, state.anchor)
    if pattern > state.level:
        new_episode = state.level == 1
        done = state.reflection_done and not new_episode
        required = state.reflection_required or (pattern >= 4 and not done)
        return Evaluation(pattern, required, state.anchor, new_episode=new_episode)
    unchanged = Evaluation(state.level, state.reflection_required, state.anchor)
    if state.level == 1 or state.reflection_required:
        return unchanged
    completions = recovery_dates(outcomes, today, state.anchor)
    if len(completions) < RECOVERY_DAYS:
        return unchanged
    return Evaluation(state.level - 1, False, completions[-1], reduced=True)


def flag_dedupe_key(episode_id: uuid.UUID, level: int) -> str:
    return f"escalation:{episode_id}:{level}"


# --------------------------------------------------------------------------- service


@dataclass(frozen=True)
class OccurrenceTrigger:
    """A due Level 1/2 notification for one Daily Action; the notification pipeline (T9) delivers it
    once per `dedupe_key`."""

    daily_action_id: uuid.UUID
    level: int
    dedupe_key: str


@dataclass(frozen=True)
class EscalationView:
    state: m.AccountabilityEscalationState
    source_title: str | None
    goal_id: uuid.UUID | None
    skips: list[tuple[date, str | None]]  # last 7 local days, most recent first (design §19.8)


def _state_of(row: m.AccountabilityEscalationState | None) -> SourceState:
    if row is None:
        return BASELINE
    return SourceState(
        row.level,
        row.reflection_required,
        row.reflection_completed_at is not None,
        row.recovery_window_anchor_date,
    )


class AccountabilityService:
    def __init__(self, clock: Clock, memory: MemoryService) -> None:
        self._clock = clock
        self._memory = memory

    # ------------------------------------------------------------------ Level 1/2 (per occurrence)

    async def occurrence_triggers(self, s: AsyncSession, user_id: uuid.UUID) -> list[OccurrenceTrigger]:
        """Level 1/2 triggers due now for today's and yesterday's open actions (R8.2–8.3)."""
        now = self._clock.now()
        today = local_date(now, await user_timezone(s, user_id))
        rows = (
            await s.execute(
                select(m.DailyAction)
                .where(
                    m.DailyAction.user_id == user_id,
                    m.DailyAction.lifecycle_state == "active",
                    m.DailyAction.status.in_(("Planned", "Started")),
                    m.DailyAction.date >= today - timedelta(days=1),
                    m.DailyAction.scheduled_start <= now,
                )
                .order_by(m.DailyAction.scheduled_start, m.DailyAction.id)
            )
        ).scalars()
        triggers: list[OccurrenceTrigger] = []
        for action in rows:
            level = occurrence_level(
                action.status, action.lifecycle_state, action.scheduled_start, action.scheduled_end, now
            )
            if level:
                triggers.append(OccurrenceTrigger(action.id, level, f"accountability_l{level}:{action.id}"))
        return triggers

    # ------------------------------------------------------------------ Level 3–5 (per source)

    async def evaluate_source(
        self, s: AsyncSession, user_id: uuid.UUID, source_type: SourceType, source_id: uuid.UUID
    ) -> m.AccountabilityEscalationState | None:
        now = self._clock.now()
        today = local_date(now, await user_timezone(s, user_id))
        occurrences = (
            await s.execute(
                select(m.DailyAction.date, m.DailyAction.status).where(
                    m.DailyAction.user_id == user_id,
                    m.DailyAction.source_type == source_type,
                    m.DailyAction.source_id == source_id,
                    m.DailyAction.lifecycle_state == "active",
                    m.DailyAction.date >= today - timedelta(days=LOOKBACK_DAYS),
                    m.DailyAction.date <= today,
                )
            )
        ).tuples()
        pauses = (  # habit pauses (a routine entry has none, so this is empty for it)
            await s.execute(
                select(m.HabitPausePeriod.starts_on, m.HabitPausePeriod.ends_on).where(
                    m.HabitPausePeriod.habit_id == source_id, m.HabitPausePeriod.user_id == user_id
                )
            )
        ).tuples()
        row = (
            await s.execute(
                select(m.AccountabilityEscalationState)
                .where(
                    m.AccountabilityEscalationState.user_id == user_id,
                    m.AccountabilityEscalationState.source_type == source_type,
                    m.AccountabilityEscalationState.source_id == source_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        decision = evaluate(_state_of(row), day_outcomes(list(occurrences), list(pauses)), today)
        if row is None and decision.level < 3:
            return None  # persistent state exists only once a source reaches Level 3 (§14.2)
        if row is None:
            row = m.AccountabilityEscalationState(
                id=uuid.uuid4(),
                user_id=user_id,
                source_type=source_type,
                source_id=source_id,
                level=1,
                escalation_episode_id=uuid.uuid4(),
                episode_started_at=now,
                reflection_required=False,
                created_at=now,
                updated_at=now,
                last_evaluated_at=now,
            )
            s.add(row)
        previous = row.level
        if decision.new_episode:
            row.escalation_episode_id = uuid.uuid4()
            row.episode_started_at = now
            row.reflection_completed_at = None
            row.reflection_id = None
        if decision.reduced:
            row.last_reduced_at = now
        row.level = decision.level
        row.reflection_required = decision.reflection_required
        row.recovery_window_anchor_date = decision.anchor
        row.last_evaluated_at = now
        await s.flush()
        if row.level != previous:
            row.updated_at = now
            await self._level_changed(s, row, previous, now)
        return row

    async def evaluate_user(
        self, s: AsyncSession, user_id: uuid.UUID
    ) -> list[m.AccountabilityEscalationState]:
        """`accountability_evaluation` sweep for one User: every recurring source with recent
        occurrences, plus every source still escalated (so recovery and expiry run without check-ins)."""
        today = local_date(self._clock.now(), await user_timezone(s, user_id))
        recent = select(m.DailyAction.source_type, m.DailyAction.source_id).where(
            m.DailyAction.user_id == user_id,
            m.DailyAction.source_type.in_(SOURCE_TYPES),
            m.DailyAction.source_id.is_not(None),
            m.DailyAction.date >= today - timedelta(days=WINDOW_DAYS - 1),
            m.DailyAction.date <= today,
        )
        escalated = select(
            m.AccountabilityEscalationState.source_type, m.AccountabilityEscalationState.source_id
        ).where(m.AccountabilityEscalationState.user_id == user_id, m.AccountabilityEscalationState.level > 1)
        sources = sorted(
            (await s.execute(union(recent, escalated))).tuples(), key=lambda x: (x[0], str(x[1]))
        )
        results: list[m.AccountabilityEscalationState] = []
        for source_type, source_id in sources:
            state = await self.evaluate_source(s, user_id, source_type, source_id)  # type: ignore[arg-type]
            results.extend([state] if state is not None else [])
        return results

    async def checkin_hook(
        self,
        s: AsyncSession,
        action: m.DailyAction,
        previous: str,
        new: str,
        note: str | None,
        ctx: TransitionContext,
    ) -> None:
        """Evaluate the affected source immediately, in the check-in's transaction (§14.2)."""
        if new in ("Completed", "Skipped") and action.source_type in SOURCE_TYPES and action.source_id:
            await self.evaluate_source(s, action.user_id, action.source_type, action.source_id)  # type: ignore[arg-type]

    async def _level_changed(
        self, s: AsyncSession, row: m.AccountabilityEscalationState, previous: int, now: datetime
    ) -> None:
        await publish(
            s,
            "escalation.level_changed",
            row.user_id,
            {
                "escalation_id": str(row.id),
                "escalation_episode_id": str(row.escalation_episode_id),
                "source_type": row.source_type,
                "source_id": str(row.source_id),
                "previous_level": previous,
                "level": row.level,
                "reflection_required": row.reflection_required,
            },
            at=now,
        )
        if row.level >= 3:
            await raise_flag(
                s,
                row.user_id,
                "escalation_level_3plus",
                "accountability",
                flag_dedupe_key(row.escalation_episode_id, row.level),
                now,
                ref_type="escalation_episode",
                ref_id=row.escalation_episode_id,
                payload={"escalation_id": str(row.id), "level": row.level, "source_type": row.source_type},
            )
        else:
            await resolve_flags(
                s, row.user_id, "escalation_level_3plus", now, ref_id=row.escalation_episode_id
            )

    # ------------------------------------------------------------------ reads

    async def _get(
        self, s: AsyncSession, user_id: uuid.UUID, escalation_id: uuid.UUID, *, lock: bool = False
    ) -> m.AccountabilityEscalationState:
        query = select(m.AccountabilityEscalationState).where(
            m.AccountabilityEscalationState.id == escalation_id,
            m.AccountabilityEscalationState.user_id == user_id,
        )
        if lock:
            query = query.with_for_update()
        row = (await s.execute(query)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("escalation")
        return row

    async def list_escalations(
        self, s: AsyncSession, user_id: uuid.UUID, include_resolved: bool = False
    ) -> list[EscalationView]:
        query = select(m.AccountabilityEscalationState).where(
            m.AccountabilityEscalationState.user_id == user_id
        )
        if not include_resolved:
            query = query.where(m.AccountabilityEscalationState.level > 1)
        rows = (
            await s.execute(
                query.order_by(
                    m.AccountabilityEscalationState.level.desc(), m.AccountabilityEscalationState.created_at
                )
            )
        ).scalars()
        return [await self._view(s, row) for row in rows]

    async def get_escalation(
        self, s: AsyncSession, user_id: uuid.UUID, escalation_id: uuid.UUID
    ) -> EscalationView:
        return await self._view(s, await self._get(s, user_id, escalation_id))

    async def _view(self, s: AsyncSession, row: m.AccountabilityEscalationState) -> EscalationView:
        today = local_date(self._clock.now(), await user_timezone(s, row.user_id))
        skips = (
            await s.execute(
                select(m.DailyAction.date, m.CheckinRecord.note)
                .join(m.CheckinRecord, m.CheckinRecord.daily_action_id == m.DailyAction.id)
                .where(
                    m.DailyAction.user_id == row.user_id,
                    m.DailyAction.source_type == row.source_type,
                    m.DailyAction.source_id == row.source_id,
                    m.DailyAction.date >= today - timedelta(days=WINDOW_DAYS - 1),
                    m.DailyAction.date <= today,
                    m.CheckinRecord.new_status == "Skipped",
                )
                .order_by(m.DailyAction.date.desc())
            )
        ).tuples()
        return EscalationView(
            row,
            await self._source_title(s, row.source_type, row.source_id),
            await goal_of_source(s, row.source_type, row.source_id),
            list(skips),
        )

    async def _source_title(self, s: AsyncSession, source_type: str, source_id: uuid.UUID) -> str | None:
        model = m.Habit if source_type == "HABIT" else m.RoutineEntry
        return (await s.execute(select(model.title).where(model.id == source_id))).scalar_one_or_none()

    # ------------------------------------------------------------------ Level 4/5 reflection (§19.8)

    async def submit_reflection(
        self, s: AsyncSession, user_id: uuid.UUID, escalation_id: uuid.UUID, answers: dict[str, Any]
    ) -> tuple[m.AccountabilityEscalationState, m.Reflection]:
        """Writes the accountability reflection and its Memory entry, clears the reflection gate, then
        re-evaluates the source (a pending recovery may now apply). `answers`: obstacle, easier, decision."""
        row = await self._get(s, user_id, escalation_id, lock=True)
        if not row.reflection_required:
            raise DomainError("INVALID_TRANSITION", "No reflection is pending for this escalation")
        now = self._clock.now()
        today = local_date(now, await user_timezone(s, user_id))
        title = await self._source_title(s, row.source_type, row.source_id)
        category = await goal_category(s, await goal_of_source(s, row.source_type, row.source_id))
        content = (
            f"Accountability reflection on {title or 'a recurring action'}. "
            f"What gets in the way: {answers['obstacle']} "
            f"What would make it easier: {answers['easier']} "
            f"Decision: {answers['decision']}."
        )
        reflection = m.Reflection(
            id=uuid.uuid4(),
            user_id=user_id,
            date=today,
            type="accountability",
            answers=answers,
            content=content,
            goal_categories=[category] if category else [],
            escalation_episode_id=row.escalation_episode_id,
            created_at=now,
            updated_at=now,
        )
        s.add(reflection)
        await s.flush()
        await self._memory.create(
            s,
            user_id,
            MemoryDraft(
                content=content,
                type="Reflection",
                source="User-stated",
                categories=["Reflections", *([category] if category else [])],
                source_ref_type="reflection",
                source_ref_id=reflection.id,
            ),
            "accountability_reflection",
        )
        row.reflection_required = False
        row.reflection_completed_at = now
        row.reflection_id = reflection.id
        row.updated_at = now
        await publish(
            s,
            "reflection.submitted",
            user_id,
            {
                "reflection_id": str(reflection.id),
                "type": "accountability",
                "escalation_id": str(row.id),
                "decision": answers["decision"],
            },
            at=now,
        )
        await s.flush()
        await self.evaluate_source(s, user_id, row.source_type, row.source_id)  # type: ignore[arg-type]
        return row, reflection
