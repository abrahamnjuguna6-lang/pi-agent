"""Commitment lifecycle and Promise Ledger (design §11.1–11.3, §11.6, §24.7; R9.1–9.10, R9.15).

Valid transitions (anything else → INVALID_COMMITMENT_TRANSITION):
    Open     → Kept | Broken | Deferred | Cancelled
    Deferred → Kept | Broken | Deferred (re-defer: explanation + acknowledgment + new due date)

Every accepted transition appends an immutable `commitment_events` row and refreshes today's integrity
snapshot in the same transaction. Completion is decided here, never by the LLM:
    single / any → a linked Daily Action is Completed; all → every non-removed link is Completed;
    explicit     → only an explicit User action.
Deadlines (the due date ends at the end of the User's local day):
    Open past due → Broken. Deferred past due → a 24 h explanation window opens; if the window ends
    without a re-deferral (explanation, then acknowledgment + new due date), it becomes Broken.
Cancelling a linked Daily Action (Daily Actions are never deleted) removes the link, may change the
condition (no links left → explicit), re-evaluates the Commitment and notifies the User (R9.9).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.checkins import TransitionContext
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.integrity import IntegrityService
from lifeos.domain.lineage import EntityType, goal_category, goal_of_entity
from lifeos.domain.memory.service import MemoryDraft, MemoryService
from lifeos.domain.pagination import Page, SortKey, clamp_limit, keyset_sorted, to_sorted_page
from lifeos.domain.schedule.common import Actor, user_timezone
from lifeos.domain.timeutil import local_date, local_day_bounds
from lifeos.events.outbox import publish

Status = Literal["Open", "Kept", "Broken", "Deferred", "Cancelled"]
Condition = Literal["single", "all", "any", "explicit"]
DeferralKind = Literal["deferred", "redeferred"]
DeadlineAction = Literal["break", "open_window"]
SortField = Literal["created_at", "due_date", "status", "goal_category"]

TRANSITIONS: dict[str, frozenset[str]] = {
    "Open": frozenset({"Kept", "Broken", "Deferred", "Cancelled"}),
    "Deferred": frozenset({"Kept", "Broken", "Deferred"}),
    "Kept": frozenset(),
    "Broken": frozenset(),
    "Cancelled": frozenset(),
}
ACTIVE: tuple[str, ...] = ("Open", "Deferred")
EXPLANATION_MIN = 20
EXPLANATION_WINDOW = timedelta(hours=24)
UNLINKED = "Unlinked"  # ledger filter value for Commitments without a Goal (design §11.6)
_STAMP = {"Kept": "kept_at", "Broken": "broken_at", "Cancelled": "cancelled_at"}
_EVENT = {"Kept": "kept", "Broken": "broken", "Cancelled": "cancelled"}


# --------------------------------------------------------------------------- pure rules


def ensure_transition(current: str, new: str) -> None:
    if new not in TRANSITIONS[current]:
        raise DomainError(
            "INVALID_COMMITMENT_TRANSITION",
            f"A {current} commitment cannot become {new}",
            {"from": current, "to": new},
        )


def resolve_condition(requested: str | None, daily_action_links: int) -> Condition:
    """Validate the completion condition against the number of linked Daily Actions (R9.2, §11.2)."""
    if daily_action_links == 0:
        if requested not in (None, "explicit"):
            raise DomainError(
                "VALIDATION_ERROR",
                "Without linked daily actions a commitment is kept only explicitly",
                {"field": "completion_condition"},
            )
        return "explicit"
    if daily_action_links == 1:
        if requested not in (None, "single"):
            raise DomainError(
                "VALIDATION_ERROR",
                "A commitment linked to one daily action uses the single condition",
                {"field": "completion_condition"},
            )
        return "single"
    if requested == "all" or requested == "any":
        return requested
    raise DomainError(
        "VALIDATION_ERROR",
        "Choose ALL or ANY for a commitment linked to several daily actions",
        {"field": "completion_condition"},
    )


def ensure_linkable(status: str, lifecycle_state: str) -> None:
    """Only open (Planned/Started, not cancelled) Daily Actions can back a new Commitment."""
    if lifecycle_state != "active" or status not in ("Planned", "Started"):
        raise DomainError(
            "VALIDATION_ERROR",
            "Only planned or started daily actions can be linked to a commitment",
            {"field": "links"},
        )


def condition_satisfied(condition: str, statuses: Sequence[str]) -> bool:
    """`statuses` are the current statuses of the non-removed linked Daily Actions."""
    if condition == "explicit" or not statuses:
        return False
    completed = [status == "Completed" for status in statuses]
    return all(completed) if condition == "all" else any(completed)


def condition_after_removal(condition: str, remaining_links: int) -> str:
    """No Daily Actions left → the Commitment can only be kept explicitly (§11.3)."""
    return "explicit" if remaining_links == 0 else condition


def window_open(window_ends_at: datetime | None, now: datetime) -> bool:
    return window_ends_at is not None and now < window_ends_at


def deadline_action(
    status: str, current_due: date, today: date, window_ends_at: datetime | None, now: datetime
) -> DeadlineAction | None:
    """What the deadline rules require now. The due date passes at the end of its local day."""
    if status not in ACTIVE or current_due >= today:
        return None
    if status == "Open":
        return "break"
    if window_ends_at is None:
        return "open_window"
    return "break" if now >= window_ends_at else None


def validate_deferral(
    status: str,
    new_due: date,
    current_due: date,
    today: date,
    *,
    in_window: bool,
    explained: bool,
    acknowledged: bool,
) -> DeferralKind:
    """First deferral from Open needs nothing else; a re-deferral needs the overdue explanation window,
    a submitted explanation and a separate acknowledgment (R9.10, §11.3, §44 item 7)."""
    if status not in ACTIVE:
        ensure_transition(status, "Deferred")
    if new_due <= today or new_due <= current_due:
        raise DomainError(
            "VALIDATION_ERROR",
            "The new due date must be later than today and than the current due date",
            {"field": "new_due_date"},
        )
    if status == "Open":
        return "deferred"
    if not in_window:
        raise DomainError(
            "INVALID_COMMITMENT_TRANSITION",
            "A deferred commitment can be deferred again only while its explanation window is open",
        )
    if not explained:
        raise DomainError(
            "INVALID_COMMITMENT_TRANSITION", "Explain why the commitment is overdue before deferring it again"
        )
    if not acknowledged:
        raise DomainError(
            "VALIDATION_ERROR", "Confirm the acknowledgment to defer again", {"field": "acknowledged"}
        )
    return "redeferred"


def validate_explanation(status: str, in_window: bool, already_explained: bool, text: str) -> str:
    if status != "Deferred" or not in_window:
        raise DomainError(
            "INVALID_COMMITMENT_TRANSITION",
            "An explanation can be given only for an overdue deferred commitment, within its window",
        )
    if already_explained:
        raise DomainError(
            "INVALID_COMMITMENT_TRANSITION",
            "An explanation was already given; acknowledge it and choose a new due date",
        )
    cleaned = text.strip()
    if len(cleaned) < EXPLANATION_MIN:
        raise DomainError(
            "VALIDATION_ERROR",
            f"The explanation must be at least {EXPLANATION_MIN} characters",
            {"field": "explanation"},
        )
    return cleaned


# --------------------------------------------------------------------------- service


@dataclass(frozen=True)
class LinkIn:
    entity_type: EntityType
    entity_id: uuid.UUID


@dataclass(frozen=True)
class CommitmentDetail:
    commitment: m.Commitment
    links: list[m.CommitmentLink]
    events: list[m.CommitmentEvent]


SORT_KEYS: dict[str, SortKey] = {
    "created_at": SortKey(
        m.Commitment.created_at, lambda c: c.created_at.isoformat(), datetime.fromisoformat
    ),
    "due_date": SortKey(
        m.Commitment.current_due_date, lambda c: c.current_due_date.isoformat(), date.fromisoformat
    ),
    "status": SortKey(m.Commitment.status, lambda c: c.status, str),
    "goal_category": SortKey(
        func.coalesce(m.Commitment.goal_category, ""), lambda c: c.goal_category or "", str
    ),
}


class CommitmentService:
    def __init__(
        self,
        clock: Clock,
        integrity: IntegrityService,
        memory: MemoryService,
        explanation_window: timedelta = EXPLANATION_WINDOW,
    ) -> None:
        self._clock = clock
        self._integrity = integrity
        self._memory = memory
        self._window = explanation_window

    # ------------------------------------------------------------------ reads

    async def _get(
        self, s: AsyncSession, user_id: uuid.UUID, commitment_id: uuid.UUID, *, lock: bool = False
    ) -> m.Commitment:
        query = select(m.Commitment).where(m.Commitment.id == commitment_id, m.Commitment.user_id == user_id)
        if lock:
            query = query.with_for_update()
        commitment = (await s.execute(query)).scalar_one_or_none()
        if commitment is None:
            raise NotFoundError("commitment")
        return commitment

    async def detail(self, s: AsyncSession, user_id: uuid.UUID, commitment_id: uuid.UUID) -> CommitmentDetail:
        commitment = await self._get(s, user_id, commitment_id)
        links = (
            await s.execute(
                select(m.CommitmentLink)
                .where(m.CommitmentLink.commitment_id == commitment.id)
                .order_by(m.CommitmentLink.entity_type, m.CommitmentLink.entity_id)
            )
        ).scalars()
        events = (
            await s.execute(
                select(m.CommitmentEvent)
                .where(m.CommitmentEvent.commitment_id == commitment.id)
                .order_by(m.CommitmentEvent.id)
            )
        ).scalars()
        return CommitmentDetail(commitment, list(links), list(events))

    async def list_commitments(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        goal_category: str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        sort: SortField = "created_at",
        descending: bool = True,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Page[m.Commitment]:
        """Promise Ledger (R9.15): filter by status, due-date range and goal category; sort by created
        date, due date, status or goal category. `goal_category=Unlinked` selects Commitments without one."""
        size = clamp_limit(limit)
        c = m.Commitment
        query = select(c).where(c.user_id == user_id)
        if status is not None:
            query = query.where(c.status == status)
        if goal_category == UNLINKED:
            query = query.where(c.goal_category.is_(None))
        elif goal_category is not None:
            query = query.where(c.goal_category == goal_category)
        if due_from is not None:
            query = query.where(c.current_due_date >= due_from)
        if due_to is not None:
            query = query.where(c.current_due_date <= due_to)
        key = SORT_KEYS[sort]
        rows = list((await s.execute(keyset_sorted(query, key, c.id, cursor, size, descending))).scalars())
        return to_sorted_page(rows, size, key)

    # ------------------------------------------------------------------ creation

    async def create(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        data: dict[str, Any],
        actor: Actor = "User",
        trace_id: uuid.UUID | None = None,
    ) -> m.Commitment:
        """Record a confirmed Commitment (R9.1–9.3). `data`: title, due_date, source, links,
        completion_condition."""
        tz = await user_timezone(s, user_id)
        now = self._clock.now()
        if data["due_date"] < local_date(now, tz):
            raise DomainError("VALIDATION_ERROR", "The due date cannot be in the past", {"field": "due_date"})
        links = [LinkIn(link["entity_type"], link["entity_id"]) for link in data.get("links", [])]
        if len(set(links)) != len(links):
            raise DomainError("VALIDATION_ERROR", "Each entity can be linked only once", {"field": "links"})
        goal_ids = [await goal_of_entity(s, user_id, link.entity_type, link.entity_id) for link in links]
        action_ids = [link.entity_id for link in links if link.entity_type == "DailyAction"]
        for action in (
            await s.execute(select(m.DailyAction).where(m.DailyAction.id.in_(action_ids)))
        ).scalars():
            ensure_linkable(action.status, action.lifecycle_state)
        condition = resolve_condition(data.get("completion_condition"), len(action_ids))

        commitment = m.Commitment(
            id=uuid.uuid4(),
            user_id=user_id,
            title=data["title"],
            source=data.get("source", "User-stated"),
            due_date=data["due_date"],
            current_due_date=data["due_date"],
            completion_condition=condition,
            status="Open",
            deferral_count=0,
            goal_category=await goal_category(s, goal_ids[0] if goal_ids else None),  # first link (§11.6)
            created_at=now,
            updated_at=now,
        )
        s.add(commitment)
        await s.flush()
        s.add_all(
            m.CommitmentLink(
                commitment_id=commitment.id,
                user_id=user_id,
                entity_type=link.entity_type,
                entity_id=link.entity_id,
            )
            for link in links
        )
        self._event(
            s, commitment, "created", actor, now, None, "Open", new_due=commitment.due_date, trace_id=trace_id
        )
        await s.flush()
        return commitment

    # ------------------------------------------------------------------ explicit User actions

    async def keep(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        commitment_id: uuid.UUID,
        actor: Actor = "User",
        trace_id: uuid.UUID | None = None,
    ) -> m.Commitment:
        return await self._explicit(s, user_id, commitment_id, "Kept", actor, trace_id)

    async def cancel(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        commitment_id: uuid.UUID,
        actor: Actor = "User",
        trace_id: uuid.UUID | None = None,
    ) -> m.Commitment:
        return await self._explicit(s, user_id, commitment_id, "Cancelled", actor, trace_id)

    async def _explicit(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        commitment_id: uuid.UUID,
        new_status: Status,
        actor: Actor,
        trace_id: uuid.UUID | None,
    ) -> m.Commitment:
        commitment = await self._get(s, user_id, commitment_id, lock=True)
        now = self._clock.now()
        await self._apply_deadline(s, commitment, await user_timezone(s, user_id), now)
        await self._transition(s, commitment, new_status, actor, now, trace_id=trace_id)
        await self._integrity.refresh(s, user_id)
        return commitment

    async def defer(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        commitment_id: uuid.UUID,
        new_due: date,
        acknowledged: bool = False,
        actor: Actor = "User",
        trace_id: uuid.UUID | None = None,
    ) -> m.Commitment:
        commitment = await self._get(s, user_id, commitment_id, lock=True)
        tz = await user_timezone(s, user_id)
        now = self._clock.now()
        await self._apply_deadline(s, commitment, tz, now)
        explanation = await self._window_explanation(s, commitment)
        kind = validate_deferral(
            commitment.status,
            new_due,
            commitment.current_due_date,
            local_date(now, tz),
            in_window=window_open(commitment.explanation_window_ends_at, now),
            explained=explanation is not None,
            acknowledged=acknowledged,
        )
        previous_due = commitment.current_due_date
        commitment.deferral_count += 1
        commitment.current_due_date = new_due
        commitment.explanation_window_ends_at = None
        await self._transition(
            s,
            commitment,
            "Deferred",
            actor,
            now,
            event_type=kind,
            previous_due=previous_due,
            explanation=explanation,
            trace_id=trace_id,
        )
        await self._integrity.refresh(s, user_id)
        return commitment

    async def explain(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        commitment_id: uuid.UUID,
        text: str,
        actor: Actor = "User",
        trace_id: uuid.UUID | None = None,
    ) -> m.Commitment:
        """Step 1 of a re-deferral: the written explanation, stored on the event row and as a Memory
        Fact (User-stated). Step 2 is `defer(..., acknowledged=True)` with the new due date."""
        commitment = await self._get(s, user_id, commitment_id, lock=True)
        now = self._clock.now()
        await self._apply_deadline(s, commitment, await user_timezone(s, user_id), now)
        cleaned = validate_explanation(
            commitment.status,
            window_open(commitment.explanation_window_ends_at, now),
            await self._window_explanation(s, commitment) is not None,
            text,
        )
        self._event(
            s,
            commitment,
            "explanation_submitted",
            actor,
            now,
            "Deferred",
            "Deferred",
            explanation=cleaned,
            trace_id=trace_id,
        )
        await self._memory.create(
            s,
            user_id,
            MemoryDraft(
                content=f'Explanation for the overdue commitment "{commitment.title}": {cleaned}',
                type="Fact",
                source="User-stated",
                categories=["Commitments", *([commitment.goal_category] if commitment.goal_category else [])],
                source_ref_type="commitment",
                source_ref_id=commitment.id,
            ),
            "commitment_explanation",
        )
        await s.flush()
        return commitment

    # ------------------------------------------------------------------ automatic evaluation

    async def evaluate_deadlines(self, s: AsyncSession, user_id: uuid.UUID) -> int:
        """`commitment_evaluation` sweep for one User (R9.7, R9.10). Returns the number of changes."""
        tz = await user_timezone(s, user_id)
        now = self._clock.now()
        due = (
            await s.execute(
                select(m.Commitment)
                .where(
                    m.Commitment.user_id == user_id,
                    m.Commitment.status.in_(ACTIVE),
                    m.Commitment.current_due_date < local_date(now, tz),
                )
                .order_by(m.Commitment.current_due_date, m.Commitment.id)
                .with_for_update()
            )
        ).scalars()
        changed = 0
        for commitment in list(due):
            changed += await self._apply_deadline(s, commitment, tz, now)
        if changed:
            await self._integrity.refresh(s, user_id)
        return changed

    async def checkin_hook(
        self,
        s: AsyncSession,
        action: m.DailyAction,
        previous: str,
        new: str,
        note: str | None,
        ctx: TransitionContext,
    ) -> None:
        """Same-transaction evaluation on `daily_action.status_changed` (design §11.2)."""
        if new != "Completed":
            return
        commitments = (
            await s.execute(
                select(m.Commitment)
                .join(m.CommitmentLink, m.CommitmentLink.commitment_id == m.Commitment.id)
                .where(
                    m.CommitmentLink.user_id == action.user_id,
                    m.CommitmentLink.entity_type == "DailyAction",
                    m.CommitmentLink.entity_id == action.id,
                    m.CommitmentLink.removed_at.is_(None),
                    m.Commitment.status.in_(ACTIVE),
                )
                .order_by(m.Commitment.id)
                .with_for_update(of=m.Commitment)
            )
        ).scalars()
        await self._reevaluate(s, action.user_id, list(commitments))

    async def cancel_hook(
        self, s: AsyncSession, user_id: uuid.UUID, actions: list[m.DailyAction], actor: Actor, reason: str
    ) -> None:
        """Linked Daily Actions were cancelled: keep the Commitment, mark the links removed, adjust the
        condition, notify the User, and re-evaluate (R9.9, §11.3)."""
        now = self._clock.now()
        rows = (
            await s.execute(
                select(m.CommitmentLink, m.Commitment)
                .join(m.Commitment, m.Commitment.id == m.CommitmentLink.commitment_id)
                .where(
                    m.CommitmentLink.user_id == user_id,
                    m.CommitmentLink.entity_type == "DailyAction",
                    m.CommitmentLink.entity_id.in_([a.id for a in actions]),
                    m.CommitmentLink.removed_at.is_(None),
                    m.Commitment.status.in_(ACTIVE),
                )
                .order_by(m.Commitment.id, m.CommitmentLink.entity_id)
                .with_for_update(of=m.Commitment)
            )
        ).tuples()
        removed: dict[uuid.UUID, tuple[m.Commitment, list[str]]] = {}
        for link, commitment in rows:
            link.removed_at = now
            link.removed_reason = reason
            self._event(s, commitment, "link_removed", actor, now, commitment.status, commitment.status)
            removed.setdefault(commitment.id, (commitment, []))[1].append(str(link.entity_id))
        await s.flush()
        for commitment, action_ids in removed.values():
            statuses = await self._linked_statuses(s, commitment.id)
            condition = condition_after_removal(commitment.completion_condition, len(statuses))
            if condition != commitment.completion_condition:
                commitment.completion_condition = condition
                self._event(
                    s, commitment, "condition_changed", "System", now, commitment.status, commitment.status
                )
            await publish(
                s,
                "commitment.condition_changed",
                user_id,
                {
                    "commitment_id": str(commitment.id),
                    "removed_daily_action_ids": action_ids,
                    "completion_condition": condition,
                    "reason": reason,
                },
                at=now,
            )
        await self._reevaluate(s, user_id, [c for c, _ in removed.values()])

    async def _reevaluate(self, s: AsyncSession, user_id: uuid.UUID, commitments: list[m.Commitment]) -> None:
        """Deadline first (a late completion cannot rescue an Open Commitment past due), then the
        completion condition."""
        if not commitments:
            return
        tz = await user_timezone(s, user_id)
        now = self._clock.now()
        for commitment in commitments:
            await self._apply_deadline(s, commitment, tz, now)
            statuses = await self._linked_statuses(s, commitment.id)
            if commitment.status in ACTIVE and condition_satisfied(commitment.completion_condition, statuses):
                await self._transition(s, commitment, "Kept", "System", now)
        await self._integrity.refresh(s, user_id)

    async def _apply_deadline(
        self, s: AsyncSession, commitment: m.Commitment, tz: str, now: datetime
    ) -> bool:
        action = deadline_action(
            commitment.status,
            commitment.current_due_date,
            local_date(now, tz),
            commitment.explanation_window_ends_at,
            now,
        )
        if action == "break":
            await self._transition(s, commitment, "Broken", "System", now)
            return True
        if action == "open_window":
            # The window starts when the due date passes, or when the System first observes it (so a
            # delayed sweep never leaves the User with less than the full window).
            _, due_end = local_day_bounds(commitment.current_due_date, tz)
            ends_at = max(due_end, now) + self._window
            commitment.explanation_window_ends_at = ends_at
            commitment.updated_at = now
            self._event(s, commitment, "explanation_window_opened", "System", now, "Deferred", "Deferred")
            await publish(
                s,
                "commitment.explanation_window_opened",
                commitment.user_id,
                {
                    "commitment_id": str(commitment.id),
                    "window_ends_at": ends_at.isoformat(),
                },
                at=now,
            )
            await s.flush()
            return True
        return False

    # ------------------------------------------------------------------ helpers

    async def _transition(
        self,
        s: AsyncSession,
        commitment: m.Commitment,
        new_status: Status,
        actor: Actor,
        now: datetime,
        *,
        event_type: str | None = None,
        previous_due: date | None = None,
        explanation: str | None = None,
        trace_id: uuid.UUID | None = None,
    ) -> None:
        ensure_transition(commitment.status, new_status)
        previous = commitment.status
        commitment.status = new_status
        commitment.updated_at = now
        stamp = _STAMP.get(new_status)
        if stamp is not None:
            setattr(commitment, stamp, now)
        kind = event_type or _EVENT[new_status]
        self._event(
            s,
            commitment,
            kind,
            actor,
            now,
            previous,
            new_status,
            previous_due=previous_due or commitment.current_due_date,
            new_due=commitment.current_due_date,
            explanation=explanation,
            trace_id=trace_id,
        )
        await publish(
            s,
            "commitment.status_changed",
            commitment.user_id,
            {
                "commitment_id": str(commitment.id),
                "event": kind,
                "previous_status": previous,
                "new_status": new_status,
                "current_due_date": commitment.current_due_date.isoformat(),
                "actor": actor,
            },
            at=now,
        )
        await s.flush()

    def _event(
        self,
        s: AsyncSession,
        commitment: m.Commitment,
        event_type: str,
        actor: Actor,
        now: datetime,
        previous_status: str | None,
        new_status: str | None,
        *,
        previous_due: date | None = None,
        new_due: date | None = None,
        explanation: str | None = None,
        trace_id: uuid.UUID | None = None,
    ) -> None:
        s.add(
            m.CommitmentEvent(
                commitment_id=commitment.id,
                user_id=commitment.user_id,
                event_type=event_type,
                previous_status=previous_status,
                new_status=new_status,
                previous_due=previous_due,
                new_due=new_due,
                explanation=explanation,
                actor=actor,
                trace_id=trace_id,
                created_at=now,
            )
        )

    async def _linked_statuses(self, s: AsyncSession, commitment_id: uuid.UUID) -> list[str]:
        return list(
            (
                await s.execute(
                    select(m.DailyAction.status)
                    .join(m.CommitmentLink, m.CommitmentLink.entity_id == m.DailyAction.id)
                    .where(
                        m.CommitmentLink.commitment_id == commitment_id,
                        m.CommitmentLink.entity_type == "DailyAction",
                        m.CommitmentLink.removed_at.is_(None),
                        m.DailyAction.lifecycle_state == "active",
                    )
                )
            ).scalars()
        )

    async def _window_explanation(self, s: AsyncSession, commitment: m.Commitment) -> str | None:
        """The explanation submitted since the current explanation window opened, if any."""
        e = m.CommitmentEvent
        await s.flush()
        opened = (
            select(func.coalesce(func.max(e.id), 0))
            .where(e.commitment_id == commitment.id, e.event_type == "explanation_window_opened")
            .scalar_subquery()
        )
        return (
            await s.execute(
                select(e.explanation)
                .where(
                    e.commitment_id == commitment.id,
                    e.event_type == "explanation_submitted",
                    e.id > opened,
                )
                .order_by(e.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
