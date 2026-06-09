from __future__ import annotations as _annotations

from agents import Agent, RunContextWrapper, handoff
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

from .context import EcommerceAgentChatContext
from .guardrails import jailbreak_guardrail, relevance_guardrail
from .output_guardrails import OUTPUT_GUARDRAILS
from .tools import (
    query_after_sales_tool,
    query_logistics_tool,
    query_order_tool,
    request_order_cancel_tool,
    request_price_protection_tool,
    request_refund_tool,
    search_policy,
)

MODEL = "gpt-5.5"

# Every front-facing agent runs the same input guardrails. Because the active
# agent persists across turns (the user's next message goes straight to the
# current specialist, not back through triage), the guardrails must be on all
# of them to consistently catch off-topic / jailbreak input.
_INPUT_GUARDRAILS = [relevance_guardrail, jailbreak_guardrail]


def triage_instructions(
    run_context: RunContextWrapper[EcommerceAgentChatContext], agent: Agent[EcommerceAgentChatContext]
) -> str:
    ctx = run_context.context.state
    order_id = ctx.order_id or "[未知订单]"
    return (
        f"{RECOMMENDED_PROMPT_PREFIX}\n"
        "你是京东电商客服的分诊客服。请用中文与用户沟通，判断用户意图。\n"
        f"当前上下文订单号：{order_id}。\n"
        "**两种处理方式，二选一：**\n"
        "1) **单一意图** → 用 handoff 把会话交接给对应专员（transfer_to_order / transfer_to_logistics / transfer_to_after_sales / transfer_to_faq）。\n"
        "   路由：订单状态/金额/商品明细→订单；配送进度/运单/延迟→物流；退货/退款/价保/维修→售后；政策/常见问题/商品知识→FAQ。\n"
        "2) **复合多意图**（用户一句话同时问 2 个或以上不同业务，例如 '查订单状态，物流到哪，能不能退款'）→ **不要 handoff**，"
        "改用 ask_order_specialist / ask_logistics_specialist / ask_after_sales_specialist 工具调用对应专员，每个 input 用中文描述要查什么 + 订单号。"
        "把各专员回的结果整合成一条简洁回复给用户。\n"
        "用户可能上传图片，图片内容会以「[用户上传了图片，图片内容：…]」形式出现，请据此判断意图。\n"
        "**规则**：每条消息只能选 1 或 2 中的一种。复合意图最多并行调 3 个专员 tool，每个 tool 最多调 1 次，不要重复调同一个专员。\n"
        "如果意图清楚，立即执行，不要反问确认。"
    )


triage_agent = Agent[EcommerceAgentChatContext](
    name="分诊客服",
    model=MODEL,
    handoff_description="识别京东购物咨询意图，并转交给订单、物流、售后或 FAQ 专员。",
    instructions=triage_instructions,
    tools=[],
    handoffs=[],
    input_guardrails=_INPUT_GUARDRAILS,
    output_guardrails=OUTPUT_GUARDRAILS,
)


def order_instructions(
    run_context: RunContextWrapper[EcommerceAgentChatContext], agent: Agent[EcommerceAgentChatContext]
) -> str:
    ctx = run_context.context.state
    order_id = ctx.order_id or "[未知订单]"
    return (
        f"{RECOMMENDED_PROMPT_PREFIX}\n"
        "你是京东订单查询客服。你只处理订单基础信息、状态、金额、支付和商品明细。\n"
        f"当前上下文订单号：{order_id}。\n"
        "用户提供订单号时，立即调用 query_order_tool。上下文已有订单号且用户继续追问该订单时，直接使用该订单号。"
        "如果用户明确表示要取消订单，调用 request_order_cancel_tool 提交人工审批申请（**不要直接说订单已取消**）。"
        "如果用户转而询问物流，请最多 handoff 一次给物流查询客服；其他主题交回分诊客服。"
        "工具调用完成后，用简洁中文回答。每条消息最多发起一次 handoff。"
    )


order_agent = Agent[EcommerceAgentChatContext](
    name="订单查询客服",
    model=MODEL,
    handoff_description="查询京东订单状态、金额、支付方式和商品明细。",
    instructions=order_instructions,
    tools=[query_order_tool, request_order_cancel_tool],
    input_guardrails=_INPUT_GUARDRAILS,
    # output_guardrails 故意省略：本 agent 既被 handoff 也被 as_tool 调用。
    # 作为 tool 时其输出是分诊客服的中间材料，没必要单独跑 3 道 LLM 护栏；
    # 终态回复由分诊客服那一道 output guardrails 把关。
)


def logistics_instructions(
    run_context: RunContextWrapper[EcommerceAgentChatContext], agent: Agent[EcommerceAgentChatContext]
) -> str:
    ctx = run_context.context.state
    order_id = ctx.order_id or "[未知订单]"
    return (
        f"{RECOMMENDED_PROMPT_PREFIX}\n"
        "你是京东物流查询客服。你负责配送进度、承运方、运单号、预计送达和延迟说明。\n"
        f"当前上下文订单号：{order_id}。\n"
        "用户提供订单号时，立即调用 query_logistics_tool。上下文已有订单号且用户继续追问物流时，直接使用该订单号。"
        "如果用户询问订单商品或金额，请最多 handoff 一次给订单查询客服；其他主题交回分诊客服。"
        "工具调用完成后，用简洁中文回答。每条消息最多发起一次 handoff。"
    )


logistics_agent = Agent[EcommerceAgentChatContext](
    name="物流查询客服",
    model=MODEL,
    handoff_description="查询京东订单物流轨迹、承运方、运单号和预计送达。",
    instructions=logistics_instructions,
    tools=[query_logistics_tool],
    input_guardrails=_INPUT_GUARDRAILS,
    # 见 order_agent 同样的说明。
)


def after_sales_instructions(
    run_context: RunContextWrapper[EcommerceAgentChatContext], agent: Agent[EcommerceAgentChatContext]
) -> str:
    ctx = run_context.context.state
    order_id = ctx.order_id or "[未知订单]"
    case_id = ctx.after_sales_case_id or "[暂无售后单]"
    return (
        f"{RECOMMENDED_PROMPT_PREFIX}\n"
        "你是京东售后客服，处理退货、换货、退款、价保、维修和售后进度查询。\n"
        f"当前上下文订单号：{order_id}；当前售后单：{case_id}。\n"
        "用户提供订单号时，立即调用 query_after_sales_tool。上下文已有订单号且用户继续追问售后时，直接使用该订单号。"
        "如果用户明确要求退款，调用 request_refund_tool 提交人工审批申请。"
        "如果用户主张价保（看到降价想要退差价），调用 request_price_protection_tool 提交人工审批申请。"
        "**不要直接承诺退款已完成**，应说明已提交申请等待人工审批。"
        "如果用户询问政策规则或资格口径，请最多 handoff 一次给 FAQ 客服；其他主题交回分诊客服。"
        "工具调用完成后，用简洁中文回答。每条消息最多发起一次 handoff。"
    )


after_sales_agent = Agent[EcommerceAgentChatContext](
    name="售后客服",
    model=MODEL,
    handoff_description="查询京东退换货、退款、价保、维修和售后进度。",
    instructions=after_sales_instructions,
    tools=[
        query_after_sales_tool,
        request_refund_tool,
        request_price_protection_tool,
    ],
    input_guardrails=_INPUT_GUARDRAILS,
    # 见 order_agent 同样的说明。
)


faq_agent = Agent[EcommerceAgentChatContext](
    name="FAQ 客服",
    model=MODEL,
    handoff_description="回答京东政策、常见问题和商品知识咨询；活动 / 优惠券 / 配送时效走 MCP 真后端。",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是京东政策、常见问题和商品知识客服。必须遵守：
1. 先识别用户最后一个问题。
2. **优惠券**类问题 → 调用 MCP 工具 get_jd_coupons(category)。
3. **促销 / 活动 / 满减**类问题 → 调用 MCP 工具 get_jd_promotions(category)。
4. **配送时效 / 多久到货**类问题 → 调用 MCP 工具 get_delivery_eta(city, sku)。
5. 其它政策 / 常见问题 / 商品知识 → 必须调用 search_policy 检索，只能依据返回内容作答。
6. 如果检索/工具不到相关信息，礼貌说明未找到，并建议用户换一种问法或提供更具体信息。
7. 如用户需要订单、物流或售后记录查询，最多 handoff 一次给对应专员或分诊客服。""",
    tools=[search_policy],
    input_guardrails=_INPUT_GUARDRAILS,
    output_guardrails=OUTPUT_GUARDRAILS,
)


# Set up handoff relationships.
# Agent names are Chinese (shown in the UI), but the OpenAI SDK derives each
# handoff tool name from agent.name and strips non-ASCII chars to underscores,
# which makes same-length Chinese names collide into one tool. Give every
# handoff an explicit ASCII tool_name_override so the model can target each
# specialist distinctly.
_HANDOFF_TOOL_NAMES = {
    "分诊客服": "transfer_to_triage",
    "订单查询客服": "transfer_to_order",
    "物流查询客服": "transfer_to_logistics",
    "售后客服": "transfer_to_after_sales",
    "FAQ 客服": "transfer_to_faq",
}


def _ho(agent: Agent[EcommerceAgentChatContext]) -> handoff:
    return handoff(agent, tool_name_override=_HANDOFF_TOOL_NAMES[agent.name])


triage_agent.handoffs = [_ho(order_agent), _ho(logistics_agent), _ho(after_sales_agent), _ho(faq_agent)]
order_agent.handoffs = [_ho(logistics_agent), _ho(triage_agent)]
logistics_agent.handoffs = [_ho(order_agent), _ho(triage_agent)]
after_sales_agent.handoffs = [_ho(faq_agent), _ho(triage_agent)]
faq_agent.handoffs = [_ho(triage_agent)]

# -- agents-as-tools (Phase 1 of step 6) -----------------------------------
# Wrap each specialist as a Tool so triage can call them in one turn for
# multi-intent queries (e.g. "查订单 + 看物流 + 能不能退款"), without doing
# the full handoff dance. Both routes coexist: simple single-intent stays on
# handoff (current behavior); multi-intent fans out via these tools.
_ask_order = order_agent.as_tool(
    tool_name="ask_order_specialist",
    tool_description="向订单查询客服询问订单状态/金额/支付/商品明细。参数 input 用中文描述要查什么及订单号。",
)
_ask_logistics = logistics_agent.as_tool(
    tool_name="ask_logistics_specialist",
    tool_description="向物流查询客服询问配送进度/运单/预计送达。参数 input 用中文描述要查什么及订单号。",
)
_ask_after_sales = after_sales_agent.as_tool(
    tool_name="ask_after_sales_specialist",
    tool_description="向售后客服询问退换货/退款/价保/维修/售后进度。参数 input 用中文描述要查什么及订单号。",
)

triage_agent.tools = [_ask_order, _ask_logistics, _ask_after_sales]
