"""ChatKit widget builders for the e-commerce CS demo.

These turn the structured dicts returned by `mock_store` into rich ChatKit
widgets (order card, logistics timeline) that render inside the chat bubble.
Tools call `context.context.stream_widget(...)` with these roots while still
returning a plain-text summary to the model.
"""
from __future__ import annotations

from typing import Any

from chatkit.widgets import (
    Badge,
    Card,
    Caption,
    Col,
    Divider,
    Row,
    Text,
    Title,
)


def _kv(label: str, value: str) -> Row:
    """A label/value row, value right-aligned."""
    return Row(
        justify="between",
        align="center",
        gap=3,
        children=[
            Caption(value=label),
            Text(value=value, weight="medium"),
        ],
    )


def order_card(result: dict[str, Any]) -> Card:
    """Render order basics + line items as a card."""
    items = result.get("items", []) or []
    item_rows: list[Any] = []
    for it in items:
        item_rows.append(
            Row(
                justify="between",
                align="center",
                gap=3,
                children=[
                    Text(value=str(it.get("name", "未知商品")), maxLines=2),
                    Caption(value=f"x{it.get('qty', 1)}  ¥{it.get('price', '-')}"),
                ],
            )
        )

    return Card(
        size="full",
        children=[
            Row(
                justify="between",
                align="center",
                children=[
                    Title(value=f"订单 {result.get('order_id', '')}", size="md"),
                    Badge(label=str(result.get("status_label", "状态未知")), color="info"),
                ],
            ),
            Divider(),
            Col(gap=2, children=item_rows or [Caption(value="暂无商品明细")]),
            Divider(),
            _kv("实付金额", f"¥{result.get('amount', '-')}"),
            _kv("下单时间", str(result.get("created_at", "-"))),
            _kv("支付方式", str(result.get("payment", "-"))),
            _kv("收货城市", str(result.get("address_city", "-"))),
        ],
    )


def logistics_card(result: dict[str, Any]) -> Card:
    """Render carrier info + a timeline (latest node first) as a card."""
    timeline = result.get("timeline", []) or []
    nodes: list[Any] = []
    for idx, node in enumerate(reversed(timeline)):
        latest = idx == 0
        nodes.append(
            Col(
                gap=1,
                children=[
                    Row(
                        gap=2,
                        align="center",
                        children=[
                            Text(
                                value=str(node.get("status", "")),
                                weight="semibold" if latest else "normal",
                            ),
                            Caption(value=str(node.get("location", ""))),
                        ],
                    ),
                    Caption(value=str(node.get("time", ""))),
                ],
            )
        )

    delayed = bool(result.get("delayed"))
    return Card(
        size="full",
        children=[
            Row(
                justify="between",
                align="center",
                children=[
                    Title(value=f"物流 · {result.get('carrier', '')}", size="md"),
                    Badge(
                        label="延迟" if delayed else "正常",
                        color="warning" if delayed else "success",
                    ),
                ],
            ),
            _kv("运单号", str(result.get("tracking_no", "-"))),
            _kv("预计送达", str(result.get("expected_delivery", "-"))),
            Divider(),
            Col(gap=3, children=nodes or [Caption(value="暂无物流轨迹")]),
        ],
    )
