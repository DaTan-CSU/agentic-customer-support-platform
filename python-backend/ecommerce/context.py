from __future__ import annotations as _annotations

from chatkit.agents import AgentContext
from pydantic import BaseModel


class EcommerceAgentContext(BaseModel):
    """Context for JD e-commerce customer service agents."""

    user_name: str | None = None
    user_id: str | None = None
    order_id: str | None = None
    sku: str | None = None
    after_sales_case_id: str | None = None
    last_intent: str | None = None
    scenario: str | None = None


class EcommerceAgentChatContext(AgentContext[dict]):
    """
    AgentContext wrapper used during ChatKit runs.
    Holds the persisted EcommerceAgentContext in `state`.
    """

    state: EcommerceAgentContext


def create_initial_context() -> EcommerceAgentContext:
    """
    Factory for a new EcommerceAgentContext.
    Seeds a demo user so the Conversation Context panel isn't all-null on
    a fresh session. Real fields (order_id / sku / after_sales_case_id /
    last_intent) get populated by tools as the user interacts.
    """
    return EcommerceAgentContext(
        user_name="demo 客户",
        user_id="JD888888",
    )


def public_context(ctx: EcommerceAgentContext) -> dict:
    """
    Return a filtered view of the context for UI display.
    Hides internal fields like scenario.
    """
    data = ctx.model_dump()
    hidden_keys = {"scenario"}
    for key in list(data.keys()):
        if key in hidden_keys:
            data.pop(key, None)
    return data
