"""MCP stdio server that proxies to the real backend on port 9001.

Run by the Agents SDK via `MCPServerStdio` — this script is launched as a
subprocess and speaks MCP over stdin/stdout. Each MCP tool here makes an
HTTP call to `jd_realbackend` (port 9001), keeping the network boundary real.

Why FastMCP instead of the lower-level Server: the tool list is tiny (3
tools) and FastMCP gives us decorator-based registration with auto-schema
from type hints, so the file stays under 100 lines.

Run standalone (smoke check):
  python jd_mcp_server.py   # blocks on stdio
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

REAL_BACKEND_URL = os.environ.get(
    "JD_REAL_BACKEND_URL", "http://127.0.0.1:9001"
)

mcp = FastMCP("jd-realbackend")


def _get(path: str, params: dict[str, Any]) -> str:
    """Sync HTTP GET — FastMCP tools are sync. Returns a JSON string the
    model can read directly. Fails into a structured error string instead
    of raising, so a flaky backend doesn't kill the MCP server."""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{REAL_BACKEND_URL}{path}", params=params)
            r.raise_for_status()
            return json.dumps(r.json(), ensure_ascii=False)
    except Exception as exc:
        return json.dumps(
            {"error": f"real backend unavailable: {exc.__class__.__name__}: {exc}"},
            ensure_ascii=False,
        )


@mcp.tool()
def get_jd_coupons(category: str = "默认") -> str:
    """查询当前可用京东优惠券。category 可选：电器 / 服饰 / 食品 / 默认。"""
    return _get("/coupons", {"category": category})


@mcp.tool()
def get_jd_promotions(category: str = "默认") -> str:
    """查询当前京东促销活动。category 可选：电器 / 服饰 / 食品 / 默认。"""
    return _get("/promotions", {"category": category})


@mcp.tool()
def get_delivery_eta(city: str, sku: str = "默认") -> str:
    """查询某城市某商品的预计送达时效。"""
    return _get("/delivery_eta", {"city": city, "sku": sku})


if __name__ == "__main__":
    # FastMCP.run() runs over stdio by default — perfect for the SDK's
    # MCPServerStdio client which spawns us as a subprocess.
    mcp.run()
