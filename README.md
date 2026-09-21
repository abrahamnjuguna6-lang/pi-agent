# Personal Life OS

AI-powered personal operating system: Goals → Plans → Daily Actions → Execution → Evidence → Reflection → Adaptation.

- `requirements.md` — normative requirements (R1–R27)
- `design.md` — implementation-grade design (single source of truth for architecture and schema)
- `tasks.md` — step-by-step build plan; work through modules in order

## Local development

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker with Compose v2.

```bash
# Infrastructure (PostgreSQL 16 + pgvector, Redis 7). Host ports: 55432 / 56379 by default
# (override with LIFEOS_PG_PORT / LIFEOS_REDIS_PORT / LIFEOS_API_PORT).
docker compose -f infra/docker-compose.yml up -d postgres redis

# Backend
cd backend
cp .env.example .env
uv sync
uv run pytest                    # unit + integration (integration skips if services are down)
uv run ruff check . && uv run ruff format --check . && uv run pyright

# Full stack incl. api (http://localhost:8001/healthz) and worker containers
docker compose -f infra/docker-compose.yml --profile app up -d --build --wait
```

Coverage gate (R24.4): `uv run pytest tests/unit --cov --cov-report=json:coverage-unit.json && uv run python scripts/check_coverage.py coverage-unit.json`

## Resolved dependency versions (T0.3 smoke-tested)

Locked in `backend/uv.lock`; verified together by `tests/integration/test_smoke_stack.py`
(AsyncPostgresSaver interrupt/resume with runtime context and v2 streaming, pgvector HNSW, Redis, APScheduler, FastAPI).

| Package | Version |
|---|---|
| Python | 3.13 (supports ≥ 3.11) |
| langchain / langchain-core | 1.4.2 / 1.6.3 |
| langgraph / langgraph-checkpoint-postgres | 1.2.11 / 3.1.2 |
| langchain-openai / langchain-tavily | 1.6.2 / 0.2.18 |
| langsmith / agentevals | 0.13.0 / 0.0.9 |
| fastapi / pydantic / sqlalchemy | 0.141.1 / 2.13.5 / 2.0.54 |
| psycopg / psycopg-pool | 3.3.6 / 3.3.2 |
| pgvector (py) / server extension | 0.5.0 / 0.8.6 on PostgreSQL 16.15 |
| redis (py) / apscheduler | 8.1.0 / 3.11.3 |
| pytest / ruff / pyright | 9.1.1 / 0.16.8 / 1.1.414 |
