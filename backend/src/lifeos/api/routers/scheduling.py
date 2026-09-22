"""Routines, habits and Daily Actions (design §26.2 "Habits", "Routines", "Daily Actions and check-ins")."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.api import serializers as ser
from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.api.idempotency import IdempotencyKey, idempotent
from lifeos.db.session import user_session
from lifeos.domain.idempotency import StoredResult

router = APIRouter(tags=["scheduling"])


def _body(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_unset=True)


# --------------------------------------------------------------------------- schemas


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    active_days: list[int] = Field(min_length=1, max_length=7)


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    active_days: list[int] | None = Field(default=None, min_length=1, max_length=7)


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    start_time: time
    end_time: time
    sort_order: int = 0
    goal_id: uuid.UUID | None = None
    habit_id: uuid.UUID | None = None


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_time: time | None = None
    end_time: time | None = None
    sort_order: int | None = None
    goal_id: uuid.UUID | None = None
    habit_id: uuid.UUID | None = None


class HabitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    goal_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    recurrence_type: Literal["daily", "weekly", "custom"]
    recurrence_days: list[int] | None = None
    preferred_start: time | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    frequency_target: int = Field(default=1, ge=1, le=7)
    start_date: date | None = None
    end_date: date | None = None


class HabitPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    recurrence_type: Literal["daily", "weekly", "custom"] | None = None
    recurrence_days: list[int] | None = None
    preferred_start: time | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    frequency_target: int | None = Field(default=None, ge=1, le=7)
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["active", "archived"] | None = None


class PauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_on: date | None = None
    reason: str | None = Field(default=None, max_length=500)


class ManualActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    date: date
    start_time: time
    end_time: time


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    start_time: time
    end_time: time
    reason: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


class TaskScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    start_time: time
    end_time: time | None = None


class TaskCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


class OccurrenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    result: Literal["completed", "skipped", "partial"]
    completion_percent: int | None = Field(default=None, ge=1, le=99)
    note: str | None = Field(default=None, max_length=2000)


class CheckinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_status: Literal["Started", "Completed", "Skipped"]
    note: str | None = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------- routines (R2.1–2.2)


@router.get("/routine-templates")
async def list_templates(request: Request, user: CurrentUser, c: ContainerDep) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        templates = await c.routines.list_templates(s, user.user_id)
        return envelope(request, [ser.routine_template(t) for t in templates])


@router.post("/routine-templates", status_code=201)
async def create_template(
    body: TemplateIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        template = await c.routines.create_template(s, user.user_id, body.model_dump())
        return StoredResult(201, envelope(request, ser.routine_template(template, [])))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/routine-templates/{template_id}")
async def get_template(
    template_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    from lifeos.domain.routines import get_template as fetch

    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        template = await fetch(s, user.user_id, template_id)
        entries = await c.routines.entries(s, user.user_id, template_id)
        return envelope(request, ser.routine_template(template, entries))


@router.patch("/routine-templates/{template_id}")
async def patch_template(
    template_id: uuid.UUID,
    body: TemplatePatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        template = await c.routines.update_template(
            s, user.user_id, template_id, body.model_dump(exclude_unset=True)
        )
        return StoredResult(200, envelope(request, ser.routine_template(template)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/routine-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.routines.delete_template(s, user.user_id, template_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)


@router.get("/routine-templates/{template_id}/entries")
async def list_entries(
    template_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(
            request, [ser.routine_entry(e) for e in await c.routines.entries(s, user.user_id, template_id)]
        )


@router.post("/routine-templates/{template_id}/entries", status_code=201)
async def create_entry(
    template_id: uuid.UUID,
    body: EntryIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        entry = await c.routines.create_entry(s, user.user_id, template_id, body.model_dump())
        return StoredResult(201, envelope(request, ser.routine_entry(entry)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/routine-entries/{entry_id}")
async def get_entry(
    entry_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    from lifeos.domain.routines import get_entry as fetch

    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.routine_entry(await fetch(s, user.user_id, entry_id)))


@router.patch("/routine-entries/{entry_id}")
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        entry = await c.routines.update_entry(s, user.user_id, entry_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.routine_entry(entry)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/routine-entries/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.routines.delete_entry(s, user.user_id, entry_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)


# --------------------------------------------------------------------------- habits (R1.10–1.12)


@router.get("/habits")
async def list_habits(request: Request, user: CurrentUser, c: ContainerDep) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        habits = await c.habits.list_habits(s, user.user_id)
        return envelope(
            request, [ser.habit(h, await c.habits.open_pause(s, h.id) is not None) for h in habits]
        )


@router.post("/habits", status_code=201)
async def create_habit(
    body: HabitIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(
            201,
            envelope(request, ser.habit(await c.habits.create(s, user.user_id, body.model_dump()), False)),
        )

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/habits/{habit_id}")
async def get_habit(
    habit_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        habit = await c.habits.get(s, user.user_id, habit_id)
        return envelope(request, ser.habit(habit, await c.habits.open_pause(s, habit.id) is not None))


@router.patch("/habits/{habit_id}")
async def patch_habit(
    habit_id: uuid.UUID,
    body: HabitPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        habit = await c.habits.update(s, user.user_id, habit_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.habit(habit)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/habits/{habit_id}", status_code=204)
async def delete_habit(
    habit_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.habits.delete(s, user.user_id, habit_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)


@router.post("/habits/{habit_id}/pause")
async def pause_habit(
    habit_id: uuid.UUID,
    body: PauseIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        period = await c.habits.pause(s, user.user_id, habit_id, body.starts_on, body.reason)
        return StoredResult(
            200, envelope(request, {"paused": True, "starts_on": period.starts_on.isoformat()})
        )

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/habits/{habit_id}/resume")
async def resume_habit(
    habit_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.habits.resume(s, user.user_id, habit_id)
        return StoredResult(200, envelope(request, {"paused": False}))

    return await idempotent(request, c, user, key, {}, op)


# --------------------------------------------------------------------------- daily actions & check-ins (R3)


@router.get("/daily-actions")
async def list_daily_actions(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    date: date | None = None,
    include_cancelled: bool = False,
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        day, actions = await c.daily_actions.list_day(s, user.user_id, date, include_cancelled)
        return envelope(request, [ser.daily_action(a) for a in actions], date=day.isoformat())


@router.post("/daily-actions", status_code=201)
async def create_manual_action(
    body: ManualActionIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        action = await c.daily_actions.create_manual(
            s, user.user_id, body.title, body.date, body.start_time, body.end_time
        )
        return StoredResult(201, envelope(request, ser.daily_action(action)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/daily-actions/{action_id}")
async def get_daily_action(
    action_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.daily_action(await c.daily_actions.get(s, user.user_id, action_id)))


@router.post("/daily-actions/{action_id}/checkins", status_code=201)
async def create_checkin(
    action_id: uuid.UUID,
    body: CheckinIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        action, record = await c.checkins.transition(
            s, user.user_id, action_id, body.new_status, body.note, "User"
        )
        return StoredResult(
            201, envelope(request, {"daily_action": ser.daily_action(action), "checkin": ser.checkin(record)})
        )

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/daily-actions/{action_id}/checkins")
async def list_checkins(
    action_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(
            request, [ser.checkin(r) for r in await c.daily_actions.checkins(s, user.user_id, action_id)]
        )


# --------------------------------------------------------------------------- rescheduling (R2.5–2.8)


@router.patch("/daily-actions/{action_id}/schedule")
async def reschedule_action(
    action_id: uuid.UUID,
    body: ScheduleIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        action = await c.reschedule.reschedule(
            s, user.user_id, action_id, body.date, body.start_time, body.end_time, "User", body.reason
        )
        return StoredResult(200, envelope(request, ser.daily_action(action)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/daily-actions/{action_id}/cancel")
async def cancel_action(
    action_id: uuid.UUID,
    body: CancelIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        action = await c.reschedule.cancel(s, user.user_id, action_id, "User", body.reason)
        return StoredResult(200, envelope(request, ser.daily_action(action)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/daily-actions/{action_id}/schedule-history")
async def schedule_history(
    action_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        rows = await c.reschedule.history(s, user.user_id, action_id)
        return envelope(request, [ser.schedule_change(h) for h in rows])


# --------------------------------------------------------------------------- task scheduling (R1.9, R3.10)


@router.post("/tasks/{task_id}/schedule", status_code=201)
async def schedule_task(
    task_id: uuid.UUID,
    body: TaskScheduleIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        action = await c.tasks.schedule(s, user.user_id, task_id, body.date, body.start_time, body.end_time)
        return StoredResult(201, envelope(request, ser.daily_action(action)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: uuid.UUID,
    body: TaskCompleteIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        task, action = await c.tasks.complete(s, user.user_id, task_id, c.checkins, "User", body.note)
        data = {"task": ser.task(task), "daily_action": ser.daily_action(action) if action else None}
        return StoredResult(200, envelope(request, data))

    return await idempotent(request, c, user, key, _body(body), op)


# --------------------------------------------------------------------------- habit occurrences (R1.11)


@router.post("/habits/{habit_id}/occurrences", status_code=201)
async def record_occurrence(
    habit_id: uuid.UUID,
    body: OccurrenceIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        record = await c.habits.record_occurrence(
            s, user.user_id, habit_id, body.date, body.result, c.checkins, body.completion_percent, body.note
        )
        return StoredResult(201, envelope(request, ser.habit_occurrence(record)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/habits/{habit_id}/metrics")
async def habit_metrics(
    habit_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep, as_of: date | None = None
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        metrics = await c.habits.metrics(s, user.user_id, habit_id, as_of)
        pauses = await c.habits.pause_periods(s, habit_id)
        return envelope(request, ser.habit_metrics(metrics, pauses))


# --------------------------------------------------------------------------- schedule suggestions (R2.9)


@router.get("/schedule-suggestions")
async def list_suggestions(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    status: Literal["proposed", "accepted", "rejected", "expired"] | None = None,
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        rows = await c.suggestions.list_suggestions(s, user.user_id, status)
        return envelope(request, [ser.schedule_suggestion(x) for x in rows])


@router.post("/schedule-suggestions/{suggestion_id}/accept")
async def accept_suggestion(
    suggestion_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(
            200,
            envelope(
                request, ser.schedule_suggestion(await c.suggestions.accept(s, user.user_id, suggestion_id))
            ),
        )

    return await idempotent(request, c, user, key, {}, op)


@router.post("/schedule-suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(
            200,
            envelope(
                request, ser.schedule_suggestion(await c.suggestions.reject(s, user.user_id, suggestion_id))
            ),
        )

    return await idempotent(request, c, user, key, {}, op)
