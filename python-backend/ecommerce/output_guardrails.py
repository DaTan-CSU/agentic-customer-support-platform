"""Output-side guardrails: run AFTER the agent's final text is ready,
BEFORE it goes to the user. Tripping rewrites the output to a safer form
in `server.py` instead of just refusing — operators still want users to see
something coherent.

Three rules cover the demo:
  1. PII leak  — regex catches phone (11-digit 1XX...), 18-digit IDs, 16-19
     digit card numbers. Cheap, deterministic, no model call. Tripwire +
     masked rewrite published via output_info.
  2. False promise — LLM judge. Flags absolute commitments like 包赔/一定/
     必须/保证 that exceed JD policy.
  3. Brand neutrality — LLM judge. Flags disparagement of competitors
     (淘宝/天猫/拼多多/苏宁/京东 itself when negative).

LLM judges share the same gpt-5.5 default client. Fail-open: if the judge
errors, we let the original output through — better to ship than block on a
flaky proxy.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    Runner,
    output_guardrail,
)

GUARDRAIL_MODEL = "gpt-5.5"


# -- 1. PII leak (regex) --------------------------------------------------

# Chinese phone: 1 + 10 digits, must NOT be inside a longer digit run (avoids
# matching the middle of e.g. order numbers / tracking IDs).
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# Chinese ID: 17 digits + (digit|X), word-boundary checked.
_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# Bank card: 16-19 digits in a row.
_BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


class PIIOutput(BaseModel):
    """Carries PII decision + masked text into the panel."""

    reasoning: str
    has_pii: bool
    masked_output: str


def _mask_pii(text: str) -> tuple[str, list[str]]:
    """Return (masked, hits) — hits is a list of (kind:match) strings for logs."""
    hits: list[str] = []

    def mask_phone(m: re.Match) -> str:
        v = m.group(0)
        hits.append(f"phone:{v[:3]}***{v[-2:]}")
        return v[:3] + "****" + v[-4:]

    def mask_id(m: re.Match) -> str:
        v = m.group(0)
        hits.append(f"id:{v[:4]}***{v[-2:]}")
        return v[:4] + "**********" + v[-4:]

    def mask_bank(m: re.Match) -> str:
        v = m.group(0)
        hits.append(f"bank:{v[:4]}***{v[-2:]}")
        return v[:4] + " **** **** " + v[-4:]

    out = _PHONE_RE.sub(mask_phone, text)
    out = _ID_RE.sub(mask_id, out)
    out = _BANK_RE.sub(mask_bank, out)
    return out, hits


@output_guardrail(name="PII Leak Guardrail")
async def pii_leak_guardrail(
    context: RunContextWrapper[Any], agent: Agent, agent_output: Any
) -> GuardrailFunctionOutput:
    text = agent_output if isinstance(agent_output, str) else str(agent_output)
    masked, hits = _mask_pii(text)
    has_pii = bool(hits)
    return GuardrailFunctionOutput(
        output_info=PIIOutput(
            reasoning=("命中：" + ", ".join(hits)) if has_pii else "未检测到敏感信息",
            has_pii=has_pii,
            masked_output=masked,
        ),
        tripwire_triggered=has_pii,
    )


# -- 2. False promise (LLM) -----------------------------------------------


class PromiseOutput(BaseModel):
    reasoning: str
    has_promise: bool


_promise_judge = Agent(
    name="False Promise Judge",
    model=GUARDRAIL_MODEL,
    instructions=(
        "你是京东客服回复合规审查员。判断给定客服回复是否包含**对外不当承诺**："
        "如 '一定能退款'、'保证 24 小时到货'、'包赔三倍'、'我承诺/个人担保' 等绝对化、"
        "或超出京东公开政策口径的承诺。\n"
        "客观陈述既有政策（如 '7 天无理由退货'）不算不当承诺。\n"
        "输出格式：第一行只输出 PROMISE 或 SAFE，第二行一句中文理由。"
    ),
)


@output_guardrail(name="False Promise Guardrail")
async def false_promise_guardrail(
    context: RunContextWrapper[Any], agent: Agent, agent_output: Any
) -> GuardrailFunctionOutput:
    text = agent_output if isinstance(agent_output, str) else str(agent_output)
    try:
        result = await Runner.run(_promise_judge, text)
        out = str(result.final_output or "").strip()
        has_promise = "PROMISE" in out.upper() and "SAFE" not in out.upper().splitlines()[0]
        reasoning = out
    except Exception as exc:
        # fail-open
        return GuardrailFunctionOutput(
            output_info=PromiseOutput(
                reasoning=f"(虚假承诺判定不可用，已放行) {exc}",
                has_promise=False,
            ),
            tripwire_triggered=False,
        )
    return GuardrailFunctionOutput(
        output_info=PromiseOutput(reasoning=reasoning, has_promise=has_promise),
        tripwire_triggered=has_promise,
    )


# -- 3. Brand neutrality (LLM) --------------------------------------------


class BrandOutput(BaseModel):
    reasoning: str
    has_disparagement: bool


_brand_judge = Agent(
    name="Brand Neutrality Judge",
    model=GUARDRAIL_MODEL,
    instructions=(
        "你是京东客服品牌合规审查员。判断给定客服回复是否对**其他电商平台**"
        "（淘宝、天猫、拼多多、苏宁易购、唯品会、抖音电商 等）有贬损、攻击或"
        "暗示其不靠谱的表述。中立提及不算。\n"
        "输出格式：第一行只输出 DISPARAGE 或 NEUTRAL，第二行一句中文理由。"
    ),
)


@output_guardrail(name="Brand Neutrality Guardrail")
async def brand_neutrality_guardrail(
    context: RunContextWrapper[Any], agent: Agent, agent_output: Any
) -> GuardrailFunctionOutput:
    text = agent_output if isinstance(agent_output, str) else str(agent_output)
    try:
        result = await Runner.run(_brand_judge, text)
        out = str(result.final_output or "").strip()
        first_line = (out.splitlines()[0] if out else "").upper()
        has_disp = "DISPARAGE" in first_line
        reasoning = out
    except Exception as exc:
        return GuardrailFunctionOutput(
            output_info=BrandOutput(
                reasoning=f"(品牌审查不可用，已放行) {exc}",
                has_disparagement=False,
            ),
            tripwire_triggered=False,
        )
    return GuardrailFunctionOutput(
        output_info=BrandOutput(reasoning=reasoning, has_disparagement=has_disp),
        tripwire_triggered=has_disp,
    )


OUTPUT_GUARDRAILS = [
    pii_leak_guardrail,
    false_promise_guardrail,
    brand_neutrality_guardrail,
]
