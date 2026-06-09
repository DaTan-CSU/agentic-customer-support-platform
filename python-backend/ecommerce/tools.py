from __future__ import annotations as _annotations

import json
import re
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from .approvals import approval_store
from .context import EcommerceAgentChatContext
from .mock_store import query_after_sales, query_logistics, query_order
from .widgets import logistics_card, order_card

_KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
_KNOWLEDGE_PATH = _KNOWLEDGE_DIR / "knowledge_base.jsonl"
_EMB_PATH = _KNOWLEDGE_DIR / "knowledge_emb.npy"
_KNOWLEDGE_CACHE: list[dict[str, Any]] | None = None
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")

# Semantic retrieval (vector) settings. Documents are embedded offline into
# knowledge_emb.npy; only the query is embedded at request time via the cloud
# text-embedding-v4 API. Falls back to keyword overlap if vectors are missing
# or the embedding API call fails.
_TOP_K = 2
_MIN_SIMILARITY = 0.45  # below this the top hit is treated as "no match";
# tuned empirically: real policy hits score ~0.55-0.79, off-topic queries ~0.4.
_NO_MATCH = "\u672a\u627e\u5230\u76f8\u5173\u653f\u7b56\uff0c\u8bf7\u5c1d\u8bd5\u6362\u4e00\u79cd\u95ee\u6cd5\u3002"

_EMB_MATRIX = None  # lazy np.ndarray (N, D), L2-normalized
_EMB_FAILED = False  # set True once if the vector matrix is missing/out of sync


def _load_knowledge() -> list[dict[str, Any]]:
    global _KNOWLEDGE_CACHE
    if _KNOWLEDGE_CACHE is None:
        records: list[dict[str, Any]] = []
        with _KNOWLEDGE_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        _KNOWLEDGE_CACHE = records
    return _KNOWLEDGE_CACHE


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "")}


def _load_emb_matrix():
    """Lazily load the precomputed, L2-normalized embedding matrix. None if missing."""
    global _EMB_MATRIX, _EMB_FAILED
    if _EMB_FAILED:
        return None
    if _EMB_MATRIX is None:
        try:
            import numpy as np

            matrix = np.load(_EMB_PATH)
            records = _load_knowledge()
            if matrix.shape[0] != len(records):
                # jsonl and vectors out of sync; rebuild needed -> fall back.
                _EMB_FAILED = True
                return None
            _EMB_MATRIX = matrix
        except Exception:
            _EMB_FAILED = True
            return None
    return _EMB_MATRIX


def _vector_search(question: str, top_k: int) -> list[dict[str, Any]] | None:
    """Semantic top-k retrieval. Returns None if the vector path is unavailable
    (missing matrix or embedding API failure) so the caller can fall back."""
    matrix = _load_emb_matrix()
    if matrix is None:
        return None
    try:
        import numpy as np

        from .embeddings import embed_texts

        q = embed_texts([question], text_type="query")[0]  # already L2-normalized
        sims = matrix @ q  # cosine, since both sides are L2-normalized
        top_idx = np.argsort(-sims)[:top_k]
        if len(top_idx) == 0 or float(sims[top_idx[0]]) < _MIN_SIMILARITY:
            return []
        records = _load_knowledge()
        return [records[i] for i in top_idx if float(sims[i]) >= _MIN_SIMILARITY]
    except Exception:
        return None


def _keyword_search(question: str, top_k: int) -> list[dict[str, Any]]:
    """Fallback retrieval by Chinese-char / token overlap."""
    query_tokens = _tokens(question)
    if not query_tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for record in _load_knowledge():
        haystack = f"{record.get('title', '')}\n{record.get('text', '')}"
        overlap = len(query_tokens & _tokens(haystack))
        if overlap:
            scored.append((overlap, record))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in scored[:top_k]]


async def _stream_widget(context: RunContextWrapper[EcommerceAgentChatContext], widget) -> None:
    """Best-effort: render a ChatKit widget in the chat. No-op outside the
    ChatKit server flow (e.g. unit tests with a lightweight context)."""
    stream = getattr(context.context, "stream_widget", None)
    if stream is None:
        return
    try:
        await stream(widget)
    except Exception:
        pass


def _format_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "暂无商品明细"
    return "、".join(
        f"{item.get('name', '未知商品')} x{item.get('qty', 1)}"
        for item in items
    )


def _format_timeline(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return "暂无物流轨迹"
    latest_nodes = timeline[-2:]
    return "；".join(
        f"{node.get('time', '')} {node.get('status', '')} {node.get('location', '')}".strip()
        for node in latest_nodes
    )


@function_tool
async def query_order_tool(
    context: RunContextWrapper[EcommerceAgentChatContext], order_id: str
) -> str:
    """查询订单基础信息。"""
    result = query_order(order_id)
    state = context.context.state
    state.order_id = result.get("order_id") or order_id
    state.last_intent = "查订单"
    if not result.get("found"):
        return result.get("message") or f"未找到订单 {order_id}，请核对订单号后再试。"

    # Populate side-context the panel shows: real user_id from the order
    # record, and the first item's SKU.
    real_user = result.get("user_id")
    if real_user:
        state.user_id = real_user
    order_items = result.get("items") or []
    if order_items and isinstance(order_items, list) and order_items[0].get("sku"):
        state.sku = order_items[0]["sku"]

    await _stream_widget(context, order_card(result))
    items_text = _format_items(order_items)
    return (
        f"订单 {result['order_id']}：{result.get('status_label', '状态未知')}。"
        f"下单时间：{result.get('created_at', '未知')}；"
        f"实付金额：{result.get('amount', '未知')}；"
        f"支付方式：{result.get('payment', '未知')}；"
        f"收货城市：{result.get('address_city', '未知')}。"
        f"商品：{items_text}。"
    )


@function_tool
async def query_logistics_tool(
    context: RunContextWrapper[EcommerceAgentChatContext], order_id: str
) -> str:
    """查询订单物流信息。"""
    result = query_logistics(order_id)
    context.context.state.order_id = result.get("order_id") or order_id
    context.context.state.last_intent = "查物流"
    if not result.get("found"):
        return result.get("message") or f"未找到订单 {order_id} 的物流信息，请核对订单号后再试。"

    await _stream_widget(context, logistics_card(result))
    delayed_text = "当前有延迟标记" if result.get("delayed") else "暂无延迟标记"
    timeline = _format_timeline(result.get("timeline", []))
    return (
        f"订单 {result['order_id']} 物流：{result.get('status_label', '状态未知')}。"
        f"承运方：{result.get('carrier', '未知')}；"
        f"运单号：{result.get('tracking_no', '未知')}；"
        f"预计送达：{result.get('expected_delivery', '未知')}；"
        f"{delayed_text}。最新轨迹：{timeline}。"
    )


@function_tool
async def query_after_sales_tool(
    context: RunContextWrapper[EcommerceAgentChatContext], order_id: str
) -> str:
    """查询订单售后记录。"""
    result = query_after_sales(order_id)
    context.context.state.order_id = result.get("order_id") or order_id
    context.context.state.last_intent = "查售后"
    if not result.get("found"):
        return result.get("message") or f"未找到订单 {order_id} 的售后信息，请核对订单号后再试。"
    if not result.get("has_case"):
        return f"订单 {result.get('order_id', order_id)} 暂无售后记录。"

    case_id = result.get("case_id")
    context.context.state.after_sales_case_id = case_id
    return (
        f"订单 {result.get('order_id', order_id)} 售后单 {case_id or '未知'}："
        f"{result.get('type_label', result.get('type', '售后'))}，"
        f"状态：{result.get('status_label', result.get('status', '未知'))}，"
        f"原因：{result.get('reason', '未填写')}。"
    )


# -- Soft approval tools ----------------------------------------------------
# These do NOT execute the destructive action. They file a request in the
# in-memory approval_store and return immediately. An operator approves /
# rejects via POST /approvals/{id}, which is where the mock execution lives.
# See ecommerce/approvals.py for the rationale.


def _thread_id_from_ctx(context: RunContextWrapper[EcommerceAgentChatContext]) -> str:
    thread = getattr(context.context, "thread", None)
    tid = getattr(thread, "id", None) if thread is not None else None
    return tid or "thr_unknown"


@function_tool
async def request_refund_tool(
    context: RunContextWrapper[EcommerceAgentChatContext],
    order_id: str,
    reason: str,
    amount: str | None = None,
) -> str:
    """发起退款审批申请（不直接执行退款，需人工审批）。"""
    context.context.state.last_intent = "退款"
    context.context.state.order_id = order_id
    req = approval_store.create(
        thread_id=_thread_id_from_ctx(context),
        kind="refund",
        tool_name="request_refund_tool",
        args={"order_id": order_id, "reason": reason, "amount": amount or ""},
        summary=f"订单 {order_id} 退款申请，金额 {amount or '原支付金额'}，原因：{reason}",
    )
    return (
        f"已提交退款审批申请（编号 {req.id}）。订单 {order_id}，原因：{reason}。"
        f"等待人工审批，审批通过后会按原路退回。"
    )


@function_tool
async def request_order_cancel_tool(
    context: RunContextWrapper[EcommerceAgentChatContext],
    order_id: str,
    reason: str,
) -> str:
    """发起订单取消审批申请（不直接取消，需人工审批）。"""
    context.context.state.last_intent = "取消订单"
    context.context.state.order_id = order_id
    req = approval_store.create(
        thread_id=_thread_id_from_ctx(context),
        kind="order_cancel",
        tool_name="request_order_cancel_tool",
        args={"order_id": order_id, "reason": reason},
        summary=f"订单 {order_id} 取消申请，原因：{reason}",
    )
    return (
        f"已提交订单取消审批申请（编号 {req.id}）。订单 {order_id}，原因：{reason}。"
        f"等待人工审批，未审批前订单状态不会变化。"
    )


@function_tool
async def request_price_protection_tool(
    context: RunContextWrapper[EcommerceAgentChatContext],
    order_id: str,
    claimed_amount: str,
    note: str = "",
) -> str:
    """发起价保理赔审批申请（不直接退差价，需人工审批）。"""
    context.context.state.last_intent = "价保"
    context.context.state.order_id = order_id
    req = approval_store.create(
        thread_id=_thread_id_from_ctx(context),
        kind="price_protection",
        tool_name="request_price_protection_tool",
        args={"order_id": order_id, "claimed_amount": claimed_amount, "note": note},
        summary=f"订单 {order_id} 价保申请，差价 {claimed_amount}。备注：{note or '无'}",
    )
    return (
        f"已提交价保理赔审批申请（编号 {req.id}）。订单 {order_id}，差价 {claimed_amount}。"
        f"等待人工审批，审批通过后差价将原路退回。"
    )


@function_tool
async def search_policy(
    context: RunContextWrapper[EcommerceAgentChatContext], question: str
) -> str:
    """语义检索京东政策、常见问题和商品知识（向量检索，关键词兜底）。"""
    context.context.state.last_intent = "政策咨询"
    if not question or not question.strip():
        return _NO_MATCH

    # Prefer semantic vector retrieval; fall back to keyword overlap when the
    # embedding model / precomputed vectors are unavailable (returns None).
    records = _vector_search(question, _TOP_K)
    if records is None:
        records = _keyword_search(question, _TOP_K)

    texts = [r.get("text", "") for r in records if r.get("text")]
    if not texts:
        return _NO_MATCH
    return "\n\n".join(texts)
