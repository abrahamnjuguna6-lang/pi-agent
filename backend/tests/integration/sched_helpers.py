"""Shared helpers for scheduling integration tests."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api, AuthedClient


async def user_id_of(api: Api, me: AuthedClient) -> uuid.UUID:
    return api.container.jwt.verify(me.access_token).user_id


async def generate(api: Api, me: AuthedClient, *days: date) -> int:
    """Run the generator (normally the worker's `routine_generation` sweeper)."""
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        result = await api.container.generation.generate_for_user(s, uid, list(days) or None)
    return result.created


async def actions(api: Api, **filters: Any) -> list[m.DailyAction]:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        query = select(m.DailyAction)
        for key, value in filters.items():
            query = query.where(getattr(m.DailyAction, key) == value)
        return list((await s.execute(query.order_by(m.DailyAction.scheduled_start))).scalars())


async def goal_with_habit(me: AuthedClient, **habit: Any) -> tuple[str, dict[str, Any]]:
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    body = {
        "title": "Run",
        "goal_id": goal["id"],
        "recurrence_type": "daily",
        "preferred_start": "07:00:00",
        "duration_minutes": 30,
    } | habit
    r = await me.post("/habits", body)
    assert r.status_code == 201, r.text
    return goal["id"], r.json()["data"]
