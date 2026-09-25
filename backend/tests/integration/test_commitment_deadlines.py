"""T6.2/T6.3: deadlines, deferral, the explanation window and integrity snapshots (R9.7, R9.10–9.14)."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import user_id_of

EMAIL = "otto@example.com"
EXPLANATION = "The client review ran over and I lost the evening."


async def commit(me: AuthedClient, due: str = "2026-09-22", title: str = "Send the draft") -> dict[str, Any]:
    r = await me.post("/commitments", {"title": title, "due_date": due})
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


async def sweep(api: Api, me: AuthedClient) -> int:
    """Run the `commitment_evaluation` job for this User (the worker calls it in T10.2)."""
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        return await api.container.commitments.evaluate_deadlines(s, uid)


async def at(api: Api, instant: datetime, email: str = EMAIL) -> AuthedClient:
    """Move the frozen clock and get a fresh token (access tokens last an hour)."""
    api.clock.set(instant)
    return await api.relogin(email)


async def detail(me: AuthedClient, commitment_id: str) -> dict[str, Any]:
    return dict((await me.get(f"/commitments/{commitment_id}")).json()["data"])


async def score_of(me: AuthedClient) -> dict[str, Any]:
    r = await me.get("/integrity-score")
    assert r.status_code == 200, r.text
    return dict(r.json()["data"])


# --------------------------------------------------------------------------- Open past due (R9.7)


async def test_open_commitment_breaks_after_its_local_day(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    me = await at(api, datetime(2026, 9, 22, 23, 59, tzinfo=UTC))
    assert await sweep(api, me) == 0  # still the due local day

    me = await at(api, datetime(2026, 9, 23, 0, 1, tzinfo=UTC))
    assert await sweep(api, me) == 1
    assert await sweep(api, me) == 0  # idempotent
    after = await detail(me, body["id"])
    assert (after["status"], after["broken_at"] is not None) == ("Broken", True)
    assert [(e["event_type"], e["actor"]) for e in after["events"]] == [
        ("created", "User"),
        ("broken", "System"),
    ]
    assert (await score_of(me))["broken"] == 1


async def test_timezone_decides_when_the_day_ends(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await me.patch("/me", {"timezone": "Pacific/Honolulu"})  # UTC-10
    body = await commit(me)
    me = await at(api, datetime(2026, 9, 23, 5, 0, tzinfo=UTC))  # still 19:00 on the 22nd locally
    assert await sweep(api, me) == 0
    me = await at(api, datetime(2026, 9, 23, 11, 0, tzinfo=UTC))  # 01:00 on the 23rd locally
    assert await sweep(api, me) == 1
    assert (await detail(me, body["id"]))["status"] == "Broken"


async def test_late_completion_cannot_rescue_an_overdue_commitment(api: Api) -> None:
    me = await api.as_user(EMAIL)
    action = (
        await me.post(
            "/daily-actions",
            {"title": "Draft", "date": "2026-09-22", "start_time": "09:00:00", "end_time": "10:00:00"},
        )
    ).json()["data"]
    body = (
        await me.post(
            "/commitments",
            {
                "title": "Send the draft",
                "due_date": "2026-09-22",
                "links": [{"entity_type": "DailyAction", "entity_id": action["id"]}],
            },
        )
    ).json()["data"]
    me = await at(api, datetime(2026, 9, 23, 9, 0, tzinfo=UTC))  # the next local day, before the sweep
    r = await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Completed"})
    assert r.status_code == 201, r.text
    after = await detail(me, body["id"])
    assert after["status"] == "Broken"  # the due date passed first (R9.7)
    assert [e["event_type"] for e in after["events"]] == ["created", "broken"]


# --------------------------------------------------------------------------- deferral (R9.10)


async def test_first_deferral_needs_no_explanation(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    r = await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})
    assert r.status_code == 200, r.text
    deferred = r.json()["data"]
    assert (deferred["status"], deferred["deferral_count"], deferred["current_due_date"]) == (
        "Deferred",
        1,
        "2026-09-25",
    )
    assert deferred["due_date"] == "2026-09-22"  # the original due date stays on the record
    assert [e["event_type"] for e in (await detail(me, body["id"]))["events"]] == ["created", "deferred"]


async def test_deferred_past_due_opens_a_window_then_re_defers(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})

    early = await me.post(f"/commitments/{body['id']}/explanation", {"explanation": EXPLANATION})
    assert (early.status_code, early.json()["error"]["code"]) == (409, "INVALID_COMMITMENT_TRANSITION")

    me = await at(api, datetime(2026, 9, 26, 1, 0, tzinfo=UTC))
    assert await sweep(api, me) == 1
    opened = await detail(me, body["id"])
    assert opened["status"] == "Deferred"
    # a full 24 h from when the System observed the overdue commitment (01:00), never less
    assert opened["explanation_window_ends_at"] == "2026-09-27T01:00:00+00:00"
    assert await sweep(api, me) == 0  # the window is open; nothing else happens

    short = await me.post(f"/commitments/{body['id']}/explanation", {"explanation": "busy"})
    assert (short.status_code, short.json()["error"]["details"]["field"]) == (422, "explanation")

    r = await me.post(f"/commitments/{body['id']}/explanation", {"explanation": EXPLANATION})
    assert (r.status_code, r.json()["data"]["acknowledgment_required"]) == (201, True)
    again = await me.post(f"/commitments/{body['id']}/explanation", {"explanation": EXPLANATION})
    assert again.status_code == 409  # one explanation per window

    without = await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-30"})
    assert (without.status_code, without.json()["error"]["details"]["field"]) == (422, "acknowledged")

    r = await me.post(
        f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-30", "acknowledged": True}
    )
    assert r.status_code == 200, r.text
    redeferred = r.json()["data"]
    assert (redeferred["status"], redeferred["deferral_count"]) == ("Deferred", 2)
    assert redeferred["explanation_window_ends_at"] is None
    events = (await detail(me, body["id"]))["events"]
    assert [e["event_type"] for e in events] == [
        "created",
        "deferred",
        "explanation_window_opened",
        "explanation_submitted",
        "redeferred",
    ]
    assert (events[-1]["explanation"], events[-1]["previous_due"]) == (EXPLANATION, "2026-09-25")

    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        memories = list(
            (await s.execute(select(m.MemoryStoreEntry).where(m.MemoryStoreEntry.user_id == uid))).scalars()
        )
    assert len(memories) == 1  # the explanation is also stored as a Memory Fact (§11.3)
    assert (memories[0].type, memories[0].source, memories[0].embedding_status) == (
        "Fact",
        "User-stated",
        "pending",
    )
    assert EXPLANATION in memories[0].content


async def test_the_window_expires_into_broken(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})
    me = await at(api, datetime(2026, 9, 26, 1, 0, tzinfo=UTC))
    await sweep(api, me)
    await me.post(f"/commitments/{body['id']}/explanation", {"explanation": EXPLANATION})

    me = await at(api, datetime(2026, 9, 27, 1, 0, tzinfo=UTC))
    assert await sweep(api, me) == 1
    assert (await detail(me, body["id"]))["status"] == "Broken"  # explained, never acknowledged (§38.1)
    late = await me.post(
        f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-30", "acknowledged": True}
    )
    assert (late.status_code, late.json()["error"]["code"]) == (409, "INVALID_COMMITMENT_TRANSITION")


async def test_a_deferred_commitment_can_still_be_kept(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})
    r = await me.post(f"/commitments/{body['id']}/keep")
    assert (r.status_code, r.json()["data"]["status"]) == (200, "Kept")


async def test_deferring_to_an_earlier_date_is_rejected(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me, due="2026-09-30")
    r = await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})
    assert (r.status_code, r.json()["error"]["details"]["field"]) == (422, "new_due_date")


async def test_an_action_opens_the_window_before_the_sweep_does(api: Api) -> None:
    me = await api.as_user(EMAIL)
    body = await commit(me)
    await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-25"})
    me = await at(api, datetime(2026, 9, 26, 1, 0, tzinfo=UTC))
    r = await me.post(f"/commitments/{body['id']}/defer", {"new_due_date": "2026-09-30"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_COMMITMENT_TRANSITION")
    r = await me.post(f"/commitments/{body['id']}/explanation", {"explanation": EXPLANATION})
    assert r.status_code == 201, r.text  # the window was opened by the rejected action's evaluation
    assert (await detail(me, body["id"]))["explanation_window_ends_at"] is not None


# --------------------------------------------------------------------------- integrity score (R9.11–9.14)


async def test_overdue_deferred_counts_in_the_denominator(api: Api) -> None:  # R9.11
    me = await api.as_user(EMAIL)
    kept = await commit(me, title="Kept one")
    overdue = await commit(me, title="Deferred one")
    await me.post(f"/commitments/{kept['id']}/keep")
    await me.post(f"/commitments/{overdue['id']}/defer", {"new_due_date": "2026-09-25"})
    me = await at(api, datetime(2026, 9, 26, 1, 0, tzinfo=UTC))
    await sweep(api, me)
    score = await score_of(me)
    assert (score["kept"], score["broken"], score["overdue_deferred"]) == (1, 0, 1)
    assert (score["score"], score["local_date"]) == ("50.0", "2026-09-26")


async def test_score_series_and_trend(api: Api) -> None:  # R9.13
    me = await api.as_user(EMAIL)
    first = await commit(me, title="One")
    await me.post(f"/commitments/{first['id']}/keep")
    day_one = await score_of(me)
    assert day_one["score"] == "100.0"
    assert [row["local_date"] for row in day_one["series"]] == ["2026-09-21"]  # only today's row exists

    me = await at(api, datetime(2026, 9, 23, 12, 0, tzinfo=UTC))
    second = await commit(me, due="2026-09-24", title="Two")
    me = await at(api, datetime(2026, 9, 25, 12, 0, tzinfo=UTC))
    await sweep(api, me)
    overview = await score_of(me)
    assert (overview["score"], overview["kept"], overview["broken"]) == ("50.0", 1, 1)
    assert [row["local_date"] for row in overview["series"]] == ["2026-09-21", "2026-09-25"]
    assert overview["trend"]["baseline_score"] is None  # no snapshot 30 days ago
    assert (await detail(me, second["id"]))["status"] == "Broken"


async def test_score_is_null_without_resolved_commitments(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await commit(me)  # an Open Commitment is not resolved
    overview = await score_of(me)
    assert (overview["score"], overview["below_threshold"], overview["trend"]["delta"]) == (None, False, None)


async def test_window_edges(api: Api) -> None:  # design §11.4: the last 30 local days, including today
    me = await api.as_user(EMAIL)
    body = await commit(me)
    await me.post(f"/commitments/{body['id']}/keep")
    me = await at(api, datetime(2026, 10, 20, 12, 0, tzinfo=UTC))  # 29 days later: still inside
    assert (await score_of(me))["kept"] == 1
    me = await at(api, datetime(2026, 10, 21, 12, 0, tzinfo=UTC))  # 30 days later: outside
    overview = await score_of(me)
    assert (overview["kept"], overview["score"]) == (0, None)
    assert (overview["trend"]["baseline_date"], overview["trend"]["baseline_score"]) == (
        "2026-09-21",
        "100.0",
    )
    assert overview["trend"]["delta"] is None  # today's score is null


async def test_snapshots_use_local_dates(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await me.patch("/me", {"timezone": "Pacific/Kiritimati"})  # UTC+14: locally already the 22nd
    body = await commit(me, due="2026-09-24")
    await me.post(f"/commitments/{body['id']}/keep")
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        snapshot = (
            await s.execute(select(m.IntegrityScoreSnapshot).where(m.IntegrityScoreSnapshot.user_id == uid))
        ).scalar_one()
    assert snapshot.local_date == date(2026, 9, 22)


async def test_threshold_crossing_raises_one_flag(api: Api) -> None:  # R9.14, design §11.5
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    good = await commit(me, title="Good")
    await me.post(f"/commitments/{good['id']}/keep")
    assert (await score_of(me))["below_threshold"] is False

    for index in range(2):
        await commit(me, due="2026-09-22", title=f"Bad {index}")
    me = await at(api, datetime(2026, 9, 23, 1, 0, tzinfo=UTC))
    assert await sweep(api, me) == 2  # 1 kept, 2 broken → 33.3, below the default threshold of 70
    overview = await score_of(me)  # reading again must not raise a second flag
    assert (overview["score"], overview["below_threshold"], overview["threshold"]) == ("33.3", True, 70)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        flags = list(
            (await s.execute(select(m.ProactiveFlag).where(m.ProactiveFlag.user_id == uid))).scalars()
        )
    assert [(f.flag_type, f.dedupe_key, f.owner_agent, f.resolved_at) for f in flags] == [
        ("integrity_below_threshold", "integrity_below:2026-09-23", "accountability", None)
    ]

    for index in range(4):  # 5 kept / 2 broken = 71.4 — back at or above the threshold
        recovering = await commit(me, due="2026-09-30", title=f"Recovery {index}")
        await me.post(f"/commitments/{recovering['id']}/keep")
    assert (await score_of(me))["below_threshold"] is False
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        resolved = (await s.execute(select(m.ProactiveFlag.resolved_at))).scalar_one()
    assert resolved is not None  # the flag expires when the score returns to the threshold (§18.1)
