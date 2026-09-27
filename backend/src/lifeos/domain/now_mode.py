"""Now Mode candidates (design §16.7; R7.2–7.4).

Deterministic answer to "what should I be doing right now?", in order:
    1. current block   — scheduled now and still open (Started first, then earlier start, then
                         higher linked-Goal priority)
    2. behind schedule — past its end and still Planned or Started; always returned, so the agent
                         has to acknowledge the gap (R7.4)
    3. next unstarted  — the next Planned action later today (R7.3)
    4. buffer          — nothing left today: the next open Task of the highest-priority active Goal,
                         or that Goal itself when it has no open Task
The Personal Assistant picks one candidate and explains the choice; the grounding validator rejects a
recommendation whose ID is not in this list (design §16.7), so the ordering here is the product rule.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.goals import priority_sort_key
from lifeos.domain.schedule.common import goal_of_source
from lifeos.domain.tasks import OPEN_TASK_STATUSES
from lifeos.domain.timeutil import local_date

Kind = Literal["current_block", "next_unstarted", "buffer_task", "buffer_goal"]
OPEN_STATUSES = ("Planned", "Started")


@dataclass(frozen=True)
class ActionView:
    """A Daily Action as Now Mode sees it, with the priority of the Goal it rolls up to."""

    id: uuid.UUID
    title: str
    start: datetime
    end: datetime
    status: str
    goal_priority: int = 0
    goal_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Candidate:
    kind: Kind
    id: uuid.UUID
    title: str
    start: datetime | None = None
    end: datetime | None = None
    status: str | None = None
    reason: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "title": self.title,
            "scheduled_start": None if self.start is None else self.start.isoformat(),
            "scheduled_end": None if self.end is None else self.end.isoformat(),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class NowPlan:
    candidates: list[Candidate]
    behind_schedule: list[Candidate]

    @property
    def recommended(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def as_json(self) -> dict[str, Any]:
        return {
            "now_candidates": [c.as_json() for c in self.candidates],
            "behind_schedule": [c.as_json() for c in self.behind_schedule],
            "recommended_id": None if self.recommended is None else str(self.recommended.id),
        }


# --------------------------------------------------------------------------- pure rules


def current_block(actions: Sequence[ActionView], now: datetime) -> list[ActionView]:
    """Open actions scheduled around `now`: Started first, then earlier start, then Goal priority."""
    live = [a for a in actions if a.start <= now < a.end and a.status in OPEN_STATUSES]
    return sorted(live, key=lambda a: (a.status != "Started", a.start, -a.goal_priority, str(a.id)))


def behind_schedule(actions: Sequence[ActionView], now: datetime) -> list[ActionView]:
    """Past its scheduled end and still open (R7.4)."""
    late = [a for a in actions if now >= a.end and a.status in OPEN_STATUSES]
    return sorted(late, key=lambda a: (a.end, str(a.id)))


def next_unstarted(actions: Sequence[ActionView], now: datetime) -> ActionView | None:
    """The next Planned action starting later today (R7.3)."""
    upcoming = [a for a in actions if a.start > now and a.status == "Planned"]
    return min(upcoming, key=lambda a: (a.start, str(a.id)), default=None)


def plan(
    actions: Sequence[ActionView],
    now: datetime,
    buffer_task: tuple[uuid.UUID, str] | None = None,
    buffer_goal: tuple[uuid.UUID, str] | None = None,
) -> NowPlan:
    """Ordered candidates plus the behind-schedule list. The buffer is only used when the day holds
    nothing else (R7.3): no open current block and nothing still to start."""
    candidates: list[Candidate] = []
    for action in current_block(actions, now):
        candidates.append(
            Candidate(
                "current_block",
                action.id,
                action.title,
                action.start,
                action.end,
                action.status,
                "scheduled for now",
            )
        )
    upcoming = next_unstarted(actions, now)
    if upcoming is not None:
        candidates.append(
            Candidate(
                "next_unstarted",
                upcoming.id,
                upcoming.title,
                upcoming.start,
                upcoming.end,
                upcoming.status,
                "next on today's schedule",
            )
        )
    late = [
        Candidate("current_block", a.id, a.title, a.start, a.end, a.status, "past its scheduled end")
        for a in behind_schedule(actions, now)
    ]
    if not candidates:
        if buffer_task is not None:
            candidates.append(
                Candidate(
                    "buffer_task",
                    buffer_task[0],
                    buffer_task[1],
                    reason="nothing scheduled: the next task of your highest-priority goal",
                )
            )
        elif buffer_goal is not None:
            candidates.append(
                Candidate(
                    "buffer_goal",
                    buffer_goal[0],
                    buffer_goal[1],
                    reason="nothing scheduled: time for your highest-priority goal",
                )
            )
    return NowPlan(candidates, late)


# --------------------------------------------------------------------------- service


class NowModeService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def candidates(self, s: AsyncSession, user_id: uuid.UUID, day: date | None = None) -> NowPlan:
        now = self._clock.now()
        tz = (await s.execute(select(m.User.timezone).where(m.User.id == user_id))).scalar_one()
        target = day or local_date(now, tz)
        actions = list(
            (
                await s.execute(
                    select(m.DailyAction).where(
                        m.DailyAction.user_id == user_id,
                        m.DailyAction.date == target,
                        m.DailyAction.lifecycle_state == "active",
                    )
                )
            ).scalars()
        )
        priorities = await self._goal_priorities(s, actions)
        views = [
            ActionView(
                action.id,
                action.title,
                action.scheduled_start,
                action.scheduled_end,
                action.status,
                *priorities.get(action.id, (0, None)),
            )
            for action in actions
        ]
        task, goal = await self._buffer(s, user_id)
        return plan(views, now, task, goal)

    async def _goal_priorities(
        self, s: AsyncSession, actions: Sequence[m.DailyAction]
    ) -> dict[uuid.UUID, tuple[int, uuid.UUID | None]]:
        priorities: dict[uuid.UUID, tuple[int, uuid.UUID | None]] = {}
        cache: dict[uuid.UUID, int] = {}
        for action in actions:
            goal_id = await goal_of_source(s, action.source_type, action.source_id)
            if goal_id is None:
                continue
            if goal_id not in cache:
                priority = (
                    await s.execute(select(m.Goal.priority).where(m.Goal.id == goal_id))
                ).scalar_one_or_none()
                cache[goal_id] = priority or 0
            priorities[action.id] = (cache[goal_id], goal_id)
        return priorities

    async def _buffer(
        self, s: AsyncSession, user_id: uuid.UUID
    ) -> tuple[tuple[uuid.UUID, str] | None, tuple[uuid.UUID, str] | None]:
        """The highest-priority active Goal (design §15.2 ordering) and its next open Task."""
        goals = list(
            (
                await s.execute(
                    select(m.Goal).where(
                        m.Goal.user_id == user_id,
                        m.Goal.status == "active",
                        m.Goal.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        if not goals:
            return None, None
        goal = sorted(goals, key=priority_sort_key)[0]
        objectives = select(m.Objective.id).where(m.Objective.goal_id == goal.id)
        projects = select(m.Project.id).where(m.Project.objective_id.in_(objectives))
        task = (
            await s.execute(
                select(m.Task)
                .where(
                    m.Task.user_id == user_id,
                    m.Task.deleted_at.is_(None),
                    m.Task.status.in_(OPEN_TASK_STATUSES),
                    or_(m.Task.goal_id == goal.id, m.Task.project_id.in_(projects)),
                )
                .order_by(m.Task.due_date.nulls_last(), m.Task.created_at, m.Task.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return (None if task is None else (task.id, task.title)), (goal.id, goal.title)
