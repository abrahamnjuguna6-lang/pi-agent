# Decision log — things to revisit

Implementation decisions that resolved an ambiguity, deviated from a literal reading of
`requirements.md`, or bought simplicity now at a cost later. Each one is live in the code and covered
by tests; each one is also a place where a product call could reasonably go the other way.

`tasks.md` links here from the tasks that carry them. When a decision is confirmed or changed, update
the entry (and `design.md`) rather than deleting it — the reasoning is the valuable part.

| ID | Decision | Revisit when |
|---|---|---|
| [D1](#d1) | An expired explanation window breaks a Commitment even if an explanation was submitted | Product reviews the Promise Ledger |
| [D2](#d2) | The explanation window starts when the System *observes* the overdue Commitment, if that is later than the due day | The worker cadence is set (T10.2) |
| [D3](#d3) | A deferral must move the due date later than the current one | Product reviews deferral |
| [D4](#d4) | Level 5 runs count only *resolved* occurrences | First real accountability data |
| [D5](#d5) | Skips on or before the recovery anchor never re-escalate | First real accountability data |
| [D6](#d6) | Escalation rows persist at Level 1 after an episode ends | Dashboard work (T14.2) |
| [D7](#d7) | Only Planned or Started Daily Actions can back a new Commitment | Agent commitment creation (T11.2) |
| [D8](#d8) | `GET /integrity-score` refreshes today's snapshot | Dashboard read model (T14.2) |
| [D9](#d9) | A Check-in note is stored with its title and date inside `content` | Memory browser UI (T16.6) |
| [D10](#d10) | New error code `CONFIRMATION_REQUIRED` (400), phrase `DELETE` | Frontend delete flows (T16.6) |
| [D11](#d11) | The accountability reflection writes its Memory entry inline, not from the event | Daily reflection flow (T13.5) |
| [D12](#d12) | Failed embeddings retry forever, with no attempt counter | Worker sweepers (T10.2) |
| [D13](#d13) | An overloaded 3-hour window means "no free gap of 10 minutes" | Daily Briefing (T13.3) |
| [D14](#d14) | Correlations look back 90 local days | First real reflection data |
| [D15](#d15) | A deleted ancestor makes a lineage chain `unlinked`, not missing | "Why?" flow (T13.4) |
| [D16](#d16) | A Commitment's rank priority comes from its Goal category | Weekly CEO Meeting (T13.8) |

---

## M6 — Commitments and accountability

### D1
**An expired explanation window breaks a Commitment even when an explanation was submitted without
the acknowledgment.**

R9.10 reads as though a written explanation alone prevents the automatic Broken transition, but
design §38.1's test table says the Commitment "stays Deferred (overdue) until acknowledgment or
window end". Taken literally, R9.10 leaves a Commitment overdue forever whenever the User explains
and then never acknowledges, and it keeps counting in the integrity denominator until it ages out of
the 30-day window. The §38.1 reading is implemented: the window is bounded, and only a completed
re-deferral (explanation → acknowledgment → new due date) stops the break.

Where: `domain/commitments.py` (`deadline_action`), design §11.3 item 4.
**Revisit:** if product wants an explanation alone to count as follow-through, `requirements.md` R9.10
should say so explicitly and §38.1's row needs rewriting.

### D2
**The explanation window starts at the end of the deferred due date's local day, or when the System
first observes the overdue Commitment if that is later.**

Design §11.3 says the window is 24 h from the due date passing. If the sweeper is late — a deploy, an
outage, an hourly cadence — a fixed start silently shortens the User's window, and a long enough
delay would open and expire it in the same pass, breaking the Commitment with no notice. The window
is therefore never shorter than a full 24 h from the first observation.

Where: `domain/commitments.py` (`_apply_deadline`), design §11.3 item 1.
**Revisit:** at T10.2, when the `commitment_evaluation` cadence is fixed. A guaranteed-fresh sweep
makes the two readings identical, and the simpler fixed start could be restored.

### D3
**A deferral must set a due date later than today *and* later than the current due date.**

R9.10 only requires "a new explicit due date"; design §11.3 adds "later than today". Allowing a
deferral to move the date *earlier* would let a User quietly rewrite a Commitment as a deferral.

Where: `domain/commitments.py` (`validate_deferral`), design §11.3.
**Revisit:** if users legitimately want to pull a commitment forward, that should be an edit
operation with its own event type, not a deferral.

### D4
**A Level 5 consecutive run counts only resolved occurrences (Completed or Skipped), and the run must
end inside the 7-day window.**

Design §14.2 measures consecutiveness over scheduled occurrences but does not say what an occurrence
still Planned or Started means. Treating it as a break would let an unchecked day hide a pattern;
treating it as a skip would punish the User for not checking in. It is ignored instead, so the run is
a statement about outcomes. Requiring the latest skip to be recent stops an old run from
re-triggering Level 5 forever.

Where: `domain/accountability.py` (`trailing_skip_run`, `pattern_level`), design §14.2.
**Revisit:** once there is real data. A user who simply stops checking in is currently handled by
Levels 1–2 only, which may be too soft.

### D5
**Skips dated on or before `recovery_window_anchor_date` never count towards escalation.**

Without this, a source at Level 3 that recovers to Level 2 is immediately re-escalated by the same
old skips on the next sweep, and it flaps between levels every five minutes. The anchor already
prevents one set of completions from being reused (§14.4); this is the mirror rule for skips.

Where: `domain/accountability.py` (`pattern_level`), design §14.2.
**Revisit:** with real data — the rule makes re-escalation need genuinely new skips, which may be
slower than an accountability product wants.

### D6
**An escalation row is kept at Level 1 once its episode ends**, rather than deleted.

Design §14.2 says persistent state exists "only when Level ≥ 3". Keeping the row preserves the
recovery anchor and the episode history, and makes the unique key stable. `GET
/accountability/escalations` hides Level 1 rows unless `include_resolved=true`.

Where: `domain/accountability.py` (`evaluate_source`), design §14.2.
**Revisit:** at T14.2, if the dashboard wants ended episodes listed differently.

### D7
**A new Commitment can only link Daily Actions that are Planned or Started.**

Nothing in R9 forbids linking an already Completed action, but a Commitment created against one would
be satisfied the moment it is created, which makes the integrity score meaningless.

Where: `domain/commitments.py` (`ensure_linkable`).
**Revisit:** at T11.2, when the agent creates Commitments from chat — "I'll commit to what I just
did" may need an explicit, and visible, path.

### D8
**`GET /integrity-score` recomputes and upserts today's snapshot before reading.**

Design §16.6 writes snapshots at local midnight and after each transition, and R24.3 says every
reader uses the stored value. Between midnight and the first transition of the day, the stored value
can be stale (the window slides, deferred Commitments become overdue). The read refreshes first, so a
GET can write a row and, in the crossing case, raise a flag.

Where: `domain/integrity.py` (`overview`).
**Revisit:** at T14.2. If the dashboard read model needs a pure read, move the refresh to the
`integrity_snapshot` sweeper and accept a bounded staleness instead.

## M7 — Memory Store

### D9
**A Check-in note is stored as "`<title>` on `<local date>` — `<status>`: `<note>`" in `content`.**

Design §23.3 says notes are embedded with their Daily Action title and date as context. Embedding
text that differs from the stored text would make search hits and the Memory Browser disagree, and
would break re-embedding after an edit, so the context lives in the content itself.

Where: `events/handlers/memory.py`, design §23.3.
**Revisit:** at T16.6. If the browser wants to render the note alone, the context belongs in
structured columns instead of the sentence.

### D10
**`CONFIRMATION_REQUIRED` (400) is a new stable error code, and the memory delete phrase is `DELETE`.**

`tasks.md` T7.2 specifies 400 for a delete without the phrase, and no existing code maps to 400
(`CASCADE_CONFIRMATION_REQUIRED` is a 409 conflict). The phrase itself is not specified anywhere;
`DELETE` is a placeholder that matches the destructive-tier pattern (design §27.5 uses `CONFIRM` for
chat tools).

Where: `api/routers/memory.py`, `domain/errors.py`, design §23.6 and §26.3.
**Revisit:** at T16.6 — the phrase is a product/UX choice and should probably be one constant shared
with the chat destructive tier and account deletion.

### D11
**The accountability reflection writes its Memory entry inside the submitting transaction; the
`reflection.submitted` handler skips `type='accountability'`.**

Design §19.8 requires the reflection and its memory entry to be written on submission, while §10.7
lists reflections as an automatic path. Doing both would double-write, so the event handler owns
daily and CEO reflections only.

Where: `domain/accountability.py`, `events/handlers/memory.py` (`INLINE_REFLECTION_TYPES`).
**Revisit:** at T13.5. If the daily flow also needs a same-transaction write, all reflection memory
should move inline and the handler should go.

### D12
**A failed embedding is retried on every backfill pass, with no attempt counter or dead-lettering.**

`memory_store_entries` has no retry columns (unlike `domain_events`), so a permanently unembeddable
entry is retried once per sweep forever. The sweep cadence is the only throttle.

Where: `domain/memory/embeddings.py` (`RETRYABLE_STATUSES`).
**Revisit:** at T10.2. If this shows up in job metrics, add `embedding_attempts` and a dead-letter
state, mirroring the outbox.

## M8 — Analytics

### D13
**An overloaded 3-hour window is one whose longest free gap is under 10 minutes**, and windows are
anchored at each Daily Action's start.

Design §16.8 says "≥ 100% booked with no gap of 10 minutes or more", which mixes two measures: a
window can be "100% booked" by double-booking while still containing free time. The gap measure is
the one that matches what the finding means to the User — no room to breathe — and anchoring at block
starts makes the finding reportable ("from 09:00 you have no break") instead of an arbitrary offset.

Where: `domain/schedule/analysis.py` (`overloaded_windows`), design §16.8.
**Revisit:** at T13.3, when the Daily Briefing phrases the finding. If it should also fire for a
double-booked-but-airy window, that is a second, separate finding.

### D14
**Correlation observations are gathered over the last 90 local days.**

§16.5 fixes the sample-size gates (7, 20) but never says how far back observations come from. Without
a bound, a year-old reflection pattern would keep influencing a "current" finding. 90 days is a
quarter — long enough to reach n = 20 for a user who reflects a few times a week.

Where: `domain/analytics/correlation.py` (`LOOKBACK_DAYS`), design §16.5.
**Revisit:** once real reflection cadence is known. If most users reflect rarely, 90 days may never
reach n = 20 and the window needs to grow (or the gate needs rethinking).

### D15
**A lineage walk that hits a deleted ancestor ends as `unlinked` rather than raising.**

Only the item being asked about must exist and belong to the User. A Daily Action whose Goal was
deleted still exists, and the honest answer to "why am I doing this?" is "this is no longer linked to
a goal" — a 404 would be a lie about the action itself.

Where: `domain/lineage.py` (`LineageService._step`), design §16.9.
**Revisit:** at T13.4. The "Why?" flow may want to distinguish "never linked" from "the goal was
deleted", which needs a richer marker than a single `unlinked` flag.

### D16
**A Commitment's ranking priority is the highest priority among the User's Goals in its category.**

Wins and gaps rank by "linked-Goal priority" (§16.10), but a Commitment stores `goal_category`, not a
Goal id (§11.6), so there is no single Goal to read a priority from. The highest priority in that
category is the closest available proxy.

Where: `domain/analytics/weekly.py` (`_category_priority`), design §16.10.
**Revisit:** at T13.8. If the briefing needs exact attribution, the Commitment should keep the
derived `goal_id` alongside the category — a small schema change, still cheap pre-release.

---

## Deferred work already tracked elsewhere

These are noted in `tasks.md` implementation notes and are *scheduled*, not open questions: the
habit-metrics Redis cache (T14.2), the schedule-suggestion proactive flag hook (T13.1), email through
the outbox (T3.3/T9.2), the cloud KMS key provider (T18.3), Level 1/2 accountability notifications
(T9.x), the Level 5 pattern report (T13.7), and the sweeper bodies that M6/M7 services expose
(`evaluate_deadlines`, `evaluate_user`, `embed_pending`, `IntegrityService.refresh`) which T10.2 will
schedule.
