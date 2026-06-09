# 仓库能力路线图

本文件记录当前仓库已实现的能力、与 OpenAI Agents Python SDK 仍存在的差距，以及按执行优先级排序的待办。每次完成一项后请勾掉对应条目并更新"最后修订"。

最后修订：2026-06-09

---

## 一、当前已实现能力（与 SDK 的映射）

| 能力 | 实现位置 | 使用的 SDK 特性 |
|---|---|---|
| 多 agent 编排（triage + 4 专员） | `ecommerce/agents.py` | `Agent` / `handoff` |
| Handoff 工具命名稳定（避免中文同名碰撞） | `agents.py:_HANDOFF_TOOL_NAMES` | `handoff(tool_name_override=...)` |
| 输入护栏（相关性 / 越狱） | `ecommerce/guardrails.py` | `@input_guardrail` + tripwire |
| 输出护栏（PII / 虚假承诺 / 品牌中立） | `ecommerce/output_guardrails.py` | `@output_guardrail` + `OutputGuardrailTripwireTriggered` |
| 函数工具 + Pydantic 参数 | `ecommerce/tools.py` | `@function_tool` |
| 工具内写 ChatContext 副作用（order_id / sku / last_intent） | `ecommerce/tools.py` | `RunContextWrapper.context` |
| Session 持久化（SQLite 落盘） | `server.py:_session_for_thread` | `SQLiteSession` |
| Trace 本地落盘 + 自绘前端面板 | `tracing_store.py`、`ui/components/traces-panel.tsx` | `TracingProcessor` / `set_trace_processors` / `agents_trace(metadata={...})` |
| 结构化输出（CaseSummary） | `ecommerce/summary_agent.py` | `Agent(output_type=PydanticModel)` |
| MCP stdio 服务（含子进程拉起） | `jd_mcp_server.py`、`main.py:_start_realbackend_and_mcp` | `MCPServerStdio` + `MCPServerStdioParams` |
| Agents-as-tools 多意图并行 fan-out | `agents.py:_ask_*` | `Agent.as_tool(...)` |
| Streamed Runner + ChatKit 集成 | `server.py:respond` | `Runner.run_streamed` + `chatkit.agents.stream_agent_response` |
| 图片输入（vision） | `ecommerce/vision.py`、`server.py:_describe_attachments` | 自接，非 SDK |
| 语音输入（STT） | `ecommerce/stt.py`、`ui/components/voice-input.tsx` | 自接腾讯 ASR |
| 工具审批（软审批） | `ecommerce/approvals.py`、`main.py:/approvals` | 自实现，未用 SDK 原生 `needs_approval=True` |
| 第三方代理 + WAF 头剥除 | `main.py:_strip_sdk_fingerprint` | `AsyncOpenAI(http_client=...)` + `set_default_openai_client` |

---

## 二、与 OpenAI Agents Python SDK 的能力差距

下表按"执行优先级"排序：靠前的"易做且价值高"，靠后的要么受外部限制（如代理不支持），要么实现成本极高。

### P0 ── 当前栈下小成本就能拿下的

| # | SDK 能力 | 当前空缺 | 落地建议 | 工作量 |
|---|---|---|---|---|
| 1 | `tool_input_guardrail` / `tool_output_guardrail` | 我们只有 agent 级 guardrail | 给敏感工具（退款 / 取消 / 价保）单独加 input/output 护栏，比 agent 级粒度更细 | 小（半天） |
| 2 | `ModelSettings`（temperature / top_p / parallel_tool_calls） | 全程默认值 | 给 guardrail/summary agent 调低 temperature 以稳定；给 triage 关 parallel_tool_calls 避免误并行 | 小（1 小时） |
| 3 | `AgentHooks` 生命周期（before_call / after_call） | 没用过 | 在 hook 里集中打 trace span / 写审计日志，比散落在 server.respond 干净 | 小（半天） |
| 4 | `Runner.run` 异常分类 | 现在只显式接 2 类（MaxTurnsExceeded、InputGuardrailTripwireTriggered、OutputGuardrailTripwireTriggered） | 补 `ModelBehaviorError` / `BadRequestError` / `OpenAIServerError` 分流，给前端可读错误 | 小 |
| 5 | 多 thread 真并发隔离压测 | `MemoryStore` 是进程全局 dict，并发未压过 | 跑 10-50 并发 thread，看 `_listeners` / state 是否串扰 | 中 |

### P1 ── 能让 demo 真升一档但需要少量服务对接

| # | SDK 能力 | 当前空缺 | 落地建议 | 工作量 / 阻塞 |
|---|---|---|---|---|
| 6 | SDK 原生 `needs_approval=True` 工具流 | 我们用软审批 | 改 1 个工具试水：Runner 在该 tool 处暂停，前端拿到 interrupted 状态，点 Approve 后调 `Runner.run` 续跑（带 approvals 参数） | 中 |
| 7 | 双向语音 = TTS 回声 | 只有语音输入 | 接腾讯 TTS / 百炼 cosyvoice，assistant 回复后给一段 mp3，前端 `<audio>` 播放 | 中 |
| 8 | Tracing → 外部出口（Datadog / Langfuse） | 只本地 SQLite | 多挂一个 processor，并发存两份；可接生产监控 | 小 |
| 9 | 评测 / regression fixture | 没有 | 起一份 fixture YAML（问题 → 期望 intent / 期望工具调用 / 期望 guardrail 命中），后端跑一次出报告 | 中 |
| 10 | `HostedMCPTool`（OpenAI 托管的 MCP） | 用自管 stdio | 接 OpenAI 平台托管 MCP，省一个子进程 | **阻塞**：需 OpenAI 直连，代理不支持 |

### P2 ── 想做但受外部环境限制 / 大改造

| # | SDK 能力 | 阻塞点 |
|---|---|---|
| 11 | 真 OpenAI Realtime（gpt-realtime 模型 WS 双向流） | 当前代理（AI巴士 / 词元神 / 猫猫小铺）一概不支持；需 OpenAI 直连账号 |
| 12 | Voice Agent 完整套件（SDK 内 voice helpers） | 同上 |
| 13 | Computer Use Agent（操控浏览器 / 桌面） | 模型 + 沙盒环境要求高 |
| 14 | 多模态 assistant 输出（生成图 / 文件） | 模型 + 代理需支持 |
| 15 | Response API native streaming | 我们用 `chat_completions`，迁移到 `responses` 需考虑代理兼容性 |
| 16 | Prompt Caching | 取决于代理是否支持 OpenAI 缓存协议 |

---

## 三、工程化 / 易维护性遗留

下面这些不是 SDK 特性差距，是仓库本身的工程债，跟新功能不冲突，建议穿插着做。

| # | 项 | 建议 |
|---|---|---|
| W1 | 没有 README 架构图 / 启动文档 | 写 README + 一张 Mermaid 数据流图 |
| W2 | 没有 docker-compose | 一份 compose：next + main:8000 + jd_realbackend:9001 一键起 |
| W3 | 没有 CI | GitHub Actions：`pytest` + `npx tsc --noEmit` 跑两步 |
| W4 | `MemoryStore` 是内存 dict | 接 Redis / Postgres，多副本可水平扩 |
| W5 | `ApprovalStore` 是内存 dict | 同上 |
| W6 | `server.py` 630 行单文件 | 拆 `ChatKitChannel` + `RunnerLoop` + `SummaryRefresher` + `ListenerHub` 子模块 |
| W7 | 凭证全靠 `~/Desktop/API key.txt` | 给 `.env.example` + 文档说明，向标准 `.env` 迁移 |
| W8 | `dd7c927` commit 信息含 BOM | `git rebase -i` 重写信息（非必须） |

---

## 四、接下来推荐 3 步（按顺序）

### 第七步：**README + Docker compose + GitHub Actions CI**
对应 W1 + W2 + W3。**先做这步**的原因：每次都靠你手动启动 npm + uvicorn 两端，新机器零配置跑起来才是稳定 demo 的前提。CI 跑 `pytest` + `tsc` 防止后续步骤把回归测试改裂。

### 第八步：**双向语音 = 腾讯云 TTS 接 cosyvoice / minimax voice**
对应 P1 #7。完成后真正闭环"按住说话 → 模型答 → 语音读出"。腾讯云密钥已有，复用 `Tencent` 客户端，新增 `/tts` 路由 + 前端 `<audio>` 播放节点。预计半天。

### 第九步：**SDK 原生 `needs_approval=True` 替换软审批**
对应 P1 #6。把退款工具切到 SDK 原生 HITL：Runner 在工具调用前暂停，前端从 `interrupted` 状态拿待批列表，POST `/approvals/{id}` 完成后调 `Runner.run` 续跑。这一改演示了 SDK 真正想给的"流程级"审批，比软审批高一档。预计 1 天。

---

附：commit 历史（最新在上）

```
cb26dc6 feat(advanced): agents-as-tools fan-out + Tencent ASR voice input
915e0a9 feat(mcp): MCP stdio server + standalone real backend
5aae695 feat(struct): CaseSummary structured output
dd7c927 feat(safety): output guardrails + soft approvals
77f937e feat(tracing): local SQLite trace processor + Traces panel
30e6a00 feat(base): JD e-commerce demo with SQLite session persistence
```
