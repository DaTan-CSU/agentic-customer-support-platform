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

import asyncio
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
    # SDK-native approvals only: serialized RunState + the approval item key
    # (tool_call_id) so server.respond() can resume the right interruption.
    # Soft-approval entries leave these None.
    run_state_json: str | None = None
    approval_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # Hand-rolled instead of dataclasses.asdict so we don't deepcopy
        # `args` — when args lands here from a ToolApprovalItem the dict can
        # transitively reference Starlette request state and recurse.
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "kind": self.kind,
            "tool_name": self.tool_name,
            "args": {k: _scalar(v) for k, v in (self.args or {}).items()},
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "operator_note": self.operator_note,
            "execution_result": self.execution_result,
            # Whether this entry came from the SDK-native HITL path. UI uses
            # it as a label; never expose the full RunState JSON.
            "sdk_native": self.run_state_json is not None,
        }


def _scalar(v: Any) -> Any:
    """Best-effort coerce nested args values to JSON-safe scalars."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return [_scalar(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _scalar(x) for k, x in v.items()}
    return str(v)


class ApprovalStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ApprovalRequest] = {}
        # SDK-native flow only: respond() blocks on this event until the
        # operator clicks Approve/Reject in the UI. Keyed by approval_id.
        self._waiters: dict[str, asyncio.Event] = {}

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
            waiter = self._waiters.get(approval_id)
        # Unblock the awaiting respond() if this is an SDK-native approval.
        # Done outside the lock because asyncio.Event.set() is thread-safe.
        if waiter is not None:
            waiter.set()
        return req

    # -- SDK-native (Runner pause/resume) helpers -------------------------

    def register_waiter(self, approval_id: str) -> asyncio.Event:
        """Reserve an asyncio.Event the resumed respond() can await on."""
        with self._lock:
            event = asyncio.Event()
            self._waiters[approval_id] = event
            return event

    def clear_waiter(self, approval_id: str) -> None:
        with self._lock:
            self._waiters.pop(approval_id, None)

    def attach_run_state(
        self,
        approval_id: str,
        *,
        run_state_json: str,
        approval_call_id: str,
    ) -> None:
        """Persist the serialized RunState beside the approval entry so the
        /approvals POST handler can resume the run later."""
        with self._lock:
            req = self._items.get(approval_id)
            if req is None:
                return
            req.run_state_json = run_state_json
            req.approval_call_id = approval_call_id


# Process-wide singleton — imported by both tools and the FastAPI routes.
approval_store = ApprovalStore()
