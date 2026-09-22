"""Goal hierarchy endpoints (design §26.2 "Goal hierarchy", R1, R15.1)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.api import serializers as ser
from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.api.idempotency import IdempotencyKey, idempotent
from lifeos.db.session import user_session
from lifeos.domain.idempotency import StoredResult

router = APIRouter(tags=["goals"])

Category = Literal["Life", "Career", "Personal", "Spiritual", "Fitness", "Family"]
Direction = Literal["higher_is_better", "lower_is_better"]


# --------------------------------------------------------------------------- schemas


class GoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    category: Category
    description: str | None = Field(default=None, max_length=5000)
    target_date: date | None = None
    priority: int = Field(default=3, ge=1, le=5)


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    description: str | None = Field(default=None, max_length=5000)
    target_date: date | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["active", "completed"] | None = None


class PriorityIn(BaseModel):
    priority: int = Field(ge=1, le=5)


class ObjectiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    metric_direction: Direction
    current_value: Decimal
    target_value: Decimal
    baseline_value: Decimal | None = None
    unit: str = Field(min_length=1, max_length=40)
    weight: Decimal = Field(default=Decimal(1), gt=0)
    target_date: date  # required by R1.2


class ObjectivePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    metric_direction: Direction | None = None
    current_value: Decimal | None = None
    target_value: Decimal | None = None
    baseline_value: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    weight: Decimal | None = Field(default=None, gt=0)
    target_date: date | None = None
    status: Literal["active", "completed", "archived"] | None = None


class ValueIn(BaseModel):
    current_value: Decimal


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: Literal["active", "completed", "archived"] | None = None


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    goal_id: uuid.UUID | None = None
    due_date: date | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    goal_id: uuid.UUID | None = None
    due_date: date | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    status: Literal["open", "in_progress", "completed", "cancelled"] | None = None


def _body(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_unset=True)


# --------------------------------------------------------------------------- goals


@router.get("/goals")
async def list_goals(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    status: Literal["active", "archived", "completed"] | None = None,
    category: Category | None = None,
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        goals = await c.goals.list_goals(s, user.user_id, status=status, category=category)
        return envelope(request, [ser.goal(g) for g in goals])


@router.post("/goals", status_code=201)
async def create_goal(
    body: GoalIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(
            201, envelope(request, ser.goal(await c.goals.create(s, user.user_id, body.model_dump())))
        )

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/goals/{goal_id}")
async def get_goal(
    goal_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    from lifeos.domain.goals import get_goal as fetch

    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.goal(await fetch(s, user.user_id, goal_id)))


@router.patch("/goals/{goal_id}")
async def patch_goal(
    goal_id: uuid.UUID,
    body: GoalPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        goal = await c.goals.update(s, user.user_id, goal_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.goal(goal)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.patch("/goals/{goal_id}/priority")
async def patch_priority(
    goal_id: uuid.UUID,
    body: PriorityIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        goal = await c.goals.set_priority(s, user.user_id, goal_id, body.priority)
        return StoredResult(200, envelope(request, ser.goal(goal)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/goals/{goal_id}/archive")
async def archive_goal(
    goal_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(200, envelope(request, ser.goal(await c.goals.archive(s, user.user_id, goal_id))))

    return await idempotent(request, c, user, key, {}, op)


@router.delete("/goals/{goal_id}")
async def delete_goal(
    goal_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
    confirm_cascade: bool = False,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        counts = await c.goals.delete(s, user.user_id, goal_id, confirm_cascade=confirm_cascade)
        return StoredResult(200, envelope(request, {"deleted": True, "cascade": counts.as_dict()}))

    return await idempotent(request, c, user, key, {"confirm_cascade": confirm_cascade}, op)


@router.get("/goals/{goal_id}/hierarchy")
async def goal_hierarchy(
    goal_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, (await c.goals.hierarchy(s, user.user_id, goal_id)).as_dict())


# --------------------------------------------------------------------------- objectives


@router.get("/goals/{goal_id}/objectives")
async def list_objectives(
    goal_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(
            request, [ser.objective(o) for o in await c.objectives.list_for_goal(s, user.user_id, goal_id)]
        )


@router.post("/goals/{goal_id}/objectives", status_code=201)
async def create_objective(
    goal_id: uuid.UUID,
    body: ObjectiveIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        objective = await c.objectives.create(s, user.user_id, goal_id, body.model_dump())
        return StoredResult(201, envelope(request, ser.objective(objective)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/objectives/{objective_id}")
async def get_objective(
    objective_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.objective(await c.objectives.get(s, user.user_id, objective_id)))


@router.patch("/objectives/{objective_id}")
async def patch_objective(
    objective_id: uuid.UUID,
    body: ObjectivePatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        objective = await c.objectives.update(
            s, user.user_id, objective_id, body.model_dump(exclude_unset=True)
        )
        return StoredResult(200, envelope(request, ser.objective(objective)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/objectives/{objective_id}/value")
async def set_objective_value(
    objective_id: uuid.UUID,
    body: ValueIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        objective = await c.objectives.set_value(s, user.user_id, objective_id, body.current_value)
        return StoredResult(200, envelope(request, ser.objective(objective)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/objectives/{objective_id}", status_code=204)
async def delete_objective(
    objective_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.objectives.delete(s, user.user_id, objective_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)


# --------------------------------------------------------------------------- projects


@router.get("/objectives/{objective_id}/projects")
async def list_projects(
    objective_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        projects = await c.projects.list_for_objective(s, user.user_id, objective_id)
        return envelope(request, [ser.project(p) for p in projects])


@router.post("/objectives/{objective_id}/projects", status_code=201)
async def create_project(
    objective_id: uuid.UUID,
    body: ProjectIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        project = await c.projects.create(s, user.user_id, objective_id, body.model_dump())
        return StoredResult(201, envelope(request, ser.project(project)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.project(await c.projects.get(s, user.user_id, project_id)))


@router.patch("/projects/{project_id}")
async def patch_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        project = await c.projects.update(s, user.user_id, project_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.project(project)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.projects.delete(s, user.user_id, project_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)


# --------------------------------------------------------------------------- tasks


@router.get("/tasks")
async def list_tasks(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    status: Literal["open", "in_progress", "completed", "cancelled"] | None = None,
    due_before: date | None = None,
    project_id: uuid.UUID | None = None,
    goal_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None),
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        page = await c.tasks.list_tasks(
            s,
            user.user_id,
            status=status,
            due_before=due_before,
            project_id=project_id,
            goal_id=goal_id,
            cursor=cursor,
            limit=limit,
        )
        return envelope(request, [ser.task(t) for t in page.items], next_cursor=page.next_cursor)


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        return StoredResult(
            201, envelope(request, ser.task(await c.tasks.create(s, user.user_id, body.model_dump())))
        )

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.task(await c.tasks.get(s, user.user_id, task_id)))


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        task = await c.tasks.update(s, user.user_id, task_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.task(task)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        await c.tasks.delete(s, user.user_id, task_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)
