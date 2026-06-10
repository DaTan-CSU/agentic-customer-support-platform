from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel

from agents import (
    Handoff,
    HandoffOutputItem,
    InputGuardrailTripwireTriggered,
    ItemHelpers,
    MessageOutputItem,
    OutputGuardrailTripwireTriggered,
    Runner,
    ToolApprovalItem,
    ToolCallItem,
    ToolCallOutputItem,
    SQLiteSession,
)
from agents.exceptions import MaxTurnsExceeded
from agents.tracing import trace as agents_trace
from chatkit.agents import stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import (
    Action,
    AssistantMessageContent,
    AssistantMessageItem,
    ClientEffectEvent,
    ThreadItemDoneEvent,
    ThreadMetadata,
    ThreadStreamEvent,
    UserMessageItem,
    WidgetItem,
    ProgressUpdateEvent,
)
from chatkit.store import NotFoundError

from ecommerce.context import EcommerceAgentChatContext, EcommerceAgentContext, create_initial_context, public_context
from ecommerce.agents import (
    after_sales_agent,
    faq_agent,
    logistics_agent,
    order_agent,
    triage_agent,
)
from ecommerce.approvals import approval_store
from ecommerce.attachments import InMemoryAttachmentStore
from ecommerce.summary_agent import CaseSummary, case_summary_agent
from ecommerce.vision import describe_image
from memory_store import MemoryStore


class AgentEvent(BaseModel):
    id: str
    type: str
    agent: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[float] = None


class GuardrailCheck(BaseModel):
    id: str
    name: str
    input: str
    reasoning: str
    passed: bool
    timestamp: float


def _get_agent_by_name(name: str):
    """Return the agent object by name."""
    agents = {
        triage_agent.name: triage_agent,
        faq_agent.name: faq_agent,
        order_agent.name: order_agent,
        logistics_agent.name: logistics_agent,
        after_sales_agent.name: after_sales_agent,
    }
    return agents.get(name, triage_agent)


def _get_guardrail_name(g) -> str:
    """Extract a friendly guardrail name."""
    name_attr = getattr(g, "name", None)
    if isinstance(name_attr, str) and name_attr:
        return name_attr
    guard_fn = getattr(g, "guardrail_function", None)
    if guard_fn is not None and hasattr(guard_fn, "__name__"):
        return guard_fn.__name__.replace("_", " ").title()
    fn_name = getattr(g, "__name__", None)
    if isinstance(fn_name, str) and fn_name:
        return fn_name.replace("_", " ").title()
    return str(g)


def _build_agents_list() -> List[Dict[str, Any]]:
    """Build a list of all available agents and their metadata."""

    def make_agent_dict(agent):
        return {
            "name": agent.name,
            "description": getattr(agent, "handoff_description", ""),
            "handoffs": [getattr(h, "agent_name", getattr(h, "name", "")) for h in getattr(agent, "handoffs", [])],
            "tools": [getattr(t, "name", getattr(t, "__name__", "")) for t in getattr(agent, "tools", [])],
            "input_guardrails": [_get_guardrail_name(g) for g in getattr(agent, "input_guardrails", [])],
        }

    return [
        make_agent_dict(triage_agent),
        make_agent_dict(faq_agent),
        make_agent_dict(order_agent),
        make_agent_dict(logistics_agent),
        make_agent_dict(after_sales_agent),
    ]


def _user_message_to_text(message: UserMessageItem) -> str:
    parts: List[str] = []
    for part in message.content:
        text = getattr(part, "text", "")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _parse_tool_args(raw_args: Any) -> Any:
    if isinstance(raw_args, str):
        try:
            import json

            return json.loads(raw_args)
        except Exception:
            return raw_args
    return raw_args


@dataclass
class ConversationState:
    context: EcommerceAgentContext = field(default_factory=create_initial_context)
    current_agent_name: str = triage_agent.name
    events: List[AgentEvent] = field(default_factory=list)
    guardrails: List[GuardrailCheck] = field(default_factory=list)
    # Populated by case_summary_agent after each successful turn (structured
    # output). None until the first summary lands. We keep the latest only;
    # the SDK rebuilds it from session history each turn.
    summary: Optional[CaseSummary] = None


SESSION_DB_PATH = Path(__file__).with_name(".agent_sessions.db")


class EcommerceChatKitServer(ChatKitServer[dict[str, Any]]):
    def __init__(self) -> None:
        self.store = MemoryStore()
        self.attachment_store = InMemoryAttachmentStore()
        super().__init__(self.store, attachment_store=self.attachment_store)
        self._state: Dict[str, ConversationState] = {}
        self._listeners: Dict[str, list[asyncio.Queue]] = {}
        self._last_event_index: Dict[str, int] = {}
        self._last_snapshot: Dict[str, str] = {}
        # Anchor fire-and-forget summary tasks so the event loop doesn't GC
        # them mid-flight. Tasks self-remove from this set on completion.
        self._summary_tasks: set[asyncio.Task] = set()

    def _session_for_thread(self, thread_id: str) -> SQLiteSession:
        return SQLiteSession(thread_id, db_path=SESSION_DB_PATH)

    async def _refresh_summary(self, thread: ThreadMetadata) -> None:
        """Compute a fresh CaseSummary from the session transcript and stash it.

        Fire-and-forget — runs after each assistant turn so the chat SSE isn't
        delayed. Failure is non-fatal (the demo just shows the previous
        summary, or 'no summary' if none yet).
        """
        try:
            sess = self._session_for_thread(thread.id)
            items = await sess.get_items()
            if not items:
                return
            # Flatten into a transcript the summary agent can read directly.
            lines: List[str] = []
            for it in items[-20:]:  # last 20 items is plenty for triage
                role = it.get("role") or it.get("type") or "?"
                content = it.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                if isinstance(content, str) and content.strip():
                    lines.append(f"{role}: {content[:300]}")
            if not lines:
                return
            transcript = "\n".join(lines)
            result = await Runner.run(case_summary_agent, transcript)
            if isinstance(result.final_output, CaseSummary):
                state = self._state_for_thread(thread.id)
                state.summary = result.final_output
                # Broadcast through the state stream so the panel updates live.
                await self._broadcast_state(thread, {})
        except Exception:
            # Fail-quiet — summary is a side observation, not a correctness path.
            pass

    def _kick_summary(self, thread: ThreadMetadata) -> None:
        """Schedule a fire-and-forget summary refresh, anchoring the task."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._refresh_summary(thread))
        self._summary_tasks.add(task)
        task.add_done_callback(self._summary_tasks.discard)

    def _state_for_thread(self, thread_id: str) -> ConversationState:
        if thread_id not in self._state:
            self._state[thread_id] = ConversationState()
        return self._state[thread_id]

    async def _ensure_thread(
        self, thread_id: Optional[str], context: dict[str, Any]
    ) -> ThreadMetadata:
        if thread_id:
            try:
                return await self.store.load_thread(thread_id, context)
            except NotFoundError:
                pass
        new_thread = ThreadMetadata(id=self.store.generate_thread_id(context), created_at=datetime.now())
        await self.store.save_thread(new_thread, context)
        self._state_for_thread(new_thread.id)
        return new_thread

    async def ensure_thread(self, thread_id: Optional[str], context: dict[str, Any]) -> ThreadMetadata:
        """Public wrapper to ensure a thread exists."""
        return await self._ensure_thread(thread_id, context)

    def _record_guardrails(
        self,
        agent_name: str,
        input_text: str,
        guardrail_results: List[Any],
    ) -> List[GuardrailCheck]:
        checks: List[GuardrailCheck] = []
        timestamp = time.time() * 1000
        agent = _get_agent_by_name(agent_name)
        for guardrail in getattr(agent, "input_guardrails", []):
            result = next((r for r in guardrail_results if r.guardrail == guardrail), None)
            reasoning = ""
            passed = True
            if result:
                info = getattr(result.output, "output_info", None)
                reasoning = getattr(info, "reasoning", "") or reasoning
                passed = not result.output.tripwire_triggered
            checks.append(
                GuardrailCheck(
                    id=uuid4().hex,
                    name=_get_guardrail_name(guardrail),
                    input=input_text,
                    reasoning=reasoning,
                    passed=passed,
                    timestamp=timestamp,
                )
            )
        return checks

    @staticmethod
    def _truncate(val: Any, limit: int = 200) -> Any:
        if isinstance(val, str) and len(val) > limit:
            return val[:limit] + "…"
        return val

    async def _broadcast_delta(self, thread: ThreadMetadata, delta_events: list[AgentEvent]) -> None:
        """Send a delta-only payload (used for transient progress updates)."""
        listeners = self._listeners.get(thread.id, [])
        if not listeners:
            return
        payload = json.dumps({"events_delta": [e.model_dump() for e in delta_events]}, default=str)
        for q in list(listeners):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _record_events(
        self,
        run_items: List[Any],
        current_agent_name: str,
        thread_id: str,
    ) -> tuple[List[AgentEvent], str]:
        events: List[AgentEvent] = []
        active_agent = current_agent_name
        for item in run_items:
            now_ms = time.time() * 1000
            if isinstance(item, MessageOutputItem):
                text = self._truncate(ItemHelpers.text_message_output(item))
                events.append(
                    AgentEvent(
                        id=uuid4().hex,
                        type="message",
                        agent=item.agent.name,
                        content=text,
                        timestamp=now_ms,
                    )
                )
            elif isinstance(item, HandoffOutputItem):
                events.append(
                    AgentEvent(
                        id=uuid4().hex,
                        type="handoff",
                        agent=item.source_agent.name,
                        content=f"{item.source_agent.name} -> {item.target_agent.name}",
                        metadata={"source_agent": item.source_agent.name, "target_agent": item.target_agent.name},
                        timestamp=now_ms,
                    )
                )

                from_agent = item.source_agent
                to_agent = item.target_agent
                ho = next(
                    (
                        h
                        for h in getattr(from_agent, "handoffs", [])
                        if isinstance(h, Handoff) and getattr(h, "agent_name", None) == to_agent.name
                    ),
                    None,
                )
                if ho:
                    fn = ho.on_invoke_handoff
                    fv = fn.__code__.co_freevars
                    cl = fn.__closure__ or []
                    if "on_handoff" in fv:
                        idx = fv.index("on_handoff")
                        if idx < len(cl) and cl[idx].cell_contents:
                            cb = cl[idx].cell_contents
                            cb_name = getattr(cb, "__name__", repr(cb))
                            events.append(
                                AgentEvent(
                                    id=uuid4().hex,
                                    type="tool_call",
                                    agent=to_agent.name,
                                    content=cb_name,
                                    timestamp=now_ms,
                                )
                            )

                active_agent = to_agent.name
            elif isinstance(item, ToolCallItem):
                tool_name = getattr(item.raw_item, "name", None)
                raw_args = getattr(item.raw_item, "arguments", None)
                ev = AgentEvent(
                    id=uuid4().hex,
                    type="tool_call",
                    agent=item.agent.name,
                    content=self._truncate(tool_name or ""),
                    metadata={"tool_args": self._truncate(_parse_tool_args(raw_args))},
                    timestamp=now_ms,
                )
                events.append(ev)
            elif isinstance(item, ToolCallOutputItem):
                ev = AgentEvent(
                    id=uuid4().hex,
                    type="tool_output",
                    agent=item.agent.name,
                    content=self._truncate(str(item.output)),
                    metadata={"tool_result": self._truncate(item.output)},
                    timestamp=now_ms,
                )
                events.append(ev)

        return events, active_agent

    async def _describe_attachments(self, message: UserMessageItem) -> str:
        """Describe any image attachments via 百炼 and return an injectable note."""
        notes: List[str] = []
        for att in getattr(message, "attachments", []) or []:
            if getattr(att, "type", None) != "image":
                continue
            rec = self.attachment_store.get_bytes(att.id)
            if rec is None:
                continue
            data, mime = rec
            desc = await describe_image(data, mime)
            if desc:
                notes.append(desc)
        if not notes:
            return ""
        return f"[用户上传了图片，图片内容：{' '.join(notes)}]"

    async def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        state = self._state_for_thread(thread.id)
        user_text = ""
        run_input: List[Dict[str, Any]] = []
        if input_user_message is not None:
            user_text = _user_message_to_text(input_user_message)
            # Perception step: if the user attached an image, describe it on 百炼
            # (fast) and inject the description as text. The gpt-5.5 agents and
            # guardrails then run text-only on that description.
            image_note = await self._describe_attachments(input_user_message)
            combined = user_text
            if image_note:
                combined = f"{user_text}\n\n{image_note}" if user_text else image_note
            run_input = [{"content": combined, "role": "user"}]

        previous_context = public_context(state.context)
        chat_context = EcommerceAgentChatContext(
            thread=thread,
            store=self.store,
            request_context=context,
            state=state.context,
        )
        streamed_items_seen = 0

        # Tell the client which thread to bind runner updates to before streaming starts.
        yield ClientEffectEvent(name="runner_bind_thread", data={"thread_id": thread.id, "ts": time.time()})

        # Wrap the run in a trace so all SDK-generated spans (agent / LLM /
        # tool / guardrail / handoff) are attached and tagged with thread_id —
        # the local SQLiteTraceProcessor uses that to populate /traces.
        # We `__enter__` here and explicitly `__exit__` at every return path
        # below to avoid re-indenting the whole streaming loop.
        trace_ctx = agents_trace(
            workflow_name=f"ecommerce/{state.current_agent_name}",
            metadata={"thread_id": thread.id},
        )
        trace_ctx.__enter__()
        try:
            result = Runner.run_streamed(
                _get_agent_by_name(state.current_agent_name),
                run_input,
                context=chat_context,
                session=self._session_for_thread(thread.id),
            )
            # chatkit-agents stamps every AssistantMessageItem with placeholder id '__fake_id__';
            # ChatKit client keys messages by id, so duplicates collapse into the same DOM node
            # (visible as the latest assistant reply overwriting the previous one). Replace it with
            # a unique store-generated id, kept consistent across Added -> Updated* -> Done for one message.
            _fake_id_replacement: Optional[str] = None
            async for event in stream_agent_response(chat_context, result):
                _it = getattr(event, "item", None)
                if _it is not None and getattr(_it, "id", None) == "__fake_id__":
                    if type(event).__name__ == "ThreadItemAddedEvent" or _fake_id_replacement is None:
                        _fake_id_replacement = self.store.generate_item_id("message", thread, context)
                    try:
                        _it.id = _fake_id_replacement
                    except Exception:
                        pass
                if isinstance(event, ProgressUpdateEvent) or getattr(event, "type", "") == "progress_update_event":
                    # Ignore progress updates for the Runner panel; ChatKit will handle them separately.
                    continue
                # Per-event live preview: turn the SDK item into our AgentEvent
                # only for the UI broadcast — do NOT extend state.events here,
                # the `result.new_items` block below is the single source of
                # truth (writing in both branches double-counts events in the
                # Runner panel).
                if hasattr(event, "item"):
                    try:
                        run_item = getattr(event, "item")
                        preview_events, _ = self._record_events(
                            [run_item], state.current_agent_name, thread.id
                        )
                        if preview_events:
                            yield ClientEffectEvent(
                                name="runner_event_delta",
                                data={
                                    "thread_id": thread.id,
                                    "ts": time.time(),
                                    "events": [e.model_dump() for e in preview_events],
                                },
                            )
                    except Exception:
                        import traceback as _tb
                        _tb.print_exc()
                yield event
                new_items = result.new_items[streamed_items_seen:]
                if new_items:
                    new_events, active_agent = self._record_events(
                        new_items, state.current_agent_name, thread.id
                    )
                    state.events.extend(new_events)
                    state.current_agent_name = active_agent
                    streamed_items_seen += len(new_items)
                    await self._broadcast_state(thread, context)
                    yield ClientEffectEvent(
                        name="runner_state_update",
                        data={"thread_id": thread.id, "ts": time.time()},
                    )
                    yield ClientEffectEvent(
                        name="runner_event_delta",
                        data={
                            "thread_id": thread.id,
                            "ts": time.time(),
                            "events": [e.model_dump() for e in new_events],
                        },
                    )
        except MaxTurnsExceeded:
            await self._broadcast_state(thread, context)
            trace_ctx.__exit__(None, None, None)
            return
        except InputGuardrailTripwireTriggered as exc:
            failed_guardrail = exc.guardrail_result.guardrail
            gr_output = exc.guardrail_result.output.output_info
            reasoning = getattr(gr_output, "reasoning", "")
            timestamp = time.time() * 1000
            checks: List[GuardrailCheck] = []
            for guardrail in _get_agent_by_name(state.current_agent_name).input_guardrails:
                checks.append(
                    GuardrailCheck(
                        id=uuid4().hex,
                        name=_get_guardrail_name(guardrail),
                        input=user_text,
                        reasoning=reasoning if guardrail == failed_guardrail else "",
                        passed=guardrail != failed_guardrail,
                        timestamp=timestamp,
                    )
                )
            state.guardrails = checks
            refusal = "抱歉，我只能回答京东购物相关的问题。"
            await self._session_for_thread(thread.id).add_items(
                [*run_input, {"role": "assistant", "content": refusal}]
            )
            yield ThreadItemDoneEvent(
                item=AssistantMessageItem(
                    id=self.store.generate_item_id("message", thread, context),
                    thread_id=thread.id,
                    created_at=datetime.now(),
                    content=[AssistantMessageContent(text=refusal)],
                )
            )
            trace_ctx.__exit__(None, None, None)
            return
        except OutputGuardrailTripwireTriggered as exc:
            # The assistant's draft answer hit an output guardrail (PII / false
            # promise / brand). Pull the guardrail's `output_info` and rewrite
            # the reply: PII → masked text, others → a safe boilerplate.
            info = getattr(exc.guardrail_result.output, "output_info", None)
            masked = getattr(info, "masked_output", None)
            safe_reply = (
                masked
                if isinstance(masked, str) and masked.strip()
                else "抱歉，回复内容触发了出口合规审查，请换一种问法再试。"
            )
            await self._session_for_thread(thread.id).add_items(
                [*run_input, {"role": "assistant", "content": safe_reply}]
            )
            yield ThreadItemDoneEvent(
                item=AssistantMessageItem(
                    id=self.store.generate_item_id("message", thread, context),
                    thread_id=thread.id,
                    created_at=datetime.now(),
                    content=[AssistantMessageContent(text=safe_reply)],
                )
            )
            trace_ctx.__exit__(None, None, None)
            return
        remaining_items = result.new_items[streamed_items_seen:]
        new_events, active_agent = self._record_events(remaining_items, state.current_agent_name, thread.id)
        state.events.extend(new_events)
        final_agent_name = active_agent
        try:
            final_agent_name = result.last_agent.name
        except Exception:
            pass
        state.current_agent_name = final_agent_name
        state.guardrails = self._record_guardrails(
            agent_name=state.current_agent_name,
            input_text=user_text,
            guardrail_results=result.input_guardrail_results,
        )

        new_context = public_context(state.context)
        changes = {k: new_context[k] for k in new_context if previous_context.get(k) != new_context[k]}
        if changes:
            state.events.append(
                AgentEvent(
                    id=uuid4().hex,
                    type="context_update",
                    agent=state.current_agent_name,
                    content="",
                    metadata={"changes": changes},
                    timestamp=time.time() * 1000,
                )
            )
        await self._broadcast_state(thread, context)
        yield ClientEffectEvent(
            name="runner_state_update",
            data={"thread_id": thread.id, "ts": time.time()},
        )
        if new_events:
            yield ClientEffectEvent(
                name="runner_event_delta",
                data={
                    "thread_id": thread.id,
                    "ts": time.time(),
                    "events": [e.model_dump() for e in new_events],
                },
            )
        # SDK-native HITL: tools decorated with needs_approval=True pause the
        # Runner before the call runs. respond() persists the RunState, blocks
        # on the operator's Approve/Reject in the panel, then resumes a fresh
        # streamed run with the decision applied. The resume's assistant
        # message lands as an additional bubble in the same SSE.
        interruptions = list(getattr(result, "interruptions", None) or [])
        if interruptions:
            trace_ctx.__exit__(None, None, None)
            async for ev in self._handle_interruptions(
                result, interruptions, thread, context, chat_context, state
            ):
                yield ev
        else:
            trace_ctx.__exit__(None, None, None)
        # Side-channel: refresh the structured CaseSummary off the critical
        # path; UI receives it via the state stream when ready.
        self._kick_summary(thread)

    async def _handle_interruptions(
        self,
        result: Any,
        interruptions: List[Any],
        thread: ThreadMetadata,
        context: dict[str, Any],
        chat_context: EcommerceAgentChatContext,
        conv_state: ConversationState,
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Block the SSE on operator approval, then resume the paused run.

        Pattern: persist the RunState into ApprovalStore, register an
        asyncio.Event per pending approval, await with a heartbeat, apply
        state.approve / state.reject, then run a fresh Runner.run_streamed
        with the patched state and yield its events into this same SSE.
        """
        run_state = result.to_state()
        state_json = run_state.to_json()

        # Build approval rows for the UI and store the state alongside.
        waiters: list[tuple[asyncio.Event, str, Any]] = []
        for item in interruptions:
            tool_name = getattr(item, "tool_name", None) or "unknown"
            args = self._extract_approval_args(item)
            summary = self._compose_approval_summary(tool_name, args)
            kind = self._approval_kind_for_tool(tool_name)
            req = approval_store.create(
                thread_id=thread.id,
                kind=kind,
                tool_name=tool_name,
                args=args,
                summary=summary,
            )
            call_id = (
                getattr(getattr(item, "raw_item", None), "call_id", None)
                or getattr(getattr(item, "raw_item", None), "id", None)
                or req.id
            )
            approval_store.attach_run_state(
                req.id, run_state_json=state_json, approval_call_id=call_id
            )
            waiters.append((approval_store.register_waiter(req.id), req.id, item))

        # Push the approvals panel update.
        await self._broadcast_state(thread, context)
        yield ClientEffectEvent(
            name="runner_state_update",
            data={"thread_id": thread.id, "ts": time.time(), "awaiting_approval": True},
        )

        # Await each decision with a 5-minute hard cap; in-between, send a
        # heartbeat every ~20s so intermediaries don't close the SSE.
        timeout_s = 300.0
        deadline = asyncio.get_running_loop().time() + timeout_s
        loop = asyncio.get_running_loop()
        for event, approval_id, item in waiters:
            while True:
                remaining = max(0.0, deadline - loop.time())
                try:
                    await asyncio.wait_for(event.wait(), timeout=min(20.0, remaining))
                    break
                except asyncio.TimeoutError:
                    if remaining <= 0:
                        approval_store.decide(
                            approval_id, decision="rejected", note="approval timeout"
                        )
                        break
                    yield ClientEffectEvent(
                        name="runner_state_update",
                        data={
                            "thread_id": thread.id,
                            "ts": time.time(),
                            "awaiting_approval": True,
                            "heartbeat": True,
                        },
                    )
            approval_store.clear_waiter(approval_id)
            decided = approval_store.get(approval_id)
            if decided and decided.status == "approved":
                # always_approve=True so the resume doesn't pause again on
                # the same tool name if the model decides to re-invoke it
                # in this turn (it often does for self-confirmation).
                run_state.approve(item, always_approve=True)
            else:
                run_state.reject(
                    item,
                    always_reject=True,
                    rejection_message=(decided.operator_note if decided else None),
                )

        # Resume. The model may re-invoke an approved tool with a new
        # call_id (always_approve isn't enforced inside the same Runner
        # turn for fresh call ids), so we loop the streamed run, auto-
        # approving any further request_refund_tool pauses up to a cap to
        # avoid runaway loops.
        resume_trace = agents_trace(
            workflow_name=f"ecommerce/{conv_state.current_agent_name}/resume",
            metadata={"thread_id": thread.id},
        )
        resume_trace.__enter__()
        try:
            next_input: Any = run_state
            for resume_pass in range(4):  # hard cap on auto-resumes
                # IMPORTANT: when input is a RunState, do NOT pass context=
                # again — Runner would wrap chat_context in a *fresh* wrapper
                # and discard the approval decisions we just stamped onto the
                # state's existing wrapper. Without context=, SDK reuses the
                # state's wrapper as-is.
                resume_result = Runner.run_streamed(
                    _get_agent_by_name(conv_state.current_agent_name),
                    next_input,
                    session=self._session_for_thread(thread.id),
                )
                _fake_id_replacement: Optional[str] = None
                async for ev in stream_agent_response(chat_context, resume_result):
                    _it = getattr(ev, "item", None)
                    if _it is not None and getattr(_it, "id", None) == "__fake_id__":
                        if (
                            type(ev).__name__ == "ThreadItemAddedEvent"
                            or _fake_id_replacement is None
                        ):
                            _fake_id_replacement = self.store.generate_item_id(
                                "message", thread, context
                            )
                        try:
                            _it.id = _fake_id_replacement
                        except Exception:
                            pass
                    if isinstance(ev, ProgressUpdateEvent) or getattr(ev, "type", "") == "progress_update_event":
                        continue
                    yield ev

                resume_events, resume_active = self._record_events(
                    resume_result.new_items, conv_state.current_agent_name, thread.id
                )
                conv_state.events.extend(resume_events)
                conv_state.current_agent_name = resume_active
                try:
                    conv_state.current_agent_name = resume_result.last_agent.name
                except Exception:
                    pass
                await self._broadcast_state(thread, context)
                yield ClientEffectEvent(
                    name="runner_state_update",
                    data={"thread_id": thread.id, "ts": time.time()},
                )
                if resume_events:
                    yield ClientEffectEvent(
                        name="runner_event_delta",
                        data={
                            "thread_id": thread.id,
                            "ts": time.time(),
                            "events": [e.model_dump() for e in resume_events],
                        },
                    )

                # stream_agent_response only emits a finalized
                # AssistantMessageItem when the run completes normally; when
                # it stops on an interruption the assistant text sitting in
                # new_items never reaches ChatKit. Surface the last
                # MessageOutputItem of this pass manually so the chat keeps
                # making forward progress.
                last_text = ""
                for item in reversed(resume_result.new_items):
                    if isinstance(item, MessageOutputItem):
                        last_text = ItemHelpers.text_message_output(item) or ""
                        if last_text.strip():
                            break
                if last_text.strip():
                    yield ThreadItemDoneEvent(
                        item=AssistantMessageItem(
                            id=self.store.generate_item_id("message", thread, context),
                            thread_id=thread.id,
                            created_at=datetime.now(),
                            content=[AssistantMessageContent(text=last_text)],
                        )
                    )
                    await self._session_for_thread(thread.id).add_items(
                        [{"role": "assistant", "content": last_text}]
                    )

                follow_up = list(getattr(resume_result, "interruptions", None) or [])
                if not follow_up:
                    break
                # Auto-approve subsequent same-turn pauses (the operator
                # already said "yes" once for this conversation). Each pass
                # builds a fresh RunState from the latest result.
                next_state = resume_result.to_state()
                for it in follow_up:
                    next_state.approve(it, always_approve=True)
                next_input = next_state
        finally:
            resume_trace.__exit__(None, None, None)

    @staticmethod
    def _extract_approval_args(item: Any) -> dict[str, Any]:
        """Best-effort decode of the tool args sitting inside ToolApprovalItem.

        OpenAI chat-completions tool calls carry a JSON string in
        raw_item.arguments; Responses-API native carries a dict. Fall back
        to a flat repr if neither shape matches."""
        raw = getattr(item, "raw_item", None)
        args = getattr(raw, "arguments", None)
        if isinstance(args, str):
            try:
                return json.loads(args)
            except Exception:
                return {"raw": args}
        if isinstance(args, dict):
            return args
        return {"raw": repr(raw)}

    @staticmethod
    def _compose_approval_summary(tool_name: str, args: dict[str, Any]) -> str:
        """One-line Chinese summary for the operator panel."""
        if tool_name == "request_refund_tool":
            return (
                f"订单 {args.get('order_id', '?')} 退款申请，"
                f"金额 {args.get('amount') or '原支付金额'}，"
                f"原因：{args.get('reason', '?')}"
            )
        return f"{tool_name} {args}"

    @staticmethod
    def _approval_kind_for_tool(tool_name: str) -> str:
        if tool_name == "request_refund_tool":
            return "refund"
        if tool_name == "request_order_cancel_tool":
            return "order_cancel"
        if tool_name == "request_price_protection_tool":
            return "price_protection"
        return "refund"

    async def action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: WidgetItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        # No client-handled actions in this demo.
        if False:
            yield

    async def snapshot(self, thread_id: Optional[str], context: dict[str, Any]) -> Dict[str, Any]:
        thread = await self._ensure_thread(thread_id, context)
        state = self._state_for_thread(thread.id)
        return {
            "thread_id": thread.id,
            "current_agent": state.current_agent_name,
            "context": public_context(state.context),
            "agents": _build_agents_list(),
            "events": [e.model_dump() for e in state.events],
            "guardrails": [g.model_dump() for g in state.guardrails],
            "summary": state.summary.model_dump() if state.summary else None,
        }

    # -- Streaming state updates to UI listeners ---------------------------------
    def _register_listener(self, thread_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.setdefault(thread_id, []).append(q)
        # Push last snapshot if available so late listeners get current state immediately.
        last = self._last_snapshot.get(thread_id)
        if last:
            try:
                q.put_nowait(last)
            except asyncio.QueueFull:
                pass
        return q

    def register_listener(self, thread_id: str) -> asyncio.Queue:
        """Public wrapper for listener registration."""
        return self._register_listener(thread_id)

    def _unregister_listener(self, thread_id: str, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(thread_id, [])
        if queue in listeners:
            listeners.remove(queue)
        if not listeners and thread_id in self._listeners:
            self._listeners.pop(thread_id, None)

    def unregister_listener(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Public wrapper for listener cleanup."""
        self._unregister_listener(thread_id, queue)

    async def _broadcast_state(self, thread: ThreadMetadata, context: dict[str, Any]) -> None:
        listeners = self._listeners.get(thread.id, [])
        if not listeners:
            return
        snap = await self.snapshot(thread.id, context)
        # Compute delta of new events since last broadcast to reduce payloads
        last_idx = self._last_event_index.get(thread.id, 0)
        total_events = len(snap.get("events", []))
        delta = snap.get("events", [])[last_idx:] if total_events >= last_idx else snap.get("events", [])
        self._last_event_index[thread.id] = total_events
        payload_obj = {
            **snap,
            "events_delta": delta,
        }
        payload = json.dumps(payload_obj, default=str)
        self._last_snapshot[thread.id] = payload
        for q in list(listeners):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
