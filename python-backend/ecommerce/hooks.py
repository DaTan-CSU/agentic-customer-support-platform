"""Centralised AgentHooks — one place for audit-log lines that previously
lived scattered across server.respond. Subclassing AgentHooks keeps tool
calls / handoffs / LLM round-trips observable without coupling business
agents to logging logic.

The hook prints structured single-line `[audit] field=value ...` records to
stdout. Same channel as `[HANDOFF]` / `[runner-error]`, so an operator can
`grep audit` over the uvicorn log to reconstruct any conversation turn.
"""
from __future__ import annotations

import time
from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper

from .context import EcommerceAgentChatContext


def _thread_id(context: RunContextWrapper[EcommerceAgentChatContext]) -> str:
    thread = getattr(context.context, "thread", None)
    return getattr(thread, "id", None) or "thr_unknown"


def _emit(kind: str, **fields: Any) -> None:
    parts = [f"{k}={v!r}" for k, v in fields.items() if v is not None]
    print(f"[audit] {kind} {' '.join(parts)}", flush=True)


class EcommerceAuditHooks(AgentHooks[EcommerceAgentChatContext]):
    """Single audit-log sink for all front-facing agents."""

    async def on_start(self, context, agent: Agent) -> None:
        _emit(
            "agent_start",
            thread=_thread_id(context),
            agent=agent.name,
            ts=int(time.time() * 1000),
        )

    async def on_end(self, context, agent: Agent, output: Any) -> None:
        text = output if isinstance(output, str) else str(output)
        _emit(
            "agent_end",
            thread=_thread_id(context),
            agent=agent.name,
            output_len=len(text or ""),
        )

    async def on_handoff(self, context, agent: Agent, source: Agent) -> None:
        _emit(
            "handoff",
            thread=_thread_id(context),
            source=source.name,
            target=agent.name,
        )

    async def on_tool_start(self, context, agent: Agent, tool) -> None:
        _emit(
            "tool_start",
            thread=_thread_id(context),
            agent=agent.name,
            tool=getattr(tool, "name", str(tool)),
            args=getattr(context, "tool_arguments", None),
        )

    async def on_tool_end(self, context, agent: Agent, tool, result: str) -> None:
        _emit(
            "tool_end",
            thread=_thread_id(context),
            agent=agent.name,
            tool=getattr(tool, "name", str(tool)),
            result_len=len(result or ""),
        )


audit_hooks = EcommerceAuditHooks()
