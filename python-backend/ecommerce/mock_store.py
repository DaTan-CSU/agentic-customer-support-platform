"""Mock order / logistics / after-sales store for the e-commerce CS demo.

This is the "transactional" data layer: in a real system these would be
internal API calls. Here we serve from a static JSON fixture so the agent
flow is runnable without any backend infrastructure.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_DATA_PATH = Path(__file__).with_name("mock_orders.json")


def _load() -> Dict[str, Any]:
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


_ORDERS: Dict[str, Any] = _load()


def _get(order_id: str) -> Optional[Dict[str, Any]]:
    return _ORDERS.get((order_id or "").strip().upper())


def query_order(order_id: str) -> Dict[str, Any]:
    """Return basic order info: status, items, amount, time."""
    o = _get(order_id)
    if not o:
        return {"found": False, "order_id": order_id, "message": "未找到该订单，请核对订单号（格式如 DMO20260101）。"}
    return {
        "found": True,
        "order_id": o["order_id"],
        "user_id": o.get("user_id"),
        "status": o["status"],
        "status_label": o["status_label"],
        "created_at": o["created_at"],
        "amount": o["amount"],
        "payment": o["payment"],
        "address_city": o["address_city"],
        "items": o["items"],
    }


def query_logistics(order_id: str) -> Dict[str, Any]:
    """Return the logistics timeline for an order."""
    o = _get(order_id)
    if not o:
        return {"found": False, "order_id": order_id, "message": "未找到该订单的物流信息。"}
    lg = o["logistics"]
    return {
        "found": True,
        "order_id": o["order_id"],
        "status_label": o["status_label"],
        "carrier": lg["carrier"],
        "tracking_no": lg["tracking_no"],
        "delayed": lg["delayed"],
        "expected_delivery": lg["expected_delivery"],
        "timeline": lg["timeline"],
    }


def query_after_sales(order_id: str) -> Dict[str, Any]:
    """Return the after-sales case for an order, if any."""
    o = _get(order_id)
    if not o:
        return {"found": False, "order_id": order_id, "message": "未找到该订单。"}
    case = o.get("after_sales")
    if not case:
        return {"found": True, "order_id": o["order_id"], "has_case": False, "message": "该订单暂无售后记录。"}
    return {"found": True, "order_id": o["order_id"], "has_case": True, **case}


def list_orders() -> Dict[str, Any]:
    """Return a compact index of all mock orders (for debugging / demo)."""
    return {
        oid: {"status_label": o["status_label"], "item": o["items"][0]["name"]}
        for oid, o in _ORDERS.items()
    }
