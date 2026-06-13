"""In-process mock CRM / ticketing system.

When the AI escalates a conversation to a human, the production handoff would
POST a ticket payload to an external CRM webhook (Zendesk, Salesforce, the
operator's own work-order system). For demo purposes we keep that hop entirely
in-process: server.respond calls `mock_crm.open_ticket(...)` and the same
process exposes `GET /tickets` so the Agent View can show the receipt.

Kept tiny on purpose — this is the BOUNDARY of the smart-CS demo, not a real
ticket lifecycle. Re-assignment, SLA timers, follow-up etc. belong downstream.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Ticket:
    id: str
    thread_id: str
    trigger: str
    user_id: str | None
    summary: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    assigned_to: str = "一号客服小张"
    eta_hours: int = 24


class MockCRM:
    def __init__(self) -> None:
        self._tickets: list[Ticket] = []

    def open_ticket(
        self,
        thread_id: str,
        trigger: str,
        user_id: str | None,
        summary: str,
        snapshot: dict[str, Any] | None = None,
    ) -> Ticket:
        ticket = Ticket(
            id=f"T{uuid4().hex[:6].upper()}",
            thread_id=thread_id,
            trigger=trigger,
            user_id=user_id,
            summary=summary,
            snapshot=snapshot or {},
        )
        self._tickets.append(ticket)
        return ticket

    def list_for_thread(self, thread_id: str) -> list[Ticket]:
        return [t for t in self._tickets if t.thread_id == thread_id]

    def list_all(self, limit: int = 20) -> list[Ticket]:
        return self._tickets[-limit:]


mock_crm = MockCRM()
