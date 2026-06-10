"""In-memory human-in-the-loop approval store, keyed by ChatKit thread_id.

Why a soft-approval pattern: the SDK's `needs_approval=True` would pause the
streamed run mid-flight, but resuming across HTTP requests inside ChatKit's
SSE flow gets ugly fast. Instead, sensitive tools (refund / cancel / price
protection) file an approval request and return immediately to the agent.
The agent tells the user "request submitted, awaiting review." Operators
approve/reject via `POST /approvals/{id}` and the demo executes (mocks) the
real action server-side.

Singleton lives for the life of the backend process. Mirrors MemoryStore's
ephemeral semantics — fine for a demo, lost on restart.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal
from uuid import uuid4

ApprovalStatus = Literal["pending", "approved", "rejected"]
ApprovalKind = Literal["refund", "order_cancel", "price_protection"]


@dataclass
class ApprovalRequest:
    id: str
    thread_id: str
    kind: ApprovalKind
    tool_name: str
    args: dict[str, Any]
    summary: str  # 一句中文，给运营看的
    status: ApprovalStatus = "pending"
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    operator_note: str | None = None
    execution_result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ApprovalRequest] = {}

    def create(
        self,
        *,
        thread_id: str,
        kind: ApprovalKind,
        tool_name: str,
        args: dict[str, Any],
        summary: str,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            id=f"appr_{uuid4().hex[:12]}",
            thread_id=thread_id,
            kind=kind,
            tool_name=tool_name,
            args=args,
            summary=summary,
        )
        with self._lock:
            self._items[req.id] = req
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._items.get(approval_id)

    def list_for_thread(self, thread_id: str) -> list[ApprovalRequest]:
        # Newest first; both pending and decided so the UI can show history.
        with self._lock:
            items = [r for r in self._items.values() if r.thread_id == thread_id]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items

    def decide(
        self,
        approval_id: str,
        *,
        decision: ApprovalStatus,
        note: str | None = None,
        execution_result: str | None = None,
    ) -> ApprovalRequest | None:
        if decision not in ("approved", "rejected"):
            raise ValueError(f"invalid decision: {decision}")
        with self._lock:
            req = self._items.get(approval_id)
            if req is None or req.status != "pending":
                return req
            req.status = decision
            req.decided_at = time.time()
            req.operator_note = note
            req.execution_result = execution_result
            return req


# Process-wide singleton — imported by both tools and the FastAPI routes.
approval_store = ApprovalStore()
