"""Structured-output 'case summary' agent.

Runs as a side-effect after each main agent turn. The headline feature being
demonstrated is the SDK's `output_type=PydanticModel` mode: the model is
forced to emit a typed object, parsed and validated before reaching us, so
downstream consumers (Ops dashboards, ticketing systems) get a contract
instead of free-text guessing.

Kept deliberately small — 5 fields cover what an ops console actually needs
to triage a JD CS conversation. Anything richer belongs in a real CRM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents import Agent

SUMMARY_MODEL = "gpt-5.5"

# Intent / sentiment kept as `str` rather than `Literal[...]` because the
# 猫猫小铺 proxy does not strictly enforce JSON-schema enum constraints — the
# model freely produces near-synonyms ('退款流程' vs '退款') and the SDK then
# raises ModelBehaviorError. The system prompt below pins the allowed values.

_INTENTS = "查订单 / 查物流 / 退款 / 取消订单 / 价保 / 售后/维修 / 政策咨询 / 商品咨询 / 投诉 / 其他"
_SENTIMENTS = "满意 / 中立 / 不满 / 投诉"


class CaseSummary(BaseModel):
    """Structured per-thread summary, refreshed after each assistant turn."""

    intent: str = Field(description=f"用户当前主要意图分类，只能从给定值选：{_INTENTS}")
    order_id: str | None = Field(default=None, description="若对话涉及具体订单，给出订单号；否则 null")
    sentiment: str = Field(description=f"客户情绪：{_SENTIMENTS}")
    action_taken: str = Field(description="一句话描述客服在本对话中已经做了什么，不超过 40 字")
    follow_up_needed: bool = Field(default=False, description="是否还需要人工或系统后续介入")
    follow_up_note: str = Field(
        default="",
        description="若 follow_up_needed=true，写明需要做什么；否则空字符串",
    )


case_summary_agent = Agent(
    name="Case Summary",
    model=SUMMARY_MODEL,
    instructions=(
        "你是京东客服会话摘要助手。根据给定的客服对话历史，输出结构化 JSON。\n"
        "严格只输出 JSON，不要任何额外解释或代码块标记。\n"
        f"intent 必须从这些值中选一个：{_INTENTS}。\n"
        f"sentiment 必须从这些值中选一个：{_SENTIMENTS}。\n"
        "action_taken：一句中文，不超过 40 字，描述客服已做的事。\n"
        "follow_up_needed 必填，true 表示需要人工/系统继续跟进。\n"
        "follow_up_note：若 follow_up_needed=true，写明跟进事项；否则空字符串。\n"
        "如果对话尚未涉及具体订单，order_id 留 null。\n"
        "**所有字段都要给出，包括 follow_up_needed 和 follow_up_note。**"
    ),
    output_type=CaseSummary,
)
