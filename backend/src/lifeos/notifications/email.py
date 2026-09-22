"""Transactional email port.

The auth flows depend on this interface only. A provider adapter (SES/Postmark/…) is added with
the notification pipeline (T9.2), routed through the domain-event outbox (T3.3) so sends happen
after commit. Development logs messages; tests capture them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

log = logging.getLogger(__name__)

EmailKind = Literal["verify_email", "password_reset", "account_locked", "password_changed"]


@dataclass(frozen=True)
class EmailMessage:
    to: str
    kind: EmailKind
    subject: str
    body: str
    link: str | None = None


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class LoggingEmailSender:
    """Development sender: logs kind and recipient domain only (links are secrets; never logged)."""

    async def send(self, message: EmailMessage) -> None:
        domain = message.to.rsplit("@", 1)[-1]
        log.info("email kind=%s to=*@%s (dev sender; not delivered)", message.kind, domain)


class InMemoryEmailSender:
    """Test sender: records messages so tests can follow verification/reset links."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)

    def last(self, kind: EmailKind, to: str | None = None) -> EmailMessage:
        for message in reversed(self.outbox):
            if message.kind == kind and (to is None or message.to == to):
                return message
        raise LookupError(f"no {kind} email sent")
