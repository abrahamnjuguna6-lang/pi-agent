"""User-timezone arithmetic (design §20.3, R16.2, R21.3–21.5).

The single place where local wall-clock times become UTC instants. Policy:
  * nonexistent local time (spring-forward gap): advance minute by minute to the next valid time;
  * ambiguous local time (fall-back overlap): the first occurrence (fold=0);
  * all returned instants are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.domain.errors import DomainError

_MAX_GAP_MINUTES = 24 * 60  # real DST gaps are <= 2 h; this only guards against a runaway loop


def zone(name: str) -> ZoneInfo:
    """Validate an IANA timezone identifier (R16.1)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainError("VALIDATION_ERROR", f"Unknown timezone: {name}", {"field": "timezone"}) from exc


def _exists(naive: datetime, tz: ZoneInfo) -> bool:
    """A local time exists iff it survives a round trip through UTC unchanged."""
    aware = naive.replace(tzinfo=tz, fold=0)
    return aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive


def resolve_local(day: date, at: time, tz: ZoneInfo | str) -> datetime:
    """UTC instant for local `day` + `at` in `tz`, applying the DST policy above."""
    tzinfo = zone(tz) if isinstance(tz, str) else tz
    naive = datetime.combine(day, at.replace(tzinfo=None, fold=0))
    for _ in range(_MAX_GAP_MINUTES):
        if _exists(naive, tzinfo):
            return naive.replace(tzinfo=tzinfo, fold=0).astimezone(UTC)
        naive += timedelta(minutes=1)
    raise RuntimeError(f"no valid local time within 24h after {day} {at} in {tzinfo.key}")  # pragma: no cover


def to_local(instant: datetime, tz: ZoneInfo | str) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("naive datetime rejected; pass a timezone-aware instant")
    return instant.astimezone(zone(tz) if isinstance(tz, str) else tz)


def local_date(instant: datetime, tz: ZoneInfo | str) -> date:
    """The User-local calendar date of a UTC instant (reporting-day boundary, R3.12)."""
    return to_local(instant, tz).date()


def local_day_bounds(day: date, tz: ZoneInfo | str) -> tuple[datetime, datetime]:
    """[start, end) in UTC of a local calendar day — 23 h or 25 h long on DST days."""
    return resolve_local(day, time(0, 0), tz), resolve_local(day + timedelta(days=1), time(0, 0), tz)


def iso_week_start(day: date) -> date:
    """Monday of the ISO week containing `day` (habit weekly periods, §17.1)."""
    return day - timedelta(days=day.weekday())


def iso_week_bounds(day: date, tz: ZoneInfo | str) -> tuple[datetime, datetime]:
    monday = iso_week_start(day)
    return resolve_local(monday, time(0, 0), tz), resolve_local(monday + timedelta(days=7), time(0, 0), tz)


def block_instants(day: date, start: time, end: time, tz: ZoneInfo | str) -> tuple[datetime, datetime]:
    """UTC [start, end) of a local time block; `end <= start` means the block crosses midnight."""
    if start == end:
        raise DomainError("VALIDATION_ERROR", "A time block must have a non-zero duration")
    end_day = day + timedelta(days=1) if end < start else day
    begin = resolve_local(day, start, tz)
    finish = resolve_local(end_day, end, tz)
    if finish <= begin:  # e.g. both ends inside one DST gap collapse to the same instant
        finish = begin + timedelta(minutes=1)
    return begin, finish


def local_weekday_sunday0(day: date) -> int:
    """Weekday in the schema's convention (0=Sunday..6=Saturday; routine_templates.active_days)."""
    return (day.weekday() + 1) % 7
