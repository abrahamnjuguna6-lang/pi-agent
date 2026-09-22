# Design Document — Personal Life OS

## 1. Overview

> **Revision status:** implementation-grade design, consolidated against the full `requirements.md` (Requirements 1–27). This document is the single normative design: every table has exactly one authoritative DDL (Section 24), and every requirement is traced in Section 42. Where the requirements contain internal ambiguity, Section 44 records the explicit V1 reconciliation used by this design.

### 1.1 Purpose

Personal Life OS is an AI-powered personal operating system that closes the gap between who the User wants to become and what they actually do each day. It operates across the full personal productivity loop:

```
Goals → Plans → Daily Actions → Execution → Evidence → Reflection → Adaptation
```

Three specialist AI personalities — Personal Assistant, Mentor, and Accountability Partner — are orchestrated by a LangGraph supervisor graph. A persistent personal memory layer built on PostgreSQL with pgvector enables longitudinal analysis grounded in real historical evidence, so the System can answer "Why am I not progressing?" with the User's own records instead of generic advice.

**Technology stack:** FastAPI (Python 3.11+), PostgreSQL 16 with pgvector, Redis 7, LangGraph 1.x / LangChain 1.x, React (web), React Native / Expo (mobile).

### 1.2 Design Goals

Every design decision in this document is judged against these goals, in priority order:

1. **Evidence over opinion.** Every statement the AI makes about the User's behaviour is backed by an authoritative record (Check-in Record, Commitment, Reflection, Memory entry) retrieved in the current interaction, and cited.
2. **Deterministic truth.** Progress, completion, escalation, commitment state and integrity are computed by tested application code. The LLM explains and motivates; it never decides what is true.
3. **User control.** No mutation of the User's data happens on the AI's initiative without an explicit confirmation. Long-term memory is only written through explicit, auditable paths.
4. **Honest accountability.** Accountability escalates deterministically on real patterns, cannot be silently reset, and is tuned (not disabled) by Accountability Style.
5. **Low friction execution.** The daily loop (briefing → Now Mode → check-in → reflection) must be fast: first AI token ≤ 5 s, dashboard ≤ 2 s.
6. **Privacy by design.** Personal data is minimised in logs and traces, encrypted where sensitive, exportable, and fully deletable.

### 1.3 V1 Scope

User profile and onboarding, goal hierarchy (Goal → Objective → Project → Task / Habit → Daily Action), routines and internal scheduling, check-ins, habits and streaks, AI chat with three agents, Now Mode, "Why?" explanations, Accountability Engine, Promise Ledger and Personal Integrity Score, Daily Briefing, Daily Reflection with correlation analysis, Weekly CEO Meeting, notifications (in-app and push), authentication, privacy controls (export/deletion), background scheduler, agent observability and evaluation.

### 1.4 Non-Goals (V1)

- External calendar integration (Requirement 27, V2 — Section 39).
- Writing to any external service on the User's behalf.
- Multi-user relationships (shared goals, accountability partners who are other humans).
- Autonomous agents acting in the background: background jobs never call mutating Tool Bus functions (Section 10.6).
- Medical, financial, or clinical advice; the Mentor is a growth coach, not a licensed professional.

### 1.5 Assumptions and Constraints

- A single LLM provider is used in V1 through LangChain provider packages; model identifiers are configuration (Section 34).
- Per-user data volumes are modest (≤ 50 000 Memory entries, ≤ 20 000 Daily Actions per year), which allows exact per-user vector search (Section 23.4).
- V1 target scale: 10 000 registered Users, 500 concurrent chat sessions, 50 chat turns/second peak (Section 35).
- All user-facing times are interpreted in the User's IANA timezone; the server clock runs in UTC.
- Requirement text in `requirements.md` is normative; this design adds precision but never relaxes a SHALL.

### 1.6 Document Conventions

- "SHALL/MUST" statements in this document are binding for implementation.
- `code_style` names are identifiers used in code, schema, or APIs.
- Requirement references use the form **R8.4** (Requirement 8, criterion 4).

---

## 2. Framework Selection Rationale

**LangGraph (1.x) is the orchestration framework**, with LangChain (1.x) used for model, message, and tool-schema abstractions. Deep Agents and prebuilt `create_agent` loops are not used for the Supervisor.

Reasons:
- The system needs a Supervisor that routes between three specialist agents with explicit, auditable control flow — a `StateGraph` with conditional edges.
- Human-in-the-loop confirmation of mutating tool calls needs durable interrupt/resume semantics — `interrupt()` + `Command(resume=...)` with a Postgres checkpointer.
- The permission tier policy (read-only vs mutating vs destructive), idempotency-key generation, rejection guards, and trace capture must sit **between** the model's tool call and tool execution. A hand-built tool loop (model node → policy node → execute node) makes this boundary explicit. A prebuilt agent loop would execute tools inside its own loop and hide that boundary.
- Onboarding and the Weekly CEO Meeting are multi-turn structured flows that must survive app restarts and resume in a later session; each is a separate compiled graph with its own durable thread (Section 6.8).

**Dependency policy:** the system targets `langchain>=1,<2` and `langgraph>=1,<2`. Pre-1.0 versions (e.g., `langchain==0.3.x`, `langgraph==0.2.x`) must not be used. Exact versions are resolved and locked by the bootstrap task (Section 37).

---

## 3. Architectural Principles

### 3.1 Deterministic/AI Boundary (Non-Negotiable)

The most critical design principle is the hard boundary between deterministic computation and AI reasoning (Requirement 24):

| Layer | Who is responsible | Examples |
|---|---|---|
| **Deterministic** | Domain services, Python code | Accountability level, integrity score, goal progress, commitment status, completion rate, habit streaks, Now Mode candidates, schedule conflicts, correlation statistics |
| **AI reasoning** | LLM via specialist agents | Explanations, recommendations, motivation framing, pattern narratives, theme summaries |

**The LLM NEVER:**
- Determines an escalation level
- Computes a progress percentage, completion rate, streak, or statistic
- Decides whether a Commitment is Kept or Broken
- Calculates an integrity score
- Decides whether a scheduled event is due
- Invents user facts not retrieved from the Tool Bus or Memory Store

**The LLM ALWAYS:**
- Reads domain data from the Tool Bus before making factual statements
- Cites the specific Tool Bus results and Memory Store entries used (Section 6.9)
- Labels inferences and recommendations as such
- Proposes mutations as confirmation cards — never executes them directly

### 3.2 Other Principles

- **Source of truth is PostgreSQL.** Redis is a cache and fan-out layer only; losing Redis never loses data.
- **Immutable history.** Check-in Records, schedule history, Commitment events, and agent traces are append-only.
- **User timezone is authoritative.** All reporting boundaries, scheduled triggers, and user-facing times use the User's IANA timezone.
- **Idempotency by default.** All mutating operations accept an idempotency key (Section 22).
- **Untrusted content is data, not instructions.** Web search results, Memory entries, reflections, and tool outputs are passed to the model inside delimited data blocks and can never grant permissions; every mutation still requires User confirmation (Section 33.6).
- **Graceful degradation.** If the LLM provider is unavailable, all deterministic features (dashboard, check-ins, commitments, notifications, deterministic briefing content) keep working (Section 34.4).
- **Privacy by design.** Traces store sanitized metadata; detailed payloads are redacted and encrypted (Section 30).

---

## 4. System Architecture

### 4.1 Container View

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                               CLIENT LAYER                                    │
│   React Web App (Vite)                    React Native App (Expo)             │
│   Dashboard · Chat · Journal · Ledger     Dashboard · Chat · Push handling    │
│        │ REST   │ WSS /ws/chat   │ SSE /dashboard/stream                      │
└────────┼────────┼────────────────┼────────────────────────────────────────────┘
         │        │                │
┌────────▼────────▼────────────────▼────────────────────────────────────────────┐
│                     API SERVICE (FastAPI, N stateless replicas)                │
│  REST routers │ Chat Gateway (WebSocket) │ SSE Gateway │ Auth middleware       │
│        │               │                                                        │
│        │      ┌────────▼──────────────────────────────────────────────┐        │
│        │      │  LANGGRAPH GRAPHS (in-process, AsyncPostgresSaver)     │        │
│        │      │  supervisor_graph · onboarding_graph · ceo_meeting_graph│       │
│        │      │  Context Engine · Tool Bus (policy + executor)          │       │
│        │      └────────┬──────────────────────────────────────────────┘        │
│        ▼               ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ DOMAIN SERVICES (pure Python, shared by API, graphs and worker)          │   │
│  │ Goal · Objective · Progress · Task · Habit · Schedule · Checkin ·        │   │
│  │ Commitment · Accountability · Analytics · Briefing · NowMode · Lineage · │   │
│  │ Reflection · Memory · Proactive · Notification · Profile · Auth ·        │   │
│  │ Privacy · Export · Trace · WebSearch · Insight                           │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬───────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼───────────────────────────────────────────────┐
│                    WORKER SERVICE (single active leader)                        │
│  APScheduler sweepers (Section 20) · Notification dispatcher · Deletion purge   │
│  Pattern detection · Embedding jobs · Retention cleanup                         │
└────────────────────────────────┬───────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼───────────────────────────────────────────────┐
│                                DATA LAYER                                       │
│  PostgreSQL 16 (source of truth)          Redis 7 (cache / fan-out only)        │
│  · relational schema (Section 24)         · session-version cache               │
│  · pgvector memory embeddings             · rate-limit counters                 │
│  · LangGraph checkpoint tables            · Pub/Sub for realtime fan-out        │
│  · realtime_events replay buffer          · context-bundle cache                │
└────────────────────────────────────────────────────────────────────────────────┘
        External: LLM provider · Embedding provider · Web search provider ·
                  FCM / APNs / Web Push · Transactional email
```

### 4.2 Key Runtime Interactions

1. **Chat turn:** client → Chat Gateway → `supervisor_graph.astream()` → Context Engine + Tool Bus → Domain Services → PostgreSQL; streamed events back over WebSocket (Section 27).
2. **Confirmation:** the graph pauses at `interrupt()`; the Gateway emits `confirmation_required`; the User's decision resumes the same thread with `Command(resume=...)` (Section 8).
3. **Check-in from UI:** REST → CheckinService (transaction: status + Check-in Record + Commitment evaluation + habit occurrence) → domain event → realtime fan-out (SSE) + accountability re-evaluation.
4. **Scheduled experience (briefing, reflection prompt, CEO meeting):** worker sweeper decides the event is due (deterministic) → service assembles deterministic content → optional bounded LLM generation → notification pipeline (Section 29).

### 4.3 Domain Events

Domain Services write domain events to the transactional outbox table `domain_events` (Section 24.13) in the same transaction as the state change, so an event exists if and only if the change committed. Consumers claim events with `FOR UPDATE SKIP LOCKED`, one event per transaction. A failing handler rolls back only that event, which is retried with exponential backoff (`available_at`, 2^attempts seconds, capped at 1 h). After 10 attempts the event is dead-lettered and a developer alert is raised, so one poison event never blocks the others. Consumers: SSE fan-out, accountability re-evaluation, commitment evaluation, context-cache invalidation, schedule-suggestion generation. Events are at-least-once; consumers are idempotent.

| Event | Producer | Consumers |
|---|---|---|
| `daily_action.status_changed` | CheckinService | Commitment evaluation, Accountability, Habit tracking, Task sync, SSE, schedule suggestions |
| `daily_action.rescheduled` / `.cancelled` | ScheduleService | Commitment link update, SSE |
| `commitment.status_changed` | CommitmentService | Integrity snapshot, Proactive flags, SSE |
| `objective.value_changed` | ObjectiveService | Progress recompute, SSE |
| `escalation.level_changed` | AccountabilityService | Notifications, Proactive flags, SSE |
| `reflection.submitted` | ReflectionService | Memory embedding, Analytics |

---

## 5. Component Responsibilities

### 5.1 Supervisor

The Supervisor is the LangGraph orchestrator (`supervisor_graph`). It owns turn lifecycle and routing concerns exclusively.

**SHALL:**
- Initialize each turn: reset per-turn state, load the routing summary and User preferences (Section 6.4)
- Classify intent with a structured-output LLM call, unless the User addressed an agent by name (`@Mentor …`) or the turn is a system-initiated opener (Section 18)
- Select the specialist agent (Personal Assistant, Mentor, or Accountability)
- Invoke the Context Engine to build the agent-specific context bundle
- Run the iterative tool loop with the Tool Bus permission policy and confirmation interrupts
- Record routing decisions (agent selected, confidence score, ambiguity) in the Agent Trace
- Default to the Personal Assistant with an ambiguity note when confidence is low (R5.8)

**SHALL NOT:**
- Calculate accountability levels, integrity scores, or goal progress
- Execute domain mutations directly (all mutations go through Tool Bus → Domain Service)
- Invent user facts or access user data outside of the Tool Bus and Context Engine

### 5.2 Context Engine

The Context Engine runs **after routing** and builds a context bundle tailored to the selected agent (Section 7). It queries Domain Services (never raw SQL from graph code), applies a token budget, and returns structured, serializable data.

### 5.3 Specialist Agents

Each specialist agent is a **configuration**, not a separate graph: a system prompt, an allowed tool set (Section 10.3), a context-bundle builder, and a model configuration. The shared `agent_model` node (Section 6.5) binds the agent's tool set to the chat model with `bind_tools()` and makes exactly one model call per loop iteration.

**Each agent SHALL:**
- Interpret the request using the supplied context bundle
- Call tools iteratively until sufficient data is retrieved, inspecting each result
- Generate the final response grounded in retrieved data, with citations (Section 6.9)
- Propose mutations only as tool calls that the policy layer turns into confirmation cards

**Each agent SHALL NOT:**
- Make factual claims about the User without Tool Bus or Memory data from the current interaction
- Override deterministic domain service values with its own estimates

### 5.4 Domain Services

Domain Services own all business logic. They are Python classes with explicit dependencies (DB session, clock, event publisher), called by REST routers, Tool Bus functions, graphs, and worker jobs alike.

| Service | Responsibility |
|---|---|
| ProfileService | Profile fields, preferences, timezone changes (R16) |
| AuthService | Registration, verification, login, sessions, lockout, reset (R17) |
| GoalService | Goal/Project CRUD, archive (read-only), cascade-delete confirmation (R1) |
| ObjectiveService | Objective CRUD, range validation, value updates (R1.2–1.3) |
| ProgressService | Objective and Goal progress computation (R1.4–1.7) |
| TaskService | Task CRUD, Task ↔ Daily Action status sync (R1.9, R3.7) |
| HabitService | Habit CRUD, recurrence, pauses, occurrence records, streaks (R1.10–1.12) |
| ScheduleService | Routine Instances, Daily Action generation, source-aware rescheduling, conflicts, schedule suggestions (R2) |
| CheckinService | Status transitions, Check-in Records, skip-reason enforcement (R3) |
| CommitmentService | Commitment lifecycle, completion evaluation, integrity score and snapshots (R9) |
| AccountabilityService | Escalation levels, episodes, reflection gate, recovery (R8) |
| AnalyticsService | Completion Rate, weekly aggregates, wins/gaps, correlation statistics (R3.12, R10.2, R11.6–11.9) |
| BriefingService | Deterministic Daily Briefing assembly and persistence (R6) |
| NowModeService | Deterministic Now Mode candidate ranking (R7) |
| LineageService | Item → Goal lineage for the "Why?" feature (R13.7) |
| ReflectionService | Daily, weekly, and accountability reflections; journal search (R11) |
| MemoryService | Memory Store CRUD, embeddings, semantic search, proposals (R4) |
| PatternService | Level 5 reports, reflection theme detection, CEO skip patterns (R8.6, R10.7, R11.10) |
| ProactiveService | Proactive flags and session openers (Section 18) |
| NotificationService | Preferences, DND, rate limits, dispatch, delivery tracking (R14, R22) |
| InsightService | Latest AI insight for the dashboard (R12.1) |
| WebSearchService | Provider adapter, result normalization, source URLs (R25) |
| TraceService | Agent trace metadata, encrypted payloads, developer queries (R23) |
| PrivacyService / ExportService | Account deletion, session deletion, JSON export (R18) |

### 5.5 Tool Bus

The Tool Bus is the only sanctioned pathway for agents to read or modify application state (Section 10). It consists of (a) agent-visible tool schemas, (b) the permission policy, and (c) the executor that injects the trusted runtime context and calls Domain Services.

**Supervisor = WHERE to route.
Context Engine = WHAT context to provide.
Specialist Agent = HOW to respond.
Domain Service = WHAT is true (source of truth).
Tool Bus = HOW the agent accesses and mutates state.**

---

## 6. LangGraph Orchestration Model

### 6.1 Graph Inventory

| Graph | Purpose | Thread ID | Entry |
|---|---|---|---|
| `supervisor_graph` | Every chat turn, Now Mode, "Why?", session openers | `chat:{conversation_session_id}` | Chat Gateway |
| `onboarding_graph` | Structured onboarding (R16.3–16.7) | `onboarding:{user_id}:{run_no}` | Chat Gateway when session `mode=onboarding` |
| `ceo_meeting_graph` | Weekly CEO Meeting (R10) | `ceo:{weekly_ceo_session_id}` | Chat Gateway when session `mode=ceo_meeting` |

All three graphs are compiled with the same `AsyncPostgresSaver` checkpointer and the same `TurnContext` runtime context schema. They are **separate graphs, not subgraphs**. Each flow owns a durable thread whose ID does not depend on the chat session, so onboarding or a CEO Meeting can be left and resumed in a later session (Section 19).

### 6.2 Supervisor Graph Structure

```
START
  │
  ▼
turn_init ──────────────┐   reset per-turn state, parse @override, load routing summary
  │ [needs classification]│ [override | now | why | opener]
  ▼                       │
intent_classifier         │   structured-output routing decision
  │                       │
  ▼                       ▼
context_engine ◄──────────┘   agent-specific context bundle (token budget)
  │
  ▼
prefetch_tools                deterministic Tool Bus reads for now/why modes (no-op in chat)
  │
  ▼
agent_model ◄──────────────────────────────────────────────┐
  │                                                          │
  ├─ [no tool calls] ──────────────► finalize ──► END        │
  │                                                          │
  ▼ [tool call]                                              │
tool_gate  (validate · tier · rejection guard · build card)  │
  │            │                    │                        │
  │ [read]     │ [mutating/destr.]  │ [invalid/blocked]      │
  ▼            ▼                    └── ToolMessage(error) ──┤
execute_tools  confirm ── interrupt() ── User decision       │
  │            │  [approve/edit]        │ [reject]           │
  │            ▼                        ▼                    │
  │         execute_tools            reject_tool ────────────┤
  │            │                                             │
  └────────────┴── ToolMessage(result) ──────────────────────┘
```

Invariants:
- Every `AIMessage.tool_calls` entry is answered by exactly one `ToolMessage` with the matching `tool_call_id` before the next model call. This covers results, validation errors, blocked calls, and rejections.
- Models are bound with `parallel_tool_calls=False`. If a provider still returns more than one call, `tool_gate` executes or proposes the first and answers the rest with an error `ToolMessage` ("one tool call per step").
- At most one mutating action is pending at any time.
- Nodes that route with `Command(goto=...)` have no static outgoing edges.

### 6.3 State and Runtime Context

```python
import operator
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

AgentName = Literal["personal_assistant", "mentor", "accountability"]
TurnMode = Literal["chat", "now", "why", "opener"]


@dataclass(frozen=True)
class TurnContext:
    """Trusted per-invocation context. Never visible to, or settable by, the model."""
    user_id: UUID
    conversation_session_id: UUID
    trace_id: UUID
    message_id: UUID
    user_timezone: str


class PendingAction(TypedDict):
    action_id: str          # uuid5(turn_id, tool_call_id): stable across node re-runs
    tool_call_id: str
    tool_name: str
    args: dict              # validated business arguments only
    tier: Literal["mutating", "destructive"]
    fingerprint: str        # sha256(tool_name + canonical_json(args))
    preview: dict           # deterministic before/after preview for the card
    validation_errors: dict | None   # set when an edited decision failed validation


class SupervisorState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]   # working history (checkpointed)

    # Per-turn fields: all reset by turn_init
    turn_id: str
    mode: TurnMode
    turn_input: dict | None             # why: {target_type, target_id}; opener: {flag_ids}
    user_agent_override: AgentName | None
    selected_agent: AgentName | None
    routing_confidence: float | None
    ambiguity_flag: bool
    is_longitudinal: bool
    context_bundle: dict | None
    model_calls: int
    pending_action: PendingAction | None
    rejected_fingerprints: list[str]
    retrieved_refs: Annotated[list[dict], operator.add]   # citation registry (Section 6.9)
    final_response: dict | None
```

`TurnContext` is supplied through LangGraph's runtime context (`StateGraph(..., context_schema=TurnContext)`, nodes receive `runtime: Runtime[TurnContext]`). Identity never enters graph state that the model could influence, and tool schemas never contain `user_id`, `session_id`, or idempotency keys.

### 6.4 Node Specifications

**`turn_init`**: pure reads plus state reset. It is safe to re-run.
- Resets all per-turn fields. `retrieved_refs` is reset with `Overwrite([])` because it has a reducer.
- Parses the leading `@Assistant | @PersonalAssistant | @Mentor | @Accountability` override (R13.4).
- Loads the routing summary: local date/time, count of active goals, current integrity score, open proactive flags count.
- Chooses the next hop. `mode in {now, why}` sets `selected_agent` (`personal_assistant` for now, `mentor` for why). `mode=opener` sets `accountability` (Section 18). An override sets the named agent with `routing_confidence=1.0`. All of these skip the classifier.

**`intent_classifier`**:
```python
class RoutingDecision(BaseModel):
    agent: AgentName
    confidence: float = Field(ge=0.0, le=1.0)
    is_longitudinal: bool          # "why am I not progressing?"-style query (R4.5, R19.5)
    reasoning: str = Field(max_length=300)

decision = await classifier_model.with_structured_output(RoutingDecision).ainvoke(
    [SystemMessage(CLASSIFIER_PROMPT), HumanMessage(render(last_user_message, routing_summary))]
)
```
Post-processing is deterministic:
- `confidence < 0.6` → `personal_assistant`, `ambiguity_flag=True`.
- `agent == "accountability"` and `confidence < 0.7` → `personal_assistant`, `ambiguity_flag=True`.
- Timeout (2 s) or provider error → `personal_assistant`, `ambiguity_flag=True`. The trace records `classifier_fallback`.

**`context_engine`**: builds the bundle for `selected_agent` (Section 7). The bundle is stored in state for trace and debug. It is rendered into the system prompt on every model call and is **not** appended to `messages`.

**`prefetch_tools`**: for `mode=now`, it executes `get_today_schedule` and `get_active_goals` through the Tool Bus executor (R7.1). For `mode=why`, it executes `get_item_lineage` (R13.7). It appends a synthetic `AIMessage(tool_calls=[...])` and the matching `ToolMessage`s, so the model sees real Tool Bus results and the trace records real Tool Bus calls. It is a no-op for `chat` and `opener`.

**`agent_model`**:
```python
async def agent_model(state: SupervisorState, runtime: Runtime[TurnContext]) -> dict:
    agent = AGENTS[state["selected_agent"]]
    budget_exhausted = state["model_calls"] >= MAX_MODEL_CALLS_PER_TURN   # default 8
    model = agent.model if budget_exhausted else agent.model.bind_tools(
        agent.tools, parallel_tool_calls=False
    )
    system = build_system_prompt(agent, state["context_bundle"], state["mode"],
                                 ambiguity=state["ambiguity_flag"],
                                 budget_exhausted=budget_exhausted)
    history = trim_history(state["messages"])          # Section 6.7
    ai = await model.ainvoke([system, *history])
    return {"messages": [ai], "model_calls": state["model_calls"] + 1}
```
When the tool budget is exhausted, the final call is made **without tools**, and the prompt tells the model to answer with what it has or explain that the request needs a narrower question. This guarantees termination without leaving an unanswered tool call. The node has `RetryPolicy(max_attempts=3)` for transient provider errors.

**`route_after_model`** (router, not a node): the last message has tool calls → `tool_gate`, otherwise → `finalize`.

**`tool_gate`**: a real node that returns `Command`.
```python
def tool_gate(state, runtime) -> Command[Literal["execute_tools", "confirm", "agent_model"]]:
    call = state["messages"][-1].tool_calls[0]           # extras answered with error ToolMessages
    spec = TOOL_REGISTRY.get(call["name"])
    if spec is None or spec.name not in AGENTS[state["selected_agent"]].tool_names:
        return Command(update={"messages": [error_tool_message(call, "TOOL_NOT_ALLOWED")]},
                       goto="agent_model")
    args, errors = spec.validate(call["args"])            # Pydantic; no DB writes
    if errors:
        return Command(update={"messages": [error_tool_message(call, "VALIDATION_ERROR", errors)]},
                       goto="agent_model")
    if spec.tier == "read_only":
        return Command(goto="execute_tools")
    fp = fingerprint(spec.name, args)
    if fp in state["rejected_fingerprints"]:              # R20.5
        return Command(update={"messages": [error_tool_message(call, "ALREADY_DECLINED")]},
                       goto="agent_model")
    preview = spec.preview(args, runtime.context)         # read-only, deterministic
    return Command(update={"pending_action": PendingAction(
        action_id=str(uuid5(ACTION_NS, f'{state["turn_id"]}:{call["id"]}')),
        tool_call_id=call["id"], tool_name=spec.name, args=args,
        tier=spec.tier, fingerprint=fp, preview=preview)}, goto="confirm")
```

**`confirm`**: calls `interrupt()` **exactly once per invocation**. Everything before it only reads state, so the node restarting on resume is harmless. Current LangGraph guidance says not to loop over several `interrupt()` calls inside one node, because resume values are matched to interrupts by index. An invalid edit therefore routes back to `confirm` with `Command(goto="confirm")` and the validation errors in state.
```python
def confirm(state, runtime) -> Command[Literal["execute_tools", "reject_tool", "confirm"]]:
    pa = state["pending_action"]
    decision = interrupt({                        # single interrupt; JSON-serializable payload
        "kind": "confirmation", "action_id": pa["action_id"], "tool_name": pa["tool_name"],
        "tier": pa["tier"], "preview": pa["preview"],
        "editable_fields": TOOL_REGISTRY[pa["tool_name"]].editable_fields,
        "validation_errors": pa.get("validation_errors"),
    })                                            # {"type": approve|edit|reject, "args"?: {...}}
    if decision["type"] == "reject":
        return Command(goto="reject_tool")
    args = pa["args"]
    if decision["type"] == "edit":
        args, errors = TOOL_REGISTRY[pa["tool_name"]].validate({**pa["args"], **decision["args"]})
        if errors:                                # re-ask via a fresh node invocation
            return Command(update={"pending_action": {**pa, "validation_errors": errors}}, goto="confirm")
    return Command(update={"pending_action": {**pa, "args": args, "validation_errors": None}},
                   goto="execute_tools")
```
`interrupt()` is never wrapped in `try/except`, because that would swallow the interrupt signal.
For the destructive tier, the Chat Gateway only forwards an `approve` whose typed confirmation phrase matched (R20.3). The graph never trusts the client for this check.

**`execute_tools`**: calls `ToolBus.execute(call, ctx=runtime.context, pending_action=...)`. For mutating tools the executor supplies `idempotency_key = f"agent:{action_id}"` (Section 22), so re-running this node after a crash cannot duplicate the mutation. It emits `tool_started` / `tool_completed` through `get_stream_writer()`. It appends one `ToolMessage` per call (`status="error"` for failures) and adds citation refs to `retrieved_refs`. It clears `pending_action`. Then → `agent_model`.

**`reject_tool`**: appends `ToolMessage(status="error", content={"success": false, "rejected": true, ...})` for the pending call, adds its fingerprint to `rejected_fingerprints`, clears `pending_action`, and emits `confirmation_rejected`. Then → `agent_model`, where the agent acknowledges the rejection and does not re-propose it (R20.5).

**`finalize`**: runs the grounding validator (Section 6.9). It upserts the assistant message into `conversation_messages` (keyed by `message_id`), upserts the Agent Trace (keyed by `trace_id`), and records an AI insight if the response contains a classified insight segment (Section 24.9). It returns `final_response = {text, citations, agent, ambiguity}`. All writes are upserts, so a re-run is safe.

### 6.5 Graph Compilation

```python
from langgraph.graph import StateGraph, START, END

def build_supervisor_graph(checkpointer) -> "CompiledStateGraph":
    g = StateGraph(SupervisorState, context_schema=TurnContext)
    g.add_node("turn_init", turn_init)
    g.add_node("intent_classifier", intent_classifier)
    g.add_node("context_engine", context_engine)
    g.add_node("prefetch_tools", prefetch_tools)
    g.add_node("agent_model", agent_model,
               retry_policy=RetryPolicy(max_attempts=3),              # from langgraph.types
               timeout=TimeoutPolicy(run_timeout=20))                 # per-attempt hard cap
    g.add_node("tool_gate", tool_gate)          # routes via Command
    g.add_node("confirm", confirm)              # routes via Command
    g.add_node("execute_tools", execute_tools)
    g.add_node("reject_tool", reject_tool)
    g.add_node("finalize", finalize)

    g.add_edge(START, "turn_init")
    g.add_conditional_edges("turn_init", route_after_init,
                            {"classify": "intent_classifier", "context": "context_engine"})
    g.add_edge("intent_classifier", "context_engine")
    g.add_edge("context_engine", "prefetch_tools")
    g.add_edge("prefetch_tools", "agent_model")
    g.add_conditional_edges("agent_model", route_after_model,
                            {"tool_gate": "tool_gate", "finalize": "finalize"})
    g.add_edge("execute_tools", "agent_model")
    g.add_edge("reject_tool", "agent_model")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
```

### 6.6 Checkpointer Lifecycle and Invocation

```python
from contextlib import asynccontextmanager
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

@asynccontextmanager
async def lifespan(app):
    async with AsyncConnectionPool(
        settings.DATABASE_URL, max_size=settings.CHECKPOINT_POOL_SIZE,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        app.state.graphs = build_all_graphs(checkpointer)
        yield
# checkpointer.setup() runs once in the migration job (Section 36), not on every startup.
```

The Chat Gateway runs each turn as a stream:

```python
config = {"configurable": {"thread_id": f"chat:{session_id}"}}
ctx = TurnContext(user_id=..., conversation_session_id=session_id, trace_id=..., message_id=..., user_timezone=...)

# New turn — version="v2" yields uniform StreamPart dicts: {"type", "ns", "data"}
stream = graph.astream({"messages": [HumanMessage(text, id=str(message_id))], "mode": "chat", "turn_input": None},
                       config, context=ctx, stream_mode=["messages", "updates", "custom"], version="v2")
# Resume after a confirmation decision (same thread, same message_id)
stream = graph.astream(Command(resume={"type": "approve"}), config, context=ctx,
                       stream_mode=["messages", "updates", "custom"], version="v2")
```

LangGraph's newer `astream_events(version="v3")` API exposes typed projections (`messages`, `interrupts`, `values`) and is the docs' recommended in-process streaming model. The Gateway sits behind a small `GraphStreamAdapter` interface, so it can adopt that API without changing the WebSocket protocol. Task T11.2 evaluates it as a spike. `astream(version="v2")` is the baseline.

Stream-to-event mapping (Section 27.3):
- `messages` mode, chunks from `agent_model` only, with text content → `token`.
- `updates` mode containing `__interrupt__` → `confirmation_required`. The Gateway sends it, never the graph node.
- `updates` from `intent_classifier` / `turn_init` → `agent_selected`.
- `custom` mode → `tool_started`, `tool_completed`, `confirmation_rejected`.
- Stream end → `done` with `final_response`. The `done` text is authoritative. Clients replace the streamed text with it, because the grounding validator may adjust citations.

One active turn per session is enforced with a Redis lock (`turn:{session_id}`, TTL = turn timeout) backed by a PostgreSQL advisory lock (Section 27.4). The turn timeout is 45 s. On timeout the Gateway cancels the stream task and emits `error{code: TURN_TIMEOUT}`. The checkpoint stays at its last completed step.

### 6.7 Conversation History

- **Working history** is the checkpointed `messages` list of the thread. It is the only history the model sees. The graph never re-hydrates it from `conversation_messages`, which would duplicate messages.
- **Audit/UI history** is `conversation_messages` (Section 24.10). The Gateway writes the user message and `finalize` writes the assistant message. Both are keyed by `message_id` for idempotency. This table powers the session history view and search (R13.3).
- **Trimming:** before each model call, `trim_history` applies `trim_messages(strategy="last", max_tokens=HISTORY_TOKEN_BUDGET, start_on="human", allow_partial=False)`. The default budget is 6 000 tokens. Tool-call/ToolMessage pairs are never split.
- **Session rotation:** a chat session ends after 30 minutes of inactivity or 200 messages. The next message opens a new session and a new thread. Multi-turn context is retained within a session (R13.6). Cross-session knowledge comes only from the Memory Store and the Tool Bus (R4.7).

### 6.8 Onboarding and CEO Meeting Graphs

Both are linear, step-based graphs. Each question node calls `interrupt()` **once**, and the resume value is the User's answer. An invalid or empty answer is handled by a conditional edge that routes back to the same node with a clarifying prompt in state. This follows the LangGraph rule of one interrupt per node invocation, with no `while True` loops around `interrupt()`. Any LLM step, such as extracting structured goals from an answer or asking a follow-up, happens **after** the corresponding `interrupt()` returns. Every persistence step happens in a dedicated final node after the User's final confirmation. The flows are specified in Section 19.1 (onboarding) and Section 19.7 (CEO Meeting).

### 6.9 Grounding, Citations, and Source Labels (R19, R25)

Each tool result carries citation refs, which are collected in `retrieved_refs`:

| Marker | Source category (R19.1) | Produced by |
|---|---|---|
| `[D:<ref>]` | (a) database via Tool Bus | any read-only tool result |
| `[M:<memory_id>]` | (b) Memory Store (label "inference" if `is_inference`) | `search_memory`, context bundle memory items |
| `[W:<n>]` | web (R25), with source URL | `search_web` |
| `Inference:` / `Recommendation:` prefix | (c) AI inference or recommendation | model |
| "I'm not certain…" phrasing | (d) unknown/uncertain provenance | model |

Agent system prompts require a marker on every sentence that states a fact about the User and a prefix on every inference or recommendation.

In `finalize`, the **grounding validator** does the following:
1. Parses the markers. Markers not present in `retrieved_refs` for this turn are removed, and the trace is flagged `grounding_violation`.
2. If `is_longitudinal` is true and the response has no `D` or `M` citation, it appends a fixed notice ("I couldn't find recorded evidence for this in your history…") and flags the trace (R19.5).
3. Builds `citations[]` for the client. Each item is `{marker, category, label, entity_id | url, is_inference}`. The UI renders these as source chips: *Your data*, *Your memory*, *Web*, *Inference*. AI-inferred Memory entries always render with the *Inference* label (R4.3, R19.3).

The evaluation suite (Section 31.3) measures grounding with the `hallucination/grounding` category.

---

## 7. Context Engine

The Context Engine is invoked as `context_engine` **after** routing and **before** the first `agent_model` call. As a result:
1. The bundle is tailored to the agent that will handle the request.
2. Expensive queries are not run for agents that won't use them.
3. The classifier call uses only the compact routing summary, which keeps it fast.

All bundles share a common header:

```python
CommonContext(
    current_time_local: str,              # ISO-8601 in the User's timezone
    user_timezone: str,
    accountability_style: str,            # Gentle | Balanced | Direct | Strict
    open_proactive_flags: list[FlagSummary],   # Section 18; lets any agent acknowledge them
    truncated: bool,
)
```

### 7.1 Personal Assistant Context Bundle (R5.6)

```python
PersonalAssistantContext(
    today_schedule: list[DailyActionSummary],     # today's Daily Actions incl. Routine Exceptions, by scheduled_start
    behind_schedule: list[DailyActionSummary],    # past scheduled_end, still Planned/Started
    open_tasks: list[TaskSummary],                # not completed/cancelled, due ≤ 7 days
    upcoming_commitments: list[CommitmentSummary],# due in next 7 days
    scheduling_constraints: SchedulingConstraints,# wake, sleep, working hours
    schedule_conflicts: list[ConflictSummary],    # Section 16.8
    active_level_12_alerts: list[AccountabilityAlert],
    pending_schedule_suggestions: list[ScheduleSuggestionSummary],  # Section 19.9
)
```

### 7.2 Mentor Context Bundle (R5.6)

```python
MentorContext(
    active_goals: list[GoalSummary],              # title, category, progress %, priority
    active_objectives: list[ObjectiveSummary],    # current, target, unit, direction, progress %
    active_projects: list[ProjectSummary],
    recent_reflections: list[ReflectionSummary],  # last 5
    achievement_history: list[MemoryEntry],       # last 10 type=Achievement
    relevant_memory: list[MemoryEntry],           # top-k (k=8) by similarity to the message, threshold 0.75
    learning_history: list[MemoryEntry],          # category Career/Lessons, last 10
)
```

### 7.3 Accountability Context Bundle (R5.6)

```python
AccountabilityContext(
    escalation_states: list[EscalationState],     # all source identities at Level ≥ 3, plus reflection_required
    recent_skips: list[SkipRecord],               # last 7 days, grouped by source identity, with skip reasons
    active_commitments: list[CommitmentSummary],  # Open + Deferred, with due / deferred dates
    integrity_score: IntegrityScore,              # current value + 30-day trend from snapshots (Section 16.6)
    integrity_threshold: int,
)
```

### 7.4 Token Budget and Caching

- The bundle serializer applies a 4 000-token budget (configurable). Lists are truncated in the documented priority order (most recent / highest importance first), and `truncated=true` is set.
- Deterministic parts of each bundle are cached in Redis per `(user_id, agent)` with a 60 s TTL. They are invalidated on relevant domain events (Section 4.3). This keeps context assembly within its 400 ms latency budget (Section 35).
- The bundle is rendered into the system prompt inside a `<user_context>` data block, marked as data and not as instructions.

---

## 8. Human-in-the-Loop Confirmation

### 8.1 Pattern

Confirmation uses LangGraph's native `interrupt()` and `Command(resume=...)` inside the `confirm` node (Section 6.4). Requirements:
- A checkpointer is configured (`AsyncPostgresSaver`) and every invocation passes the thread's `thread_id`.
- Code before `interrupt()` is read-only. When the graph resumes, the node restarts from its beginning, so all side effects occur in later nodes (`execute_tools`, `finalize`).
- Interrupt payloads are JSON-serializable.

### 8.2 Confirmation Flow

1. `agent_model` emits a tool call for a mutating or destructive tool.
2. `tool_gate` validates the arguments, applies the rejection guard, computes a deterministic preview, and stores `pending_action`.
3. `confirm` calls `interrupt(request)`. The checkpointer durably saves the paused thread.
4. The Chat Gateway sees `__interrupt__` in the stream and sends `confirmation_required{action_id, tool_name, tier, preview, editable_fields}`. It persists a `pending_confirmations` row (Section 24.10) so the card survives reconnects and app restarts.
5. The client renders a Confirmation Card (Section 32.3).
6. The User approves, edits, or rejects. The client sends `resume{message_id, action_id, decision}`.
7. The Gateway validates the resume (Section 27.5) and calls `astream(Command(resume=decision), ...)` on the same thread.
8. On approve/edit → `execute_tools`. On reject → `reject_tool`. Either way the agent then produces the final response.

### 8.3 Card Content by Tool

Previews are computed deterministically by the tool's `preview()` function. For example:
- `reschedule_task` shows the before/after local times and dates, and whether a Routine Exception or Habit override will be created (R2.9–2.10).
- `create_commitment` shows the title, due date, linked entities, and completion condition (ALL/ANY required when linking more than one Daily Action) (R9.2).
- `delete_goal` shows the counts of Objectives, Projects, Tasks, and Habits affected by the cascade (R1.15).
- `create_memory_entry` shows the content, type, source, and inference flag (R4.7).

### 8.4 Rejection Handling (R20.5)

- The agent acknowledges the rejection in its response.
- The rejected action's fingerprint is stored for the turn. An identical proposal is answered with `ALREADY_DECLINED` without showing a card.
- A **different** action (for example a different time) may still be proposed in the same turn.

### 8.5 Abandoned Confirmations

A pending confirmation that receives no decision within 24 hours, or when its session is deleted, is expired by the worker. It is marked `expired` in `pending_confirmations` and the thread is resumed with `{"type": "reject", "reason": "expired"}`, so the checkpoint does not remain paused indefinitely.

### 8.6 System-Proposed Actions Outside Chat

Some confirmations originate from deterministic system proposals rather than an agent turn:
- schedule suggestions after late completion (Section 19.9)
- Lesson proposals (Section 19.6)
- the CEO Meeting focus plan (Section 19.7)

These are stored as proposal rows with status `proposed`. They are presented as cards in the UI or at the start of the next chat session, and they are applied only when the User accepts via an authenticated request. This satisfies R20.4: no background process ever executes the mutation.

---

## 9. Checkpointing vs Long-Term Memory

These are two separate systems with different scopes and purposes.

### 9.1 LangGraph Checkpointer — Execution State

| Property | Value |
|---|---|
| Purpose | Durable execution state of a graph thread (messages, pending interrupts, per-turn state) |
| Scope | One `thread_id` (Section 6.1) |
| Production class | `AsyncPostgresSaver` (`langgraph.checkpoint.postgres.aio`) |
| Test class | `InMemorySaver` (`langgraph.checkpoint.memory`) |
| Retention | Chat threads: 30 days after last activity. Onboarding/CEO threads: 30 days after the flow completes or expires |
| Deletion | Deleted with the conversation session (R18.9) and in account deletion (R18.2) |

Checkpoint cleanup is a worker job (`checkpoint_retention`, Section 20.2). It calls the checkpointer's thread-deletion API (`adelete_thread`) for each expired thread ID, which is listed from `conversation_sessions`, `onboarding_runs`, and `weekly_ceo_sessions`. Checkpoints contain personal data (messages, context bundles). They are therefore protected by storage-level encryption, excluded from analytics, and covered by the deletion scope.

### 9.2 Long-Term Memory Store — Cross-Session Knowledge

| Property | Value |
|---|---|
| Purpose | Durable user knowledge across all sessions |
| Contents | Values, principles, lessons, achievements, failures, reflections, check-in notes, patterns, approved facts/preferences |
| Scope | User |
| Persistence | Until the User deletes it (R4.8) |
| Access | `search_memory` tool, Context Engine, MemoryService |
| Implementation | `memory_store_entries` table with pgvector (Section 23, Section 24.8) |

**The LangGraph `Store` abstraction is not used.** Memory operations go through `MemoryService`, which enforces the creation rules, the inference labelling, and user scoping.

### 9.3 Separation Table

| | Checkpointer | Memory Store |
|---|---|---|
| Technology | checkpoint tables | `memory_store_entries` |
| Scope | thread | user, all sessions |
| Auto-saved | yes (every graph step) | no: only the explicit paths in Section 23.2 |
| Searchable | no | yes (semantic + filters) |
| User can delete | via session deletion | yes, per entry |
| Visible to agents | trimmed working history of the current thread | context bundles and `search_memory` |

---

## 10. Tool Bus

### 10.1 Security and Invocation Model

The Tool Bus is the only agent-facing interface to application data. Agent-visible tool schemas **MUST NOT expose `user_id`, `session_id`, access tokens, or idempotency keys**.

Each tool is declared once:

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                     # model-facing; written as a prompt
    args_model: type[BaseModel]          # business arguments only
    tier: Literal["read_only", "mutating", "destructive"]
    handler: Callable[[BaseModel, TurnContext, ToolExecution], Awaitable[ToolResult]]
    preview: Callable[..., dict] | None  # required for mutating/destructive
    editable_fields: tuple[str, ...] = ()
```

Agent-visible schemas are generated from `args_model` and bound with `bind_tools()`. Execution never goes through a generic tool runner. `ToolBus.execute()`:
1. re-validates the arguments
2. builds `ToolExecution(actor="Agent", agent_name, idempotency_key, action_id)` from the trusted `TurnContext` and `pending_action`
3. calls the handler, which calls a Domain Service
4. sanitizes the result
5. records the invocation (R15.8)

The model therefore cannot choose another tenant, invent identity, or replay a mutation.

### 10.2 Result and Error Contract (R15.7)

```json
{
  "success": true,
  "data": {},
  "refs": [{"marker": "D:ta_3", "entity_type": "daily_action", "entity_id": "…"}]
}
```

```json
{
  "success": false,
  "error": {
    "code": "INVALID_TRANSITION",
    "message": "Cannot transition a completed Daily Action.",
    "field_errors": {},
    "input": {"target_type": "daily_action", "target_id": "…", "new_status": "Skipped"}
  }
}
```

`error.input` echoes the sanitized business arguments that caused the failure. Personal free text longer than 200 characters is truncated. Raw stack traces, SQL, tokens, other users' IDs, and internal exception messages are never exposed to the model or client. Each tool invocation log records the agent name, function name, sanitized input (excluding PII), response status, and latency in ms (R15.8). These logs are stored in the trace metadata (Section 30.1).

### 10.3 Tool Registry

The ten tools required by R15.2 are available to **all** agents. The other tools are scoped per agent to keep the prompt small and the behaviour predictable.

| Tool | Tier | Agents | Service | Purpose |
|---|---|---|---|---|
| `get_today_schedule` | read_only | all | ScheduleService / NowModeService | Today's Daily Actions with status, conflicts, `now_candidates` |
| `get_active_goals` | read_only | all | GoalService | Active goals in priority order (Section 15.2) with progress |
| `get_goal_progress` | read_only | all | ProgressService | Goal/Objective progress, optional history |
| `search_memory` | read_only | all | MemoryService | Semantic + filtered memory search |
| `search_web` | read_only | all | WebSearchService | Web results with URLs (Section 10.5) |
| `create_task` | mutating | all | TaskService | Create a Task, optionally scheduled |
| `complete_task` | mutating | all | TaskService / CheckinService | Complete a Task or Daily Action (R3.10) |
| `reschedule_task` | mutating | all | ScheduleService | Source-aware reschedule of a Task or Daily Action (R3.11) |
| `record_habit` | mutating | all | HabitService | Record a Habit occurrence: completed / skipped / partial |
| `log_reflection` | mutating | all | ReflectionService | Store a daily reflection from chat |
| `get_schedule_for_date` | read_only | PA, ACC | ScheduleService | Daily Actions for any date |
| `get_open_tasks` | read_only | PA, MEN | TaskService | Open tasks with filters |
| `get_checkin_history` | read_only | PA, ACC | CheckinService | Check-in Records for a Daily Action or source identity |
| `get_item_lineage` | read_only | all | LineageService | Daily Action/Task → … → Goal chain + linked values (R13.7) |
| `get_commitment_status` | read_only | PA, ACC | CommitmentService | Commitments with status and conditions |
| `get_integrity_score` | read_only | ACC, MEN | CommitmentService | Current score, components, 30-day trend |
| `get_accountability_state` | read_only | ACC | AccountabilityService | Escalation states, episodes, reflection gates |
| `get_reflections` | read_only | MEN, ACC | ReflectionService | Reflections by date range / keyword / category |
| `get_analytics` | read_only | MEN, ACC | AnalyticsService | Completion rates, correlation results (stored) |
| `start_daily_action` | mutating | PA | CheckinService | Planned → Started |
| `skip_daily_action` | mutating | PA, ACC | CheckinService | → Skipped; `reason` required (R3.6) |
| `create_daily_action` | mutating | PA | ScheduleService | One-off MANUAL Daily Action |
| `cancel_daily_action` | mutating | PA | ScheduleService | Lifecycle cancel (not a status) |
| `update_task` | mutating | PA | TaskService | Title, due date, goal link |
| `create_commitment` | mutating | all | CommitmentService | Promise Ledger entry (R9.1–9.2) |
| `resolve_commitment` | mutating | ACC, PA | CommitmentService | Keep / cancel (explicit User action) |
| `defer_commitment` | mutating | ACC, PA | CommitmentService | Defer with new due date; explanation when required (Section 11.3) |
| `create_memory_entry` | mutating | all | MemoryService | Propose a memory entry (R4.6–4.7) |
| `update_objective_value` | mutating | MEN, PA | ObjectiveService | Update current value |
| `create_goal` / `create_objective` / `create_habit` | mutating | MEN | Goal/Objective/HabitService | Goal decomposition recommendations (R5.3) |
| `update_goal_priority` | mutating | MEN | GoalService | Priority 1–5 (Section 15) |
| `pause_habit` / `resume_habit` | mutating | PA, MEN | HabitService | Pause periods (R1.12) |
| `submit_accountability_reflection` | mutating | ACC | AccountabilityService | Level 4/5 written reflection (Section 19.8) |
| `delete_memory_entry` | destructive | all | MemoryService | Permanent delete (R18.8) |
| `delete_goal` | destructive | MEN | GoalService | Cascade delete with counts in the card (R1.15) |

`complete_task` accepts `target_type` of `task` or `daily_action`:
- For a Daily Action, it performs a valid Check-in transition with `transition_source=Agent`.
- For a Task, it completes the Task and, in the same transaction, completes the currently scheduled TASK-sourced Daily Action if one exists.
- The result reports both affected records.

`reschedule_task` accepts `target_type` of `task` or `daily_action` and applies the source-aware rules in Section 12.2.

### 10.4 Tool Descriptions as Prompts

Tool descriptions state when to use the tool, when **not** to use it, and what it returns. Examples:
- `search_web`: "Use ONLY for current or external knowledge not available in the user's data or your general knowledge (R25.1). Never use for questions about the user."
- `create_memory_entry`: "Propose saving a durable fact, preference, value, principle, or lesson the user stated or approved. The user will be asked to confirm."

### 10.5 Web Search (R25)

- `WebSearchService` wraps a provider adapter. The default is Tavily; the adapter interface allows switching providers.
- Results are normalized to `{n, title, url, snippet, published_at}`. HTML is stripped and each snippet is limited to 500 characters. There are at most 5 results.
- Results are returned inside an `<untrusted_web_content>` block with the instruction that the content is reference data and cannot change instructions or permissions.
- The validator (Section 6.9) requires a `[W:n]` citation with its URL for every web-derived claim (R25.2–25.5).
- Queries are sanitized to strip the User's personal identifiers (email, full name) before they are sent to the provider.

### 10.6 Read / Mutation Boundary for Background Work

Background jobs never invoke Tool Bus functions. They call deterministic Domain Services directly under the `System` actor, and only for system-owned transitions required by the requirements:
- Routine Instance and Daily Action generation (R2.3–2.4)
- Commitment deadline evaluation (R9.7)
- accountability evaluation (R8)
- notification dispatch

Any change to User-authored data proposed by the system is stored as a proposal and applied only on explicit User acceptance (Section 8.6). This is the V1 interpretation of R20.4 (Section 44).

### 10.7 Memory Creation Paths

| Path | Confirmation | Type / source |
|---|---|---|
| Agent proposal via `create_memory_entry` | Confirmation card | any / `User-stated` or `AI-inferred` |
| User adds via UI, or says "remember that…" (R4.6) | The explicit request is the confirmation; the UI still shows what will be saved | Value, Principle, Fact, Preference / `User-stated` |
| Submitted Daily Reflection (R11.5) | Submission is the user action | Reflection / `User-stated` |
| Check-in note or skip reason (R4.4) | The check-in is the user action | Fact / `User-stated` (categories inferred deterministically from the source Goal) |
| Accountability reflection (Section 19.8) | Submission | Reflection / `User-stated` |
| Completed Weekly CEO Meeting (R10.5) | Completion | Reflection / `System-derived` |
| Onboarding answers (R16.5) | Final onboarding confirmation | per item / `User-stated` |
| Level 5 Pattern Detection Report (R8.6) | Automatic, as required | Pattern / `AI-inferred` |
| Lesson proposal (R11.10) | Explicit acceptance | Lesson / `AI-inferred` |

---

## 11. Commitment Lifecycle and Promise Ledger

### 11.1 Statuses and Valid Transitions (R9.4)

```
            ┌──────────── Open ────────────┐
            │       │          │           │
          Kept   Broken    Deferred    Cancelled
                           │  │   ▲
                           │  │   └── re-defer (explanation + acknowledgment; Section 11.3)
                         Kept Broken
```

| From | To | Trigger |
|---|---|---|
| Open | Kept | completion condition satisfied (automatic) or explicit User action |
| Open | Broken | due date passed without satisfaction (automatic, R9.7) |
| Open | Deferred | User defers once with a new due date (R9.10) |
| Open | Cancelled | explicit User action |
| Deferred | Kept | completion condition satisfied or explicit User action |
| Deferred | Broken | deferred due date passed and explanation window expired without an explanation (automatic) |
| Deferred | Deferred (re-defer) | User supplies a written explanation **and** a second explicit acknowledgment, plus a new due date |

Any other transition is rejected with `INVALID_COMMITMENT_TRANSITION`. Every accepted transition appends a row to `commitment_events` (Section 24.7) with actor, previous/new status, due dates, and explanation. The history is immutable.

### 11.2 Completion Condition Semantics (R9.5–9.6)

`CommitmentService` determines Commitment state deterministically. The LLM never determines it.

| Condition | Linked Daily Actions | Kept when |
|---|---|---|
| `single` | exactly 1 | that Daily Action reaches Completed |
| `all` | ≥ 2 | every linked, non-cancelled Daily Action reaches Completed |
| `any` | ≥ 2 | any linked Daily Action reaches Completed |
| `explicit` | 0 | only by explicit User action (UI or confirmed chat tool) |

- The condition is chosen at creation and validated against the number of links. `all`/`any` is required for ≥ 2 links (R9.2).
- Evaluation runs in the same transaction as the Daily Action status change (`daily_action.status_changed`). It records `kept_at`.
- A Commitment linked only to a Goal, Objective, Project, or Task (no Daily Actions) uses `explicit`.

### 11.3 Deadlines and Deferral (R9.7, R9.10)

**Open past due:** the hourly `commitment_evaluation` job transitions Open Commitments whose due date (end of local day, in the User's timezone) has passed without satisfaction to **Broken**. It notifies the User (`commitment_breach`).

**First deferral:** from Open, the User may defer once to a new explicit due date later than today. No explanation is required. `deferral_count = 1`.

**Deferred past due:**
1. When the deferred due date passes without satisfaction, the Commitment stays `Deferred` and enters an **explanation window** of 24 hours (configurable). `explanation_window_ends_at` is set and the User is notified: "Your deferred commitment is overdue — explain or it will be marked Broken."
2. During the window it counts as an **overdue Deferred** Commitment in the integrity denominator (R9.11).
3. If the User submits a written explanation (≥ 20 characters) within the window, the System asks for a second explicit acknowledgment. The acknowledgment is a separate confirmation step, a second card or checkbox. Only after both may the User set a new due date (re-defer; `deferral_count += 1`). The explanation is stored on the `commitment_events` row and as a Memory entry (type Fact, `User-stated`).
4. If the window expires without an explanation, the Commitment transitions to **Broken**.

**Other rules:**
- Rescheduling a linked Daily Action never changes the Commitment's identity, due date, or links (R9.8).
- **Deleting or cancelling a linked Daily Action** keeps the Commitment. The link row is marked removed and the User is notified that the completion condition changed (R9.9). If the remaining links satisfy the condition, the Commitment becomes Kept. If none remain, the condition becomes `explicit`.

### 11.4 Personal Integrity Score (R9.11–9.13)

```
Score = Kept_30 / (Kept_30 + Broken_30 + OverdueDeferred_30) × 100     (rounded to 1 dp)
```

Window membership is evaluated in the User's timezone over the last 30 local days, including today:
- **Kept_30:** Commitments whose `kept_at` local date falls in the window.
- **Broken_30:** Commitments whose `broken_at` local date falls in the window.
- **OverdueDeferred_30:** Commitments currently `Deferred` whose current deferred due date has passed and falls within the window.
- Cancelled Commitments are excluded. If the denominator is 0, the score is `null` and the UI shows "No resolved commitments in the last 30 days".

The score is computed by `CommitmentService.compute_integrity_score()`. It is persisted as a daily snapshot in `integrity_score_snapshots` (Section 16.6) and recomputed after any commitment transition. The dashboard trend and every agent reference read the stored value (R24.3).

### 11.5 Threshold Trigger (R9.14)

When a recomputed score falls below `users.integrity_score_threshold` (default 70), a crossing from ≥ threshold (or `null`) to < threshold creates a proactive flag `integrity_below_threshold` (Section 18). The Accountability Agent opens the next User session with an accountability conversation. A crossing produces one flag, deduplicated per crossing.

### 11.6 Promise Ledger View (R9.15)

`GET /commitments` supports filters (status, date range, goal category) and sorting (created date, due date, status, goal category). `goal_category` is derived deterministically at creation from the first linked entity's Goal, through lineage (Section 16.9), and stored on the Commitment. Commitments without a Goal link have category `null` and are grouped as "Unlinked".

---

## 12. Daily Action Source Model and Rescheduling

### 12.1 Source Types

Every Daily Action carries an immutable `source_type`, `source_id`, and `occurrence_date` (the local date of the originating occurrence). These are set at creation and never changed.

| Source Type | Source ID | Origin |
|---|---|---|
| `ROUTINE_ENTRY` | Routine Entry ID | Routine Template instantiation |
| `HABIT` | Habit ID | Habit recurrence schedule |
| `TASK` | Task ID | User-scheduled Task |
| `MANUAL` | NULL | User-created one-off action or a Routine Exception `added` entry (Section 44, item 1) |

**Active** generated Daily Actions are unique per `(user_id, source_type, source_id, occurrence_date)`, so generation jobs are idempotent. A Daily Action rescheduled to another date keeps its `occurrence_date`. Its `date` (the scheduled local date) changes, so it does not collide with that date's own occurrence.

Daily Actions are never deleted, because their Check-in Records are append-only (Section 24.1). A system-cancelled occurrence (for example, cancelled by a habit pause) leaves the unique index and may be regenerated later. A **User** cancellation also writes a `removed` Routine Exception or Habit override, which the generator honours, so user cancellations are never regenerated.

### 12.2 Source-Aware Rescheduling

`reschedule_task` (target `daily_action`) and `PATCH /daily-actions/{id}/schedule` apply different logic depending on `source_type`.

**ROUTINE_ENTRY source:**
- Creates a `routine_exceptions` row (`exception_type='modified'`) for the occurrence date. The Routine Template is unchanged (R2.5, R2.7).
- Future Routine Instances continue to be generated from the unmodified Template.

**HABIT source:**
- Creates a `habit_occurrence_overrides` row (`override_type='rescheduled'`). The Habit recurrence is unchanged.

**TASK source:**
- Updates `tasks.scheduled_date` / `tasks.scheduled_time`. No Routine Exception is created.

**MANUAL source:**
- Updates the Daily Action directly.

**All sources:**
- Update `daily_actions.scheduled_start/scheduled_end/date` and append a `daily_action_schedule_history` row with previous and new times (R2.8). This row preserves the original planned time.
- Only Daily Actions with status `Planned` may be rescheduled. `Started` actions must be completed or skipped first.
- The Daily Action counts only on its new date for Completion Rate (R2.8, R3.12).

### 12.3 Cross-Day Rescheduling

When a Daily Action is rescheduled to a different calendar date:
- It disappears from the original date's schedule and appears on the new date's schedule.
- The original date's Completion Rate excludes it, and the new date's Completion Rate includes it.
- Accountability evaluates the occurrence on the date where it was executed or skipped.

### 12.4 Natural-Language Reschedule (R2.10)

The Personal Assistant resolves "Move my 4 PM learning session to 6 PM" as follows:
1. Call `get_today_schedule` (or `get_schedule_for_date`).
2. Match candidates by local start time (±15 min) and case-insensitive title similarity. The matching is deterministic, done by the tool when it is given `match_time` and `match_title` arguments.
3. If exactly one candidate matches, propose `reschedule_task`. If several match, ask the User to choose, without proposing a mutation.

### 12.5 Timezone Change (R21.4)

When the User changes timezone, `ProfileService` performs the following in one transaction:
- Keeps all past Daily Actions, Check-in Records, and timestamps unchanged.
- **Every active, Planned Daily Action that has not started yet** (`scheduled_start` in the future), whatever its source, keeps its local calendar date and local wall-clock time. Its UTC instants are recomputed in the new timezone (DST policy of Section 20.3), and a schedule-history row is written with `change_type='timezone_change'` and `changed_by='System'`.
  - For untouched Routine and Habit occurrences, this equals regenerating them from their definition.
  - Actions are re-anchored in place rather than deleted, because Check-in Records are append-only (Section 12.1).
- Future scheduler triggers are recomputed automatically, because sweepers resolve local times at execution (Section 20.3).

---

## 13. Schedule History

`daily_action_schedule_history` (Section 24.5) is an append-only log of every change to `scheduled_start`/`scheduled_end` (R2.8). It answers questions like "How often did I reschedule this activity?" from authoritative records. Rows are never updated or deleted, except by account deletion. `GET /daily-actions/{id}/schedule-history` and the `get_checkin_history` tool (with `include_schedule_history=true`) expose it.

---

## 14. Accountability Engine

### 14.1 Responsibility Boundary

`AccountabilityService` computes levels from authoritative Check-in Records (R8.1). The Accountability Agent only explains the deterministic state and phrases challenges in the configured style. Escalation is tracked per **stable source identity** (Habit ID or Routine Entry ID), never by title.

The Accountability Agent may disagree with the User's stated preferences when their behaviour is inconsistent with their stated Goals (R5.9). Every counter-argument must cite Tool Bus or Memory data retrieved in the current interaction: skip history, Commitment outcomes, the integrity score, or the User's own stated values. The grounding validator (Section 6.9) enforces this in the same way as any other factual claim.

### 14.2 Escalation Rules

| Level | Name | Scope | Trigger (deterministic) |
|---|---|---|---|
| 1 | Reminder | occurrence | a Daily Action reaches `scheduled_start` with status Planned (R8.2) |
| 2 | Nudge | occurrence | a Daily Action passes `scheduled_end` with status Planned or Started (R8.3) |
| 3 | Challenge | source | Skipped on ≥ 3 distinct local dates in the last 7 local days (R8.4) |
| 4 | Reflection Prompt | source | Skipped on ≥ 5 distinct local dates in the last 7 local days (R8.5) |
| 5 | Pattern Detection Report | source | Skipped on ≥ 5 consecutive local dates (R8.6) |

- The highest applicable level wins (R8.8).
- Levels 1–2 apply to every Daily Action source, including TASK and MANUAL. Levels 3–5 apply only to HABIT and ROUTINE_ENTRY sources.
- "Local date" is the Daily Action's scheduled local `date` in the User's timezone.
- Days on which the source had no scheduled occurrence do not break a "consecutive" run. Consecutiveness is measured over the source's **scheduled occurrences** (for example, a Mon/Wed/Fri habit skipped on 5 consecutive scheduled days). Days inside a Habit pause period are excluded entirely (R1.12).
- Persistent source-level state is stored only when Level ≥ 3. A source with no current pattern is reported as baseline Level 1.

The `accountability_evaluation` sweeper runs every 5 minutes (Section 20.2). The CheckinService event handler also evaluates the affected source immediately, so a skip is reflected without waiting for the sweep. Each evaluation is idempotent for `(source identity, evaluation window, resulting level)`.

### 14.3 Escalation Episodes and the Reflection Gate (R8.5)

- An **episode** starts when a source first reaches Level 3 and ends when it returns to Level 1. `escalation_episode_id` identifies it.
- On reaching Level 4 or 5, `reflection_required = true`, and a Level 4 Reflection Prompt notification is issued (Section 19.8).
- While `reflection_required = true`, the level **cannot be reduced**. Submitting the written accountability reflection sets `reflection_completed_at` and `reflection_required = false`, and links the reflection.
- Escalating from 4 to 5 does not require a new reflection if one was already submitted in the same episode.

### 14.4 Recovery / Reset Semantics (R8.10)

A reduction is eligible when all of the following hold:
1. the source has Completed occurrences on ≥ 3 distinct local dates within the current rolling 7-day window;
2. `reflection_required = false`;
3. those completion dates are later than `recovery_window_anchor_date` (they have not already been used for a previous reduction).

On reduction:
- the level decreases by exactly 1, to a minimum of 1;
- `last_reduced_at` is updated, and `recovery_window_anchor_date` is set to the latest completion date used;
- a new skip pattern re-escalates normally.

This prevents one set of three completions from reducing Level 5 to Level 1 over repeated scheduler runs, while ensuring escalation is never permanent (R8.10).

### 14.5 Level 5 Pattern Detection Report (R8.6)

When a source reaches Level 5, `PatternService.generate_pattern_report()` does the following:
1. Assembles the deterministic evidence: skipped dates, skip reasons, time-of-day distribution, linked Goal and its progress, prior episodes, and related Memory entries (top-k search on the source title and skip reasons).
2. Makes a **bounded** LLM call (Mentor/Accountability prompt, structured output). It produces a narrative with required citations to the evidence items, plus up to 3 hypotheses labelled as inference.
3. Stores the report as a `memory_store_entries` row with `type='Pattern'`, `source='AI-inferred'`, `is_inference=true`, and `source_ref_type='escalation_episode'`.
4. Sends the Level 5 notification and creates a proactive flag.

If the LLM call fails, a deterministic template report containing the evidence only is stored and marked `generation_status='template'`. Generation is retried by the pattern job.

### 14.6 Accountability Style (R8.11)

Style changes delivery frequency and wording only. It never changes the thresholds in Section 14.2.

| Style | Max accountability pushes/hour (Levels 1–3) | Proactive challenge in chat | Language intensity |
|---|---:|---|---|
| Gentle | 1 | on Level ≥ 4 only | supportive, curious |
| Balanced | 2 | on Level ≥ 3 | balanced, direct questions |
| Direct | 3 | on Level ≥ 3, every session while active | direct, names the gap |
| Strict | 3 | on Level ≥ 3, every session, plus Level 2 summaries in the daily briefing | strongest permitted framing: firm, never insulting |

The global push rule (Section 29.4) caps all non-critical pushes at 3 per hour. Style may lower that limit for accountability pushes but never raise it. The language intensity rules are part of the Accountability Agent system prompt and are covered by evaluation cases. All styles forbid shaming, insults, and diagnostic language.

---

## 15. Goal Priority Model

R7.2 requires a priority ranking of active Goals. The requirements flag the ranking mechanism as a product ambiguity. The design resolves it without hard-coding value judgments between life categories.

### 15.1 User-Controlled Priority

Goals have `priority smallint 1..5` (default 3, where 5 is highest). The User may change priority in Goal settings or through a confirmed `update_goal_priority` tool call. No category (Career, Family, Spiritual, etc.) receives an intrinsic system weight.

### 15.2 Deterministic Sort

`GoalService.list_active()` (and therefore `get_active_goals`) orders active goals by:

1. `priority DESC`
2. goals with a target date before goals without one
3. `target_date ASC`
4. `created_at ASC`
5. `id ASC` as a stable final tiebreaker

Now Mode, the Daily Briefing ("top active Goal per category"), and the CEO Meeting focus plan all use this ordering. Goal progress never changes priority automatically.

---

## 16. Deterministic Business Logic

The logic in this section is implemented in Domain Services and is never delegated to an LLM (R24.1). All of it is covered by unit tests, with 100% branch coverage on status-transition logic (R24.4).

| Logic | Owner | Section |
|---|---|---|
| Objective and Goal progress | ProgressService | 16.1 |
| Daily Action transitions and Check-in creation | CheckinService | 16.2 |
| Completion Rate | AnalyticsService | 16.3 |
| Commitment transitions and deadlines | CommitmentService | 11 |
| Personal Integrity Score and snapshots | CommitmentService | 11.4, 16.6 |
| Escalation levels, episodes, recovery | AccountabilityService | 14 |
| Habit counts, streaks, pauses | HabitService | 17 |
| Now Mode candidates | NowModeService | 16.7 |
| Schedule conflicts and overload | ScheduleService | 16.8 |
| Item lineage | LineageService | 16.9 |
| Weekly wins/gaps | AnalyticsService | 16.10 |
| Correlation statistics | AnalyticsService | 16.5 |
| Notification eligibility, rate limits, DND | NotificationService | 29 |
| Goal priority ordering | GoalService | 15.2 |

### 16.1 Objective and Goal Progress (R1.3–1.7)

**Validation on create/update (R1.3):**
- `higher_is_better`: `target_value ≠ 0`.
- `lower_is_better`: `baseline_value` required and `baseline_value ≠ target_value`.
- A violation returns `VALIDATION_ERROR` and nothing is saved.

**Objective progress:**
- `higher_is_better`: `progress = clamp(current / target × 100, 0, 100)`
- `lower_is_better`: `progress = clamp((baseline − current) / (baseline − target) × 100, 0, 100)`

**Goal progress:**
- `Σ(progress_i × weight_i) / Σ(weight_i)` over **active** Objectives, clamped to 0–100.
- A Goal with no active Objectives shows 0% (R1.7).

**Storage and history:**
- Progress is computed with `Decimal` and stored rounded to 2 dp in `objectives.progress` and `goals.progress`.
- It is recomputed synchronously in the same transaction as any Objective value, weight, status, or target change.
- Each recompute appends to `objective_value_history` (Section 24.3) for trend reporting.
- Archived and completed Objectives are excluded from active progress but remain queryable for historical reporting.

### 16.2 Daily Action State Invariant (R3)

**Statuses and transitions:**
- Execution statuses are exactly `Planned | Started | Completed | Skipped`.
- Valid transitions: `Planned→Started`, `Planned→Completed`, `Planned→Skipped`, `Started→Completed`, `Started→Skipped`.
- Any other transition is rejected (`INVALID_TRANSITION`) and produces **no** Check-in Record.

**Required data on transitions:**
- A transition to `Skipped` requires a non-empty `reason` (R3.6). This is enforced by the service and by a table CHECK.
- A transition to `Completed` records the completion timestamp and an optional note (R3.5).

**Creation and cancellation:**
- Daily Action creation and its initial `none → Planned` Check-in Record (`transition_source=System`, R2.4) occur in the same transaction.
- Cancellation is a lifecycle change (`lifecycle_state='cancelled'`, `cancelled_at`), not a status. It is audited in `daily_action_schedule_history` with `reason='cancelled'`.
- Cancelled and archived-Goal Daily Actions accept no transitions.

**Transaction and sync:**
- Each transition updates `daily_actions.status` and inserts the Check-in Record in one transaction, using `SELECT … FOR UPDATE` on the Daily Action row to serialize concurrent check-ins.
- **Task sync:** when a TASK-sourced Daily Action becomes Started, the Task becomes `in_progress`. When it becomes Completed, the Task becomes `completed`. Completing a Task directly completes its currently scheduled Planned or Started Daily Action.

**Overdue handling (R3.13):**
- A Daily Action past `scheduled_end` with no update remains Planned. It counts as Planned (incomplete) in the Completion Rate and triggers Level 2.

### 16.3 Completion Rate (R3.12)

For a User-local reporting period:

```text
Completion Rate = Completed / (Planned + Started + Skipped + Completed)
```

- The calculation covers non-cancelled Daily Actions whose scheduled local `date` falls in the period.
- Each action's status as of the evaluation time comes from its latest Check-in Record, the authoritative source. For historical periods, this is the latest Check-in Record at or before the period end.
- Cross-day rescheduled actions count only on their destination date.
- A day with zero scheduled actions has no Completion Rate (`null`), not 0%.
- Daily Actions from a paused Habit are not generated, so they never enter the denominator (R1.12).

### 16.4 Habit Partial Completion

Partial completion is handled with Habit occurrence records (Section 17). It is not a Daily Action status.

### 16.5 Reflection Correlation Analysis (R11.6–11.9)

An observation is a local day with both a self-reported `energy` value and a non-null Completion Rate.
- `n < 7`: no observation is computed or shown.
- `7 ≤ n < 20`: the System may show a descriptive observation labelled **"Preliminary — insufficient data for statistical conclusions"**, with no coefficient significance claim.
- `n ≥ 20`: compute the two-tailed Spearman rank correlation (`scipy.stats.spearmanr`). A finding is surfaced only when `p < 0.05`. Any surfaced finding always reports the coefficient, `n`, and p-value, and states that correlation does not imply causation.
- Pearson correlation may be shown only after a Shapiro–Wilk test on both variables gives `p > 0.05`.
- The same analysis runs for `mood` versus Completion Rate.

Results are computed nightly by the `analytics_refresh` job and stored in `analytics_results` (Section 24.9). Agents read stored results through `get_analytics` and only explain them.

### 16.6 Integrity Score Snapshots (R9.13)

`integrity_score_snapshots` stores one row per User per local date: score (nullable), kept, broken, overdue_deferred. Rows are written:
- at local midnight by the `integrity_snapshot` job, and
- as an upsert of today's row after any Commitment transition.

The 30-day trend compares today's value with the value 30 days earlier and returns the daily series for the sparkline.

### 16.7 Now Mode Candidates (R7)

`NowModeService.candidates(now_local)` returns an ordered list:
1. **Current block:** Daily Actions with `scheduled_start ≤ now < scheduled_end`, status Planned or Started. Started comes first, then earlier start, then higher linked-Goal priority.
2. **Behind schedule:** Daily Actions past `scheduled_end` and still Planned or Started (R7.4). These are always returned as `behind_schedule`, even when not recommended.
3. **Next unstarted:** the next Planned Daily Action today by `scheduled_start` (R7.3).
4. **Buffer suggestion:** when nothing remains today, or the current block is complete, the next open Task of the highest-priority active Goal (Section 15.2). If no such Task exists, the Goal itself is suggested as a focus.

`get_today_schedule` returns `now_candidates` and `behind_schedule`. The Personal Assistant picks one candidate, which is normally the first, and explains the choice. The grounding validator checks that the recommended item ID is one of the candidates. A recommendation outside the candidates is rejected and replaced with the top candidate plus a trace flag.

### 16.8 Schedule Conflicts and Overload (R6.4)

For a local date:
- **Conflict:** two non-cancelled Daily Actions whose `[start, end)` intervals overlap.
- **Outside waking hours:** a Daily Action starting before `wake_time` or ending after `sleep_time`.
- **Overloaded block:** any rolling 3-hour window that is ≥ 100% booked with no gap of 10 minutes or more. The day is also overloaded when total scheduled minutes exceed 90% of waking minutes.

`ScheduleService.analyze_day()` returns these as structured findings for the Daily Briefing and the Personal Assistant context.

### 16.9 Item Lineage (R13.7)

`LineageService.lineage(target_type, target_id)` walks from the item up to its Goal:
- Daily Action → source: Task, Habit, or Routine Entry (whose optional `goal_id`/`habit_id` is followed).
- Task → Project and/or Goal.
- Project → Objective → Goal.

It returns the chain with titles, progress, and priority, plus the Goal's category. When no link exists, it returns an explicit `unlinked` marker so the agent says so instead of inventing a purpose.

### 16.10 Weekly Wins and Gaps (R10.2)

Over the prior 7 local days (Monday–Sunday):
- **Wins:** candidates are source identities and Tasks with Completed Daily Actions, Kept Commitments, and Objectives whose progress increased. They are ranked by linked-Goal priority, then completion count, then progress delta. The top 3 are kept.
- **Gaps:** candidates are source identities with Skipped or incomplete actions, Broken Commitments, and Objectives with no progress that have a target date within 30 days. They are ranked by skip + incomplete count, then linked-Goal priority. The top 3 are kept.
- **Completion Rate by Goal category:** Daily Actions are attributed to a category through lineage. Unlinked actions are reported as "Unlinked".

---

## 17. Habit Tracking (R1.10–1.12)

### 17.1 Recurrence and Generation

- `recurrence_type`: `daily` (every day), `weekly` (the listed `recurrence_days`, fixed), or `custom` (the listed `recurrence_days`). `frequency_target` is the number of completions required per **period**.
- The period is the local day for `daily` habits with `frequency_target = 1`. Otherwise it is the local ISO week (Monday–Sunday).
- The `routine_generation` sweeper generates HABIT Daily Actions for today and tomorrow (local) on scheduled days within `[start_date, end_date]`, except on days inside a pause period. Habits linked from a Routine Entry are represented by that Routine Entry's Daily Action; no duplicate HABIT action is generated for the same day.

### 17.2 Occurrence Records

`habit_occurrence_records` holds one row per Habit per occurrence date. It is written by HabitService in the same transaction as the related Daily Action check-in.

| `record_habit` result | Daily Action transition | Counts toward frequency target / streak |
|---|---|---|
| `completed` | → Completed | yes |
| `partial` (`completion_percent` 1–99) | → Completed, note "partial NN%" | no (V1 decision, Section 40) |
| `skipped` (reason required) | → Skipped | no; counts as missed |

A partial completion means the scheduled block was executed, so it is not a skip for accountability. It does not satisfy the habit's frequency target. If no Daily Action exists for the date, for example on an unscheduled day, the occurrence record is written alone and counts toward the weekly target.

### 17.3 Metrics (R1.11)

Computed by `HabitService.metrics(habit_id, as_of)`:
- **Completion count per period.**
- **Current streak:** consecutive completed periods meeting `frequency_target`, ending at the most recent complete period. The current period is included once it has met its target.
- **Longest streak.**
- **Missed occurrences:** scheduled occurrences that are Skipped, or that ended with the period still Planned/Started and no occurrence record.
- **Partial completions count.**
- **Pause periods:** listed from `habit_pause_periods`.

Pause periods are excluded from all of these: pause days neither break nor extend a streak (R1.12), and missed occurrences inside a pause are not counted. Metrics are computed on read and cached per Habit for 5 minutes. They are invalidated on `daily_action.status_changed` for that Habit.

### 17.4 Pauses

- `pause_habit` opens a `habit_pause_periods` row (`starts_on`, `ends_on` nullable). `resume_habit` closes it.
- Pausing **cancels** the Habit's future Planned Daily Actions from `starts_on` onward (`lifecycle_state='cancelled'`, schedule-history `change_type='habit_paused'`, `changed_by='System'`). Resuming lets the generator create fresh occurrences again, because the index only constrains active rows (Section 12.1).
- Accountability ignores pause days (Section 14.2), and the Completion Rate never sees them.

---

## 18. Proactive Surfacing

Several requirements need the AI to raise something at the start of the next session:
- R8.4: surface a Level 3 pattern in the next AI interaction
- R9.14: the integrity score is below the threshold
- R10.7: two consecutive CEO Meetings were skipped
- R11.10: a Lesson proposal
- R2.9: a schedule suggestion

These are unified under `ProactiveService`.

### 18.1 Flags

`proactive_flags` rows (Section 24.9) are created deterministically by the owning service. A `dedupe_key` prevents repeats, for example `integrity_below:{crossing_date}` or `escalation:{episode_id}:{level}`.

| Flag type | Created by | Owning agent | Expires |
|---|---|---|---|
| `escalation_level_3plus` | AccountabilityService on reaching Level ≥ 3 | Accountability | when level drops below 3 |
| `integrity_below_threshold` | CommitmentService on a downward crossing | Accountability | when the score returns to ≥ threshold |
| `ceo_skipped_consecutive` | PatternService on the 2nd consecutive skip | Accountability | on the next completed CEO Meeting |
| `lesson_proposal` | PatternService | Mentor | when accepted, rejected, or after 14 days |
| `schedule_suggestion` | ScheduleService | Personal Assistant | at the end of the affected local day |

### 18.2 Session Openers

When a chat session starts (`session_started`), the Gateway asks `ProactiveService.opener_plan(user)` for unsurfaced flags. Flags are ordered: Accountability flags first, by severity (integrity < CEO skip < escalation level), then Mentor flags, then Personal Assistant flags.

If there are any, the Gateway runs a **system-initiated opener turn** before the User's first message:
- `mode="opener"`, `turn_input={flag_ids}`, and the agent owning the top flag.
- The input message is a synthetic `HumanMessage("(session started)")` marked `synthetic=true`. It is hidden in the UI, and the opener instructions go into the agent's system prompt.
- The agent is required to retrieve the evidence through tools. It cites the same data used for the flag, such as `get_integrity_score` or `get_accountability_state`.
- Flags are marked `surfaced_at` when the opener turn completes.
- At most 2 flags are addressed per opener. The rest are listed in the context bundle (`open_proactive_flags`) for later turns.

The Life Dashboard shows unresolved Level ≥ 3 states and open flags above the fold (R12.5), independently of chat.

### 18.3 Style Interaction

The Accountability Style controls whether escalation flags produce an opener (Section 14.6). The `integrity_below_threshold` and `ceo_skipped_consecutive` flags always produce an opener, because R9.14 and R10.7 are unconditional.

---

## 19. Product Flows

### 19.1 Onboarding (R16.3–16.7)

**Entry:**
- After email verification and the first login, a User with `onboarding_completed=false` is routed to the onboarding screen before the dashboard (R16.3).
- The client opens a chat session with `mode=onboarding`. The Gateway starts or resumes `onboarding_graph` on thread `onboarding:{user_id}:{run_no}`, taken from the `onboarding_runs` row.

**Graph:**
```
START → welcome → ask_profile_basics (timezone, wake/sleep, working hours, style)
      → ask_life_areas → ask_goals_per_area → ask_values_principles_vision
      → ask_responsibilities_routines → ask_ai_role → review_summary (interrupt: confirm/edit)
      → persist → END
```

**Question steps:**
- Each `ask_*` step calls `interrupt({question, hints, prefill})`.
- After the answer is received, a bounded LLM call extracts structured items using `with_structured_output`, for example `[{category, title, target_date?}]` goals or `[{type: Value|Principle, text}]`.
- It may ask one follow-up question when the answer is empty or ambiguous.
- Answers and extractions are kept in graph state only. Nothing is written to the database before `persist`.

**Review:** `review_summary` presents everything that will be saved as one editable card: profile fields, Goals, values and principles, responsibilities, routine outline, and AI role expectations.

**Persist (single transaction, idempotent on `onboarding_run_id`):**
- Profile fields are saved to `users`.
- Memory entries are created with `source='User-stated'` (R16.5) for values, principles, vision, responsibilities, routines, AI-role expectations, and goals per category. They are tagged `source_ref_type='onboarding_run'`.
- Goals are created, with default priority 3, from the confirmed goal list.
- Optionally, a draft Routine Template is created from the routine outline, when the User ticked "create my routine".
- `onboarding_completed=true`.

**Resumability:** the User can leave at any step and resume later, including from a new session, because the thread is keyed to the run and not to the chat session.

**Re-initiation (R16.7):**
- Settings → "Redo onboarding" creates a new run with `run_no+1`. Its `prefill` values come from current profile and memory.
- On persist, Memory entries from the previous run that the User changed are marked `superseded_by` the new entry. They are not duplicated, and history is preserved.
- Existing Goals are never deleted by re-onboarding. New goals are added only when the User confirms them.

**Profile edits (R16.6):** every profile field is editable in Settings at any time without onboarding.

### 19.2 Daily Briefing (R6)

1. The `briefing_dispatch` sweeper, running every minute, finds Users whose local `briefing_time` has passed today and who have no `daily_briefings` row for today. Routine and Habit generation for today is guaranteed to have run first (Section 20.2).
2. `BriefingService.assemble(user, local_date)` builds deterministic content:
   - **schedule:** today's Daily Actions in time order, or `null` with `define_routine_prompt=true` when no Routine Template applies today (R6.6);
   - **top_goals:** the top active Goal per category (Section 15.2);
   - **principle_of_the_day:** a Memory entry of type Principle or Value, chosen by least-recently-featured (`last_featured_at`), then higher importance, then oldest. It is recorded as featured;
   - **accountability_flags:** yesterday's unresolved Level 2 occurrences, Level ≥ 3 states, and Commitments due today;
   - **schedule_findings:** conflicts and overload (Section 16.8, R6.4).
3. The Personal Assistant generates a short narrative (≤ 120 words) from that content in a bounded call. If generation fails, a template narrative is used.
4. The briefing is stored in `daily_briefings` and a `briefing` notification is sent (Section 29).
5. Tapping the notification opens a new chat session seeded with the briefing (R6.3). The first turn is a Personal Assistant opener that presents the briefing interactively and offers to resolve any conflicts through confirmation cards.

The delivery time is configurable in Settings (R6.5).

### 19.3 Now Mode (R7)

**Entry points (R7.5, R12.3):** a single tap on the Dashboard "Now" button, or the chat message "What should I be doing now?". Both run a supervisor turn with `mode=now`. `POST /chat/now` starts a session-less Now turn for clients that are not connected to chat, and streams the result over SSE.

**Flow:**
1. `turn_init` sets `selected_agent=personal_assistant`, then `context_engine` runs.
2. `prefetch_tools` executes `get_today_schedule` and `get_active_goals` (R7.1).
3. The model receives `now_candidates` and `behind_schedule` (Section 16.7). It returns one recommended action with reasoning. It explicitly acknowledges any behind-schedule items (R7.4) and may offer a reschedule card.
4. The validator enforces that the recommendation is a real candidate.

### 19.4 "Why?" (R13.7)

**Entry:** a "Why?" button on any Daily Action or Task calls `POST /why {target_type, target_id}`. This opens or reuses a chat session and runs a supervisor turn with `mode=why` and `selected_agent=mentor`.

**Flow:**
1. `prefetch_tools` executes `get_item_lineage` (Section 16.9) and `search_memory`. The memory query combines the Goal title, category, and item title, filtered to types Value, Principle, and Lesson plus the onboarding vision.
2. The Mentor connects the item → Goal → values and purpose, citing each link.
3. If the item is unlinked, the Mentor says so and offers to link it to a Goal, which is a mutating card.

### 19.5 Daily Reflection (R11.1–11.5, R11.11)

1. The `reflection_dispatch` sweeper sends the `daily_reflection` notification at the local `reflection_time`.
2. `GET /reflections/prompt?date=` returns deterministic content:
   - the day's Completion Rate;
   - the current Integrity Score;
   - Skipped Daily Actions with their recorded reasons;
   - the three questions: accomplished / held back / one thing tomorrow;
   - optional 1–5 scales for mood, energy, and stress.
3. The User submits through the reflection form (`POST /reflections`), or in chat through `log_reflection`, which is confirmed.
4. `ReflectionService` stores the structured answers (`answers` jsonb with the three keys), the scales, the local date, and the Goal categories. The categories are derived from the day's Daily Actions through lineage, plus any the User selects.
5. It then embeds the reflection and writes the Memory entry (`type=Reflection`, `source=User-stated`, R11.5).
6. **Journal (R11.11):** the chronological list supports keyword search (PostgreSQL full-text index on the reflection text), a date-range filter, and a Goal-category filter.

### 19.6 Lesson Proposals from Recurring Themes (R11.10)

1. The nightly `pattern_detection` job (Section 20.2) takes each User's Daily Reflection embeddings from the last 60 days.
2. **Deterministic theme detection:** reflections are grouped greedily by cosine similarity (≥ 0.80 to the group's centroid). The "held back" answer is embedded separately, because obstacles are the target signal. A group of ≥ 5 reflections that has no existing Lesson or pending proposal within similarity 0.85 is a theme candidate.
3. **Bounded generation:** the Mentor model summarizes the theme into a Lesson draft (≤ 60 words) that cites the reflection IDs.
4. The draft is stored in `memory_proposals` (`status=proposed`) and a `lesson_proposal` proactive flag is created.
5. The proposal is shown as a card in the next session opener and in the Journal.
6. On acceptance, `MemoryService` creates the entry: `type=Lesson`, `source=AI-inferred`, `is_inference=true`, and the cited reflections are linked. On rejection the proposal is stored as rejected and the same theme is suppressed for 30 days.

### 19.7 Weekly CEO Meeting (R10)

**Scheduling:**
- The `ceo_meeting_dispatch` sweeper creates a `weekly_ceo_sessions` row (`status=scheduled`) for each User on Sunday at the local `ceo_meeting_time`.
- In the same transaction it computes and stores `pre_session_briefing` (Section 16.10):
  - Completion Rate by Goal category for the past 7 days;
  - the Integrity Score trend;
  - the top 3 wins and top 3 gaps;
  - reflection questions.
- Then it sends the `ceo_meeting` notification.

**Session graph** (`ceo_meeting_graph`, thread `ceo:{weekly_ceo_session_id}`):
```
START → present_briefing (interrupt: acknowledge)          # briefing before free text (R10.3)
      → ask_q1_went_well → ask_q2_avoided_why → ask_q3_do_differently → ask_q4_goal_attention   (R10.4)
      → summarize (bounded LLM; cites briefing items and answers)
      → propose_focus_plan (deterministic skeleton from Goal priority + Q4 answer; LLM wording)
      → confirm_focus_plan (interrupt: accept / edit / reject)                                  (R10.6)
      → persist → END
```

**Questions:**
- Each question is an `interrupt()`.
- The Mentor may ask one follow-up per question, generated after the answer and before the next question.
- Answers are kept in graph state until `persist`.

**Persist (idempotent on the session ID):**
- A `reflections` row (`type='weekly_ceo'`) with the summary, the structured answers, and the full transcript.
- A Memory entry (`type=Reflection`, `source=System-derived`) containing the summary and a transcript reference (R10.5).
- The `weekly_focus_plans` row when the plan is accepted.
- The session is marked `completed`.

**Opening and skipping:**
- `POST /ceo-meetings/{id}/open` sets `opened_at` and `status=opened`.
- The meeting can be completed until the grace window ends, which is Tuesday 23:59 local by default (48 h after Sunday midnight). After that, `ceo_meeting_close` marks unopened or uncompleted sessions `skipped`.
- Two consecutive `skipped` sessions create a `ceo_skipped_consecutive` flag (R10.7). The Accountability Agent raises it at the start of the next session.

### 19.8 Level 4/5 Accountability Reflection (R8.5)

1. On reaching Level 4, a `accountability_l4` notification deep-links to the accountability reflection screen, or to chat with the Accountability Agent.
2. The prompt shows:
   - the skipped dates and reasons (from Check-in history);
   - the linked Goal;
   - three questions: what is getting in the way / what would make it easier / keep, change, or drop this commitment.
3. Submitted through `POST /accountability/escalations/{id}/reflection`, or through the confirmed `submit_accountability_reflection` tool.
4. On submission, a `reflections` row (`type='accountability'`, linked to `escalation_episode_id`) and a Memory entry (`type=Reflection`, `source=User-stated`) are written.
5. The escalation state is updated: `reflection_required=false`, `reflection_completed_at=now()`.
6. If the User chooses "change" or "drop", the Accountability Agent proposes the corresponding mutation as a card: reschedule the Routine Entry, pause the Habit, or archive it.

### 19.9 Late-Completion Schedule Suggestions (R2.9)

1. When a Daily Action is marked Completed after its `scheduled_end`, `ScheduleService` computes a deterministic proposal. The overrun is `completed_at − scheduled_end`. The proposal shifts the **following unstarted** Planned Daily Actions today by that overrun, preserving order and durations. Actions that would end after `sleep_time` are not shifted; the proposal marks them `suggest_move_to_tomorrow`.
2. The proposal is stored in `schedule_suggestions` (`status=proposed`), and a `schedule_suggestion` flag is created.
3. If the User has an active chat session, the Gateway runs a Personal Assistant opener turn that presents the suggestion as a confirmation card. Otherwise, it appears on the Dashboard and in the next session.
4. On acceptance, each shift is applied through the source-aware reschedule rules (Section 12.2) with `transition_source=User`. Rejection or expiry leaves the schedule unchanged (R2.9).

---

## 20. Background Job Scheduler (R21)

### 20.1 Production Topology

- APScheduler 3.x runs in a **dedicated worker service**, not in the API replicas (R21.1, R21.9).
- Exactly one worker holds the scheduler leadership lock: a PostgreSQL advisory lock taken at startup. It retries every 10 s, which provides hot-standby failover.
- APScheduler uses an in-memory job store with a fixed set of recurring **sweepers**. All per-user scheduling state lives in application tables, so no per-user APScheduler jobs exist.
- The API tier never executes time-triggered work. It only writes the state that sweepers read, such as preferences and due dates.

### 20.2 Sweepers

| Job | Cadence | Responsibility | Requirement |
|---|---|---|---|
| `routine_generation` | every 5 min | Generate Routine Instances and ROUTINE_ENTRY/HABIT Daily Actions for local today and tomorrow (idempotent unique keys) | R2.3, R21.2 |
| `briefing_dispatch` | every 1 min | Users whose local briefing time is due and who have no briefing today | R6.1 |
| `reflection_dispatch` | every 1 min | Due Daily Reflection prompts | R11.1 |
| `ceo_meeting_dispatch` | every 5 min | Create and notify due Sunday sessions | R10.1 |
| `ceo_meeting_close` | hourly | Mark sessions `skipped` after the grace window; consecutive-skip flags | R10.7 |
| `commitment_evaluation` | hourly | Open past-due → Broken; Deferred past-due → explanation window → Broken | R9.7, R9.10 |
| `accountability_evaluation` | every 5 min | Level 1/2 occurrence triggers; Level 3–5 source evaluation; recovery | R8 |
| `notification_dispatch` | every 30 s | Send due notifications; release DND queue; retries at 1/4/16 min | R14, R22 |
| `pattern_detection` | nightly 02:00 user-local (batched hourly by timezone) | Level 5 report retries, reflection themes, CEO skip patterns | R8.6, R11.10, R21.2 |
| `analytics_refresh` | nightly (user-local) | Correlation results, weekly aggregates | R11.6–11.9 |
| `integrity_snapshot` | local midnight | Daily integrity snapshot | R9.13 |
| `confirmation_expiry` | every 15 min | Expire abandoned confirmations (Section 8.5) and proposals | R20 |
| `embedding_backfill` | every 1 min | Embed pending Memory entries; retry failures | R4.4 |
| `checkpoint_retention` | daily | Delete expired LangGraph threads | Section 9.1 |
| `deletion_purge` | daily | Permanently delete accounts past `purge_due_at` | R17.7, R18.2 |
| `retention_cleanup` | daily | Traces > 90 days, `realtime_events` > 1 h, expired idempotency records, expired tokens | R23.5 |

Every sweep runs in batches of at most 500 Users. It records a `background_job_runs` row with the job name, logical scheduled time, actual start and end, attempt number, status, and sanitized error details (R21.7).

### 20.3 Timezone and DST Policy (R21.3–21.5)

- Due times are resolved at sweep time from the User's current timezone (`zoneinfo`). A timezone change therefore takes effect immediately for all future triggers (R21.4).
- **Nonexistent local time** (spring forward): advance minute by minute to the next valid local time.
- **Ambiguous local time** (fall back): use the first occurrence (`fold=0`).
- A single tested function, `resolve_local(date, time, tz) -> datetime_utc`, is used by all sweepers and by Daily Action generation. The design does not rely on scheduler-library DST behaviour.
- Stored UTC timestamps of past events are never rewritten.

### 20.4 Failure Handling (R21.7–21.8)

- A failed unit of work (one User in one job) is retried up to 3 times with exponential backoff (30 s, 2 min, 8 min), tracked by `attempt_no`.
- After the final failure, a `developer_alerts` row is created (Section 24.12) with the job, logical time, User reference, and error code, and the unit is marked failed.
- Duplicate processing after a crash is prevented by the target rows' unique keys, for example `daily_briefings(user_id, local_date)` and `weekly_ceo_sessions(user_id, week_start_date)`.

### 20.5 LLM Boundary (R21.6)

The scheduler never uses an LLM to decide whether something is due. When a scheduled experience needs generated language (briefing narrative, Pattern report, Lesson draft), deterministic code first creates the due event and its content, then calls the model as a bounded generation step with a timeout and a template fallback.

---

## 21. Authentication Design (R17)

### 21.1 Session Model

Authentication uses short-lived access JWTs plus rotating opaque refresh tokens.

- Access JWT lifetime: 1 hour. Refresh token lifetime: 24 hours (R15.6, R17.4).
- PostgreSQL `auth_sessions` is authoritative. Redis caches session-version state but is never authoritative.
- Access JWT claims: `sub=user_id`, `sid=auth_session_id`, `jti`, `iat`, `exp`, `ver=session_version`.
- Signing: EdDSA (Ed25519) or RS256. The private key comes from the secret manager / KMS (Section 36.4) and is never kept in code or plain environment files. The `kid` header supports rotation.

### 21.2 Logout / Session Invalidation (R17.5)

Every authenticated request validates `(sid, ver)` against the cached session version. On a cache miss it checks PostgreSQL.

Logout:
1. increments `session_version` and sets `invalidated_at` in PostgreSQL;
2. revokes the refresh token family;
3. publishes the revocation to Redis (the cache entry is updated, not deleted);
4. closes the session's WebSocket and SSE connections, using the `sid` the Gateway tracks per connection.

Every previously issued JWT for that session is rejected immediately.

### 21.3 Refresh Rotation and Replay

Refresh tokens are high-entropy random opaque values. Only an HMAC-SHA-256 of each token is stored. Every successful refresh rotates the token. Reuse of a retired token, detected through `auth_refresh_token_history`, invalidates the entire session family.

### 21.4 Registration, Verification, Password Reset

- Registration stores the account with `email_verified=false` and sends a verification link with a 24 h token (R17.2).
- Login before verification is rejected with `EMAIL_NOT_VERIFIED`, and the client offers to resend the link (R17.3).
- A password reset token is valid for 1 hour (R17.6). A successful reset invalidates all active auth sessions.
- Tokens are stored as HMAC hashes only and are single-use.
- Reset requests always return the same response whether or not the email exists.

### 21.5 Login Lockout (R17.8)

Failed attempts are tracked by normalized account identifier and by IP. Five or more failures for the account within 10 minutes lock the account for 15 minutes and queue an email notification. IP-level throttling (Section 33.4) limits credential stuffing without locking out the legitimate User from other networks.

### 21.6 Passwords

Passwords are hashed with **Argon2id** (`argon2-cffi`; memory 64 MiB, time cost 3, parallelism 1). Parameters are re-tuned on hardware changes, and hashes are rehashed on login when the parameters change.

### 21.7 PII Storage

Randomized AES-GCM ciphertext cannot be used for uniqueness checks, so the users table stores:

```text
email_ciphertext
email_lookup_hash   # HMAC-SHA-256(normalized_email), UNIQUE
full_name_ciphertext
```

Email is normalized before hashing (lowercased, trimmed). Encryption keys and HMAC keys are separate and are managed by KMS through envelope encryption.

---

## 22. Idempotency Design (R15.5)

### 22.1 Scope

- Every mutating REST endpoint accepts an `Idempotency-Key` header. It is required for POST, PATCH, and DELETE from clients.
- Every agent mutation receives `agent:{action_id}` (Section 6.4).
- Keys are scoped per User and retained for 24 hours.

### 22.2 Two-Phase Claim Pattern

A claim must be visible to concurrent duplicates before the operation runs, so it is committed in its **own** transaction:

```sql
-- Phase 1 (autocommit): claim
INSERT INTO idempotency_records (user_id, idempotency_key, request_fingerprint, status, expires_at)
VALUES ($user_id, $key, $fingerprint, 'processing', now() + interval '24 hours')
ON CONFLICT (user_id, idempotency_key) DO NOTHING
RETURNING id;
```

- **Row returned** → proceed to phase 2.
- **No row returned** → read the existing record:
  - different `request_fingerprint` → `422 IDEMPOTENCY_KEY_REUSED`;
  - `completed` → return the stored `result` (no re-execution);
  - `processing` and `updated_at` < 60 s ago → `409 REQUEST_IN_PROGRESS`;
  - `processing` and stale (≥ 60 s, a crashed worker) → reclaim with `UPDATE … SET updated_at=now() WHERE status='processing' AND updated_at < now()-interval '60 seconds'`, then proceed;
  - `failed` → reclaim the same way (`status='failed'`), then proceed.

```sql
-- Phase 2 (one transaction): business writes + completion marker commit atomically
BEGIN;
  -- … domain writes …
  UPDATE idempotency_records SET status='completed', result=$result, updated_at=now()
  WHERE user_id=$user_id AND idempotency_key=$key;
COMMIT;
-- On exception: ROLLBACK, then (autocommit) UPDATE … SET status='failed', updated_at=now()
```

The business effect and the `completed` marker commit together. A crash after commit returns the stored result on retry. A crash before commit leaves a stale `processing` row that is safely reclaimed.

### 22.3 Behaviour Summary

| Scenario | Response |
|---|---|
| Duplicate after success (within 24 h) | Stored result, same status code |
| Concurrent duplicate | 409 `REQUEST_IN_PROGRESS` |
| Same key, different payload | 422 `IDEMPOTENCY_KEY_REUSED` |
| Retry after failure | Re-executes |
| After 24 h expiry | Treated as a new request (record purged by `retention_cleanup`) |

---

## 23. Memory Store and Semantic Search (R4)

### 23.1 Entry Model

Each entry records:
- `content`, `type` (Fact, Preference, Value, Principle, Lesson, Achievement, Failure, Reflection, Commitment, Pattern), and `source` (User-stated, AI-inferred, System-derived);
- `importance` (1–10), `confidence` (0.0–1.0), `is_inference`, `created_at`, `updated_at` (R4.2);
- `categories text[]`, the topic dimension from R4.1: Goals, Values, Principles, Responsibilities, Career, Projects, Habits, Routines, Preferences, Commitments, Achievements, Failures, Reflections, Lessons (Section 44, item 6);
- an optional `source_ref_type/source_ref_id` link back to the originating record;
- `embedding`, `embedding_model`, and `embedding_status`.

The full DDL is in Section 24.8.

**Defaults:**

| Source | `importance` | `confidence` |
|---|---|---|
| User-stated | 7 | 1.0 |
| System-derived | 5 | 1.0 |
| AI-inferred | model-proposed, clamped to 1–7 | model-proposed, clamped to 0.3–0.8 |

The User may edit importance.

### 23.2 Creation Rules

Section 10.7 lists every permitted creation path. No other code path may insert into `memory_store_entries`, which is enforced by a single `MemoryService.create()` entry point. Conversation history is never promoted automatically (R4.7, R18.10).

### 23.3 Embeddings

- Entries are embedded asynchronously. The row is inserted with `embedding_status='pending'`, and `embedding_backfill` completes it within about 1 minute. Search excludes pending rows.
- The embedding model and dimension are fixed per deployment (`text-embedding-3-small`, 1536 dimensions, by default). The column is `vector(1536)`.
- Changing the model is a controlled migration: add a new column, backfill it, switch reads, then drop the old column.
- Check-in notes and skip reasons (R4.4) are embedded together with their Daily Action title and local date as context.

### 23.4 Semantic Search (R4.5, R4.9, R15.10)

- Similarity is cosine similarity: `1 − (embedding <=> query_embedding)`. The configurable minimum threshold defaults to `0.75`, and **k = 8** by default (at most 20).
- Search is always filtered by the authenticated `user_id` from the trusted runtime context. It excludes soft-deleted and superseded entries and supports filters for `type`, `source`, `categories`, and date range.
- Results are ordered by similarity descending.
- **Index strategy:** per-user corpora are small, so V1 runs **exact** KNN within the User's rows, using the `(user_id)` B-tree index and a scan of that User's embeddings. The HNSW index (`vector_cosine_ops`) is created for scale. When the User has more than 20 000 entries, queries enable `SET LOCAL hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8) so the user filter does not starve results.
- Each result returns `id, content, type, source, is_inference, importance, confidence, similarity, created_at`.
- Longitudinal agent responses cite the entry IDs used (Section 6.9).

### 23.5 Inference Labelling (R4.3, R19.3)

`source='AI-inferred'` implies `is_inference=true`, and a CHECK constraint enforces it. The rendering layer labels such entries as inferences everywhere: chat citations, the Memory Browser, and the dashboard.

### 23.6 Browse and Delete (R18.8, R18.11)

- The Memory Browser lists entries filterable by type, source, and category, with keyword search.
- An individual entry is deleted only after strong confirmation, either in the UI or through `delete_memory_entry`, which is in the destructive tier. Deletion hard-deletes the row and its embedding in the same transaction.
- Deletion does not delete the originating record, such as a Reflection. The UI states this and offers a link to delete the source separately.

---

## 24. Data Model

This section is the **single authoritative schema**. Other sections refer to it and never redefine columns.

### 24.1 Conventions

- **Timestamps and IDs:** timestamps are `timestamptz` stored in UTC. Columns representing a User-local calendar date are `date`, interpreted in the User's timezone by services. Primary keys are `uuid DEFAULT gen_random_uuid()`, except append-only high-volume logs, which use `bigserial`.
- **Enumerations:** stored as `text` with CHECK constraints, so values are readable in exports and cheap to migrate.
- **User scoping:** every user-owned table has `user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE`. The final account purge is `DELETE FROM users WHERE id = $1` inside a purge transaction (Section 24.14).
- **Row-level security (defense in depth):** every user-owned table has `ENABLE ROW LEVEL SECURITY` with the policy `USING (user_id = nullif(current_setting('app.user_id', true), '')::uuid)` (`users` uses `id`). An unset or reset `app.user_id` evaluates to NULL, so the policy matches no rows and fails closed. `nullif` is needed because a reset custom setting reads back as `''`. Tables without a `user_id` column, and the excluded operational tables, are not granted to `lifeos_app` at all.
  - **User-scoped transactions** run `SET LOCAL ROLE lifeos_app` and `SET LOCAL app.user_id`. `lifeos_app` is a NOLOGIN role granted to the connection user, with DML privileges and no RLS bypass. This is necessary because superusers and table owners bypass RLS.
  - **System transactions** (worker, auth lookups before a user is known, developer/admin queries after a staff check) use the schema-owner connection. RLS is not `FORCE`d, so the owner bypasses it, and every system query passes an explicit `user_id` filter.
  - **Excluded tables:** the operational tables `background_job_runs`, `developer_alerts` and `account_deletion_requests` have no user-facing access path and are not RLS-scoped.
  - Service-layer ownership checks remain the primary control (Section 33.2).
- **Immutable tables:** `checkin_records`, `daily_action_schedule_history`, `commitment_events`, and `objective_value_history` have a trigger that rejects UPDATE and DELETE unless `current_setting('app.allow_history_delete', true) = 'on'`. Only the purge transaction sets that value.
- **Soft delete:** user-deletable domain entities (goals, objectives, projects, tasks, habits, routine templates/entries, reflections, conversation sessions) have `deleted_at`. Soft-deleted rows are hidden immediately and hard-deleted by `retention_cleanup` after 30 days. Memory entries are hard-deleted immediately (Section 23.6).
- **Extensions:** `pgcrypto`, `vector`, and `pg_trgm`.

### 24.2 Users, Profile, and Authentication

```sql
CREATE TABLE users (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email_ciphertext           bytea NOT NULL,
    email_lookup_hash          bytea NOT NULL UNIQUE,          -- HMAC-SHA-256(normalized email)
    email_verified             boolean NOT NULL DEFAULT false,
    password_hash              text NOT NULL,                  -- Argon2id
    full_name_ciphertext       bytea,
    status                     text NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active','locked','deletion_pending')),
    locked_until               timestamptz,
    timezone                   text NOT NULL DEFAULT 'UTC',    -- IANA identifier, validated by zoneinfo
    wake_time                  time,
    sleep_time                 time,
    working_hours_start        time,
    working_hours_end          time,
    accountability_style       text NOT NULL DEFAULT 'Balanced'
                               CHECK (accountability_style IN ('Gentle','Balanced','Direct','Strict')),
    notification_prefs         jsonb NOT NULL DEFAULT '{}',    -- schema in Section 24.11
    integrity_score_threshold  int NOT NULL DEFAULT 70 CHECK (integrity_score_threshold BETWEEN 0 AND 100),
    briefing_time              time NOT NULL DEFAULT '05:00',
    reflection_time            time NOT NULL DEFAULT '21:00',
    ceo_meeting_weekday        smallint NOT NULL DEFAULT 0 CHECK (ceo_meeting_weekday = 0), -- 0=Sunday; V1 fixed (Section 40)
    ceo_meeting_time           time NOT NULL DEFAULT '19:00',
    life_categories            text[] NOT NULL DEFAULT '{}',
    values_text                text,
    principles_text            text,
    vision_statement           text,
    onboarding_completed       boolean NOT NULL DEFAULT false,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staff_roles (                      -- developer access for traces / delivery log (R22.4, R23.3)
    user_id     uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('developer','trace_payload_reader')),
    granted_by  uuid REFERENCES users(id),
    granted_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash  bytea NOT NULL UNIQUE,     -- HMAC of the current refresh token
    token_family_id     uuid NOT NULL,
    session_version     int NOT NULL DEFAULT 1,
    device_label        text,
    issued_at           timestamptz NOT NULL DEFAULT now(),
    refresh_expires_at  timestamptz NOT NULL,      -- issued/rotated + 24 h
    invalidated_at      timestamptz,
    last_accessed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_refresh_token_history (        -- retired refresh tokens for replay detection
    token_hash       bytea PRIMARY KEY,
    auth_session_id  uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    retired_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);

CREATE TABLE email_verification_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  bytea NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,             -- + 24 h
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE password_reset_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  bytea NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,             -- + 1 h
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE onboarding_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_no        int NOT NULL,
    status        text NOT NULL CHECK (status IN ('in_progress','completed','abandoned')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,
    UNIQUE (user_id, run_no)
);
```

### 24.3 Goal Hierarchy

```sql
CREATE TABLE goals (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        text NOT NULL,
    category     text NOT NULL CHECK (category IN ('Life','Career','Personal','Spiritual','Fitness','Family')),
    description  text,
    target_date  date,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','completed')),
    progress     numeric(5,2) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),  -- computed (16.1)
    priority     smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    archived_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz
);

CREATE TABLE objectives (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id          uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            text NOT NULL,
    metric_direction text NOT NULL CHECK (metric_direction IN ('higher_is_better','lower_is_better')),
    current_value    numeric NOT NULL,
    target_value     numeric NOT NULL,
    baseline_value   numeric,
    unit             text NOT NULL,
    weight           numeric NOT NULL DEFAULT 1 CHECK (weight > 0),
    target_date      date NOT NULL,                                   -- required by R1.2
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    progress         numeric(5,2) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    CONSTRAINT objectives_no_zero_target      CHECK (metric_direction <> 'higher_is_better' OR target_value <> 0),
    CONSTRAINT objectives_lower_requires_base CHECK (metric_direction <> 'lower_is_better' OR baseline_value IS NOT NULL),
    CONSTRAINT objectives_no_equal_range      CHECK (metric_direction <> 'lower_is_better' OR baseline_value <> target_value)
);

CREATE TABLE objective_value_history (             -- immutable
    id            bigserial PRIMARY KEY,
    objective_id  uuid NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_value numeric NOT NULL,
    progress      numeric(5,2) NOT NULL,
    changed_by    text NOT NULL CHECK (changed_by IN ('User','Agent','System')),
    changed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    objective_id uuid NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        text NOT NULL,
    description  text,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz
);

CREATE TABLE tasks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id      uuid REFERENCES projects(id) ON DELETE CASCADE,
    goal_id         uuid REFERENCES goals(id) ON DELETE SET NULL,
    title           text NOT NULL,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','completed','cancelled')),
    due_date        date,
    scheduled_date  date,
    scheduled_time  time,
    duration_minutes int CHECK (duration_minutes > 0),
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
```

Archiving a Goal (R1.14) sets `status='archived'`. Services then reject writes to the Goal's subtree (Objectives, Projects, Tasks, Habits, and their future Daily Actions) with `GOAL_ARCHIVED`. Future Planned Daily Actions of archived sources are cancelled. History remains readable.

Deleting a Goal that has active Objectives or Projects (R1.15) requires a cascade confirmation that shows counts. The Goal and its subtree are then soft-deleted. Daily Actions, Check-in Records, and Commitments are preserved for history, and their lineage shows "deleted goal".

### 24.4 Habits and Routines

```sql
CREATE TABLE habits (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    goal_id          uuid REFERENCES goals(id) ON DELETE SET NULL,
    project_id       uuid REFERENCES projects(id) ON DELETE SET NULL,
    title            text NOT NULL,
    recurrence_type  text NOT NULL CHECK (recurrence_type IN ('daily','weekly','custom')),
    recurrence_days  smallint[],                   -- 0=Sun..6=Sat; required for weekly/custom
    preferred_start  time,                          -- local time for generated Daily Actions
    duration_minutes int CHECK (duration_minutes > 0),
    frequency_target int NOT NULL DEFAULT 1 CHECK (frequency_target > 0),
    start_date       date,
    end_date         date,
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    CHECK (recurrence_type = 'daily' OR cardinality(recurrence_days) > 0),
    CHECK (goal_id IS NOT NULL OR project_id IS NOT NULL)          -- R1.10: beneath a Goal or Project
);

CREATE TABLE habit_pause_periods (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id   uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    starts_on  date NOT NULL,
    ends_on    date,                                -- NULL = still paused
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE habit_occurrence_overrides (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id        uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    occurrence_date date NOT NULL,
    override_type   text NOT NULL CHECK (override_type IN ('rescheduled','removed')),
    new_start       timestamptz,
    new_end         timestamptz,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (habit_id, occurrence_date)
);

CREATE TABLE habit_occurrence_records (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id           uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    occurrence_date    date NOT NULL,
    daily_action_id    uuid,                        -- FK added after daily_actions
    result             text NOT NULL CHECK (result IN ('completed','skipped','partial')),
    completion_percent smallint,
    note               text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (habit_id, occurrence_date),
    CHECK ((result = 'partial') = coalesce(completion_percent BETWEEN 1 AND 99, false)),   -- NULL-safe
    CHECK (result <> 'skipped' OR coalesce(length(trim(note)), 0) > 0)
);

CREATE TABLE routine_templates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       text NOT NULL,
    active_days smallint[] NOT NULL CHECK (cardinality(active_days) > 0),   -- 0=Sun..6=Sat
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE TABLE routine_entries (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),      -- stable Routine Entry ID
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               text NOT NULL,
    start_time          time NOT NULL,
    end_time            time NOT NULL,              -- end < start means the block crosses midnight
    sort_order          int NOT NULL DEFAULT 0,
    goal_id             uuid REFERENCES goals(id) ON DELETE SET NULL,
    habit_id            uuid REFERENCES habits(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CHECK (start_time <> end_time)
);

CREATE TABLE routine_instances (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                date NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, routine_template_id, date)
);
```

At most one Routine Template may be active for a given weekday. This is validated by `ScheduleService`, since arrays cannot express it as a unique constraint.

### 24.5 Daily Actions, Check-ins, and Schedule Changes

```sql
CREATE TABLE daily_actions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               text NOT NULL,
    date                date NOT NULL,                  -- scheduled local date (changes on cross-day reschedule)
    occurrence_date     date NOT NULL,                  -- originating occurrence local date (immutable)
    scheduled_start     timestamptz NOT NULL,
    scheduled_end       timestamptz NOT NULL,
    status              text NOT NULL DEFAULT 'Planned'
                        CHECK (status IN ('Planned','Started','Completed','Skipped')),
    lifecycle_state     text NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active','cancelled')),
    cancelled_at        timestamptz,
    source_type         text NOT NULL CHECK (source_type IN ('ROUTINE_ENTRY','HABIT','TASK','MANUAL')),
    source_id           uuid,
    routine_instance_id uuid REFERENCES routine_instances(id) ON DELETE SET NULL,
    completed_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (scheduled_end > scheduled_start),
    CHECK ((source_type = 'MANUAL') = (source_id IS NULL)),
    CHECK ((lifecycle_state = 'cancelled') = (cancelled_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_daily_actions_occurrence            -- one ACTIVE action per source occurrence
    ON daily_actions (user_id, source_type, source_id, occurrence_date)
    WHERE source_type IN ('ROUTINE_ENTRY','HABIT','TASK') AND lifecycle_state = 'active';

ALTER TABLE habit_occurrence_records
    ADD FOREIGN KEY (daily_action_id) REFERENCES daily_actions(id) ON DELETE SET NULL;

CREATE TABLE checkin_records (                      -- immutable
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    daily_action_id   uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    previous_status   text CHECK (previous_status IN ('Planned','Started','Completed','Skipped')),  -- NULL = none
    new_status        text NOT NULL CHECK (new_status IN ('Planned','Started','Completed','Skipped')),
    transition_source text NOT NULL CHECK (transition_source IN ('User','Agent','System')),
    note              text,                             -- skip reason (required) or completion note
    trace_id          uuid,                             -- agent trace when transition_source = 'Agent'
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (new_status <> 'Skipped' OR coalesce(length(trim(note)), 0) > 0)                                  -- R3.6
);

CREATE TABLE routine_exceptions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    routine_entry_id    uuid REFERENCES routine_entries(id) ON DELETE SET NULL,   -- NULL for 'added'
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                date NOT NULL,
    exception_type      text NOT NULL CHECK (exception_type IN ('modified','removed','added')),
    daily_action_id     uuid REFERENCES daily_actions(id) ON DELETE SET NULL,
    exception_payload   jsonb NOT NULL DEFAULT '{}',   -- changed fields / one-day entry definition
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE daily_action_schedule_history (         -- immutable
    id              bigserial PRIMARY KEY,
    daily_action_id uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    change_type     text NOT NULL CHECK (change_type IN ('rescheduled','cancelled','timezone_change','habit_paused')),
    previous_start  timestamptz NOT NULL,
    previous_end    timestamptz NOT NULL,
    new_start       timestamptz,                         -- NULL for cancellation
    new_end         timestamptz,
    changed_by      text NOT NULL CHECK (changed_by IN ('User','Agent','System')),
    reason          text,
    changed_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schedule_suggestions (                  -- R2.9 proposals (Section 19.9)
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trigger_action_id  uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    local_date         date NOT NULL,
    proposal           jsonb NOT NULL,                   -- [{daily_action_id, new_start, new_end, move_to_tomorrow}]
    status             text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected','expired')),
    decided_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trigger_action_id)
);
```

### 24.6 Accountability and Integrity

```sql
CREATE TABLE accountability_escalation_states (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type                 text NOT NULL CHECK (source_type IN ('HABIT','ROUTINE_ENTRY')),
    source_id                   uuid NOT NULL,
    level                       smallint NOT NULL CHECK (level BETWEEN 1 AND 5),
    escalation_episode_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    episode_started_at          timestamptz NOT NULL DEFAULT now(),
    reflection_required         boolean NOT NULL DEFAULT false,
    reflection_completed_at     timestamptz,
    reflection_id               uuid,                    -- FK to reflections (added below)
    recovery_window_anchor_date date,
    last_reduced_at             timestamptz,
    last_evaluated_at           timestamptz NOT NULL DEFAULT now(),
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, source_type, source_id)
);

CREATE TABLE integrity_score_snapshots (
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date        date NOT NULL,
    score             numeric(4,1),                     -- NULL when denominator = 0
    kept              int NOT NULL,
    broken            int NOT NULL,
    overdue_deferred  int NOT NULL,
    computed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, local_date)
);
```

`source_type` uses the same uppercase vocabulary as `daily_actions.source_type`.

### 24.7 Commitments (Promise Ledger)

```sql
CREATE TABLE commitments (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title                      text NOT NULL,
    source                     text NOT NULL CHECK (source IN ('User-stated','AI-recommended')),
    due_date                   date NOT NULL,             -- original due date
    current_due_date           date NOT NULL,             -- = due_date, or latest deferred due date
    completion_condition       text NOT NULL CHECK (completion_condition IN ('single','all','any','explicit')),
    status                     text NOT NULL DEFAULT 'Open'
                               CHECK (status IN ('Open','Kept','Broken','Deferred','Cancelled')),
    deferral_count             smallint NOT NULL DEFAULT 0,
    explanation_window_ends_at timestamptz,
    goal_category              text,                      -- derived at creation (11.6)
    kept_at                    timestamptz,
    broken_at                  timestamptz,
    cancelled_at               timestamptz,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE commitment_links (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    commitment_id  uuid NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_type    text NOT NULL CHECK (entity_type IN ('Goal','Objective','Project','Task','DailyAction')),
    entity_id      uuid NOT NULL,
    removed_at     timestamptz,                         -- set when a linked Daily Action is deleted/cancelled
    removed_reason text,
    UNIQUE (commitment_id, entity_type, entity_id)
);

CREATE TABLE commitment_events (                     -- immutable audit
    id               bigserial PRIMARY KEY,
    commitment_id    uuid NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type       text NOT NULL CHECK (event_type IN
                     ('created','kept','broken','deferred','redeferred','cancelled',
                      'explanation_window_opened','explanation_submitted','link_removed','condition_changed')),
    previous_status  text,
    new_status       text,
    previous_due     date,
    new_due          date,
    explanation      text,
    actor            text NOT NULL CHECK (actor IN ('User','Agent','System')),
    trace_id         uuid,
    created_at       timestamptz NOT NULL DEFAULT now()
);
```

### 24.8 Memory Store

```sql
CREATE TABLE memory_store_entries (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content          text NOT NULL,
    type             text NOT NULL CHECK (type IN ('Fact','Preference','Value','Principle','Lesson',
                                                   'Achievement','Failure','Reflection','Commitment','Pattern')),
    source           text NOT NULL CHECK (source IN ('User-stated','AI-inferred','System-derived')),
    categories       text[] NOT NULL DEFAULT '{}',
    importance       smallint NOT NULL CHECK (importance BETWEEN 1 AND 10),
    confidence       numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    is_inference     boolean NOT NULL DEFAULT false,
    source_ref_type  text,        -- reflection | checkin | onboarding_run | ceo_session | escalation_episode | conversation_message
    source_ref_id    uuid,
    superseded_by    uuid REFERENCES memory_store_entries(id) ON DELETE SET NULL,
    last_featured_at timestamptz,                       -- principle-of-the-day rotation (19.2)
    embedding        vector(1536),
    embedding_model  text,
    embedding_status text NOT NULL DEFAULT 'pending' CHECK (embedding_status IN ('pending','ready','failed')),
    generation_status text NOT NULL DEFAULT 'final' CHECK (generation_status IN ('final','template')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (source <> 'AI-inferred' OR is_inference),
    CHECK (embedding_status <> 'ready' OR embedding IS NOT NULL)
);

CREATE TABLE memory_proposals (                        -- Lesson proposals (19.6)
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proposed_type    text NOT NULL CHECK (proposed_type IN ('Lesson')),
    content          text NOT NULL,
    evidence_refs    jsonb NOT NULL,                     -- [{reflection_id, similarity}]
    theme_embedding  vector(1536) NOT NULL,
    status           text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected','expired')),
    memory_entry_id  uuid REFERENCES memory_store_entries(id) ON DELETE SET NULL,
    decided_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);
```

### 24.9 Reflections, Briefings, CEO Meetings, Insights, Analytics, Flags

```sql
CREATE TABLE reflections (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                  date NOT NULL,                  -- local date
    type                  text NOT NULL CHECK (type IN ('daily','weekly_ceo','accountability')),
    answers               jsonb NOT NULL DEFAULT '{}',    -- daily: {accomplished, held_back, tomorrow}; ceo: q1..q4; accountability: 3 answers
    content               text NOT NULL,                  -- user text (daily) or summary (ceo/accountability)
    transcript            text,                           -- weekly_ceo only
    mood                  smallint CHECK (mood BETWEEN 1 AND 5),
    energy                smallint CHECK (energy BETWEEN 1 AND 5),
    stress                smallint CHECK (stress BETWEEN 1 AND 5),
    goal_categories       text[] NOT NULL DEFAULT '{}',
    escalation_episode_id uuid,                           -- accountability reflections
    search_tsv            tsvector GENERATED ALWAYS AS
                          (to_tsvector('simple', coalesce(content,'') || ' ' || coalesce(answers::text,''))) STORED,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz
);
CREATE UNIQUE INDEX uq_reflections_daily ON reflections(user_id, date)
    WHERE type = 'daily' AND deleted_at IS NULL;          -- one daily reflection per day; edits update it
ALTER TABLE accountability_escalation_states
    ADD FOREIGN KEY (reflection_id) REFERENCES reflections(id) ON DELETE SET NULL;

CREATE TABLE daily_briefings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date      date NOT NULL,
    content         jsonb NOT NULL,                       -- deterministic sections (19.2)
    narrative       text,
    narrative_status text NOT NULL CHECK (narrative_status IN ('generated','template')),
    generated_at    timestamptz NOT NULL,
    notification_id uuid,
    UNIQUE (user_id, local_date)
);

CREATE TABLE weekly_ceo_sessions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start_date      date NOT NULL,                   -- Monday of the reviewed week
    scheduled_at         timestamptz NOT NULL,
    grace_ends_at        timestamptz NOT NULL,
    opened_at            timestamptz,
    completed_at         timestamptz,
    status               text NOT NULL CHECK (status IN ('scheduled','opened','completed','skipped')),
    pre_session_briefing jsonb NOT NULL,
    summary              text,
    reflection_id        uuid REFERENCES reflections(id) ON DELETE SET NULL,
    focus_plan_status    text CHECK (focus_plan_status IN ('proposed','accepted','rejected')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, week_start_date)
);

CREATE TABLE weekly_focus_plans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ceo_session_id  uuid NOT NULL UNIQUE REFERENCES weekly_ceo_sessions(id) ON DELETE CASCADE,
    week_start_date date NOT NULL,                        -- the upcoming week
    content         jsonb NOT NULL,                       -- [{goal_id, focus, planned_actions[]}]
    accepted_at     timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_insights (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent       text NOT NULL CHECK (agent IN ('personal_assistant','mentor','accountability')),
    content     text NOT NULL,
    citations   jsonb NOT NULL DEFAULT '[]',
    trace_id    uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analytics_results (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('energy_completion_corr','mood_completion_corr','weekly_summary')),
    computed_for date NOT NULL,
    result       jsonb NOT NULL,       -- {method, n, coefficient, p_value, status: none|preliminary|not_significant|significant}
    computed_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, computed_for)
);

CREATE TABLE proactive_flags (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag_type    text NOT NULL CHECK (flag_type IN ('escalation_level_3plus','integrity_below_threshold',
                                                    'ceo_skipped_consecutive','lesson_proposal','schedule_suggestion')),
    owner_agent  text NOT NULL CHECK (owner_agent IN ('personal_assistant','mentor','accountability')),
    ref_type     text,
    ref_id       uuid,
    dedupe_key   text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    surfaced_at  timestamptz,
    resolved_at  timestamptz,
    UNIQUE (user_id, dedupe_key)
);
```

`ai_insights` rows are written by `finalize` only when the response pipeline classifies a segment as an insight. That is a recommendation or pattern statement that has at least one citation. The Dashboard reads the newest row (R12.1).

### 24.10 Conversations and Confirmations

```sql
CREATE TABLE conversation_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode            text NOT NULL DEFAULT 'chat' CHECK (mode IN ('chat','onboarding','ceo_meeting')),
    flow_ref_id     uuid,                                -- onboarding_runs.id or weekly_ceo_sessions.id
    origin          text NOT NULL CHECK (origin IN ('user','notification','dashboard_now','why','briefing')),
    title           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    deleted_at      timestamptz
);

CREATE TABLE conversation_messages (
    id          uuid PRIMARY KEY,                          -- = message_id (idempotent upsert)
    session_id  uuid NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('user','assistant')),
    content     text NOT NULL,
    agent       text CHECK (agent IN ('personal_assistant','mentor','accountability')),
    citations   jsonb NOT NULL DEFAULT '[]',
    synthetic   boolean NOT NULL DEFAULT false,            -- opener placeholders are hidden in UI
    trace_id    uuid,
    search_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pending_confirmations (
    action_id     uuid PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id    uuid NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    message_id    uuid NOT NULL,
    thread_id     text NOT NULL,
    tool_name     text NOT NULL,
    tier          text NOT NULL CHECK (tier IN ('mutating','destructive')),
    preview       jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz,
    expires_at    timestamptz NOT NULL
);
```

### 24.11 Notifications

```sql
CREATE TABLE notifications (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type           text NOT NULL CHECK (type IN ('briefing','daily_reflection','ceo_meeting','commitment_breach',
                                                 'commitment_explanation_due','accountability_l1','accountability_l2',
                                                 'accountability_l3','accountability_l4','accountability_l5',
                                                 'schedule_suggestion','lesson_proposal','account_security')),
    level          smallint CHECK (level BETWEEN 1 AND 5),
    title          text NOT NULL,
    body           text NOT NULL,
    deep_link      jsonb NOT NULL,                  -- {screen, context_ref (signed)}; no raw sensitive content
    delivery_state text NOT NULL DEFAULT 'Scheduled'
                   CHECK (delivery_state IN ('Scheduled','Delivered','Opened','Acted Upon')),
    scheduled_at   timestamptz NOT NULL,
    not_before     timestamptz,                     -- DND / rate-limit deferral
    stale_after    timestamptz,                     -- Level 1–2: scheduled_at + 4 h
    delivered_at   timestamptz,
    opened_at      timestamptz,
    acted_at       timestamptz,
    retry_count    smallint NOT NULL DEFAULT 0,
    final_failure  boolean NOT NULL DEFAULT false,
    dedupe_key     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, dedupe_key)
);

CREATE TABLE notification_attempts (
    id              bigserial PRIMARY KEY,
    notification_id uuid NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    attempt_no      smallint NOT NULL,
    channel         text NOT NULL CHECK (channel IN ('push','in_app')),
    status          text NOT NULL CHECK (status IN ('success','failed','dropped_stale','suppressed_rate_limit')),
    error_code      text,
    attempted_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE push_devices (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform         text NOT NULL CHECK (platform IN ('ios','android','web')),
    token_ciphertext bytea NOT NULL,
    token_hash       bytea NOT NULL UNIQUE,
    enabled          boolean NOT NULL DEFAULT true,
    last_seen_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
```

**`users.notification_prefs` schema** (validated by a Pydantic model; missing keys take defaults):

```json
{
  "do_not_disturb": { "enabled": true, "start_time": "22:00", "end_time": "07:00" },
  "types": {
    "briefing":                   { "push": true, "in_app": true },
    "daily_reflection":           { "push": true, "in_app": true },
    "ceo_meeting":                { "push": true, "in_app": true },
    "commitment_breach":          { "push": true, "in_app": true },
    "commitment_explanation_due": { "push": true, "in_app": true },
    "accountability_l1":          { "push": true, "in_app": true },
    "accountability_l2":          { "push": true, "in_app": true },
    "accountability_l3":          { "push": true, "in_app": true },
    "accountability_l4":          { "push": true, "in_app": true },
    "accountability_l5":          { "push": true, "in_app": true },
    "schedule_suggestion":        { "push": false, "in_app": true },
    "lesson_proposal":            { "push": false, "in_app": true }
  }
}
```

- The delivery **times** of the briefing, reflection, and CEO Meeting are the dedicated `users` columns. Channel choice is per type, and therefore per accountability level (R8.9, R14.3).
- DND times are local, and a window may cross midnight.
- At least one channel must remain enabled for `accountability_l4`, `accountability_l5`, and `account_security`. In-app delivery cannot be disabled for these types.

### 24.12 Observability and Operations

```sql
CREATE TABLE agent_traces (
    id                  uuid PRIMARY KEY,                    -- = trace_id
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id          uuid REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    message_id          uuid,
    graph               text NOT NULL CHECK (graph IN ('supervisor','onboarding','ceo_meeting','background')),
    mode                text,
    selected_agent      text,
    routing_confidence  numeric(3,2),
    ambiguity_flag      boolean NOT NULL DEFAULT false,
    classifier_fallback boolean NOT NULL DEFAULT false,
    tool_calls          jsonb NOT NULL DEFAULT '[]',   -- [{name, tier, status, latency_ms, error_code, sanitized_input}]
    memory_queries      jsonb NOT NULL DEFAULT '[]',   -- [{query_hash, k, threshold, result_ids[]}]
    proposed_actions    jsonb NOT NULL DEFAULT '[]',   -- [{action_id, tool_name, tier, outcome: approved|edited|rejected|expired}]
    outcome             text CHECK (outcome IN ('accepted','rejected','mixed','none')),
    has_tool_error      boolean NOT NULL DEFAULT false, -- flagged for developer review (R23.4)
    grounding_flags     text[] NOT NULL DEFAULT '{}',   -- grounding_violation, missing_longitudinal_citation, ...
    review_status       text NOT NULL DEFAULT 'none' CHECK (review_status IN ('none','flagged','reviewed')),
    model_calls         smallint NOT NULL DEFAULT 0,
    input_tokens        int,
    output_tokens       int,
    first_token_ms      int,
    total_ms            int,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_trace_payloads (
    trace_id           uuid PRIMARY KEY REFERENCES agent_traces(id) ON DELETE CASCADE,
    capture_mode       text NOT NULL CHECK (capture_mode IN ('metadata','full_redacted','full')),
    encrypted_payload  bytea NOT NULL,
    key_version        text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE background_job_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name       text NOT NULL,
    logical_run_at timestamptz NOT NULL,
    user_id        uuid REFERENCES users(id) ON DELETE CASCADE,  -- NULL for whole-sweep rows
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    attempt_no     smallint NOT NULL DEFAULT 1,
    status         text NOT NULL CHECK (status IN ('running','succeeded','failed')),
    error_code     text,
    error_details  text                                  -- sanitized
);

CREATE TABLE developer_alerts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source       text NOT NULL,                          -- job name / subsystem
    severity     text NOT NULL CHECK (severity IN ('warning','error','critical')),
    user_id      uuid REFERENCES users(id) ON DELETE CASCADE,
    details      jsonb NOT NULL,                         -- sanitized
    created_at   timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz
);
```

### 24.13 Platform Tables

```sql
CREATE TABLE idempotency_records (
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key     text NOT NULL,
    request_fingerprint bytea NOT NULL,                  -- sha256(method + route + canonical body)
    status              text NOT NULL CHECK (status IN ('processing','completed','failed')),
    result              jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    PRIMARY KEY (user_id, idempotency_key)
);

CREATE TABLE domain_events (                          -- transactional outbox (Section 4.3)
    id               bigserial PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type       text NOT NULL,
    payload          jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    available_at     timestamptz NOT NULL DEFAULT now(),  -- retry backoff: not claimable before this
    attempts         smallint NOT NULL DEFAULT 0,
    last_error       text,                                -- sanitized
    processed_at     timestamptz,
    dead_lettered_at timestamptz                          -- after max attempts; raises a developer alert
);

CREATE TABLE realtime_events (                        -- SSE replay buffer (Section 28.3)
    id          bigserial PRIMARY KEY,                  -- monotonic sequence
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type  text NOT NULL,
    payload     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE data_exports (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       text NOT NULL CHECK (status IN ('queued','running','ready','failed','expired')),
    object_key   text,                                   -- encrypted object storage location
    requested_at timestamptz NOT NULL DEFAULT now(),
    ready_at     timestamptz,
    expires_at   timestamptz                             -- ready_at + 24 h
);

CREATE TABLE account_deletion_requests (               -- survives the purge for audit (no FK, no PII)
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL,
    confirmed_at     timestamptz NOT NULL,
    purge_due_at     timestamptz NOT NULL,               -- confirmed_at + 30 days (purge runs earlier by default: + 7 days)
    purged_at        timestamptz,
    status           text NOT NULL CHECK (status IN ('pending_purge','purged','failed'))
);
```

### 24.14 Account Deletion Semantics (R17.7, R18.2–18.7)

On confirmed deletion (`POST /account-deletion/confirm` with the typed phrase and password re-entry), the following happens in one transaction:
1. `users.status='deletion_pending'`. Every auth session is invalidated and every realtime connection is closed. From this moment, authentication, RLS policies, and all services reject access, so all data becomes inaccessible immediately (R18.3).
2. An `account_deletion_requests` row is created with `purge_due_at`.

The `deletion_purge` job then:
1. deletes LangGraph threads for all of the User's thread IDs (chat, onboarding, CEO);
2. deletes object-storage exports;
3. deletes Redis keys (`user:{id}:*`);
4. revokes push tokens with the provider where supported;
5. deletes LangSmith runs tagged with the User's pseudonymous ID, if export was ever enabled (Section 31.2);
6. runs `DELETE FROM users WHERE id=$1` with `app.allow_history_delete='on'`. This cascades to every user-owned table, including Agent Traces (R18.6).

Purge completes within 7 days by default, and never later than 30 days. Anonymization is never used as a substitute (R18.7). Backups expire within 30 days (Section 36.5), so deleted data does not outlive the window (R18.4).

**Individual session deletion (R18.9)** deletes the conversation messages, the session's traces, its pending confirmations, and its LangGraph thread. Memory entries are unaffected (R18.10).

---

## 25. Database Indexes

```sql
-- Goal hierarchy
CREATE INDEX idx_goals_user_priority ON goals(user_id, priority DESC, target_date, created_at)
    WHERE status = 'active' AND deleted_at IS NULL;
CREATE INDEX idx_objectives_goal ON objectives(goal_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_objective ON projects(objective_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_tasks_user_status_due ON tasks(user_id, status, due_date) WHERE deleted_at IS NULL;
CREATE INDEX idx_objective_history ON objective_value_history(objective_id, changed_at);

-- Habits and routines
CREATE INDEX idx_habits_user_active ON habits(user_id) WHERE status = 'active' AND deleted_at IS NULL;
CREATE INDEX idx_habit_pauses ON habit_pause_periods(habit_id, starts_on);
CREATE INDEX idx_routine_entries_template ON routine_entries(routine_template_id, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX idx_routine_exceptions ON routine_exceptions(user_id, date);

-- Daily Actions and check-ins
CREATE INDEX idx_daily_actions_user_date ON daily_actions(user_id, date) WHERE lifecycle_state = 'active';
CREATE INDEX idx_daily_actions_user_start ON daily_actions(user_id, scheduled_start);
CREATE INDEX idx_daily_actions_source ON daily_actions(user_id, source_type, source_id, date);
CREATE INDEX idx_daily_actions_due_eval ON daily_actions(scheduled_start, scheduled_end)
    WHERE status IN ('Planned','Started') AND lifecycle_state = 'active';      -- Level 1/2 sweeps
CREATE INDEX idx_checkin_action_created ON checkin_records(daily_action_id, created_at);
CREATE INDEX idx_checkin_user_created ON checkin_records(user_id, created_at);
CREATE INDEX idx_schedule_history_action ON daily_action_schedule_history(daily_action_id, changed_at);

-- Commitments and accountability
CREATE INDEX idx_commitments_user_status_due ON commitments(user_id, status, current_due_date);
CREATE INDEX idx_commitments_eval ON commitments(current_due_date) WHERE status IN ('Open','Deferred');
CREATE INDEX idx_commitment_links_entity ON commitment_links(entity_type, entity_id) WHERE removed_at IS NULL;
CREATE INDEX idx_commitment_events ON commitment_events(commitment_id, created_at);
CREATE INDEX idx_escalation_user_level ON accountability_escalation_states(user_id, level);

-- Memory
CREATE INDEX idx_memory_user_type ON memory_store_entries(user_id, type, created_at DESC);
CREATE INDEX idx_memory_user_ready ON memory_store_entries(user_id)
    WHERE embedding_status = 'ready' AND superseded_by IS NULL;
CREATE INDEX idx_memory_embedding_hnsw ON memory_store_entries USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memory_pending ON memory_store_entries(created_at) WHERE embedding_status = 'pending';
CREATE INDEX idx_memory_content_trgm ON memory_store_entries USING gin (content gin_trgm_ops);

-- Reflections, conversations
CREATE INDEX idx_reflections_user_date ON reflections(user_id, date DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_reflections_tsv ON reflections USING gin (search_tsv);
CREATE INDEX idx_messages_session_created ON conversation_messages(session_id, created_at);
CREATE INDEX idx_messages_tsv ON conversation_messages USING gin (search_tsv);
CREATE INDEX idx_sessions_user_activity ON conversation_sessions(user_id, last_activity_at DESC) WHERE deleted_at IS NULL;

-- Notifications
CREATE INDEX idx_notifications_due ON notifications(scheduled_at)
    WHERE delivery_state = 'Scheduled' AND final_failure = false;
CREATE INDEX idx_notifications_user_created ON notifications(user_id, created_at DESC);

-- Traces (R23.2)
CREATE INDEX idx_traces_user_created ON agent_traces(user_id, created_at);
CREATE INDEX idx_traces_session ON agent_traces(session_id);
CREATE INDEX idx_traces_agent_created ON agent_traces(selected_agent, created_at);
CREATE INDEX idx_traces_flagged ON agent_traces(created_at) WHERE review_status = 'flagged';

-- Platform
CREATE INDEX idx_idempotency_expires ON idempotency_records(expires_at);
CREATE INDEX idx_domain_events_pending ON domain_events(available_at, id)
    WHERE processed_at IS NULL AND dead_lettered_at IS NULL;
CREATE INDEX idx_realtime_events_user ON realtime_events(user_id, id);
CREATE INDEX idx_proactive_open ON proactive_flags(user_id) WHERE resolved_at IS NULL;
CREATE INDEX idx_auth_sessions_user_active ON auth_sessions(user_id) WHERE invalidated_at IS NULL;
```

---

## 26. API Design

### 26.1 Conventions

- Base path `/api/v1`. JSON only. Timestamps are ISO-8601 UTC. Local dates are `YYYY-MM-DD`, interpreted in the User's timezone.
- **Authentication:** all endpoints except the public auth endpoints need `Authorization: Bearer <access JWT>` (R15.6).
- **Idempotency:** mutations need an `Idempotency-Key` header (Section 22).
- **Pagination:** cursor-based, using `?cursor=&limit=` (default 50, max 200).
- **Ownership failures:** a resource that is not owned returns the generic `404 NOT_FOUND`, which prevents enumeration.
- **Generated reference:** the OpenAPI schema is generated from FastAPI and is the contract reference for the clients.

### 26.2 Endpoint Catalogue

```text
# Auth (public unless noted)
POST   /auth/register
POST   /auth/login
POST   /auth/refresh
POST   /auth/logout                                (auth)
POST   /auth/email-verification/confirm
POST   /auth/email-verification/resend
POST   /auth/password-reset/request
POST   /auth/password-reset/confirm

# Profile and onboarding (R16)
GET    /me
PATCH  /me                                         # any profile field (R16.6); timezone change → Section 12.5
PATCH  /me/notification-preferences
POST   /onboarding/runs                            # start / re-initiate (R16.7) → returns chat session
GET    /onboarding/runs/current

# Goal hierarchy (R1, R15.1)
GET|POST            /goals
GET|PATCH|DELETE    /goals/{id}                    # DELETE → 409 CASCADE_CONFIRMATION_REQUIRED with counts unless ?confirm_cascade=true
POST                /goals/{id}/archive
GET                 /goals/{id}/hierarchy          # tree view (R1.16)
PATCH               /goals/{id}/priority
GET|POST            /goals/{id}/objectives
GET|PATCH|DELETE    /objectives/{id}
POST                /objectives/{id}/value          # current value update
GET|POST            /objectives/{id}/projects
GET|PATCH|DELETE    /projects/{id}
GET|POST            /tasks                          # filters: status, due_before, project_id, goal_id
GET|PATCH|DELETE    /tasks/{id}
POST                /tasks/{id}/schedule            # creates TASK Daily Action (R1.9)
POST                /tasks/{id}/complete

# Habits (R1.10–1.12)
GET|POST            /habits
GET|PATCH|DELETE    /habits/{id}
GET                 /habits/{id}/metrics            # counts, streaks, missed, partial, pauses
POST                /habits/{id}/pause
POST                /habits/{id}/resume
POST                /habits/{id}/occurrences        # record completed / skipped / partial

# Routines (R2)
GET|POST            /routine-templates
GET|PATCH|DELETE    /routine-templates/{id}
GET|POST            /routine-templates/{id}/entries
GET|PATCH|DELETE    /routine-entries/{id}

# Daily Actions and check-ins (R2, R3)
GET                 /daily-actions?date=            # unified day view ordered by start (R3.8)
POST                /daily-actions                  # MANUAL one-off
GET                 /daily-actions/{id}
POST                /daily-actions/{id}/checkins    # {new_status, note} → Check-in Record (R3.4–3.6)
GET                 /daily-actions/{id}/checkins    # full history (R3.9)
PATCH               /daily-actions/{id}/schedule    # source-aware reschedule (R2.7)
GET                 /daily-actions/{id}/schedule-history
POST                /daily-actions/{id}/cancel
GET                 /schedule-suggestions?status=proposed
POST                /schedule-suggestions/{id}/accept | /reject

# Commitments (R9)
GET|POST            /commitments                    # filters/sort (R9.15)
GET                 /commitments/{id}               # includes events history
POST                /commitments/{id}/keep | /cancel | /defer
POST                /commitments/{id}/explanation   # explanation + acknowledgment (Section 11.3)
GET                 /integrity-score                # current + 30-day series

# Accountability (R8)
GET                 /accountability/escalations
POST                /accountability/escalations/{id}/reflection

# Reflections and journal (R11)
GET                 /reflections/prompt?date=
GET|POST            /reflections                    # journal: q, start, end, category (R11.11)
GET|PATCH|DELETE    /reflections/{id}
GET                 /analytics/correlations
GET                 /memory-proposals?status=proposed
POST                /memory-proposals/{id}/accept | /reject

# Memory (R4, R18.8, R18.11)
GET|POST            /memory                         # browse: type, source, category, q
GET|PATCH           /memory/{id}                    # PATCH: importance, content (User-stated only)
DELETE              /memory/{id}                    # requires X-Confirm-Phrase header

# Briefings, Now, Why, CEO (R6, R7, R10, R13.7)
GET                 /briefings/today
GET                 /briefings?start=&end=
POST                /chat/now                       # session-less Now Mode; result via SSE
POST                /why                            # {target_type, target_id} → chat session id
GET                 /ceo-meetings/current
POST                /ceo-meetings/{id}/open         # → chat session (mode=ceo_meeting)
GET                 /ceo-meetings/history

# Dashboard (R12)
GET                 /dashboard                      # single aggregate read model (Section 35)

# Chat (R13)
GET|POST            /chat/sessions                  # list/search (q) past sessions (R13.3); create session
GET|DELETE          /chat/sessions/{id}             # DELETE → Section 24.14 (R18.9)
GET                 /chat/sessions/{id}/messages
GET                 /chat/sessions/{id}/pending-confirmations

# Notifications and devices (R14, R22)
GET                 /notifications
POST                /notifications/{id}/opened | /acted
POST                /push-devices
DELETE              /push-devices/{id}

# Realtime
POST                /realtime/tickets               # {purpose: chat|dashboard, session_id?}

# Privacy (R17.7, R18)
POST                /exports                        # async JSON export job
GET                 /exports/{id}                   # status + signed download URL (24 h)
POST                /account-deletion/confirm

# Developer (staff_roles.developer; audited) (R22.4, R23.3)
GET                 /admin/traces?session_id=&user_id=&agent=&start=&end=&outcome=&flagged=
GET                 /admin/traces/{id}              # metadata; payload requires trace_payload_reader
PATCH               /admin/traces/{id}/review
GET                 /admin/notifications/delivery-log?type=&state=&start=&end=
GET                 /admin/job-runs · /admin/alerts
```

### 26.3 Response Contract

Success:
```json
{ "data": {}, "meta": { "request_id": "uuid", "timestamp": "ISO-8601 UTC", "next_cursor": null } }
```

Error:
```json
{ "error": { "code": "VALIDATION_ERROR", "message": "Human-readable message", "details": {} },
  "meta":  { "request_id": "uuid", "timestamp": "ISO-8601 UTC" } }
```

Stable error codes include:
- `VALIDATION_ERROR`, `NOT_FOUND`, `INVALID_TRANSITION`, `INVALID_COMMITMENT_TRANSITION`
- `SKIP_REASON_REQUIRED`, `GOAL_ARCHIVED`, `CASCADE_CONFIRMATION_REQUIRED`
- `REQUEST_IN_PROGRESS`, `IDEMPOTENCY_KEY_REUSED`, `EMAIL_NOT_VERIFIED`, `ACCOUNT_LOCKED`
- `RATE_LIMITED`, `TURN_IN_PROGRESS`, `AI_UNAVAILABLE`
- authentication: `UNAUTHENTICATED`, `INVALID_CREDENTIALS` (unknown email and wrong password are indistinguishable), `INVALID_TOKEN`, `EMAIL_TAKEN`, `WEAK_PASSWORD`, `FORBIDDEN`

HTTP mapping: validation 422 · not-found 404 · state conflicts 409 · `EMAIL_NOT_VERIFIED`/`FORBIDDEN` 403 · `ACCOUNT_LOCKED` 423 · `RATE_LIMITED` 429 · authentication 401 (with `WWW-Authenticate: Bearer`) · `AI_UNAVAILABLE` 503 · unhandled 500 `INTERNAL_ERROR` with no internals.

### 26.4 JSON Export (R18.1)

`POST /exports` queues a job. The job writes a single JSON document, with top-level keys matching the Section 24 table groups:
- `profile`, `goals` (nested objectives/projects), `tasks`, `habits` (with occurrence records and pauses), `routines`
- `daily_actions` (with check-ins and schedule history), `commitments` (with links and events)
- `reflections`, `memory` (content and metadata, no embeddings), `conversations`, `notifications`, `trace_metadata`

The file is stored encrypted in object storage and exposed through a signed URL valid for 24 hours. Secrets and credential material are excluded.

---

## 27. WebSocket Chat Protocol

### 27.1 Authentication

Browser WebSocket APIs cannot set `Authorization` headers reliably, and long-lived bearer tokens must not be placed in query strings where infrastructure may log them. The flow is:
1. the client calls `POST /realtime/tickets {purpose: "chat", session_id}` over authenticated HTTPS;
2. the server issues a one-time opaque ticket bound to `user_id`, `auth_session_id`, `conversation_session_id`, and purpose, with a TTL of 60 seconds or less;
3. the client connects to `WSS /ws/chat?ticket=<ticket>`;
4. the server atomically consumes the ticket (Redis `GETDEL`). The connection is also bound to `auth_session_id` and is closed on logout (Section 21.2).

Every reconnect obtains a new ticket.

### 27.2 Client Messages

```json
{"type":"message","message_id":"uuid","content":"...","mode":"chat"}
{"type":"message","message_id":"uuid","content":"What should I be doing now?","mode":"now"}
{"type":"resume","message_id":"uuid","action_id":"uuid","decision":{"type":"approve"}}
{"type":"resume","message_id":"uuid","action_id":"uuid","decision":{"type":"edit","args":{"new_start":"18:00"}}}
{"type":"resume","message_id":"uuid","action_id":"uuid","decision":{"type":"approve","confirmation_phrase":"CONFIRM"}}
{"type":"resume","message_id":"uuid","action_id":"uuid","decision":{"type":"reject"}}
{"type":"flow_answer","message_id":"uuid","content":"..."}          // onboarding / CEO question answers
```

The connection is bound to one conversation session, so messages never carry a `session_id`. The client generates `message_id` (a UUID) as the idempotency key for the turn.

### 27.3 Server Events

The required events are `session_started`, `message_started`, `agent_selected`, `tool_started`, `tool_completed`, `confirmation_required`, `confirmation_rejected`, `flow_question`, `token`, `error`, and `done`.

Every event includes `message_id`, `trace_id`, and a per-connection sequence number.
- `agent_selected` carries the attribution label ("Personal Assistant", "Mentor", "Accountability Partner") (R13.2).
- `done` carries `final_response {text, citations, agent, ambiguity}`.

### 27.4 Exactly One Active Turn per Session

A session processes one graph turn at a time (Section 6.6). A second `message` received while a model turn or a confirmation is pending is rejected with `TURN_IN_PROGRESS`. The client may queue it locally. This prevents checkpoint races.

### 27.5 Resume Validation

A `resume` is accepted only when all of the following hold:
- it matches the active `message_id` and `action_id`, and a `pending_confirmations` row with status `pending`;
- it arrives on the same authenticated user and conversation session;
- for the destructive tier, `confirmation_phrase` equals the configured phrase (default `CONFIRM`).

The row transitions atomically (`UPDATE … WHERE status='pending'`). A duplicate resume returns the already-completed `done` result and never executes twice.

### 27.6 Reconnection

On reconnect with the same session, the server replays the latest `done`, or re-sends `confirmation_required` for any pending confirmation read from `pending_confirmations`, so a card is never lost.

---

## 28. SSE Dashboard Transport

### 28.1 Transport Choice

SSE is the V1 dashboard transport because dashboard updates are server-to-client only (R12.2).

### 28.2 Authentication and Reconnection

Native `EventSource` cannot send Authorization headers, and its automatic reconnect would reuse a consumed ticket. Clients therefore use a small reconnect wrapper:
1. before **every** connection attempt, call `POST /realtime/tickets {purpose: "dashboard"}`;
2. connect to `GET /dashboard/stream?ticket=<ticket>&last_event_id=<seq>`;
3. on error, close the stream and reconnect with exponential backoff (1 s → 30 s max), with a fresh ticket and the last seen sequence ID.

On React Native, `react-native-sse` is used with the same wrapper.

### 28.3 Event Durability

- Redis Pub/Sub is the low-latency fan-out layer. `realtime_events` in PostgreSQL stores event envelopes for **at least 1 hour**, with a monotonic `id` that serves as the sequence.
- On connect with `last_event_id`, the server replays newer events from `realtime_events` and then switches to live delivery.
- If `last_event_id` is older than the retained window, the server sends `resync_required`. The client then refetches `GET /dashboard`.

### 28.4 Event Types

`daily_action_updated`, `accountability_state_changed`, `notification_received`, `integrity_score_updated`, `goal_progress_updated`, `ai_insight_updated`, `proactive_flag_changed`, `schedule_suggestion_created`, `now_mode_result`, `resync_required`.

SSE is an invalidation/update channel. Clients refetch canonical REST data when an event is missed or malformed.

---

## 29. Notification Architecture (R14, R22)

### 29.1 Channels

V1 supports in-app notifications and push notifications through an adapter interface: FCM for Android and web, APNs for iOS, and Web Push through VAPID where FCM is not used. Device registrations live in `push_devices`.

### 29.2 Pipeline

```text
deterministic trigger (sweeper / domain event)
→ create notification row (dedupe_key; Level 1–2 get stale_after = +4 h)
→ channel preferences (Section 24.11)
→ DND evaluation (29.3) → sets not_before for queued items
→ rate limit (29.4)     → push deferred or downgraded to in-app
→ dispatch channels → notification_attempts row per attempt
→ delivery_state updates (Scheduled → Delivered → Opened → Acted Upon)
```

Tapping a push deep-links into Chat with a **signed context reference**. The Gateway resolves the reference to seed the session: for example, the escalation details for Level 3, or the briefing ID. The push payload never contains raw sensitive content (R14.2).

### 29.3 Do Not Disturb (R14.4)

Level 1–2 accountability notifications are queued until DND ends: `not_before` is set to the DND end. Level 3–5 notifications are delivered immediately regardless of DND. Other types (briefing, reflection, CEO Meeting, commitment notices) are queued during DND.

### 29.4 Rate Limit (R14.5)

- **Global rule:** no more than 3 push notifications in any rolling 60-minute window per User, across all types. Level 4 and Level 5 are exempt and are delivered even when the cap is reached.
- **Accountability Style** may lower the cap for accountability Levels 1–3 (Section 14.6). It never raises the cap above 3.
- **When the cap is reached:**
  - Levels 1–3 and other non-critical types are delivered **in-app immediately**.
  - The push is deferred to the next free slot while the notification is still within its staleness window, or is dropped with `suppressed_rate_limit`.
- Counters are implemented as a Redis sorted-set sliding window, keyed per User.

### 29.5 Retry (R14.7, R22.2)

A failed push delivery is retried at +1, +4, and +16 minutes. Each attempt is stored in `notification_attempts`. After the third failed retry, `final_failure=true` and a developer alert is raised if the failure rate exceeds a threshold.

### 29.6 Offline and Staleness (R14.8, R22.3)

- Push providers queue messages for offline devices, and the server sets a provider TTL equal to the staleness window. In-app notifications are fetched when the client reconnects (`GET /notifications`).
- Levels 1–2 expire after 4 hours by default and are marked `dropped_stale`.
- Levels 3–5 are always delivered. There is no provider TTL for them, and in-app delivery is guaranteed.

### 29.7 Delivery State and Log (R14.6, R22.1, R22.4)

The primary state is `Scheduled | Delivered | Opened | Acted Upon`. `Opened` is set when the push is tapped or the in-app item is viewed. `Acted Upon` is set when the linked action completes, for example a check-in, an opened CEO Meeting, or a submitted reflection.

Failed attempts do not add a primary state; they live in `notification_attempts`. The developer delivery log (`/admin/notifications/delivery-log`) shows type, scheduled time, delivery state, and retry count.

---

## 30. Agent Trace Privacy and Storage (R23)

R23.1 requires a complete trace and also allows configurable PII exclusion. The design meets both with two storage layers.

### 30.1 Trace Metadata (queryable)

`agent_traces` (Section 24.12) stores:
- the routing decision, confidence, and agent;
- tool calls with sanitized input, status, latency, and error code (R15.8);
- memory query hashes and result IDs;
- proposed actions and User outcomes;
- grounding flags, token counts, and latency.

A trace with any tool error is set to `has_tool_error=true` and `review_status='flagged'` (R23.4). Traces are indexed by session, user, agent, and time (R23.2).

### 30.2 Encrypted Trace Payload

`agent_trace_payloads` stores the detailed trace used for debugging:
- the model request messages after redaction, and the model responses;
- sanitized tool arguments and results;
- memory queries and results;
- graph node events.

**Encryption and capture modes:**
- Payloads are encrypted with a KMS-managed data key (envelope encryption, `key_version` recorded).
- Production defaults to `capture_mode='full_redacted'`, which masks emails, phone numbers, full name, and free-text notes longer than 200 characters. Development uses synthetic data with `capture_mode='full'`.
- Secrets, tokens, password material, encryption keys, and push tokens are always excluded, in every capture mode.

**Access:** reading a payload requires the `trace_payload_reader` role, and every read is audit-logged.

### 30.3 Retention

Traces are retained for at least 90 days (R23.5) and deleted by `retention_cleanup` after 180 days. Account deletion overrides operational retention (Section 24.14, R18.6).

### 30.4 Developer Interface (R23.3)

The `/admin/traces` endpoints (Section 26.2) and a minimal internal web page support queries by session ID, user ID, agent name, date range, outcome (accepted/rejected), and flagged status. Access is restricted to `staff_roles` and audited.

---

## 31. Observability and Evaluation

### 31.1 Authoritative Application Trace

The database trace in Section 30 is the product's authoritative audit and debug record.

### 31.2 LangSmith

LangSmith is used for development, staging, and evaluation runs. Production export is **off** by default. If it is enabled for an incident, runs are tagged with a pseudonymous user ID (an HMAC of `user_id`) and inputs and outputs pass through the same redaction as `full_redacted` payloads. Account deletion deletes such runs (Section 24.14).

### 31.3 Evaluation Suite (R26)

A standalone command, `python -m lifeos_eval run --suite all --report out/`, runs independently of the production application (R26.5). It uses a seeded fixture database and the configured models. Trajectory checks use `agentevals`:
- `create_trajectory_match_evaluator`, in `superset` mode for `expected_tools` and `strict` mode for confirmation-discipline cases;
- `create_trajectory_llm_as_judge`, for rubric-based `pass_criteria`.

Results are logged to LangSmith experiments in staging.

Case format (R26.2):
```python
{
  "input_message": "...",
  "expected_agent": "Personal_Assistant_Agent",      # | Mentor_Agent | Accountability_Agent
  "expected_tools": ["get_today_schedule"],
  "expected_behaviour": "...",
  "pass_criteria": "..."                              # machine-checkable predicate id + params, or LLM-judge rubric id
}
```

The suite maintains at least 5 cases for each category (R26.1, R26.3):
- routing accuracy;
- tool selection correctness;
- schedule reasoning, including Now Mode and conflicts;
- accountability escalation, meaning explanation of deterministic states;
- memory retrieval relevance;
- hallucination/grounding detection, meaning the citation validator and inference labels.

Additional V1 categories are confirmation discipline (no mutation without a card), rejection handling, web citation, and Accountability Style tone.

The report (R26.4) lists the pass rate per category, the total pass rate, failed cases, and actual versus expected agent, tools, and behaviour evidence. The CI release gate requires ≥ 90% total, and 100% on confirmation discipline and grounding.

### 31.4 Operational Telemetry

OpenTelemetry traces, metrics, and logs are exported to the observability backend. Key metrics:
- chat first-token latency (p50/p95) and turn duration;
- classifier fallback rate, tool error rate, grounding-violation rate;
- LLM tokens and cost per user per day;
- scheduler lag (actual start minus logical time) and job failure count;
- notification delivery success rate;
- SSE/WebSocket connection counts.

Alerts: p95 first token > 5 s for 10 minutes, job failure after retries, notification failure rate > 5%, LLM provider error rate > 10%.

### 31.5 Test Separation

- pytest unit tests for pure deterministic logic;
- PostgreSQL integration tests for the database, services, and API;
- graph tests for interrupt/resume, routing, the tool policy, and ToolMessage pairing, using `InMemorySaver` and fake chat models;
- evaluation tests for model behaviour against fixtures;
- production traces for operational observability only.

---

## 32. Frontend Architecture

### 32.1 Stack

- **Web:** React 18+, TypeScript, TailwindCSS, TanStack Query (server state), Zustand (local UI state).
- **Mobile:** React Native (Expo), sharing a TypeScript API client and domain types generated from OpenAPI.
- **Real-time:** the ticketed SSE wrapper for the dashboard (Section 28.2) and WebSocket for chat.
- **Build:** Vite (web), Expo EAS (mobile).

### 32.2 Key Screens

| Screen | Description |
|---|---|
| Onboarding | Guided conversation with a final review card; resumable (Section 19.1) |
| Life Dashboard | Today's schedule with status, integrity score and trend, goals by category with progress, latest AI insight, Level ≥ 3 alerts and proactive flags above the fold, one-tap **Now** and **Chat** (R12) |
| Chat | Agent attribution labels, source-citation chips, confirmation cards, session history with search (R13) |
| Goal Hierarchy | Tree view Goal → Objective → Project → Task / Habit → Daily Action (R1.16) |
| Daily Actions | Today's schedule with check-in controls; the skip-reason prompt is mandatory; "Why?" button (R3, R13.7) |
| Habits | Streaks, completion counts, missed/partial, pauses (R1.11) |
| Reflections Journal | Daily prompt form; chronological list; keyword, date, and category filters; correlation findings; Lesson proposals (R11) |
| Promise Ledger | Commitments with filters and sorting; deferral and explanation flow (R9) |
| Memory Browser | Entries by type, source, and category; inference labels; delete (R18.11) |
| CEO Meeting | Pre-session briefing, then the 4 questions, then summary and focus plan (R10) |
| Settings | Profile, timezone, briefing/reflection/CEO times, notification channels per type and level, DND, accountability style and threshold, redo onboarding, export, account deletion |

### 32.3 Confirmation Card Component

When a `confirmation_required` event arrives, the UI renders a `ConfirmationCard`:
- **Content:** a plain-language description of the action, the deterministic preview (before/after), and a tier badge (Mutating/Destructive).
- **Buttons:** Confirm, Edit (limited to `editable_fields`), and Reject.
- **Destructive tier:** an explicit warning label, and the User must type `CONFIRM` before Confirm is enabled (R20.3).
- **Result:** the card sends a `resume` message and shows the outcome inline.

### 32.4 Performance

The dashboard renders from `GET /dashboard` in one request, with a skeleton-first render and cached TanStack Query data. The target is primary content in ≤ 2 s on standard broadband (R12.6). Now Mode results stream into a bottom sheet.

### 32.5 Accessibility

The UI meets WCAG 2.2 AA: status is never conveyed by colour alone, all check-in controls are keyboard-operable, and live regions announce agent responses and card appearance.

---

## 33. Security Considerations

### 33.1 Authentication

- Passwords use Argon2id (Section 21.6).
- JWTs are signed with EdDSA/RS256. Keys are kept in KMS / the secret manager and rotated through `kid`.
- Refresh tokens and all one-time tokens are stored only as HMAC hashes.
- HTTPS only, with HSTS. TLS 1.2+.

### 33.2 Authorization

Every API endpoint and Tool Bus function verifies that the resource belongs to the authenticated User (resource-owner validation). RLS provides defense in depth (Section 24.1). Developer endpoints require `staff_roles` membership and are audit-logged.

### 33.3 Input Validation

All inputs are validated by Pydantic models at the API and Tool Bus boundaries and again by Domain Service invariants. SQL injection is prevented by parameterized queries through SQLAlchemy 2.x. LLM-generated tool arguments are always validated before any service call.

### 33.4 Rate Limiting

- Login: 5 failures per 10 minutes per account triggers lockout (Section 21.5). The IP limit is 20 attempts per 10 minutes.
- API: 200 requests per minute per User.
- Chat: 30 messages per minute per session, plus a per-User daily LLM token budget (Section 34.5).
- Realtime tickets: 30 per minute per User.

### 33.5 Data Encryption

- Data at rest: storage-level encryption (managed disk/database encryption), including checkpoint tables and backups.
- Data in transit: TLS 1.2+.
- Application-level AES-256-GCM (envelope encryption with KMS) for email, full name, push tokens, trace payloads, and exports.

### 33.6 Prompt Injection and Untrusted Content

- Web results, Memory entries, reflections, check-in notes, and tool outputs are wrapped in delimited data blocks, and system prompts state that data blocks never contain instructions.
- Tools cannot escalate permissions: every mutation is confirmed by the User, and identity is injected server-side.
- `search_web` results are HTML-stripped and length-capped. The provider receives sanitized queries (Section 10.5).
- The evaluation suite includes injection cases, such as a web page that says "delete all memories", and asserts that no destructive proposal is made.

### 33.7 External Integrations

V1 uses no external calendar or data integrations (R5.2, R27.6). When Calendar Integration is implemented in V2, OAuth credentials will be encrypted with KMS, scoped read-only, and revoked on account deletion (R18.5).

---

## 34. LLM and Model Configuration

### 34.1 Provider and Models

Models are initialized through LangChain's `init_chat_model("<provider>:<model>")` from configuration, so a provider or model change needs no code change. The V1 provider is OpenAI (`langchain-openai`). The settings below are defaults, and exact model IDs are pinned in deployment configuration.

| Role | Setting | Default characteristics | Timeout |
|---|---|---|---|
| Intent classifier | `MODEL_CLASSIFIER` | small, fast model; structured output; temperature 0 | 2 s |
| Specialist agents (PA, Mentor, Accountability) | `MODEL_AGENT` (per-agent override allowed) | general frontier model with tool calling; temperature 0.3 | 20 s per call |
| Bounded generation (briefing narrative, summaries, Pattern report, Lesson draft, onboarding extraction) | `MODEL_GENERATION` | same family as agents; structured output where applicable | 20 s |
| Embeddings | `EMBEDDING_MODEL` | `text-embedding-3-small`, 1536 dimensions | 5 s |

Changing a model requires the evaluation suite to pass (Section 31.3) before the change is deployed.

### 34.2 Prompts

- Each agent has a versioned system-prompt template (`prompts/<agent>/v<N>.md`) with these sections: role and boundaries, grounding and citation rules (Section 6.9), tool-use rules, style rules (Section 14.6), the data-block notice, and a mode-specific appendix (now / why / opener / ambiguity / budget exhausted).
- The prompt version is recorded on every trace.
- Prompt changes are reviewed like code and gated by the evaluation suite.

### 34.3 Token Budgets

| Component | Budget |
|---|---|
| System prompt (without context) | ≤ 1 500 tokens |
| Context bundle | ≤ 4 000 tokens |
| Working history | ≤ 6 000 tokens |
| Tool results per call | ≤ 2 000 tokens (truncated with `truncated: true`) |
| Model calls per turn | ≤ 8 |

### 34.4 Degradation

- **Provider failure:** transient errors are retried by `RetryPolicy` (3 attempts, exponential backoff). A persistent failure produces `error{code: AI_UNAVAILABLE}` in chat.
- **Classifier failure:** falls back to the Personal Assistant (Section 6.4).
- **Scheduled generation failure:** uses template text and is marked `template`. Deterministic content, such as the briefing sections and the reflection prompt, is always delivered.
- **Embedding failure:** the entry stays `pending` and is retried by `embedding_backfill`. Search excludes it until it is embedded.

### 34.5 Cost Controls

- A per-User daily token budget is enforced by the Gateway (default 200 000 tokens, configurable). Soft warnings appear at 80%. When the budget is exceeded, chat is limited to Now Mode and briefings until local midnight.
- Token usage is recorded per trace, and cost per active User per day is a monitored metric.

---

## 35. Non-Functional Requirements

| Area | Target | Source |
|---|---|---|
| Chat first token | p95 ≤ 5 s, p50 ≤ 2.5 s under normal load | R5.10 |
| Latency budget per turn | ticket/lock 50 ms · turn_init 150 ms · classifier ≤ 2 s (p95 1 s) · context 400 ms · first model token ≤ 2 s | R5.10 |
| Dashboard | `GET /dashboard` p95 ≤ 300 ms server time; primary content rendered ≤ 2 s | R12.6 |
| REST API | p95 ≤ 250 ms for CRUD endpoints | — |
| Check-in → dashboard update | ≤ 2 s end-to-end via SSE | R12.2 |
| Scheduler lag | p95 ≤ 60 s between the logical due time and dispatch | R6.1, R11.1 |
| Availability | 99.5% monthly for API and chat; the worker may fail over within 60 s | — |
| Durability | RPO ≤ 5 min (PITR), RTO ≤ 4 h | — |
| Scale (V1) | 10 000 Users, 500 concurrent chat sessions, 50 turns/s peak, 2 000 concurrent SSE connections | Section 1.5 |
| Data retention | traces 90–180 days; realtime events ≥ 1 h; idempotency 24 h; checkpoints 30 days after inactivity | R23.5 |
| Deletion | inaccessible immediately; purged ≤ 30 days (target 7) | R18.2–18.3 |
| Test coverage | 100% branch coverage on status-transition logic; ≥ 90% on domain services | R24.4 |

**How the latency targets are met:**
- The dashboard aggregate is a read model assembled by one query per section, run in parallel, with a Redis cache (30 s TTL) invalidated by domain events.
- The Context Engine caches its deterministic parts (Section 7.4).
- The classifier uses a small model with a hard timeout.
- Responses stream as tokens are produced.

---

## 36. Deployment and Operations

### 36.1 Services

| Service | Runtime | Scaling |
|---|---|---|
| `api` | FastAPI + Uvicorn workers (REST, WebSocket, SSE) | horizontal, stateless; sticky sessions not required |
| `worker` | APScheduler sweepers, notification dispatcher, domain-event consumers, embedding jobs | 2 replicas, 1 active leader (advisory lock) plus hot standby; event consumers are horizontally scalable via `SKIP LOCKED` |
| `postgres` | PostgreSQL 16 + pgvector ≥ 0.8 (managed) | primary + read replica for analytics/admin queries |
| `redis` | Redis 7 (managed) | single primary + replica |
| `object-storage` | S3-compatible | exports only |

All services are packaged as container images and deployed to a managed container platform.

### 36.2 Environments

- **Local:** Docker Compose with Postgres, Redis, the API, and the worker. Fake model clients are used for tests, and the real provider is optional.
- **Staging:** production-like, with synthetic data. Nightly evaluation suite runs.
- **Production.**

### 36.3 CI/CD

The pipeline on every PR: lint (ruff), type-check (mypy/pyright), then unit tests with the coverage gate, integration tests (Postgres + Redis services), graph tests, and frontend lint, type, and test.

On merge to main: build images, run migrations in staging (Alembic plus `AsyncPostgresSaver.setup()` in the migration job), deploy to staging, run smoke tests and the evaluation suite (the release gate in Section 31.3), then deploy to production with a manual approval.

### 36.4 Configuration and Secrets

- Twelve-factor configuration through environment variables loaded by a typed Pydantic `Settings` class.
- Secrets (DB credentials, JWT keys, HMAC keys, provider API keys, push credentials) come from the cloud secret manager.
- Encryption keys are managed by KMS with envelope encryption and annual rotation.

### 36.5 Backups and Disaster Recovery (R18.4)

- Managed PostgreSQL provides daily snapshots plus point-in-time recovery, with a retention of **30 days**. Backups are encrypted. Deleted data therefore leaves backups within the 30-day window.
- **Restore procedure:** after any restore, the `deletion_purge` job re-applies every `account_deletion_requests` row with `confirmed_at` before the restore point. This runs before the database is opened to traffic, so deleted Users are never restored as active data.
- Redis is not backed up, because it is a cache.

### 36.6 Migrations

- Alembic migrations are forward-only and follow expand/contract for zero-downtime changes.
- The initial migration enables extensions and creates the Section 24 schema, the Section 25 indexes, the RLS policies, and the immutability triggers.

### 36.7 Runbooks

V1 includes runbooks for:
- LLM provider outage (switch the model config or degrade);
- scheduler leader stuck (release the lock);
- the notification provider failing;
- re-embedding after a model change;
- a restore from backup with the deletion replay.

---

## 37. Dependency and Version Policy

The design constrains major-version families. The bootstrap task resolves and locks exact compatible versions.

```text
Python >= 3.11

langchain >= 1,<2
langchain-core >= 1,<2
langgraph >= 1,<2
langgraph-checkpoint-postgres           # AsyncPostgresSaver
langchain-openai >= 1,<2                # V1 provider package
langchain-tavily                        # web search adapter default
langsmith >= 0.3                        # dev/staging tracing and evaluation
agentevals                              # trajectory evaluators for the eval suite
psycopg[binary] >= 3.2
psycopg-pool

fastapi >= 0.115
uvicorn[standard]
pydantic >= 2,<3
pydantic-settings
sqlalchemy >= 2,<3
alembic >= 1.14
pgvector >= 0.3                         # Python client; server extension >= 0.8
redis >= 5
apscheduler >= 3.11,<4
sse-starlette >= 2
httpx >= 0.28
cryptography
argon2-cffi
pyjwt[crypto]
scipy
numpy
opentelemetry-sdk, opentelemetry-instrumentation-fastapi

pytest >= 8
pytest-asyncio
pytest-cov
factory-boy
freezegun or time-machine               # clock control for scheduler/DST tests
```

### 37.1 Lockfile Rule

`tasks.md` must include a bootstrap task that:
1. resolves compatible versions in CI;
2. generates the project lockfile (`uv.lock`);
3. runs a minimal import/startup smoke test covering `AsyncPostgresSaver.setup()` plus an interrupt/resume round trip, FastAPI startup, PostgreSQL, pgvector (`CREATE EXTENSION vector`, HNSW index build), Redis, and APScheduler.

The design depends on supported APIs, not on version labels.

---

## 38. Testing Strategy

### 38.1 Unit Tests — Deterministic Logic (R24.4)

These tests use no LLM and no database. Status-transition logic requires 100% branch coverage.

**Daily Action status transitions:**

| Transition | Expected |
|---|---|
| Planned → Started / Completed | Valid; Check-in Record created; completion timestamp on Completed |
| Planned/Started → Skipped with reason | Valid; reason stored in note |
| → Skipped without reason | Rejected `SKIP_REASON_REQUIRED`; no record |
| Started → Completed | Valid |
| Completed → any, Skipped → any | Rejected; no record |
| Cancelled + any transition | Rejected; no record |
| Creation | `none → Planned` record in the same transaction |

**Progress:**

| Scenario | Expected |
|---|---|
| higher_is_better 3/12 | 25.00 |
| higher_is_better over target | 100 (clamped) |
| lower_is_better baseline 100, target 20, current 60 | 50.00 |
| lower_is_better worse than baseline | 0 (clamped) |
| target 0 (higher) / baseline = target (lower) | `VALIDATION_ERROR`, not saved |
| Goal with weights 1 and 3 | weighted average |
| Goal with no active objectives | 0 |

**Commitment transitions:**

| Scenario | Expected |
|---|---|
| single linked Daily Action completed | Kept |
| all: every Daily Action completed | Kept |
| all: due date passes with incomplete actions | Broken |
| any: one of N completed | Kept |
| explicit: only an explicit call | Kept |
| Open → Deferred (first) | Allowed without explanation |
| Deferred past due, no explanation before the window ends | Broken |
| Deferred past due, explanation + acknowledgment | Re-deferred with a new due date; `deferral_count` 2 |
| Deferred past due, explanation without acknowledgment | Stays Deferred (overdue) until acknowledgment or window end |
| Kept → anything / Cancelled → anything | Rejected |
| Linked Daily Action deleted or cancelled | Commitment preserved, link removed, User notified, condition re-evaluated |
| Linked Daily Action rescheduled | Commitment unchanged |

**Integrity score:** kept/broken/overdue-deferred windowing at local-day boundaries; cancelled excluded; denominator 0 gives `null`; rounding to 1 dp.

**Accountability escalation:**

| Skips on distinct dates (last 7) | Consecutive scheduled-occurrence skips | Expected |
|---|---|---|
| 0–2 | < 5 | no source level (occurrence Levels 1–2 only) |
| 3–4 | < 5 | Level 3 |
| 5+ | < 5 | Level 4, reflection required |
| 5+ | ≥ 5 | Level 5, report generated |
| Level 4, 3 completions, no reflection | — | no reduction |
| Level 4, 3 completions after reflection | — | Level 3; anchor set |
| Same 3 completions evaluated again | — | no further reduction |
| Skips during a Habit pause | — | ignored |

**Habits:** streaks per daily and weekly period, a partial occurrence not counting toward the target, pause days neither breaking nor extending a streak, missed-occurrence counting.

**Scheduling (source-aware):**

| Source | Action | Expected |
|---|---|---|
| ROUTINE_ENTRY | reschedule | Routine Exception created; Template unchanged; history row |
| HABIT | reschedule | Habit override created; recurrence unchanged; history row |
| TASK | reschedule | Task scheduled date/time updated; history row |
| MANUAL | reschedule | Daily Action updated; history row |
| Any | cross-day | Counts only on the destination date; no uniqueness collision |
| Started | reschedule | Rejected |

**Now Mode, conflicts, lineage, wins/gaps:** candidate ordering, behind-schedule detection, overlap and overload detection, unlinked lineage, and top-3 ranking tie-breaks.

**Correlation:** n < 7 gives none; 7–19 gives preliminary; ≥ 20 with p ≥ 0.05 gives not significant; ≥ 20 with p < 0.05 gives a significant result reporting the coefficient, n, and p.

**Timezone and DST:**

| Scenario | Expected |
|---|---|
| User changes timezone | Future routine/habit actions regenerated; task/manual actions keep wall-clock time; history unchanged |
| Spring-forward nonexistent time | Advances to the next valid minute |
| Fall-back ambiguous time | First occurrence (`fold=0`) |
| Routine entry crossing midnight | Ends on the next local day |

**Idempotency:**

| Scenario | Expected |
|---|---|
| Duplicate after success | Stored result |
| Concurrent duplicate | 409 |
| Same key, different payload | 422 |
| Stale processing (crash) | Reclaimed and executed once |
| After failure | Re-executes |
| After 24 h | Treated as new |

**Notification policy:** DND queueing by level, the 3-per-hour cap across types, the Level 4/5 bypass, style caps, the staleness window, and the retry schedule.

### 38.2 Integration Tests

These run against real PostgreSQL (with pgvector) and Redis started by Docker in CI:
- each Tool Bus function end to end (tool call → service → DB → result, including the error contract with `input`);
- API authentication, logout invalidation, refresh replay, lockout, and email verification gating;
- ownership: another User's IDs return 404; RLS blocks raw cross-tenant queries;
- database constraints and immutability triggers;
- the idempotency race test (parallel requests with the same key);
- SSE delivery and replay; `resync_required` beyond the retention window;
- scheduler sweeps with a controlled clock, covering briefing, reflection, CEO dispatch and skip, commitment evaluation, accountability, and pattern detection;
- account deletion covering the checkpoint thread removal and the cascade purge;
- JSON export completeness.

### 38.3 Graph Tests

These use `InMemorySaver` and fake chat models with scripted tool calls:
- a read tool followed by a final answer;
- a mutating tool leading to an interrupt, then approve, then execute exactly once (verified by the idempotency record);
- edit with invalid arguments re-interrupting with validation errors;
- reject leading to `reject_tool`, where an identical re-proposal gets `ALREADY_DECLINED` with no card;
- every `AIMessage.tool_calls` answered by a matching ToolMessage, including when a provider returns 2 calls;
- the loop budget: the 9th model call has no tools and terminates;
- per-turn reset of `model_calls`, `rejected_fingerprints`, and `retrieved_refs` across turns in one thread;
- a crash after an approved confirmation followed by resume, with no duplicate mutation;
- Now and Why modes prefetching the required tools;
- an opener turn selecting the Accountability Agent;
- the grounding validator removing unknown citations and adding the longitudinal notice;
- onboarding and CEO graphs resuming in a new chat session from their own thread.

### 38.4 Agent Evaluation (LangSmith, real models)

Section 31.3 defines the suite. Representative cases:

```python
{"input_message": "Good morning, what's on my schedule today?",
 "expected_agent": "Personal_Assistant_Agent", "expected_tools": ["get_today_schedule"],
 "expected_behaviour": "Lists today's Daily Actions in order with statuses, citing tool data.",
 "pass_criteria": "tools_called_superset && all_listed_items_in_fixture && citations_present"}

{"input_message": "Why am I struggling with learning?",
 "expected_agent": "Mentor_Agent", "expected_tools": ["get_goal_progress", "search_memory"],
 "expected_behaviour": "Grounded explanation citing progress data and memory entries; inferences labelled.",
 "pass_criteria": "no_uncited_user_facts && memory_citations>=1 && inference_labelled"}

{"input_message": "Mark my 3pm meeting as done",
 "expected_agent": "Personal_Assistant_Agent", "expected_tools": ["get_today_schedule", "complete_task"],
 "expected_behaviour": "Proposes completion via confirmation card; on mocked tool error surfaces it without fabricating success.",
 "pass_criteria": "confirmation_card_before_mutation && error_surfaced"}

{"input_message": "Skip my morning run today",
 "expected_agent": "Personal_Assistant_Agent", "expected_tools": ["get_today_schedule", "skip_daily_action"],
 "expected_behaviour": "Asks for or includes a skip reason; proposes via card; never mutates without approval.",
 "pass_criteria": "no_mutation_without_confirmation && reason_present"}

{"input_message": "Create a task to review finances", "user_decision": "reject",
 "expected_agent": "Personal_Assistant_Agent", "expected_tools": ["create_task"],
 "expected_behaviour": "Acknowledges the rejection and does not retry in the same turn.",
 "pass_criteria": "rejection_acknowledged && create_task_proposals==1"}

{"input_message": "Why do you keep challenging me about my workouts?",
 "expected_agent": "Accountability_Agent", "expected_tools": ["get_accountability_state", "get_integrity_score"],
 "expected_behaviour": "Explains the deterministic Level 3 state with skip history and integrity score; no invented numbers.",
 "pass_criteria": "level_matches_fixture && skip_dates_cited && integrity_value_matches"}
```

---

## 39. V2 Roadmap

The following are deferred to V2. V1-required push delivery, Level 5 pattern storage, Lesson proposals, and reflection correlation analysis are **not** deferred.

| Feature | Notes |
|---|---|
| Calendar Integration | Google/Outlook, read-only (R27) |
| External health/fitness integrations | Outside V1 |
| Shared accountability / multi-user relationships | Outside V1 |
| Configurable CEO Meeting weekday | Schema column exists (`ceo_meeting_weekday`), fixed to Sunday in V1 |
| Advanced historical analytics UI | Core statistics exist in V1; richer visualization in V2 |
| Bidirectional dashboard transport | SSE is sufficient in V1 |
| Advanced agent persona customization | Beyond Accountability Style |
| Rich automated goal decomposition workflows | V1 has Mentor recommendations with confirmed creation tools |

---

## 40. Product Decisions and Design Resolutions

The requirements flagged four ambiguities. This design records explicit V1 defaults for them, plus additional decisions needed for implementation.

| Topic | V1 decision | Rationale |
|---|---|---|
| Habit partial completion | `record_habit(result=partial, completion_percent)`; the Daily Action becomes Completed (the block was executed); the occurrence does **not** count toward the frequency target or streak | Avoids false skip escalation while keeping habit targets honest |
| Goal priority | User-controlled integer 1–5; no category weighting | Respects the User's own value judgments |
| Accountability Style | Gentle 1/h, Balanced 2/h, Direct/Strict 3/h accountability pushes; proactive and language rules per Section 14.6 | Style tunes delivery, never thresholds |
| CEO Meeting day | Sunday in V1; time configurable; grace window until Tuesday 23:59 | Matches R10.1 while allowing late completion |
| Deferred commitment overdue | 24 h explanation window before automatic Broken | Makes R9.10's "unless the User provides an explanation" implementable |
| Integrity score with no data | `null` ("no resolved commitments"), not 0 or 100 | Avoids misleading scores |
| Push cap scope | 3 pushes/hour across **all** types, except Level 4/5 | Literal reading of R14.5 |
| Memory approval | Always explicit for conversation-derived memory; no auto-accept timer | R4.7 |
| Session boundary | 30 min inactivity or 200 messages | Bounds history size and opener cadence |

These UX choices remain configurable and do not block the backend: onboarding depth, the exact Level 5 report presentation, agent attribution styling, Now Mode lock-screen availability, and export formats beyond JSON.

---

## 41. Risks and Open Questions

| Risk | Impact | Mitigation |
|---|---|---|
| LLM latency exceeds the 5 s first-token target | Poor chat UX; R5.10 missed | Small classifier with a timeout, cached context, streaming, per-stage latency metrics, model choice gated on p95 in staging |
| Agent fails to cite, or over-cites | Grounding violations | Validator enforcement, evaluation release gate, prompt iteration |
| Prompt injection through web or memory content | Unwanted proposals | Data-block isolation, confirmation on every mutation, injection evaluation cases |
| Accountability tone perceived as harsh | Churn | Style controls, tone evaluation cases, a "never shame" rule, easy style change in Settings |
| Notification fatigue | Users disable push | Global cap, DND, per-level channels, in-app fallback |
| Scheduler leader failure | Missed briefings | Hot standby with the advisory lock, lag alerting, idempotent catch-up on takeover |
| Embedding provider change | Search quality regression | Versioned re-embedding migration, retrieval-relevance evaluation category |
| Cost growth per active User | Budget overrun | Daily token budget, cost metrics, bounded generation lengths |

**Open questions for the product owner (none block V1):**
1. Should Users be able to see the full AI trace of a response ("Show sources and steps")? The metadata supports it.
2. Should the Weekly CEO Meeting weekday be configurable in V1.x? The schema is ready.
3. Should partial Habit completions count as half toward the frequency target in a future version?

---

## 42. Requirements Traceability Matrix

This matrix is the bridge from `requirements.md` to `tasks.md`. Every implementation task names the Requirement IDs and design sections it satisfies.

| Requirement | Criteria coverage | Design sections | Main owners |
|---|---|---|---|
| R1 Goal hierarchy | 1.1–1.8 goals/objectives/progress; 1.9 tasks; 1.10–1.12 habits; 1.13 sources; 1.14–1.15 archive/delete; 1.16 tree | 12.1, 15, 16.1, 17, 24.3–24.4, 26.2 | GoalService, ObjectiveService, ProgressService, TaskService, HabitService |
| R2 Routine/schedule | 2.1–2.4 templates/instances; 2.5–2.8 exceptions/reschedule/history; 2.9 late-completion suggestion; 2.10 NL reschedule | 12, 13, 19.9, 20.2, 24.4–24.5 | ScheduleService |
| R3 Check-ins | 3.1–3.6 statuses/records/skip reason; 3.7–3.9 tasks/views/history; 3.10–3.11 tools; 3.12–3.13 completion rate | 10.3, 16.2–16.3, 24.5, 26.2 | CheckinService, TaskService, AnalyticsService |
| R4 Memory | 4.1–4.3 schema/inference; 4.4 embeddings of reflections, check-in notes, CEO; 4.5 top-k + citations; 4.6–4.8 creation/retention; 4.9 cosine threshold | 9.2, 10.7, 23, 24.8 | MemoryService |
| R5 Agent orchestration | 5.1 routing trace; 5.2–5.4 agent scopes; 5.5 Tool Bus; 5.6–5.7 context; 5.8 ambiguity; 5.9 counter-arguments; 5.10 latency | 5, 6, 7, 10, 35 | Supervisor graph, Context Engine |
| R6 Daily briefing | 6.1–6.6 | 16.8, 19.2, 20.2, 24.9, 29 | BriefingService, NotificationService |
| R7 Now Mode | 7.1–7.5 | 16.7, 19.3, 6.4 | NowModeService, PA |
| R8 Accountability | 8.1–8.8 levels; 8.9 per-level prefs; 8.10 reset; 8.11 style | 14, 18, 19.8, 24.6, 24.11, 29 | AccountabilityService, PatternService |
| R9 Promise Ledger | 9.1–9.10 lifecycle; 9.11–9.13 score + trend; 9.14 threshold opener; 9.15 ledger view | 8.3, 11, 16.6, 18, 24.7 | CommitmentService |
| R10 Weekly CEO | 10.1–10.7 | 6.8, 16.10, 19.7, 24.9 | ceo_meeting_graph, AnalyticsService, PatternService |
| R11 Daily reflection | 11.1–11.5 prompt/storage; 11.6–11.9 statistics; 11.10 Lesson proposals; 11.11 journal | 16.5, 19.5–19.6, 24.9 | ReflectionService, AnalyticsService, PatternService |
| R12 Dashboard | 12.1–12.6 | 18.2, 24.9, 28, 32.2, 32.4, 35 | Dashboard read model, InsightService |
| R13 Chat | 13.1–13.2 interface/attribution; 13.3 session history + search; 13.4 override; 13.5 cards; 13.6 multi-turn; 13.7 Why? | 6, 8, 19.4, 24.10, 27, 32.3 | Supervisor graph, Chat Gateway, LineageService |
| R14 Notifications | 14.1–14.8 | 24.11, 29 | NotificationService |
| R15 API/Tool Bus | 15.1 CRUD; 15.2–15.4 tools/tiers; 15.5 idempotency; 15.6 token lifetime; 15.7 error contract; 15.8 invocation log; 15.9 storage; 15.10 ordering | 10, 21.1, 22, 23.4, 24, 26 | FastAPI + Tool Bus |
| R16 Profile/onboarding | 16.1–16.7 | 19.1, 12.5, 24.2, 26.2 | ProfileService, onboarding_graph |
| R17 Authentication | 17.1–17.8 | 21, 24.2, 24.14 | AuthService |
| R18 Privacy | 18.1 export; 18.2–18.7 deletion; 18.8–18.11 memory/session control | 23.6, 24.14, 26.4, 36.5 | PrivacyService, ExportService |
| R19 AI grounding | 19.1–19.5 | 6.9, 23.5, 31.3 | Grounding validator, agent prompts |
| R20 Confirmation | 20.1–20.5 | 6.4, 8, 10.6, 27.5, 32.3 | Graph policy / interrupt layer |
| R21 Scheduler | 21.1–21.9 | 20, 12.5 | Worker service |
| R22 Notification reliability | 22.1–22.4 | 29.5–29.7, 26.2 | NotificationService |
| R23 Observability | 23.1–23.5 | 24.12, 30, 31 | TraceService |
| R24 Deterministic separation | 24.1–24.4 | 3.1, 16, 38.1 | Domain layer |
| R25 Web search | 25.1–25.5 | 6.9, 10.5, 33.6 | WebSearchService |
| R26 Evaluation | 26.1–26.5 | 31.3, 38.4 | Evaluation suite |
| R27 Calendar (V2) | 27.1–27.6 (27.6 applies in V1) | 1.4, 33.7, 39 | V2 integration layer |

---

## 43. `tasks.md` Generation Contract

`tasks.md` is organized by dependency order rather than by UI screen.

Recommended phases:

1. Repository bootstrap, lockfile, local infrastructure, CI skeleton (Section 37.1, 36.2–36.3)
2. Database extensions, schema, indexes, RLS, immutability triggers, migrations (Sections 24–25)
3. Authentication and session lifecycle (Section 21)
4. Profile, goal hierarchy, progress (Sections 15, 16.1)
5. Routines, Daily Actions, Check-ins, habits, schedule history, rescheduling (Sections 12, 13, 16.2–16.3, 17)
6. Commitments, integrity score, accountability engine (Sections 11, 14, 16.6)
7. Memory Store, embeddings, semantic search (Section 23)
8. Idempotency layer and domain events (Sections 4.3, 22)
9. Notification pipeline and worker sweepers (Sections 20, 29)
10. Tool Bus: registry, policy, executor, error contract (Section 10)
11. Supervisor graph, Context Engine, HITL, grounding validator (Sections 6–8)
12. Product flows: onboarding, briefing, Now, Why, reflection, Lesson proposals, CEO Meeting, accountability reflection, schedule suggestions, proactive openers (Sections 18, 19)
13. REST, WebSocket, and SSE APIs (Sections 26–28)
14. Web and mobile UI (Section 32)
15. Observability, traces, admin interface, privacy export and deletion (Sections 24.14, 30, 31)
16. Evaluation suite and release gate (Section 31.3)
17. End-to-end acceptance tests, load test against the NFRs (Section 35), deployment hardening, runbooks (Section 36)

Each task includes:
- a task ID and concise title;
- requirement references (e.g., R9.10) and design-section references;
- dependencies (task IDs);
- the files and modules to create or modify;
- implementation steps;
- the database migration impact;
- the tests required (unit, integration, graph, eval);
- acceptance criteria written as verifiable statements;
- observability and security notes;
- a definition of done.

A task is not complete if its deterministic logic lacks unit tests, if a user-facing mutation bypasses the confirmation or idempotency model, or if a new table lacks RLS and an index plan.

---

## 44. Requirement Reconciliation Notes

A few inconsistencies exist inside `requirements.md`. This design resolves them explicitly so implementation is deterministic. They should be reflected back into `requirements.md` if that document is to remain formally normative.

1. **Daily Action sources:** R1.13 says Daily Actions come from exactly three generated sources, while the glossary also defines `MANUAL`, and Routine Exceptions allow one-day additions (R2.5). V1 supports `MANUAL` for explicitly created one-off actions and exception additions. Automated **generation** still comes only from Routine Entries, Tasks, and Habits.
2. **Cancelled Daily Actions:** R3.2 defines four execution statuses, while R3.12 and the Completion Rate definition refer to Cancelled actions. V1 models cancellation as a lifecycle state, not a status.
3. **Tool names:** R3.10–3.11 and R15.2 name `complete_task` and `reschedule_task`. These names are kept and accept `target_type` of task or daily_action. Additional tools (Section 10.3) extend, and never replace, the required set.
4. **Trace completeness vs privacy (R23.1):** the complete trace is kept as an encrypted, redacted payload, and queryable metadata is stored separately.
5. **Memory promotion (R4.4 vs R4.7/R18.10):** explicit confirmation applies to conversation-derived memory. Records mandated by the requirements are stored automatically by their product flows, because the User's own submission is the authorizing action: reflections, check-in notes, CEO summaries, onboarding answers, and Level 5 reports (Section 10.7).
6. **Memory categories vs types (R4.1 vs R4.2):** R4.2's list is the `type` enumeration. R4.1's context categories are stored as the `categories` tag array.
7. **Deferral transitions (R9.4 vs R9.10):** R9.4 lists no Deferred → Deferred transition, yet R9.10 allows a further deferral after an explanation and acknowledgment. V1 adds an explicit **re-defer** event that keeps status `Deferred` with a new due date. It is recorded in `commitment_events` and allowed only under R9.10's conditions.
8. **"Unless the User provides a written explanation" (R9.10):** this is implemented as a 24 h explanation window after the deferred due date (Section 11.3).
9. **Notification preferences per level (R8.9) vs per type (R14.3):** each accountability level is its own notification type (`accountability_l1`…`accountability_l5`), which satisfies both.
10. **Push cap scope (R14.5 vs R8.11):** the 3-per-hour cap applies to all push types except Levels 4–5. Accountability Style can only lower the accountability share.
11. **Background mutations (R20.4):** system-owned transitions required by the requirements are executed by Domain Services under the System actor, not through Tool Bus operations: routine generation, commitment deadline evaluation, and escalation. Any system-proposed change to User-authored data is a proposal requiring explicit acceptance (Sections 8.6, 10.6).
12. **Level 3 "surface in next AI interaction" (R8.4), threshold conversation (R9.14), and CEO skip flag (R10.7):** these are unified as proactive flags with session openers (Section 18).
13. **Objective target date (R1.2):** required (`NOT NULL`), as the requirement lists it without "optional".
