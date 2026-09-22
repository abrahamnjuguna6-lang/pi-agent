"""Injectable clock. Domain code never calls `datetime.now()` directly (tests freeze time)."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Test clock: returns a fixed instant until advanced."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Guard for persisted timestamps: reject naive datetimes, normalize to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime rejected; all timestamps must be timezone-aware (UTC)")
    return value.astimezone(UTC)
