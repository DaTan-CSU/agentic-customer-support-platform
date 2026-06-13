"""Output-side guardrails: run AFTER the agent's final text is ready,
BEFORE it goes to the user. Tripping rewrites the output to a safer form
in `server.py` instead of just refusing — operators still want users to see
something coherent.

Two rules cover the demo:
  1. PII leak  — regex catches phone (11-digit 1XX...), 18-digit IDs, 16-19
     digit card numbers. Cheap, deterministic, no model call. Tripwire +
     masked rewrite published via output_info.
  2. Compliance — single LLM judge that catches BOTH false promises (包赔/
     一定/保证 超出政策) AND brand disparagement (negative mentions of 淘宝/
     天猫/拼多多 etc). Folded into one model call to save ~3 seconds per turn
     vs. running two independent judges.

The judge shares the default LLM client. Fail-open: if the judge errors, we
let the original output through — better to ship than block on a flaky proxy.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from agents import (
    Agent,
    GuardrailFunctionOutput,
    ModelSettings,
    RunContextWrapper,
    Runner,
    output_guardrail,
)

GUARDRAIL_MODEL = "gpt-5.4-mini"
_GUARDRAIL_SETTINGS = ModelSettings(temperature=0.2)


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


# -- 2. Compliance: false-promise + brand-disparagement (one LLM call) ----


class ComplianceOutput(BaseModel):
    reasoning: str
    has_promise: bool
    has_disparagement: bool


_compliance_judge = Agent(
    name="Compliance Judge",
    model=GUARDRAIL_MODEL,
    model_settings=_GUARDRAIL_SETTINGS,
    instructions=(
        "你是京东客服合规双重审查员。同时判断下方回复是否触发以下两类问题：\n"
        "1) **不当承诺 PROMISE**：'一定能退款'、'保证 24 小时到货'、'包赔三倍'、"
        "'我承诺/个人担保' 等绝对化或超出京东公开政策口径的承诺。陈述既有政策"
        "（如 '7 天无理由退货'）不算。\n"
        "2) **品牌贬损 BRAND**：对其他电商平台（淘宝、天猫、拼多多、苏宁易购、"
        "唯品会、抖音电商 等）的贬损、攻击或暗示其不靠谱。中立提及不算。\n"
        "输出格式（严格 2 行，不要任何其他内容）：\n"
        "第一行：`PROMISE=YES|NO BRAND=YES|NO`（两个标签必须都给）\n"
        "第二行：一句中文综合理由。"
    ),
)


_VERDICT_RE = re.compile(
    r"PROMISE\s*=\s*(YES|NO)\s+BRAND\s*=\s*(YES|NO)", re.IGNORECASE
)


@output_guardrail(name="Compliance Guardrail")
async def compliance_guardrail(
    context: RunContextWrapper[Any], agent: Agent, agent_output: Any
) -> GuardrailFunctionOutput:
    text = agent_output if isinstance(agent_output, str) else str(agent_output)
    try:
        result = await Runner.run(_compliance_judge, text)
        out = str(result.final_output or "").strip()
        m = _VERDICT_RE.search(out)
        has_promise = bool(m and m.group(1).upper() == "YES")
        has_disp = bool(m and m.group(2).upper() == "YES")
        reasoning = out
    except Exception:
        # Fail-open: a flaky judge must never block legitimate replies.
        return GuardrailFunctionOutput(
            output_info=ComplianceOutput(
                reasoning="(合规判定不可用，已放行)",
                has_promise=False,
                has_disparagement=False,
            ),
            tripwire_triggered=False,
        )
    return GuardrailFunctionOutput(
        output_info=ComplianceOutput(
            reasoning=reasoning,
            has_promise=has_promise,
            has_disparagement=has_disp,
        ),
        tripwire_triggered=has_promise or has_disp,
    )


OUTPUT_GUARDRAILS = [
    pii_leak_guardrail,
    compliance_guardrail,
]
