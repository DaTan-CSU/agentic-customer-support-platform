from __future__ import annotations

import base64
import os
import re

from openai import AsyncOpenAI

# Vision runs on 阿里云百炼 (DashScope, OpenAI-compatible), NOT the gpt-5.5 proxy:
# the proxy adds 20-50s of variable latency on image requests, while 百炼 qwen-vl
# answers in ~1.4s. So perception (看图) goes to 百炼; the gpt-5.5 agents only ever
# see the resulting text description.
_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VISION_MODEL = "qwen-vl-max"

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        return None
    _client = AsyncOpenAI(api_key=key, base_url=_BAILIAN_BASE_URL)
    return _client


_PROMPT = (
    "你是京东电商客服的读图助手。用中文简洁描述这张图片中与购物客服相关的内容"
    "（商品、破损/瑕疵、页面截图、单据等），并提取图中可见的订单号。\n"
    "订单号格式固定为字母 DMO 开头（第三位是字母 O，不是数字 0）后接 8 位数字，"
    "例如 DMO20260101；识别时务必把开头读成字母 DMO，不要把 O 误读为 0。\n"
    "若图中没有订单号则不要提及。整体控制在两句话内。"
)


async def describe_image(image_bytes: bytes, mime_type: str) -> str | None:
    """Describe an uploaded image via 百炼 qwen-vl.

    Returns a short Chinese description (and any visible order number), or None
    on any failure — callers fail-open so a vision hiccup never blocks the turn.
    """
    client = _get_client()
    if client is None:
        return None
    mt = (mime_type or "image/jpeg").split(";", 1)[0]
    data_url = f"data:{mt};base64,{base64.b64encode(image_bytes).decode()}"
    try:
        resp = await client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return _normalize_order_ids(text) or None
    except Exception:
        return None


# Order IDs are a fixed "DMO" (letter O) + 8 digits. OCR routinely misreads the
# letter O as digit 0, so the prompt alone is unreliable; deterministically
# repair any "DM0########" the model emits back to "DMO########".
_ORDER_OCR_RE = re.compile(r"\bDM[O0](\d{8})\b")


def _normalize_order_ids(text: str) -> str:
    return _ORDER_OCR_RE.sub(lambda m: f"DMO{m.group(1)}", text)
