from __future__ import annotations as _annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

load_dotenv()

# --- Default model provider: gpt-5.5 via the AI巴士 proxy ---
# Read the local key file at startup so all SDK agents use the same proxy without
# hardcoding secrets in this repository.
from openai import AsyncOpenAI
from agents import set_default_openai_client, set_default_openai_api, set_tracing_disabled
from agents.tracing import set_trace_processors

from tracing_store import SQLiteTraceProcessor, load_traces_for_thread


def _load_proxy_from_key_file(provider: str = "词元神") -> tuple[str, str] | None:
    """Pull (base_url, api_key) for a named provider out of ~/Desktop/API key.txt.

    The file is a free-form list of providers, each section starting with a
    line containing the provider name. After that line we look forward for:
      • the first `http…` line (base url)
      • an api key on either a `代码调用：sk-…` prefixed line, or a standalone
        `sk-…` line (covers providers that don't prefix the key)
    """
    key_file = Path.home() / "Desktop" / "API key.txt"
    try:
        lines = key_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for idx, line in enumerate(lines):
        if provider not in line:
            continue
        base_url = ""
        api_key = ""
        for candidate in lines[idx + 1 : idx + 8]:
            text = candidate.strip()
            if text.startswith("http") and not base_url:
                base_url = text
                continue
            if "代码调用：" in text and not api_key:
                api_key = text.split("代码调用：", 1)[1].strip()
                continue
            # Fallback: any bare `sk-...` token on its own line.
            if not api_key and text.startswith("sk-"):
                api_key = text.split()[0]
        if base_url and api_key:
            return base_url, api_key
    return None


_proxy = _load_proxy_from_key_file("AI巴士")


def _load_tencent_creds_from_key_file() -> tuple[str, str] | None:
    """Pull (SecretId, SecretKey) for 腾讯云 ASR out of ~/Desktop/API key.txt.

    Key file format:
        腾讯云密钥
        SecretId AKID...
        SecretKey ...
    """
    key_file = Path.home() / "Desktop" / "API key.txt"
    try:
        lines = key_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for idx, line in enumerate(lines):
        if "腾讯" not in line:
            continue
        sid, skey = "", ""
        for cand in lines[idx + 1 : idx + 6]:
            t = cand.strip()
            if t.lower().startswith("secretid"):
                sid = t.split(None, 1)[1].strip() if " " in t else ""
            elif t.lower().startswith("secretkey"):
                skey = t.split(None, 1)[1].strip() if " " in t else ""
        if sid and skey:
            return sid, skey
    return None


_tc = _load_tencent_creds_from_key_file()
if _tc is not None:
    os.environ.setdefault("TENCENTCLOUD_SECRET_ID", _tc[0])
    os.environ.setdefault("TENCENTCLOUD_SECRET_KEY", _tc[1])
if _proxy is None:
    _base_url = os.environ.get("OPENAI_BASE_URL")
    _api_key = os.environ.get("OPENAI_API_KEY")
else:
    _base_url, _api_key = _proxy

if _base_url and _api_key:
    # OpenAI SDK appends `/chat/completions` to base_url. Most key-file
    # entries (AI巴士 / 词元神 / 猫猫小铺) list the gateway origin without
    # `/v1`, which makes the SDK hit the gateway homepage (returns HTML,
    # breaks parsing). Normalize once here.
    if not _base_url.rstrip("/").endswith("/v1"):
        _base_url = _base_url.rstrip("/") + "/v1"
    os.environ["OPENAI_BASE_URL"] = _base_url
    os.environ["OPENAI_API_KEY"] = _api_key

    # AI巴士-style gateways block the OpenAI SDK's default `User-Agent:
    # OpenAI/Python ...` + `x-stainless-*` fingerprint (returns 403 "Your
    # request was blocked."). Strip those headers before the request leaves
    # the box. Plain httpx requests work; only the SDK's fingerprint trips
    # the WAF.
    async def _strip_sdk_fingerprint(request: httpx.Request) -> None:
        for h in list(request.headers.keys()):
            if h.lower().startswith("x-stainless-"):
                del request.headers[h]
        request.headers["user-agent"] = "openai-python-compat/1.0"

    _async_http = httpx.AsyncClient(event_hooks={"request": [_strip_sdk_fingerprint]})
    set_default_openai_client(
        AsyncOpenAI(api_key=_api_key, base_url=_base_url, http_client=_async_http)
    )
    set_default_openai_api("chat_completions")
    # Keep tracing ON, but replace the default OpenAI exporter with a local
    # SQLite processor — the 猫猫小铺 proxy can't accept OpenAI's trace ingest,
    # and we want spans on disk for the local Traces panel anyway.
    set_trace_processors([SQLiteTraceProcessor()])
# ---------------------------------------------------------------------

from chatkit.server import StreamingResult
from fastapi import Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from ecommerce.agents import (
    after_sales_agent,
    faq_agent,
    logistics_agent,
    order_agent,
    triage_agent,
)
from ecommerce.approvals import approval_store
from ecommerce.stt import transcribe as stt_transcribe
from ecommerce.context import (
    EcommerceAgentChatContext,
    EcommerceAgentContext,
    create_initial_context,
    public_context,
)
from pydantic import BaseModel
from server import EcommerceChatKitServer

# -- MCP + real backend wiring (step 5) --------------------------------------
# `jd_realbackend` is launched as a separate uvicorn subprocess on port 9001
# (genuine HTTP boundary). `jd_mcp_server` is launched by the SDK's
# `MCPServerStdio` and proxies MCP tool calls to that backend via httpx.
from agents.mcp import MCPServerStdio, MCPServerStdioParams

_REAL_BACKEND_PROC: subprocess.Popen | None = None
_MCP_SERVER: MCPServerStdio | None = None

app = FastAPI()

# CORS configuration (adjust as needed for deployment)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_server = EcommerceChatKitServer()


def get_server() -> EcommerceChatKitServer:
    return chat_server


# -- step 5: lifecycle hooks for real backend + MCP server -------------------


async def _wait_real_backend(timeout_s: float = 8.0) -> bool:
    """Poll /health on the real backend until it answers. The MCP server
    proxies to it, so we don't attach the MCP server to any agent until
    the upstream is up."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=1.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                r = await client.get("http://127.0.0.1:9001/health")
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


@app.on_event("startup")
async def _start_realbackend_and_mcp() -> None:
    global _REAL_BACKEND_PROC, _MCP_SERVER

    backend_dir = Path(__file__).parent
    venv_py = backend_dir / ".venv" / "Scripts" / "python.exe"
    py = str(venv_py if venv_py.exists() else sys.executable)

    # 1) Launch jd_realbackend on port 9001 as a child process. We don't use
    #    --reload here; the file shouldn't change during a session.
    _REAL_BACKEND_PROC = subprocess.Popen(
        [py, "-m", "uvicorn", "jd_realbackend:app", "--host", "127.0.0.1", "--port", "9001"],
        cwd=str(backend_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ready = await _wait_real_backend()
    if not ready:
        # Real backend never came up; skip MCP wiring so the rest of the
        # demo still runs (tools just won't be reachable for the FAQ flow).
        return

    # 2) Spawn the MCP stdio server. The SDK forks it on connect().
    _MCP_SERVER = MCPServerStdio(
        params=MCPServerStdioParams(
            command=py,
            args=[str(backend_dir / "jd_mcp_server.py")],
            cwd=str(backend_dir),
        ),
        name="jd-realbackend",
        cache_tools_list=True,
        client_session_timeout_seconds=10,
    )
    try:
        await _MCP_SERVER.connect()
    except Exception:
        _MCP_SERVER = None
        return

    # 3) Attach to the FAQ agent so 优惠/促销/时效 类问答自动通过 MCP 工具回答。
    from ecommerce.agents import faq_agent

    faq_agent.mcp_servers = [_MCP_SERVER]


@app.on_event("shutdown")
async def _stop_realbackend_and_mcp() -> None:
    global _REAL_BACKEND_PROC, _MCP_SERVER
    if _MCP_SERVER is not None:
        try:
            await _MCP_SERVER.cleanup()
        except Exception:
            pass
        _MCP_SERVER = None
    if _REAL_BACKEND_PROC is not None:
        try:
            _REAL_BACKEND_PROC.terminate()
            _REAL_BACKEND_PROC.wait(timeout=5)
        except Exception:
            pass
        _REAL_BACKEND_PROC = None


@app.post("/chatkit")
async def chatkit_endpoint(
    request: Request, server: EcommerceChatKitServer = Depends(get_server)
) -> Response:
    payload = await request.body()
    result = await server.process(payload, {"request": request})
    if isinstance(result, StreamingResult):
        return StreamingResponse(result, media_type="text/event-stream")
    if hasattr(result, "json"):
        return Response(content=result.json, media_type="application/json")
    return Response(content=result)


@app.get("/chatkit/state")
async def chatkit_state(
    thread_id: str = Query(...),
    server: EcommerceChatKitServer = Depends(get_server),
) -> Dict[str, Any]:
    return await server.snapshot(thread_id, {"request": None})


@app.get("/chatkit/bootstrap")
async def chatkit_bootstrap(
    server: EcommerceChatKitServer = Depends(get_server),
) -> Dict[str, Any]:
    return await server.snapshot(None, {"request": None})


@app.get("/chatkit/state/stream")
async def chatkit_state_stream(
    thread_id: str = Query(...),
    server: EcommerceChatKitServer = Depends(get_server),
):
    thread = await server.ensure_thread(thread_id, {"request": None})
    queue = server.register_listener(thread.id)

    async def event_generator():
        try:
            initial = await server.snapshot(thread.id, {"request": None})
            yield f"data: {json.dumps(initial, default=str)}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            server.unregister_listener(thread.id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.put("/attachments/{attachment_id}")
async def upload_attachment(
    attachment_id: str,
    request: Request,
    server: EcommerceChatKitServer = Depends(get_server),
) -> Response:
    """Receive raw bytes for a two-phase attachment upload."""
    data = await request.body()
    server.attachment_store.put_bytes(attachment_id, data)
    # The PUT is the upload-complete signal, so clear upload_descriptor per
    # ChatKit's contract. NOTE: the sent image still can't be re-displayed in the
    # hosted ChatKit UI — its iframe (cdn.platform.openai.com) loads previews via
    # fetch(preview_url), gated by a connect-src CSP that only allows OpenAI's own
    # domains. No self-hosted preview_url (http://localhost or data:) can pass, so
    # the bubble shows gray after send. The bytes are still read server-side for
    # the vision/OCR step, which is what this upload is actually for.
    try:
        att = await server.store.load_attachment(attachment_id, {})
        att.upload_descriptor = None
        await server.store.save_attachment(att, {})
    except Exception:
        pass
    return Response(status_code=200)


@app.get("/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: str,
    server: EcommerceChatKitServer = Depends(get_server),
) -> Response:
    """Serve attachment bytes (used as the image preview_url)."""
    rec = server.attachment_store.get_bytes(attachment_id)
    if rec is None:
        return Response(status_code=404)
    data, mime = rec
    return Response(content=data, media_type=mime)


@app.get("/traces")
async def get_traces(thread_id: str = Query(...)) -> Dict[str, Any]:
    """Return traces + spans recorded for this thread (newest first)."""
    return {"thread_id": thread_id, "traces": load_traces_for_thread(thread_id)}


class ApprovalDecisionBody(BaseModel):
    decision: str  # "approve" | "reject"
    note: str | None = None


def _execute_approved(req) -> str:
    """Mock-execute the action the operator just approved. In a real backend
    this is where you'd call the refund / cancel / price-protection API."""
    if req.kind == "refund":
        return f"已为订单 {req.args.get('order_id')} 发起退款，预计 3-7 个工作日原路退回。"
    if req.kind == "order_cancel":
        return f"订单 {req.args.get('order_id')} 已成功取消。"
    if req.kind == "price_protection":
        return (
            f"订单 {req.args.get('order_id')} 价保差价 {req.args.get('claimed_amount')} 已发起退款。"
        )
    return "已执行。"


@app.get("/approvals")
async def list_approvals(thread_id: str = Query(...)) -> Dict[str, Any]:
    """Return all approval requests for a thread (pending + decided)."""
    items = [r.to_dict() for r in approval_store.list_for_thread(thread_id)]
    return {"thread_id": thread_id, "approvals": items}


@app.post("/approvals/{approval_id}")
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionBody,
) -> Dict[str, Any]:
    """Approve or reject a pending request. Approved requests mock-execute
    immediately and the result is stored on the request for UI display."""
    decision = (body.decision or "").lower().strip()
    if decision not in ("approve", "reject"):
        return {"ok": False, "error": "decision must be 'approve' or 'reject'"}
    target_status = "approved" if decision == "approve" else "rejected"
    req_preview = approval_store.get(approval_id)
    if req_preview is None:
        return {"ok": False, "error": "not found"}
    if req_preview.status != "pending":
        return {"ok": False, "error": f"already {req_preview.status}"}
    # SDK-native approvals (Runner is awaiting on a waiter) run the real
    # tool body on resume — don't also mock-execute here, that would
    # double-print "已发起退款...". The decide() call sets the waiter so
    # server.respond() unblocks and finishes the run.
    sdk_native = req_preview.run_state_json is not None
    execution = (
        None
        if sdk_native or target_status == "rejected"
        else _execute_approved(req_preview)
    )
    req = approval_store.decide(
        approval_id,
        decision=target_status,  # type: ignore[arg-type]
        note=body.note,
        execution_result=execution,
    )
    return {"ok": True, "approval": req.to_dict() if req else None}


@app.post("/stt")
async def stt_endpoint(audio: UploadFile = File(...)) -> Dict[str, Any]:
    """Transcribe a single push-to-talk audio clip via 百炼 Paraformer."""
    data = await audio.read()
    mime = audio.content_type or "audio/webm"
    result = await stt_transcribe(data, mime)
    # Log the upstream error verbatim so the UI can show it without guessing.
    if result.get("error"):
        print(f"[/stt] bytes={len(data)} mime={mime} error={result['error']}", flush=True)
    return {
        "text": result.get("text", ""),
        "error": result.get("error"),
        "mime": mime,
        "bytes": len(data),
    }


@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}


__all__ = [
    "EcommerceAgentChatContext",
    "EcommerceAgentContext",
    "after_sales_agent",
    "app",
    "chat_server",
    "create_initial_context",
    "faq_agent",
    "logistics_agent",
    "order_agent",
    "public_context",
    "triage_agent",
]
