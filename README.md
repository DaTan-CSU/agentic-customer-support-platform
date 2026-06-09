# 京东客服 Agent 编排 Demo

基于 [OpenAI Agents Python SDK](https://github.com/openai/openai-agents-python) + [ChatKit](https://github.com/openai/chatkit-js) 构建的中文电商客服多智能体编排示例。从 [openai/openai-cs-agents-demo](https://github.com/openai/openai-cs-agents-demo) 的航司 demo fork 而来，业务面切换到京东电商场景，并在 SDK 之上叠加了 Session 持久化、本地 Tracing、出口护栏、结构化输出、MCP 真后端接入、agents-as-tools 多意图 fan-out、语音输入等能力。

---

## 1. 系统架构

```mermaid
flowchart LR
    subgraph 浏览器
        UI[Next.js 前端<br/>localhost:3000]
        Mic[麦克风]
    end

    subgraph 主后端 ":8000"
        FastAPI[FastAPI<br/>main.py]
        Runner[Agents SDK<br/>Runner.run_streamed]
        ChatKit[ChatKit Server]
        Store[(SQLiteSession<br/>.agent_sessions.db)]
        Trace[(SQLite Trace<br/>.agent_traces.db)]
        Approv[(In-mem<br/>ApprovalStore)]
    end

    subgraph 真后端 ":9001"
        Real[jd_realbackend<br/>FastAPI]
    end

    subgraph MCP stdio
        MCP[jd_mcp_server<br/>FastMCP]
    end

    subgraph 外部服务
        Proxy[gpt-5.5 代理<br/>词元神 / AI巴士 / 猫猫小铺]
        Bailian[阿里百炼<br/>vision + embedding]
        Tencent[腾讯云 ASR]
    end

    UI -->|/chatkit SSE| FastAPI
    Mic -->|WAV 16k| UI
    UI -->|/stt multipart| FastAPI
    UI -->|/approvals| FastAPI
    UI -->|/traces| FastAPI
    FastAPI --> Runner
    Runner -->|chat.completions| Proxy
    Runner --> ChatKit
    Runner --> Store
    Runner --> Trace
    Runner --> MCP
    MCP -->|httpx| Real
    FastAPI -->|OCR| Bailian
    FastAPI -->|STT| Tencent
    Runner -->|工具调用| Approv
```

---

## 2. 已实现能力

| 能力 | 实现位置 | SDK 特性 |
|---|---|---|
| 多 agent 编排（triage + 4 专员） | `python-backend/ecommerce/agents.py` | `Agent` / `handoff` |
| 输入护栏（相关性 / 越狱） | `python-backend/ecommerce/guardrails.py` | `@input_guardrail` |
| 输出护栏（PII / 虚假承诺 / 品牌中立） | `python-backend/ecommerce/output_guardrails.py` | `@output_guardrail` |
| Session 持久化（SQLite 落盘） | `python-backend/server.py` | `SQLiteSession` |
| 本地 Tracing + 自绘前端面板 | `python-backend/tracing_store.py`、`ui/components/traces-panel.tsx` | `TracingProcessor` |
| 结构化输出（CaseSummary） | `python-backend/ecommerce/summary_agent.py` | `Agent(output_type=...)` |
| MCP stdio 服务 + 真后端 | `python-backend/jd_mcp_server.py`、`jd_realbackend.py` | `MCPServerStdio` |
| Agents-as-tools 多意图 fan-out | `python-backend/ecommerce/agents.py` | `Agent.as_tool()` |
| 软审批（退款 / 取消 / 价保） | `python-backend/ecommerce/approvals.py` | 自实现 |
| 图片输入（OCR + 描述） | `python-backend/ecommerce/vision.py` | 百炼 qwen-vl |
| 语音输入（push-to-talk） | `python-backend/ecommerce/stt.py`、`ui/components/voice-input.tsx` | 腾讯 ASR |

详细能力 / 差距 / 后续规划见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

---

## 3. 快速启动

### 3.1 凭证准备

后端启动时从 `~/Desktop/API key.txt`（Windows 下 `C:\Users\<你>\Desktop\API key.txt`）按段落名匹配读取以下条目：

```
13.AI巴士
https://api.ccbus.top
sk-xxxxxxxxxxxxxxxx

18.阿里云百炼
https://bailian.console.aliyun.com
sk-xxxxxxxxxxxxxxxx

腾讯云密钥
SecretId AKIDxxxxxxxxxxxxxxxx
SecretKey xxxxxxxxxxxxxxxxxxxxxxxx
```

至少需要：

- 一组 OpenAI 兼容代理（默认 `AI巴士`，可改 `python-backend/main.py` 的 `_load_proxy_from_key_file(...)`）
- `DASHSCOPE_API_KEY`（百炼，图片 OCR） — 走 `.env` 或环境变量
- 腾讯云 SecretId/SecretKey（语音输入） — 也可走 `.env`

### 3.2 方案 A：Docker Compose（推荐）

```bash
docker compose up --build
```

浏览器打开 http://localhost:3000

compose 会拉起 3 个服务：`ui:3000`、`backend:8000`、`realbackend:9001`，并把 `~/Desktop/API key.txt` 与 `.env` 挂入 backend 容器。Windows 下默认 mount 路径已配好，把 `${HOME}` 换成 `C:\Users\<你>` 同样工作。

### 3.3 方案 B：本机直接跑

**后端（终端 1）**
```bash
cd python-backend
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

主后端启动时会自动 spawn 一个 `jd_realbackend` 子进程（port 9001）和一个 MCP stdio 子进程。

**前端（终端 2）**
```bash
cd ui
npm install
npm run dev:next
```

浏览器打开 http://localhost:3000

### 3.4 跑测试

```bash
# 后端静态断言（无网络）
cd python-backend
python -m unittest discover -s . -p 'test_*.py'

# 前端 TS 检查
cd ../ui
npx tsc --noEmit
```

GitHub Actions 在 push main 和所有 PR 上都跑这两步，见 `.github/workflows/ci.yml`。

---

## 4. 仓库目录速览

```
.
├── docs/ROADMAP.md                  # 能力 & 后续规划
├── docker-compose.yml               # 一键拉起三服务
├── .github/workflows/ci.yml         # pytest + tsc CI
├── python-backend/
│   ├── Dockerfile
│   ├── main.py                      # FastAPI + 代理装载 + MCP/真后端启动钩子
│   ├── server.py                    # EcommerceChatKitServer：Runner + Session + Trace + Summary
│   ├── tracing_store.py             # SQLite trace processor
│   ├── jd_realbackend.py            # :9001 真后端
│   ├── jd_mcp_server.py             # MCP stdio 桥接
│   └── ecommerce/
│       ├── agents.py                # 5 个 agent + handoff + as_tool
│       ├── tools.py                 # 查询工具 + 软审批工具
│       ├── guardrails.py            # 输入护栏
│       ├── output_guardrails.py     # 输出护栏
│       ├── approvals.py             # 软审批 store
│       ├── summary_agent.py         # 结构化输出 CaseSummary
│       ├── stt.py                   # 腾讯 ASR
│       ├── vision.py                # 百炼 qwen-vl
│       ├── widgets.py               # ChatKit widget
│       ├── mock_orders.json
│       └── knowledge/
└── ui/
    ├── Dockerfile
    ├── app/page.tsx
    ├── components/                  # 4 面板 + voice-input + chatkit-panel ...
    └── next.config.mjs              # /chatkit /traces /approvals /stt 代理到 :8000
```

---

## 5. 常见问题

- **`PermissionDeniedError: 403 Your request was blocked.`** — 代理网关 WAF 拦截 OpenAI SDK 的 `x-stainless-*` 指纹头。`main.py` 自动剥除，确保是最新代码即可。
- **腾讯云 ASR `User is unopened`** — 账号没开通"一句话识别"。去 https://console.cloud.tencent.com/asr 开通，5 万次/月免费。
- **`empty transcript`** — 录音时长不足 0.6 秒或环境太吵。
- **`.agent_sessions.db` / `.agent_traces.db` 删不掉** — 后端进程持有 SQLite 句柄。先停后端再删。

---

## 6. License

延续 origin/main 的 MIT 协议。
