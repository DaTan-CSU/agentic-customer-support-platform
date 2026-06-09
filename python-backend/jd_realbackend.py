"""Standalone "real" backend service for the JD demo.

Lives in its own process (port 9001) to make the HTTP boundary genuine — the
MCP server talks to it over the network exactly the way an MCP server in
production would talk to an internal API. We only mock the data; we don't
mock the transport.

Endpoints:
  GET /health
  GET /coupons?category=
  GET /promotions?category=
  GET /delivery_eta?city=&sku=

Run standalone (for debugging):
  python -m uvicorn jd_realbackend:app --host 127.0.0.1 --port 9001
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from fastapi import FastAPI, Query

app = FastAPI(title="JD RealBackend (demo)")


# Static mock data; deterministic per (category) so demo responses are stable.
_COUPONS: dict[str, list[dict[str, Any]]] = {
    "电器": [
        {"code": "DQ100", "name": "家电满 1000 减 100", "expires": "2026-07-31"},
        {"code": "PLUS50", "name": "PLUS 会员家电额外 -50", "expires": "2026-06-30"},
    ],
    "服饰": [
        {"code": "FS80", "name": "服饰满 599 减 80", "expires": "2026-06-30"},
        {"code": "NEW20", "name": "新人首单 -20", "expires": "2026-12-31"},
    ],
    "食品": [
        {"code": "SP30", "name": "食品满 199 减 30", "expires": "2026-06-15"},
    ],
    "默认": [
        {"code": "JD10", "name": "全品类 99-10", "expires": "2026-06-30"},
    ],
}

_PROMOTIONS: dict[str, list[dict[str, Any]]] = {
    "电器": [
        {"name": "京东 618 家电焕新", "desc": "下单 12 期免息 + 旧机回收补贴"},
    ],
    "服饰": [
        {"name": "夏日衣橱季", "desc": "买二件 8.5 折，第三件半价"},
    ],
    "食品": [
        {"name": "生鲜 24h 达", "desc": "下单当日生鲜满 99 包邮"},
    ],
    "默认": [
        {"name": "京东 618", "desc": "全品类跨店满 300 减 50"},
    ],
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/coupons")
def coupons(category: str = Query("默认")) -> dict[str, Any]:
    items = _COUPONS.get(category) or _COUPONS["默认"]
    return {"category": category, "coupons": items}


@app.get("/promotions")
def promotions(category: str = Query("默认")) -> dict[str, Any]:
    items = _PROMOTIONS.get(category) or _PROMOTIONS["默认"]
    return {"category": category, "promotions": items}


@app.get("/delivery_eta")
def delivery_eta(
    city: str = Query("北京"),
    sku: str = Query("默认"),
) -> dict[str, Any]:
    # Stable per (city, sku): "下单后 X-Y 天送达" where X depends on the
    # hash of city+sku to produce a deterministic but plausible spread.
    h = abs(hash(f"{city}|{sku}")) % 4
    min_days, max_days = (1 + h, 1 + h + 2)
    eta_low = date.today() + timedelta(days=min_days)
    eta_high = date.today() + timedelta(days=max_days)
    return {
        "city": city,
        "sku": sku,
        "min_days": min_days,
        "max_days": max_days,
        "eta_range": f"{eta_low.isoformat()} ~ {eta_high.isoformat()}",
        "free_shipping_threshold": 99,
    }
