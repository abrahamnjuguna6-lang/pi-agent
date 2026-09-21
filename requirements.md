# Requirements Document

## Introduction

Personal Life OS is an AI-powered personal operating system that helps users close the gap between who they want to become and what they actually do each day. It operates across the full personal productivity loop: Goals → Plans → Daily Actions → Execution → Evidence → Reflection → Adaptation.

The system exposes three AI personalities — a Personal Assistant, a Mentor, and an Accountability Partner — orchestrated by a LangGraph supervisor. A persistent personal memory layer enables longitudinal analysis, so the AI can answer questions like "Why am I not progressing?" with real historical evidence rather than generic advice.

The MVP targets: user profile and onboarding, goal management, daily routine scheduling, task tracking, check-ins, AI assistant chat, accountability engine, daily reflection, weekly review, authentication, privacy controls, background scheduling, and agent observability.

**Tech Stack:** FastAPI backend, PostgreSQL with pgvector, LangGraph/LangChain agent orchestration, Web/Mobile UI.

---

## Glossary

- **System**: The Personal Life OS application as a whole.
- **User**: The authenticated person using the System.
- **Goal**: A high-level aspiration at the Life, Career, Personal, Spiritual, Fitness, or Family level.
- **Objective**: A time-bounded, measurable milestone beneath a Goal. Each Objective is defined by a metric direction (higher_is_better or lower_is_better), a current value, a target value, an optional baseline value (required for lower_is_better objectives), and a unit. Progress is calculated as a clamped percentage (0–100%).
- **Objective Weight**: A positive numeric weight assigned to an Objective for the purpose of Goal progress aggregation. Defaults to 1 if not explicitly set.
- **Project**: A collection of related Tasks that contribute to an Objective.
- **Task**: A discrete unit of work belonging to a Project or created ad-hoc. A Task is the work to be done; it is not itself a scheduled occurrence. Scheduling a Task on a specific date and time produces a Daily Action.
- **Habit**: A recurring behaviour the User intends to perform according to a defined frequency or schedule. A Habit is NOT automatically a Commitment. A User may optionally create a Commitment associated with a Habit, but the mere existence of a Habit does not create a Promise Ledger record.
- **Daily Action**: A concrete, scheduled occurrence on the User's daily plan. Daily Actions are generated from three sources: Routine Templates (via Routine Instance instantiation), Tasks (when explicitly scheduled by the User), and Habits (via their recurrence schedule). Each Daily Action carries a stable source reference identifying its origin.
- **Daily Action Source**: A typed reference that identifies the origin of a Daily Action. Source type is one of: HABIT (with a stable Habit ID), ROUTINE_ENTRY (with a stable Routine Entry ID), TASK (with a stable Task ID), or MANUAL (no upstream source; the Daily Action is its own origin).
- **Check-in Record**: An immutable event created whenever a Daily Action undergoes a valid status transition. A Check-in Record contains: the Daily Action reference, the previous status, the new status, a UTC timestamp, the transition source (one of: User, Agent, System), and an optional note or reason. Invalid or rejected transitions SHALL NOT produce a Check-in Record.
- **Routine Template**: The reusable definition of an ordered sequence of named time-block entries that repeat on a defined weekly schedule. Each entry has a stable Routine Entry ID.
- **Routine Instance**: The instantiation of a Routine Template for a specific calendar date, producing a set of Daily Actions for that day.
- **Routine Exception**: A per-date override of one or more Daily Actions within a Routine Instance. Created when the User modifies a specific day's schedule without altering the underlying Routine Template.
- **Commitment**: An explicit, User-acknowledged intention to achieve a specific outcome by a specific date, recorded in the Promise Ledger. Commitments are created only through explicit User action — via a chat statement, a UI commitment action, or acceptance of an AI-proposed Commitment. Routine entries, Tasks, and Habits do NOT automatically create Commitments. A Commitment may optionally reference one or more associated Daily Actions, a Goal, Objective, Project, or Task, but these relationships must be explicitly created.
- **Commitment Completion Condition**: For a Commitment linked to exactly one Daily Action, it becomes Kept when that Daily Action reaches Completed status. For a Commitment linked to multiple Daily Actions, the completion condition (ALL linked actions completed, or ANY one sufficient) must be explicitly set at the time the Commitment is created. A Commitment with no linked Daily Actions becomes Kept only through explicit User action.
- **Check-in**: See Check-in Record above. The term "Check-in" throughout this document refers to the immutable historical record of a Daily Action status transition.
- **Reflection**: A structured written entry the User creates at the end of a day or week summarising progress, learnings, and intentions.
- **Promise Ledger**: The record of all Commitments the User has made to themselves, each annotated with source, dates, status, and follow-through outcome.
- **Personal Integrity Score**: A computed metric (0–100) representing the ratio of Kept Commitments to total resolved Commitments (Kept + Broken + overdue Deferred) over a rolling 30-day window. Cancelled Commitments are excluded.
- **Daily Briefing**: An AI-generated morning summary covering the day's internal schedule, active goals, a principle of the day, and any open accountability flags.
- **Now Mode**: A real-time query interaction that returns the single most important thing the User should be doing at this moment, based on the internal schedule, Goals, and current context, with AI reasoning.
- **Weekly CEO Meeting**: A structured Sunday review session, AI-facilitated, covering the previous week's performance across all goal categories.
- **Accountability Engine**: The sub-system that monitors completion gaps and escalates responses through five deterministic levels: Level 1 — Reminder, Level 2 — Nudge, Level 3 — Challenge, Level 4 — Reflection Prompt, Level 5 — Pattern Detection Report.
- **Accountability Style**: A User-configured preference controlling the Accountability Engine's challenge frequency, notification limits, and language intensity. One of: Gentle, Balanced, Direct, or Strict.
- **Escalation State**: The current escalation level (1–5) tracked per recurring Daily Action source identity. Escalation is deterministic and based on Check-in history against the source identity.
- **Internal Schedule**: The set of time-structured events owned and managed by the System: Routine Instances, Daily Actions, scheduled Tasks, Commitment deadlines, and internal reminders. This is distinct from any external calendar service. In V1, the System operates exclusively from the Internal Schedule.
- **Personal Assistant Agent**: The AI personality responsible for scheduling queries, reminders, Task creation, Task rescheduling, and real-time "What should I be doing now?" requests. In V1, this agent operates from the Internal Schedule only.
- **Mentor Agent**: The AI personality responsible for long-term growth guidance, skill recommendations, career pattern analysis, and longitudinal evidence-based guidance.
- **Accountability Agent**: The AI personality responsible for tracking follow-through, surfacing gaps, and challenging the User when patterns of avoidance emerge.
- **Supervisor**: The LangGraph orchestrator that routes User messages to the appropriate Agent based on intent classification.
- **Memory Store**: The persistent vector and relational storage layer holding all long-term User context. Distinct from conversation history, which is session-scoped and not automatically persisted.
- **Context Engine**: The shared layer between the Supervisor and the Tool Bus that constructs agent-appropriate context bundles for each interaction, drawing from the database and Memory Store without stuffing the full User history into every LLM call.
- **Tool Bus**: The shared set of callable tools available to all Agents. Every Tool Bus operation is classified as Read-only, Mutating, or Destructive/External.
- **Agent Trace**: A complete, structured log of a single AI interaction, capturing routing decisions, Tool Bus calls, Memory Store queries, LLM prompt/response, actions proposed, and User accept/reject outcome.
- **Life Dashboard**: The primary UI screen showing goal progress, today's Internal Schedule, the latest AI insight, and active accountability alerts.
- **User Timezone**: The IANA timezone identifier configured in the User's profile. All User-facing times, scheduled events, and reporting boundaries are interpreted in the User's Timezone. The server's local timezone does not determine User schedules.
- **Completion Rate**: The ratio of Completed Daily Actions to the total set of scheduled Daily Actions for a reporting period, where the total includes all Daily Actions with status Planned, Started, Skipped, or Completed at the close of the reporting period. Cancelled Daily Actions are excluded from both numerator and denominator. The reporting period boundary is defined using the User's Timezone.

---

## Requirements

### Requirement 1: Goal Hierarchy Management

**User Story:** As a User, I want to create and organise goals across Life, Career, Personal, Spiritual, Fitness, and Family categories with a clear Goal → Objective → Project → Task → Daily Action hierarchy, so that the System understands what I am ultimately trying to achieve and can track measurable progress.

#### Acceptance Criteria

1. THE System SHALL allow the User to create a Goal with a title, category (one of: Life, Career, Personal, Spiritual, Fitness, Family), description, and optional target date.

2. THE System SHALL allow the User to create Objectives beneath a Goal, each with:
   - title
   - metric direction (one of: higher_is_better or lower_is_better)
   - current value (numeric)
   - target value (numeric)
   - unit (e.g., books, days, hours, kg)
   - optional baseline value (required when metric direction is lower_is_better)
   - target date
   - optional weight (positive numeric; defaults to 1 if not provided)

3. THE System SHALL reject creation or update of an Objective where the measurable range would result in division by zero:
   - For higher_is_better: target value MUST NOT equal zero.
   - For lower_is_better: baseline value MUST NOT equal target value.
   If either condition is violated, THE System SHALL return a validation error and SHALL NOT save the Objective.

4. FOR ALL Objectives with metric direction higher_is_better, THE System SHALL compute Objective progress as:
   `progress = CLAMP((current_value / target_value) × 100, 0, 100)`
   where CLAMP enforces a minimum of 0% and a maximum of 100%.

5. FOR ALL Objectives with metric direction lower_is_better, THE System SHALL compute Objective progress as:
   `progress = CLAMP(((baseline_value − current_value) / (baseline_value − target_value)) × 100, 0, 100)`
   Example: baseline = 100, target = 20, current = 60 → progress = (40 / 80) × 100 = 50%.

6. FOR ALL Goals, THE System SHALL compute Goal progress as a weighted average of the progress percentages of all active Objectives beneath that Goal:
   `Goal Progress = Σ(Objective_Progress × Objective_Weight) / Σ(Objective_Weight)`
   The result SHALL be clamped to 0–100%. Only active Objectives SHALL contribute to this calculation. Archived or completed Objectives SHALL be excluded from active Goal progress but SHALL be preserved for historical reporting.

7. WHEN a Goal has no active Objectives, THE System SHALL display its progress as 0%.

8. THE System SHALL allow the User to create Projects beneath an Objective, each with a title and description.

9. THE System SHALL allow the User to create Tasks beneath a Project or ad-hoc, each with a title, optional due date, and optional Goal link. Scheduling a Task on a specific date and time SHALL produce a Daily Action for that date with source type TASK referencing the originating Task ID.

10. THE System SHALL allow the User to create Habits beneath a Goal or Project, each with:
    - title
    - recurrence schedule (daily, weekly, or custom days of the week)
    - optional duration in minutes
    - frequency target (e.g., 5 times per week)
    - optional start date
    - optional end date

11. FOR ALL Habits, THE System SHALL track: completion count per reporting period, current streak (consecutive periods meeting the frequency target), longest streak, missed occurrences, partial completions, and active pause periods.

12. WHEN a Habit is paused, THE System SHALL suspend streak tracking and SHALL exclude missed occurrences during the pause period from accountability and completion rate calculations.

13. THE System SHALL generate Daily Actions from exactly three sources: (a) Routine Template instantiation, producing Daily Actions with source type ROUTINE_ENTRY; (b) User-scheduled Tasks, producing Daily Actions with source type TASK; (c) Habit recurrence schedules, producing Daily Actions with source type HABIT. Each Daily Action SHALL carry the stable ID of its originating entity as its source reference.

14. WHEN the User marks a Goal as archived, THE System SHALL preserve all associated Objectives, Projects, Tasks, Habits, Daily Actions, and historical Check-in Records in read-only form.

15. IF the User attempts to delete a Goal that has associated active Objectives or Projects, THEN THE System SHALL require the User to confirm the cascading removal before proceeding.

16. THE System SHALL display the full hierarchy (Goal → Objective → Project → Task / Habit → Daily Action) in a tree view on demand.

---

### Requirement 2: Daily Routine and Schedule Management

**User Story:** As a User, I want to define and manage a daily routine with time-blocked slots, so that the System can track my intended schedule and detect deviations without corrupting my Routine Template when I make one-off changes.

#### Acceptance Criteria

1. THE System SHALL allow the User to create a Routine Template composed of ordered, named time-block entries, each with a start time, end time, title, a stable Routine Entry ID, and an optional link to a Goal or Habit.

2. THE System SHALL allow the User to assign a Routine Template to one or more days of the week.

3. THE System SHALL generate a Routine Instance each day — in the User's Timezone — by instantiating the applicable Routine Template entries as Daily Actions for that specific calendar date. Each resulting Daily Action SHALL carry source type ROUTINE_ENTRY and reference the originating Routine Entry ID.

4. WHEN a Routine Instance is generated, THE System SHALL assign each Daily Action the status "Planned" by default, creating an initial Check-in Record with previous status = none, new status = Planned, and source = System.

5. WHEN the User modifies a Daily Action in a specific Routine Instance (e.g., changes its time, removes it, or adds a new entry for that date only), THE System SHALL create a Routine Exception for that date rather than modifying the underlying Routine Template. Future Routine Instances SHALL continue to be generated from the unmodified Template.

6. THE System SHALL store Routine Exceptions linked to their source Routine Template ID and the specific calendar date.

7. THE System SHALL allow the User to reschedule a Daily Action to a different start time or date without altering the underlying Routine Template. Rescheduling SHALL create a Routine Exception for the affected date.

8. WHEN the User reschedules a Daily Action, THE System SHALL record the original planned time alongside the rescheduled time. A rescheduled Daily Action SHALL appear only once in the Completion Rate calculation for its new scheduled date; it SHALL NOT be double-counted against its original date.

9. THE Personal_Assistant_Agent SHALL suggest rescheduling unstarted Daily Actions when a preceding Daily Action is marked Completed later than its scheduled end time. The Agent SHALL propose the adjustment as a confirmation card; the Internal Schedule SHALL NOT be modified without User confirmation.

10. IF the User requests a reschedule via natural language (e.g., "Move my 4 PM learning session to 6 PM"), THEN THE Personal_Assistant_Agent SHALL parse the request, identify the matching Daily Action by its scheduled time and title, propose the updated schedule as a confirmation card, and apply it upon User confirmation.

---

### Requirement 3: Task, Daily Action Status, and Check-in History

**User Story:** As a User, I want to log check-ins against my Daily Actions and have the System maintain a complete, immutable history of every status change, so that analytics and accountability are based on authoritative records rather than just current state.

#### Acceptance Criteria

1. THE System SHALL maintain two separate concepts for every Daily Action: (a) the current status, which is the most recent status as of now; and (b) the Check-in history, which is the ordered, immutable sequence of Check-in Records recording every valid status transition.

2. THE valid statuses for a Daily Action are: Planned, Started, Completed, and Skipped. These statuses are mutually exclusive at any given moment.

3. THE valid status transitions are:
   - Planned → Started
   - Planned → Completed
   - Planned → Skipped
   - Started → Completed
   - Started → Skipped

   Any attempted transition not listed above SHALL be rejected by the System. A rejected transition SHALL NOT produce a Check-in Record.

4. EVERY valid status transition SHALL produce an immutable Check-in Record containing: Daily Action reference, previous status, new status, UTC timestamp, transition source (one of: User, Agent, System), and optional note or reason.

5. WHEN the User sets a Daily Action status to Completed, THE System SHALL record the completion timestamp in the Check-in Record. The User MAY optionally include a completion note.

6. WHEN the User sets a Daily Action status to Skipped, THE System SHALL prompt the User for a skip reason (free text, required). The skip reason SHALL be stored in the Check-in Record's note field.

7. THE System SHALL allow the User to create ad-hoc Tasks not derived from a Routine Template, each with a title, optional due date, and optional Goal link. Scheduling such a Task produces a Daily Action with source type TASK.

8. THE System SHALL display the User's Daily Actions for the current date in a unified view ordered by scheduled start time.

9. THE System SHALL allow the User to view the full Check-in history for any individual Daily Action.

10. THE System SHALL expose a `complete_task` tool on the Tool Bus that transitions a Daily Action or Task to Completed status, records the completion timestamp, and creates a Check-in Record with source = Agent.

11. THE System SHALL expose a `reschedule_task` tool on the Tool Bus that moves a Daily Action to a specified date and time and creates a Routine Exception where applicable.

12. FOR ALL days with at least one scheduled Daily Action, THE System SHALL compute the Completion Rate using Check-in history as the authoritative data source, as defined in the Completion Rate glossary entry: Completed / (Planned + Started + Skipped + Completed), using the User's Timezone to determine the reporting day boundary. Daily Actions with status Cancelled are excluded.

13. A Daily Action that has neither been updated nor reached its scheduled end time SHALL retain status Planned and SHALL be included in the denominator of the Completion Rate as Planned. A Daily Action that has passed its scheduled end time with no status update SHALL be treated as Planned (incomplete) for Completion Rate purposes; the Accountability Engine MAY escalate based on this state.

---

### Requirement 4: Personal Memory Store

**User Story:** As a User, I want the System to remember my goals, values, history, reflections, and preferences over time, so that the AI can give contextually accurate and longitudinally aware guidance.

#### Acceptance Criteria

1. THE Memory_Store SHALL persist the following long-term User context categories: Goals, Values, Principles, Responsibilities, Career, Projects, Habits, Routines, Preferences, Commitments, Achievements, Failures, Reflections, and Lessons.

2. FOR ALL Memory_Store entries, THE System SHALL record:
   - content (text)
   - type (one of: Fact, Preference, Value, Principle, Lesson, Achievement, Failure, Reflection, Commitment, Pattern)
   - source (one of: User-stated, AI-inferred, System-derived)
   - importance score (integer 1–10)
   - confidence level (decimal 0.0–1.0)
   - created_at timestamp (UTC)
   - updated_at timestamp (UTC)

3. WHEN a Memory_Store entry has source AI-inferred, THE System SHALL mark the entry with an explicit inference flag. THE Agent SHALL NOT present an AI-inferred entry to the User as an established fact, as required by Requirement 19.

4. THE System SHALL store all Reflections, Check-in notes, and Weekly CEO Meeting summaries as vector-embedded documents in the Memory_Store.

5. WHEN the Mentor_Agent or Accountability_Agent answers a longitudinal query (e.g., "Why am I not progressing?"), THE System SHALL retrieve the top-k semantically relevant Memory_Store entries and include them as context in the Agent's response. The Agent SHALL cite the specific entries used, consistent with Requirement 19.

6. THE System SHALL allow the User to explicitly add a Principle or Value entry to the Memory_Store via the UI or via natural language in the chat interface.

7. Conversation history is session-scoped and is NOT automatically persisted to the Memory_Store. A Memory_Store entry SHALL only be created from a conversation when: (a) an Agent proposes the entry and the User accepts via a confirmation card, or (b) the User explicitly requests storage. This is consistent with Requirement 18, criterion 5.

8. THE System SHALL retain all Memory_Store entries unless the User explicitly requests deletion with confirmation, as defined in Requirement 18.

9. FOR ALL vector similarity searches against the Memory_Store, THE System SHALL use cosine similarity with a configurable minimum threshold (default: 0.75).

---

### Requirement 5: AI Agent Orchestration

**User Story:** As a User, I want to interact with three distinct AI personalities — a Personal Assistant, a Mentor, and an Accountability Partner — so that I receive scheduling help, growth guidance, and honest challenge all within one interface.

#### Acceptance Criteria

1. THE Supervisor SHALL route each incoming User message to the most appropriate Agent (Personal_Assistant_Agent, Mentor_Agent, or Accountability_Agent) based on intent classification, and SHALL record the routing decision and confidence score in the Agent Trace.

2. THE Personal_Assistant_Agent SHALL handle scheduling queries, reminders, Task creation, Task rescheduling, and real-time "What should I be doing now?" requests. In V1, this agent SHALL use the Internal Schedule only; it SHALL NOT claim access to or knowledge of external calendar events unless Calendar Integration (Requirement 27) is enabled.

3. THE Mentor_Agent SHALL handle long-term growth queries, career guidance, skill gap analysis, pattern analysis, and goal decomposition recommendations.

4. THE Accountability_Agent SHALL handle Commitment tracking queries, skip-pattern detection, integrity score explanation, and direct challenge when the User exhibits avoidance patterns. All escalation-level determinations SHALL be produced by the deterministic Accountability Engine (Requirement 8 and Requirement 24), not by LLM reasoning.

5. WHEN an Agent requires User data, THE System SHALL invoke the appropriate Tool Bus function rather than relying on the Agent's parametric memory.

6. THE Context_Engine SHALL construct an agent-appropriate context bundle for each interaction:
   - The Accountability_Agent context SHALL include: recent Skipped Daily Actions (by source identity), Commitments, Personal Integrity Score, and detected Escalation States.
   - The Mentor_Agent context SHALL include: Goals, Objectives, Projects, learning history, Reflections, and achievements.
   - The Personal_Assistant_Agent context SHALL include: the current time in the User's Timezone, today's Internal Schedule (Daily Actions from the current Routine Instance and any Routine Exceptions), Tasks, and Commitment deadlines.

7. THE Supervisor SHALL pass the context bundle produced by the Context_Engine as system-level context to each Agent invocation.

8. IF the Supervisor cannot classify the intent with sufficient confidence, THEN THE Supervisor SHALL default to the Personal_Assistant_Agent and include the ambiguity in the response for User clarification.

9. THE Accountability_Agent SHALL be permitted to disagree with the User's stated preferences and surface evidence-based counter-arguments when the User's behaviour is inconsistent with stated Goals. All counter-arguments SHALL be grounded in Tool Bus or Memory_Store data retrieved within the current interaction (Requirement 19).

10. WHEN the User sends a message, THE System SHALL return the Agent's initial response within 5 seconds under normal load conditions.

---

### Requirement 6: Daily Briefing

**User Story:** As a User, I want to receive an AI-generated morning briefing each day, so that I start the day with a clear picture of my focus, schedule, and growth principle.

#### Acceptance Criteria

1. THE System SHALL generate a Daily Briefing each morning at a User-configured time (default: 05:00). All times are interpreted in the User's Timezone (Requirement 16, Requirement 21).

2. THE Daily_Briefing SHALL include: today's scheduled Daily Actions from the Internal Schedule in time order, the top active Goal per category, a principle of the day drawn from the User's Memory_Store, and any open accountability flags from the previous day.

3. THE System SHALL deliver the Daily Briefing as a push notification that, when opened, initiates an interactive AI conversation rather than displaying a static message.

4. WHEN the Daily Briefing is generated, THE Personal_Assistant_Agent SHALL surface any scheduling conflicts or overloaded time blocks within the Internal Schedule.

5. THE System SHALL allow the User to configure the Daily Briefing delivery time in the settings.

6. IF no Routine Template is defined for the current day, THEN THE System SHALL generate the Daily Briefing without a schedule section and include a prompt for the User to define one.

---

### Requirement 7: Now Mode

**User Story:** As a User, I want to ask "What should I be doing right now?" and receive an AI-reasoned answer based on my schedule, goals, and current context, so that I can immediately re-focus without having to think about it myself.

#### Acceptance Criteria

1. WHEN the User invokes Now Mode, THE Personal_Assistant_Agent SHALL query the Tool Bus for `get_today_schedule` and `get_active_goals`, then return a single recommended action with explicit reasoning. `get_today_schedule` returns the Internal Schedule for the current date.

2. THE Personal_Assistant_Agent SHALL consider the current time (in the User's Timezone), the current status of all Daily Actions for today (from Check-in history via the Tool Bus), and the priority ranking of active Goals when computing the Now Mode recommendation.

3. WHEN all scheduled Daily Actions for the current time block are Completed, THE Personal_Assistant_Agent SHALL recommend the next unstarted Daily Action or suggest a buffer activity aligned with an active Goal.

4. WHEN the User is behind schedule (current time is past a Daily Action's scheduled end time and the action is still Planned or Started), THE Personal_Assistant_Agent SHALL acknowledge the gap explicitly in the Now Mode response.

5. THE System SHALL make Now Mode accessible from the Life Dashboard with a single interaction (tap or click).

---

### Requirement 8: Accountability Engine

**User Story:** As a User, I want the System to monitor my completion follow-through and escalate its responses based on deterministic rules applied to recurring patterns, so that I am genuinely challenged rather than just gently reminded.

#### Acceptance Criteria

1. THE Accountability_Engine SHALL operate across five deterministic escalation levels. The Escalation State for a recurring Daily Action is tracked per stable source identity (Habit ID or Routine Entry ID), NOT by title matching. The LLM SHALL NOT determine the escalation level; the Accountability Engine SHALL compute it from Check-in history.

2. **Level 1 — Reminder.**
   WHEN a scheduled Daily Action reaches its scheduled start time with status Planned, THE Accountability_Engine SHALL issue a Level 1 Reminder notification for that Daily Action.

3. **Level 2 — Nudge.**
   WHEN a Daily Action has passed its scheduled end time and its current status is Planned or Started, THE Accountability_Engine SHALL escalate to Level 2 and issue a Nudge notification. Level 2 applies per-occurrence and does not require a multi-day pattern.

4. **Level 3 — Challenge.**
   WHEN the same source identity (Habit ID or Routine Entry ID) has a Check-in Record with status Skipped on 3 or more of the last 7 calendar days, THE Accountability_Engine SHALL set the Escalation State for that source identity to Level 3 and surface the pattern in the next AI interaction. Level 3 persists until the reset condition in criterion 10 is met.

5. **Level 4 — Reflection Prompt.**
   WHEN the same source identity that triggered Level 3 continues to accrue Skipped Check-in Records such that the skip count reaches 5 or more of the last 7 calendar days, THE Accountability_Engine SHALL escalate the Escalation State to Level 4. At Level 4, the System SHALL generate a structured Reflection Prompt requiring the User to record a written explanation before the Escalation State can be reduced.

6. **Level 5 — Pattern Detection Report.**
   WHEN the same source identity has been Skipped on 5 or more consecutive calendar days, THE Accountability_Engine SHALL escalate the Escalation State to Level 5 and generate a Pattern Detection Report. The Report SHALL be stored as an AI-inferred Memory_Store entry of type Pattern, as defined in Requirement 4, criterion 3.

7. THE Accountability_Agent SHALL include the relevant Skipped Check-in history and the current Personal Integrity Score in all Level 3 and above responses. These values SHALL be retrieved from the Tool Bus, not inferred by the LLM.

8. THE Accountability_Engine escalation levels are ordered; the Engine SHALL always apply the highest applicable level for the current state of a source identity.

9. THE System SHALL allow the User to configure notification delivery preferences (push, in-app, or both) for each Accountability_Engine level independently.

10. **Escalation reset.**
    THE Accountability_Engine SHALL reduce the Escalation State for a source identity by one level when the User completes a Daily Action from that source identity on 3 or more of any 7 consecutive calendar days within the current 7-day window. The Escalation State SHALL NOT be reduced to below Level 1. Escalation SHALL NOT be permanent.

11. THE System SHALL allow the User to configure an Accountability Style (one of: Gentle, Balanced, Direct, Strict). THE Accountability_Engine SHALL adjust challenge frequency, maximum notifications per hour, and the language intensity of AI-generated challenge content based on the configured Accountability Style.

---

### Requirement 9: Promise Ledger and Personal Integrity Score

**User Story:** As a User, I want the System to track my explicit Commitments and compute an honest integrity score, so that I have an objective measure of how well I follow through on what I say I will do.

#### Acceptance Criteria

1. THE System SHALL create a Commitment ONLY when the User explicitly acknowledges an intention to hold themselves accountable — via a chat statement, a UI commitment action, or acceptance of an AI-proposed Commitment. Routine entries, Tasks, and Habits SHALL NOT automatically create Commitments.

2. WHEN the User creates a Commitment, THE System SHALL present a confirmation step clearly identifying the Commitment's title, due date, optional linked entities (Goal, Objective, Project, Task, or Daily Actions), and the completion condition (if linked to multiple Daily Actions: ALL or ANY). The Commitment SHALL be recorded in the Promise_Ledger only after User confirmation.

3. THE Promise_Ledger SHALL record every Commitment with: source (User-stated or AI-recommended), date created, due date, optional linked entity references, completion condition, and status (one of: Open, Kept, Broken, Deferred, Cancelled).

4. THE System SHALL enforce the following Commitment status transitions only:
   - Open → Kept
   - Open → Broken
   - Open → Deferred
   - Open → Cancelled
   - Deferred → Kept
   - Deferred → Broken

   Any attempted transition not listed above SHALL be rejected.

5. **Completion rules for linked Commitments:**
   - A Commitment linked to exactly one Daily Action becomes Kept when that Daily Action reaches Completed status.
   - A Commitment linked to multiple Daily Actions with completion condition ALL becomes Kept when every linked Daily Action reaches Completed status.
   - A Commitment linked to multiple Daily Actions with completion condition ANY becomes Kept when at least one linked Daily Action reaches Completed status.
   - A Commitment with no linked Daily Actions becomes Kept only through an explicit User action in the UI or chat.

6. WHEN the completion condition for a Commitment is satisfied (per criterion 5), THE System SHALL automatically transition the Commitment to Kept status and record the timestamp.

7. WHEN a Commitment's due date passes without the completion condition being satisfied, THE System SHALL automatically transition the Commitment to Broken status and notify the User.

8. Rescheduling a linked Daily Action SHALL NOT change the Commitment's identity, due date, or linked entities unless the User explicitly modifies the Commitment.

9. Deleting a linked Daily Action SHALL NOT automatically delete the associated Commitment. THE System SHALL preserve the Commitment, remove the specific Daily Action reference from the linked entities list, and notify the User that the Commitment's completion condition has changed.

10. A Commitment MAY be deferred once by the User to a new due date. WHEN a Commitment in Deferred status passes its deferred due date without the completion condition being satisfied, THE System SHALL automatically transition it to Broken unless the User provides a written explanation; if a written explanation is provided, THE System SHALL require a second explicit acknowledgment before allowing a further deferral. Each deferral MUST establish a new explicit due date.

11. THE System SHALL include Deferred Commitments whose deferred due date has passed without completion in the denominator of the Personal Integrity Score calculation.

12. THE System SHALL compute the Personal Integrity Score as a deterministic calculation (not LLM inference):
    `Score = (Kept Commitments in last 30 days) / (Kept + Broken + overdue Deferred Commitments in last 30 days) × 100`
    rounded to one decimal place. Cancelled Commitments are excluded. This calculation uses the User's Timezone to determine the 30-day window boundary.

13. THE Life_Dashboard SHALL display the Personal Integrity Score prominently with a 30-day trend indicator.

14. WHEN the Personal Integrity Score drops below a User-configured threshold (default: 70), THE Accountability_Agent SHALL proactively initiate an accountability conversation at the next User session.

15. THE System SHALL expose the Promise_Ledger history to the User as a filterable list, sortable by date, status, and Goal category.

---

### Requirement 10: Weekly CEO Meeting

**User Story:** As a User, I want a structured weekly review session facilitated by the AI each Sunday, so that I can evaluate the week honestly, identify patterns, and set clear intentions for the following week.

#### Acceptance Criteria

1. THE System SHALL initiate the Weekly CEO Meeting session each Sunday at a User-configured time (default: 19:00), interpreted in the User's Timezone.

2. THE Mentor_Agent SHALL generate a pre-session briefing containing: Completion Rates by Goal category for the past 7 days (computed deterministically from Check-in history), Personal Integrity Score trend, top 3 wins, top 3 gaps, and a set of reflection questions.

3. WHEN the User opens the Weekly CEO Meeting session, THE System SHALL present the pre-session briefing before accepting free-text input.

4. THE Mentor_Agent SHALL ask at least the following structured questions during the session: (a) What went well this week? (b) What did you avoid and why? (c) What will you do differently next week? (d) Which Goal needs the most attention?

5. WHEN the User completes the Weekly CEO Meeting, THE System SHALL store the full session transcript and AI-generated summary as a Reflection entry in the Memory_Store.

6. THE System SHALL generate a next-week focus plan based on the User's answers and the current Goal priority rankings, presenting it for User confirmation before saving.

7. IF the User skips the Weekly CEO Meeting for 2 or more consecutive weeks, THEN THE Accountability_Agent SHALL surface this as a Pattern Detection flag at the start of the next User session.

---

### Requirement 11: Daily Reflection

**User Story:** As a User, I want to record a structured daily reflection each evening, so that I build a longitudinal record of my progress, learnings, and decisions.

#### Acceptance Criteria

1. THE System SHALL prompt the User for a Daily Reflection each evening at a User-configured time (default: 21:00), interpreted in the User's Timezone.

2. THE Daily_Reflection prompt SHALL include: the day's Completion Rate (computed deterministically from Check-in history), the Personal Integrity Score, a list of Skipped Daily Actions with their recorded reasons, and three structured questions: (a) What did you accomplish today? (b) What held you back? (c) What is one thing you will do tomorrow?

3. THE Daily_Reflection prompt SHALL include optional mood, energy, and stress capture as numeric scales (1–5). The User MAY skip these fields.

4. WHEN the User submits a Daily Reflection that includes mood, energy, or stress values, THE System SHALL tag the stored Reflection entry with those numeric values alongside the date and associated Goal categories.

5. WHEN the User submits a Daily Reflection, THE System SHALL store it as a vector-embedded entry in the Memory_Store tagged with the current date (in the User's Timezone) and associated Goal categories.

6. THE System SHALL only evaluate correlations between mood/energy values and Completion Rate when the minimum sample size of 20 observations is met (not 7). For observations between 7 and 19, the System MAY surface a preliminary observation labelled explicitly as preliminary and insufficient for statistical conclusions.

7. WHEN the minimum sample size of 20 observations is met, THE System SHALL compute the Spearman rank correlation coefficient between the self-reported energy score and the daily Completion Rate. THE System SHALL use Spearman rank correlation as the default method because it does not assume a normal distribution. If the System also computes Pearson correlation, it SHALL first verify that the normality assumption is reasonably satisfied before presenting the Pearson result.

8. THE System SHALL only surface a correlation finding to the User when: (a) the sample size is at least 20; and (b) the computed correlation has a two-tailed p-value < 0.05 using the chosen method. THE System SHALL report the correlation coefficient, the sample size, and the p-value in any finding surfaced to the User. THE System SHALL NOT claim statistical significance without meeting both conditions.

9. WHEN surfacing a correlation finding, THE System SHALL clearly state: (a) that a correlation was observed; (b) the statistical measures; and (c) that correlation does not imply causation. THE System SHALL NOT assert that one variable causes another based on correlation alone.

10. WHEN the Mentor_Agent detects a recurring theme across 5 or more Daily Reflections (e.g., the same obstacle mentioned repeatedly), THE System SHALL surface a Lesson entry proposal to the User. WHEN the User accepts the proposal, THE System SHALL store it as a Memory_Store entry of type Lesson with source AI-inferred.

11. THE System SHALL allow the User to view past Daily Reflections in a chronological journal view, with search by keyword and filter by date range or Goal category.

---

### Requirement 12: Life Dashboard

**User Story:** As a User, I want a single home screen that gives me an at-a-glance view of my goals, today's schedule, AI insights, and accountability alerts, so that I can orient myself in under 30 seconds.

#### Acceptance Criteria

1. THE Life_Dashboard SHALL display: today's Daily Actions from the Internal Schedule in time order with current status indicators, the Personal Integrity Score with trend, active Goals grouped by category with progress percentages, the latest AI insight from any Agent, and active Accountability_Engine alerts.

2. THE Life_Dashboard SHALL update the Daily Action status indicators in real time as Check-in Records are created.

3. THE System SHALL make Now Mode accessible from the Life_Dashboard with a single user interaction.

4. THE System SHALL make the AI chat interface accessible from the Life_Dashboard with a single user interaction.

5. WHEN there are unresolved Level 3 or higher Accountability_Engine Escalation States, THE Life_Dashboard SHALL display them prominently above the fold.

6. THE Life_Dashboard SHALL load and render all primary content within 2 seconds on a standard broadband connection.

---

### Requirement 13: AI Chat Interface

**User Story:** As a User, I want a conversational chat interface where I can ask questions, give commands, and receive guidance from any of the three AI personalities, so that I have a single natural-language entry point to the System.

#### Acceptance Criteria

1. THE System SHALL provide a persistent chat interface accessible from the Life_Dashboard and as a standalone screen.

2. WHEN the User sends a message in the chat interface, THE Supervisor SHALL route it to the appropriate Agent and display the response with an attribution label indicating which Agent responded (e.g., "Personal Assistant", "Mentor", "Accountability Partner").

3. THE System SHALL maintain the full conversation history for each session and expose past sessions in a searchable history view. Conversation history is session-scoped and is NOT automatically persisted to the Memory_Store (Requirement 4, criterion 7; Requirement 18, criterion 4).

4. THE System SHALL allow the User to explicitly address a specific Agent by name (e.g., "@Mentor What skills should I build this quarter?") to override the Supervisor's routing decision.

5. WHEN an Agent response includes a proposed mutating or destructive action (e.g., creating a Task, rescheduling a Daily Action, adding a Principle to the Memory_Store), THE System SHALL present the action as a confirmation card the User can accept or reject inline, consistent with Requirement 20.

6. THE System SHALL support multi-turn conversations where the Agent retains context from previous messages within the same session.

7. WHEN the User invokes the "Why?" feature on any Daily Action or Task, THE System SHALL respond with a message connecting that item to its parent Goal, the User's stated values, and the deeper purpose recorded in the Memory_Store. The Agent SHALL retrieve the relevant Goal and Memory_Store entries via the Tool Bus before generating the response.

---

### Requirement 14: Notification System

**User Story:** As a User, I want notifications that initiate meaningful interactions rather than passive reminders, so that each alert is an on-ramp to action or reflection rather than noise.

#### Acceptance Criteria

1. THE System SHALL deliver notifications for: Daily Briefing, Accountability Engine escalations (all levels), Daily Reflection prompt, Weekly CEO Meeting initiation, and Commitment deadline breaches.

2. WHEN the User taps a push notification, THE System SHALL open the chat interface pre-loaded with the relevant context (e.g., the accountability escalation details) rather than a static screen.

3. THE System SHALL allow the User to configure notification delivery times and channels (push, in-app, or both) for each notification type independently.

4. WHEN the User has enabled Do Not Disturb for a time window, THE System SHALL queue Level 1 and Level 2 Accountability_Engine notifications and deliver them when the window ends. Level 3, 4, and 5 notifications SHALL be delivered immediately regardless of Do Not Disturb.

5. THE System SHALL NOT deliver more than 3 push notifications within any 1-hour window unless an event is classified as Level 4 or Level 5 by the Accountability_Engine.

6. FOR ALL notifications, THE System SHALL track delivery state as one of: Scheduled, Delivered, Opened, Acted Upon.

7. WHEN a notification delivery attempt fails, THE System SHALL retry delivery up to 3 times using exponential backoff (intervals: 1 minute, 4 minutes, 16 minutes).

8. WHEN a User device is offline and misses a notification, THE System SHALL deliver the notification upon connectivity restoration if the notification is within the staleness window (default: 4 hours for Level 1–2 notifications). Level 3, 4, and 5 Accountability_Engine notifications SHALL always be delivered regardless of staleness.

---

### Requirement 15: API and Tool Bus

**User Story:** As a developer, I want a well-defined backend API with a Tool Bus exposing agent-callable functions, so that all Agents operate on consistent, authoritative data.

#### Acceptance Criteria

1. THE System SHALL expose a REST API with authenticated endpoints for all CRUD operations on Goals, Objectives, Projects, Tasks, Habits, Daily Actions, Commitments, Reflections, and Memory_Store entries.

2. THE Tool_Bus SHALL expose at minimum the following callable functions to all Agents: `get_today_schedule`, `get_active_goals`, `get_goal_progress`, `create_task`, `complete_task`, `reschedule_task`, `record_habit`, `log_reflection`, `search_memory`, and `search_web`.

3. THE System SHALL classify every Tool Bus operation into one of three permission tiers:
   - **Read-only** (no User confirmation required): `get_today_schedule`, `get_active_goals`, `get_goal_progress`, `search_memory`, `search_web`.
   - **Mutating** (User confirmation required before execution): `create_task`, `reschedule_task`, `record_habit`, `log_reflection`, and any operation that creates or modifies application data.
   - **Destructive/External** (strong User confirmation with explicit acknowledgment required): any operation that permanently deletes data or writes to an external service.

4. THE System SHALL NOT execute a Mutating or Destructive/External Tool Bus operation without receiving explicit User confirmation, as defined in Requirement 20.

5. FOR ALL Mutating Tool Bus operations, THE System SHALL accept an idempotency_key in the request. WHEN a request is received with an idempotency_key that matches a previously completed operation for the same User within the last 24 hours, THE System SHALL return the result of the original operation without re-executing the mutation.

6. THE System SHALL authenticate all API requests using the session authentication mechanism defined in Requirement 17. JWT token lifetime SHALL NOT exceed 24 hours.

7. WHEN a Tool_Bus function fails due to a data error, THE System SHALL return a structured error object containing an error code, human-readable message, and the input that caused the failure, without exposing internal stack traces.

8. THE System SHALL log all Tool_Bus function invocations with: agent name, function name, input parameters (excluding PII), response status, and latency in milliseconds.

9. THE System SHALL store all primary relational data in a relational database and all vector-embedded documents in a vector-capable store. The specific technologies shall be determined in design.

10. FOR ALL Tool_Bus search functions operating on the Memory_Store, THE System SHALL return results in descending order of cosine similarity score.

---

## System Requirements

### Requirement 16: User Profile and Onboarding

**User Story:** As a new User, I want to configure my profile and complete an AI-guided onboarding conversation, so that the System understands my goals, values, and preferences before I begin using it.

#### Acceptance Criteria

1. THE System SHALL allow the User to configure the following profile fields: full name, timezone (IANA timezone identifier), wake time, sleep time, working hours (start and end), notification preferences, life categories of focus, personal values, principles, long-term vision statement, and preferred Accountability Style (one of: Gentle, Balanced, Direct, Strict).

2. THE User's configured timezone SHALL be used to interpret all User-facing scheduled times, reporting period boundaries, and scheduled event triggers throughout the System. The server's local timezone SHALL NOT determine the User's schedule.

3. WHEN a new User account is created, THE System SHALL initiate an AI-guided onboarding conversation before presenting the Life Dashboard.

4. THE Onboarding_Conversation SHALL collect at minimum: the User's top goals per life category, core values and principles, current responsibilities and routines, and expectations of the AI's role.

5. WHEN the onboarding conversation is completed, THE System SHALL store all collected information as Memory_Store entries with source User-stated.

6. THE System SHALL allow the User to update any profile field at any time from the settings screen without restarting the full onboarding flow.

7. THE System SHALL allow the User to re-initiate the onboarding conversation at any time from the settings screen.

---

### Requirement 17: Authentication and Account Lifecycle

**User Story:** As a User, I want secure account registration, login, and lifecycle management, so that my personal data is protected and I retain full control over my account.

#### Acceptance Criteria

1. THE System SHALL be architected as multi-user from the start, with all data records scoped to an authenticated User identifier.

2. THE System SHALL allow a new User to register with an email address and password. THE System SHALL send an email verification link upon registration and SHALL NOT permit login until the email is verified.

3. WHEN a User attempts to log in before verifying their email address, THE System SHALL reject the login and prompt the User to complete email verification.

4. THE System SHALL support session-based authentication using tokens with a maximum lifetime of 24 hours per token.

5. **Logout invalidation:** Logging out SHALL invalidate the User's active authentication session immediately. Previously issued credentials associated with that session SHALL no longer be accepted for authenticated access after logout. Refresh credentials, if issued, SHALL also be invalidated on logout. The System SHALL support server-side session invalidation semantics sufficient to satisfy this requirement. The specific implementation mechanism (token blacklist, server-side session store, token versioning, etc.) SHALL be determined in design.md.

6. THE System SHALL allow a User to request a password reset via their registered email address. THE System SHALL send a time-limited reset link valid for 1 hour.

7. THE System SHALL allow a User to request permanent account deletion. WHEN account deletion is requested, THE System SHALL require explicit written confirmation, then permanently delete all User-owned application data within 30 days, as defined in Requirement 18.

8. IF a login attempt uses incorrect credentials 5 or more times within a 10-minute window, THEN THE System SHALL lock the account for 15 minutes and notify the User via email.

---

### Requirement 18: Privacy and Data Control

**User Story:** As a User, I want full control over my personal data, so that I can export, correct, or delete any information the System holds about me.

#### Acceptance Criteria

1. THE System SHALL allow the User to export all personal data as a machine-readable file (JSON format) from the settings screen.

2. WHEN a User requests account deletion and confirms explicitly, THE System SHALL make all of the following data inaccessible to the User immediately and permanently delete it within 30 days:
   - User profile and settings
   - Goals, Objectives, Projects, Tasks, Habits
   - Daily Actions and Check-in Records
   - Routine Templates, Routine Instances, and Routine Exceptions
   - Commitments and Promise Ledger entries
   - Reflections and journal entries
   - Memory Store entries
   - Conversation session histories
   - Agent Traces
   - Notification logs
   - Integration credentials and configuration

3. Application-visible personal data SHALL become inaccessible immediately upon account deletion confirmation. Permanent deletion of stored data SHALL be completed within 30 days.

4. System backups MAY retain encrypted copies of deleted data temporarily within the 30-day window. These backups SHALL NOT be restored as active User data. After the 30-day retention window, the backup copies SHALL also be permanently deleted.

5. WHEN Calendar Integration (Requirement 27) or any external integration is active, the System SHALL delete or revoke all credentials and tokens stored by the application upon account deletion. Data already stored in the external service is governed by that service's own deletion policy.

6. Agent Traces are within the account deletion scope and SHALL be treated as User-owned personal data for deletion purposes.

7. Anonymization SHALL NOT be used as a substitute for deletion of User-owned personal data under this requirement.

8. THE System SHALL allow the User to delete individual Memory_Store entries after explicit confirmation.

9. THE System SHALL allow the User to delete individual conversation session histories.

10. THE System SHALL clearly distinguish conversation history (session-scoped, not automatically persisted) from Memory_Store entries (explicitly persisted long-term memory). Conversation history SHALL NOT be automatically persisted to the Memory_Store, consistent with Requirement 4, criterion 7.

11. THE System SHALL allow the User to view all Memory_Store entries in a browsable list, filterable by type and source.

---

### Requirement 19: AI Safety and Grounding

**User Story:** As a User, I want the AI to clearly distinguish between facts it retrieved and things it inferred, so that I can trust the information the System presents.

#### Acceptance Criteria

1. WHEN an Agent presents information to the User, THE Agent SHALL distinguish between the following information sources: (a) data retrieved from the database via the Tool Bus, (b) data retrieved from the Memory_Store, (c) AI inference or recommendation, and (d) information of unknown or uncertain provenance.

2. THE Agent SHALL NOT assert factual claims about User behaviour, history, or preferences unless the underlying data has been retrieved via the Tool Bus or Memory_Store within the current interaction.

3. THE Agent SHALL NOT represent an AI-inferred or AI-generated statement as a recorded User fact. Memory_Store entries with source AI-inferred SHALL be labelled as inferences when presented to the User.

4. WHEN an Agent presents a recommendation derived from AI inference, THE Agent SHALL label it explicitly as a recommendation or inference and distinguish it from retrieved User data.

5. FOR ALL longitudinal queries (e.g., "Why am I not progressing?"), THE Agent SHALL cite the specific Memory_Store entries or Tool Bus data retrieved within the interaction that support its response.

---

### Requirement 20: Agent Tool Permissions and Confirmation Model

**User Story:** As a User, I want the AI to ask for my confirmation before making changes on my behalf, so that I remain in control of all mutations to my data.

#### Acceptance Criteria

1. THE System SHALL classify all Tool Bus operations into permission tiers as defined in Requirement 15, criterion 3.

2. WHEN an Agent determines that a Tool Bus call is Mutating, THE Agent SHALL present a confirmation card to the User describing the proposed action before invoking the tool.

3. WHEN an Agent determines that a Tool Bus call is Destructive/External, THE Agent SHALL present a confirmation card with an explicit warning label and require the User to press a dedicated confirm button or type a confirmation phrase before invoking the tool.

4. THE System SHALL NOT invoke a Mutating or Destructive/External Tool Bus operation from within an automated or background process without a prior explicit User confirmation from an active session.

5. IF the User rejects a proposed Tool Bus action, THEN THE Agent SHALL acknowledge the rejection and SHALL NOT retry the same action within the same conversation turn.

---

### Requirement 21: Background Job Scheduler

**User Story:** As a developer, I want a dedicated background job scheduler that owns all time-triggered system events, so that the LLM is never responsible for initiating scheduled actions.

#### Acceptance Criteria

1. THE System SHALL include a background job scheduler responsible for initiating all scheduled system events.

2. THE Scheduler SHALL trigger the following jobs at their configured times: Daily Plan (Routine Instance) generation, Daily Briefing generation and delivery, Daily Reflection prompt delivery, Weekly CEO Meeting session initiation, Commitment deadline evaluation, Accountability Engine notification dispatch, and pattern detection analysis.

3. ALL scheduled event times SHALL be computed using the User's Timezone. The Scheduler SHALL convert User-configured local times to the server's clock as required. THE server's timezone SHALL NOT determine User schedules.

4. WHEN the User's Timezone is changed, THE Scheduler SHALL recompute all future scheduled event times for that User using the new timezone. Historical events SHALL retain their original timestamps.

5. THE Scheduler SHALL handle nonexistent local times (e.g., the hour that is skipped during a daylight-saving forward transition) by advancing to the next valid local time. THE Scheduler SHALL handle ambiguous local times (e.g., the repeated hour during a daylight-saving backward transition) by using the first occurrence of that local time.

6. THE LLM SHALL NOT be responsible for initiating any scheduled event. All scheduled triggers SHALL originate from the background job scheduler.

7. WHEN a scheduled job fails, THE System SHALL log the failure with the job name, scheduled time, error details, and retry count.

8. THE System SHALL retry failed scheduled jobs up to 3 times with exponential backoff before marking the job as failed and recording it in the developer alert log.

9. THE Scheduler SHALL operate independently of active User sessions; scheduled jobs SHALL execute whether or not the User is currently using the application.

---

### Requirement 22: Notification Reliability

**User Story:** As a User, I want notifications to be reliably delivered even when I am temporarily offline, so that I do not miss accountability prompts or briefings.

#### Acceptance Criteria

1. FOR ALL notifications, THE System SHALL track delivery state as one of: Scheduled, Delivered, Opened, Acted Upon, as established in Requirement 14, criterion 6.

2. THE System SHALL retry failed notification delivery attempts up to 3 times using exponential backoff (intervals: 1 minute, 4 minutes, 16 minutes), consistent with Requirement 14, criterion 7.

3. WHEN a User device is offline and misses a notification, THE System SHALL deliver the notification upon connectivity restoration subject to the staleness window defined in Requirement 14, criterion 8.

4. THE System SHALL expose a notification delivery log queryable by the developer, showing: notification type, scheduled time, delivery state, and retry count for each notification.

---

### Requirement 23: Agent Observability and Tracing

**User Story:** As a developer, I want a complete trace recorded for every AI interaction, so that I can debug agent behaviour, measure performance, and detect regressions.

#### Acceptance Criteria

1. THE System SHALL record an Agent Trace for every AI interaction, capturing: Supervisor routing decision and confidence score, Agent selected, Tool Bus calls made (with arguments and responses), Memory_Store queries and results, LLM prompt and response (with PII exclusion configurable), actions proposed, and User accept/reject outcome.

2. THE System SHALL store Agent Traces in a queryable store, indexed by session ID, user ID, agent name, and timestamp.

3. THE System SHALL expose a developer-accessible interface for querying Agent Traces by session ID, user ID, agent name, date range, and outcome (accepted or rejected).

4. WHEN an Agent Trace records a Tool Bus call that returned an error, THE System SHALL flag that trace for developer review.

5. THE System SHALL retain Agent Traces for a minimum of 90 days.

---

### Requirement 24: Deterministic vs. AI Reasoning Separation

**User Story:** As a developer, I want all state-based logic implemented as deterministic application code, so that business rules are predictable, testable, and not subject to LLM non-determinism.

#### Acceptance Criteria

1. THE System SHALL implement the following logic as deterministic application code and NOT as LLM inference: Daily Action status tracking and Check-in Record creation, overdue Daily Action detection, Completion Rate calculation, Personal Integrity Score calculation, Accountability Engine escalation level determination and Escalation State management, notification scheduling and dispatch, Commitment deadline evaluation, and Commitment status transitions.

2. THE LLM SHALL be invoked only for: intent classification and routing, natural language response generation, recommendations, reasoning explanations, and Memory_Store semantic search.

3. WHEN a deterministic value (e.g., Personal Integrity Score, Completion Rate, Escalation State) is referenced in an Agent response, THE Agent SHALL retrieve the pre-computed value from the database via the Tool Bus rather than computing it inline via LLM reasoning.

4. THE System SHALL maintain unit tests covering all deterministic calculation logic defined in criterion 1, with 100% branch coverage for all status transition logic.

---

### Requirement 25: Web Search and External Knowledge

**User Story:** As a User, I want the AI to search the web when it lacks sufficient knowledge, so that I receive accurate and current information with clear attribution.

#### Acceptance Criteria

1. THE Agent MAY invoke the `search_web` Tool Bus function only when it lacks sufficient knowledge to answer the query from parametric memory, Memory_Store, or database data, and the query requires current or external information.

2. WHEN the Agent invokes `search_web`, THE Tool Bus SHALL return results including the source URL for each result.

3. THE Agent SHALL clearly label web-derived information in its response, distinguishing it from personal Memory_Store data and Tool Bus data.

4. WHEN the Agent presents a factual claim derived from a web search result, THE Agent SHALL cite the source URL in the response.

5. THE Agent SHALL NOT present web-derived information as User personal data or Memory_Store content.

---

### Requirement 26: Agent Evaluation and Testing

**User Story:** As a developer, I want a maintained evaluation test suite covering key agent behaviours, so that regressions in AI routing, reasoning, and tool use are detected before deployment.

#### Acceptance Criteria

1. THE System SHALL maintain an evaluation test suite covering: intent routing accuracy, Tool Bus tool selection correctness, schedule reasoning, Accountability Engine escalation logic, Memory_Store retrieval relevance, and hallucination detection.

2. FOR ALL evaluation test cases, THE System SHALL use the following format: input_message (string), expected_agent (one of: Personal_Assistant_Agent, Mentor_Agent, Accountability_Agent), expected_tools (list of Tool Bus function names), expected_behaviour (plain-language description), and pass_criteria (the verifiable condition that determines pass or fail).

3. THE evaluation test suite SHALL include at minimum 5 test cases per covered behaviour category listed in criterion 1.

4. WHEN the evaluation suite is executed, THE System SHALL produce a report showing pass rate per category, total pass rate, and a list of failed test cases with actual vs. expected behaviour.

5. THE System SHALL make the evaluation suite executable as a standalone command independent of the production application.

---

### Requirement 27: Calendar Integration (V2)

**User Story:** As a User, I want the System to read my external calendar, so that the Personal Assistant Agent can account for external meetings when computing my schedule and Now Mode recommendations.

#### Acceptance Criteria

1. WHERE Calendar_Integration is enabled, THE System SHALL support read-only connection to external calendar providers (Google Calendar, Microsoft Outlook).

2. WHERE Calendar_Integration is enabled, THE Personal_Assistant_Agent SHALL include external calendar events when computing Now Mode recommendations and Daily Briefing schedule sections, supplementing the Internal Schedule.

3. WHERE Calendar_Integration is enabled, THE System SHALL NOT write to, modify, or delete events in the connected external calendar.

4. WHERE Calendar_Integration is enabled, WHEN an external calendar event overlaps with a scheduled Daily Action, THE Personal_Assistant_Agent SHALL surface the conflict in the Daily Briefing and in Now Mode responses.

5. WHERE Calendar_Integration is enabled, THE System SHALL refresh external calendar data at least once every 15 minutes during active User sessions.

6. WHERE Calendar_Integration is NOT enabled, THE Personal_Assistant_Agent SHALL operate exclusively from the Internal Schedule and SHALL NOT claim knowledge of external calendar events.

---

## Change Summary

### Issues Resolved

**Issue 1 — Goal Progress Aggregation:**
Requirement 1 criteria 2–7 replaced. Objectives now require a metric direction (higher_is_better / lower_is_better), optional baseline (required for lower_is_better), and weight. Progress formulae for both directions are explicit with CLAMP(0,100). Division-by-zero is rejected at creation. Goal progress uses weighted average of active Objective percentages, capped 0–100%. Goals with no active Objectives display 0%. Glossary updated with Objective and Objective Weight definitions.

**Issue 2 — Check-in History:**
Requirement 3 rewritten to establish two distinct concepts: current Daily Action status and immutable Check-in Records. Check-in Record schema defined (Daily Action ref, previous status, new status, UTC timestamp, source, optional note). Valid status transitions enumerated; rejected transitions produce no record. Historical analytics use Check-in history. Glossary updated: Check-in Record and Check-in defined separately.

**Issue 3 — Commitment ↔ Daily Action Relationship:**
Requirement 9 rewritten. Commitment may reference zero, one, or multiple Daily Actions. Completion condition (ALL / ANY) must be set explicitly at creation time for multi-Daily-Action Commitments. Commitments with no linked Daily Actions become Kept only through explicit User action. Rescheduling a linked Daily Action does not change the Commitment's identity or deadline. Deleting a linked Daily Action preserves the Commitment. Glossary updated with Commitment Completion Condition.

**Issue 4 — Accountability Levels 1–5:**
Requirement 8 rewritten with explicit deterministic trigger conditions for all five levels. Level 2 (Nudge) trigger: action past scheduled end time, still Planned/Started. Level 4 (Reflection Prompt) trigger: skip count reaches 5 of last 7 days (extending from Level 3's threshold of 3). Escalation reset rule defined. LLM explicitly excluded from escalation decisions.

**Issue 5 — Stable Daily Action Identity:**
Requirement 1, 2, 3, and 8 updated. Daily Action Source concept introduced (type: HABIT, ROUTINE_ENTRY, TASK, MANUAL; with stable source ID). Accountability analytics use source identity, not title matching. Glossary updated with Daily Action Source.

**Issue 6 — Daily Completion Rate:**
Requirement 3, criterion 12–13 and the Completion Rate glossary entry rewritten. Denominator is Planned + Started + Skipped + Completed. Started actions are in the denominator but not the numerator. Cancelled actions excluded from both. Rescheduled actions not double-counted. Overdue actions with no update stay as Planned (incomplete). Reporting period boundary uses User's Timezone.

**Issue 7 — Habit vs Commitment Terminology:**
Habit glossary entry updated to remove "committed to" language; now reads "intends to perform according to a defined frequency or schedule." Explicit statement added that a Habit is NOT automatically a Commitment. Consistent across Requirement 1, 9, and Glossary.

**Issue 8 — JWT Logout / Session Invalidation:**
Requirement 17, criterion 5 rewritten as a behavioral requirement only. States: logout SHALL invalidate the session immediately; previously issued credentials SHALL not be accepted; refresh credentials also invalidated; server-side invalidation semantics required. Specific implementation mechanism (blacklist, versioning, etc.) deferred to design.md.

**Issue 9 — Privacy and Data Deletion Scope:**
Requirement 18, criterion 2 now enumerates the full scope of deleted data (14 categories). Immediate inaccessibility on confirmation + permanent deletion within 30 days. Backup retention window defined. External integration credential deletion addressed. Agent Traces included in scope. Anonymization explicitly excluded as a substitute.

**Issue 10 — Statistically Meaningful Correlation:**
Requirement 11, criteria 6–9 rewritten. Minimum sample size raised to 20 (preliminary observation possible at 7–19). Spearman rank correlation specified as the default method. Pearson allowed only with normality verification. Significance threshold: p < 0.05, two-tailed. Agent must report coefficient, sample size, and p-value. Causation claims explicitly prohibited.

**Issue 11 — User Timezone and Background Scheduling:**
Requirement 16 criterion 2, Requirement 21 criteria 3–5, and Requirement 9 criterion 12 updated. User's IANA Timezone is the authoritative basis for all scheduled times and reporting boundaries. Scheduler converts to server time. DST handling: nonexistent times advance to next valid time; ambiguous times use first occurrence. Historical timestamps retain original timezone context. User Timezone and Internal Schedule added to Glossary.

**Issue 12 — Internal Schedule vs External Calendar:**
Internal Schedule defined in Glossary as the V1-only schedule source. Requirement 5, criterion 2 updated to restrict Personal_Assistant_Agent to Internal Schedule in V1. Requirement 6, criterion 2 updated to reference Internal Schedule. Requirement 7, criterion 1 updated. Context Engine definition updated. Requirement 27, criterion 6 added: when Calendar Integration is not enabled, the agent operates from Internal Schedule only and SHALL NOT claim knowledge of external events.

### Existing Requirements Reworded for Consistency

- Requirement 3 title updated to "Task, Daily Action Status, and Check-in History" to reflect expanded scope.
- Requirement 9 title unchanged; criteria fully rewritten for Commitment lifecycle clarity.
- Requirement 14, criterion 4 updated: Do Not Disturb now blocks Level 1–2 only; Level 3–5 are always immediate.
- Requirement 15, criterion 9 updated: specific database technology reference (PostgreSQL) removed; deferred to design.md.
- Requirement 24, criterion 1 updated to include Check-in Record creation and Commitment status transitions as deterministic logic.

### Remaining Ambiguities Flagged for Product Decision

1. **Habit partial completion:** Requirement 1 criterion 11 retains "partial completions" as a tracked metric, but the definition of what constitutes a partial completion for a Habit (e.g., 15 of 30 planned minutes) is not defined. The design phase will need a product decision on whether partial completion is a separate status or a numeric value.

2. **Goal priority ranking:** Requirement 7 references "priority ranking of active Goals" for Now Mode, but no requirement defines how Goals are ranked or whether the User sets priority explicitly. This should be resolved before the design phase.

3. **Accountability Style behavior thresholds:** Requirement 8 criterion 11 states the Accountability Style adjusts challenge frequency and notification limits, but the exact adjustments per style (Gentle / Balanced / Direct / Strict) are not quantified. These thresholds are a product decision to be made before or during design.

4. **Weekly CEO Meeting day configurability:** Requirement 10 fixes the Weekly CEO Meeting to Sunday. It is not specified whether the day of the week is user-configurable. This should be confirmed as a product decision.
