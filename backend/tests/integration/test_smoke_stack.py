"""T0.3: dependency smoke test (design §37.1 lockfile rule).

Proves the locked versions work together: AsyncPostgresSaver + interrupt/resume round trip
(with runtime context and v2 streaming, as used by the Chat Gateway), pgvector + HNSW,
Redis, APScheduler and FastAPI startup.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Required

import psycopg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from typing_extensions import TypedDict


class ApprovalState(TypedDict, total=False):
    request: Required[str]
    approved: bool
    handled_by: str


@dataclass(frozen=True)
class SmokeContext:
    user_id: str


def ask(state: ApprovalState) -> dict[str, Any]:
    decision = interrupt({"kind": "confirmation", "request": state["request"]})
    return {"approved": decision["type"] == "approve"}


def record(state: ApprovalState, runtime: Runtime[SmokeContext]) -> dict[str, Any]:
    return {"handled_by": runtime.context.user_id}


def build_graph(checkpointer: AsyncPostgresSaver):  # compiled graph type is internal
    builder = StateGraph(ApprovalState, context_schema=SmokeContext)
    builder.add_node("ask", ask)
    builder.add_node("record", record)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", "record")
    builder.add_edge("record", END)
    return builder.compile(checkpointer=checkpointer)


async def test_checkpointer_interrupt_resume_roundtrip(database_url: str) -> None:
    async with AsyncConnectionPool(
        database_url,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await checkpointer.setup()
        graph = build_graph(checkpointer)
        thread_id = f"smoke:{uuid.uuid4()}"
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        ctx = SmokeContext(user_id="user-123")

        # First run pauses at the interrupt; v2 streaming yields uniform {"type","ns","data"} parts.
        interrupts = []
        async for part in graph.astream(
            {"request": "create task"}, config, context=ctx, stream_mode=["updates"], version="v2"
        ):
            assert set(part) >= {"type", "ns", "data"}
            if part["type"] == "updates" and "__interrupt__" in part["data"]:
                interrupts.extend(part["data"]["__interrupt__"])
        assert len(interrupts) == 1
        assert interrupts[0].value == {"kind": "confirmation", "request": "create task"}

        # A *new* graph instance (simulated restart) resumes from the durable checkpoint.
        resumed = build_graph(checkpointer)
        result = await resumed.ainvoke(Command(resume={"type": "approve"}), config, context=ctx)
        assert result["approved"] is True
        assert result["handled_by"] == "user-123"

        await checkpointer.adelete_thread(thread_id)
        assert await checkpointer.aget_tuple(config) is None


async def test_pgvector_hnsw(database_url: str) -> None:
    table = sql.Identifier(f"smoke_vec_{uuid.uuid4().hex[:8]}")
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        try:
            await conn.execute(
                sql.SQL("CREATE TABLE {} (id int PRIMARY KEY, embedding vector(1536))").format(table)
            )
            v1 = "[" + ",".join(["1"] + ["0"] * 1535) + "]"
            v2 = "[" + ",".join(["0", "1"] + ["0"] * 1534) + "]"
            await conn.execute(
                sql.SQL("INSERT INTO {} VALUES (1, %s::vector), (2, %s::vector)").format(table), (v1, v2)
            )
            await conn.execute(
                sql.SQL("CREATE INDEX ON {} USING hnsw (embedding vector_cosine_ops)").format(table)
            )
            cur = await conn.execute(
                sql.SQL(
                    "SELECT id, 1 - (embedding <=> %s::vector) AS sim FROM {} "
                    "ORDER BY embedding <=> %s::vector"
                ).format(table),
                (v1, v1),
            )
            rows = await cur.fetchall()
            assert rows[0][0] == 1
            assert abs(rows[0][1] - 1.0) < 1e-6
            cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = await cur.fetchone()
            assert row is not None
            major_minor = tuple(int(part) for part in row[0].split(".")[:2])
            assert major_minor >= (0, 8), "design §23.4 needs pgvector >= 0.8"
        finally:
            await conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))


async def test_redis_ping(redis_url: str) -> None:
    client = Redis.from_url(redis_url)
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()


async def test_apscheduler_runs_job() -> None:
    ran = asyncio.Event()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(ran.set, "interval", seconds=0.05)
    scheduler.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        scheduler.shutdown(wait=False)


def test_fastapi_startup(test_env: dict[str, str]) -> None:
    from lifeos.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
