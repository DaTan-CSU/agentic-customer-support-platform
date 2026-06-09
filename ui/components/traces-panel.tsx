"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PanelSection } from "./panel-section";

interface Span {
  span_id: string;
  parent_id: string | null;
  kind: string | null;
  name: string | null;
  started_at: string | null;
  ended_at: string | null;
  error: any;
  data: any;
}

interface TraceRecord {
  trace_id: string;
  workflow_name: string | null;
  thread_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  spans: Span[];
}

interface TracesPanelProps {
  threadId: string | null;
  refetchKey?: number;
}

// SDK SpanData type → 中文短 chip + 颜色。Key 保留 SDK 原值不动。
const KIND_STYLE: Record<string, { label: string; cls: string }> = {
  agent: { label: "客服", cls: "bg-blue-50 text-blue-700 border-blue-200" },
  generation: { label: "模型", cls: "bg-purple-50 text-purple-700 border-purple-200" },
  function: { label: "工具", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  guardrail: { label: "护栏", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  handoff: { label: "交接", cls: "bg-rose-50 text-rose-700 border-rose-200" },
  response: { label: "响应", cls: "bg-slate-50 text-slate-700 border-slate-200" },
  mcp_tools: { label: "MCP", cls: "bg-cyan-50 text-cyan-700 border-cyan-200" },
  custom: { label: "区间", cls: "bg-gray-50 text-gray-700 border-gray-200" },
};

function spanLabel(s: Span): string {
  // Prefer the SDK-provided name; fall back to data.name (some span types only
  // expose the operation name through data_json).
  if (s.name) return s.name;
  if (s.data && typeof s.data === "object") {
    const d = s.data as any;
    if (d.name) return d.name;
    if (d.from_agent && d.to_agent) return `${d.from_agent} → ${d.to_agent}`;
    if (d.tool_name) return d.tool_name;
    if (d.model) return d.model;
  }
  return s.kind ?? "(span)";
}

function durationMs(s: Span): string | null {
  if (!s.started_at || !s.ended_at) return null;
  try {
    const a = new Date(s.started_at).getTime();
    const b = new Date(s.ended_at).getTime();
    if (isFinite(a) && isFinite(b) && b >= a) {
      const d = b - a;
      return d < 1000 ? `${d}ms` : `${(d / 1000).toFixed(2)}s`;
    }
  } catch {}
  return null;
}

interface TreeNode {
  span: Span;
  children: TreeNode[];
}

function buildTree(spans: Span[]): TreeNode[] {
  const map = new Map<string, TreeNode>();
  for (const s of spans) map.set(s.span_id, { span: s, children: [] });
  const roots: TreeNode[] = [];
  for (const node of map.values()) {
    const parentId = node.span.parent_id;
    if (parentId && map.has(parentId)) {
      map.get(parentId)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function SpanRow({ node, depth }: { node: TreeNode; depth: number }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const kind = node.span.kind ?? "custom";
  const style = KIND_STYLE[kind] ?? KIND_STYLE.custom;
  const dur = durationMs(node.span);
  const hasChildren = node.children.length > 0;
  const isError = !!node.span.error;

  return (
    <div>
      <div
        className={`flex items-center gap-2 py-1 px-2 rounded hover:bg-gray-50 ${
          isError ? "bg-red-50" : ""
        }`}
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="w-4 h-4 flex items-center justify-center text-gray-400"
          aria-label={expanded ? "collapse" : "expand"}
        >
          {hasChildren ? (
            expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )
          ) : (
            <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
          )}
        </button>
        <span
          className={`text-[10px] uppercase tracking-wide border px-1.5 py-0.5 rounded ${style.cls}`}
        >
          {style.label}
        </span>
        <span className="text-sm text-gray-800 truncate flex-1" title={spanLabel(node.span)}>
          {spanLabel(node.span)}
        </span>
        {dur && (
          <span className="text-[11px] text-gray-500 font-mono shrink-0">{dur}</span>
        )}
      </div>
      {expanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <SpanRow key={child.span.span_id} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
      {expanded && isError && (
        <pre
          className="text-[11px] bg-red-50 text-red-800 border border-red-100 rounded mx-2 mt-1 p-2 whitespace-pre-wrap break-words"
          style={{ marginLeft: `${depth * 14 + 28}px` }}
        >
          {JSON.stringify(node.span.error, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function TracesPanel({ threadId, refetchKey }: TracesPanelProps) {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!threadId) {
      setTraces([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetch(`/traces?thread_id=${encodeURIComponent(threadId)}`)
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        setTraces(Array.isArray(d.traces) ? d.traces : []);
        setErr(null);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(String(e));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [threadId, refetchKey]);

  const trees = useMemo(
    () =>
      traces.map((t) => ({
        trace: t,
        roots: buildTree(t.spans),
      })),
    [traces]
  );

  return (
    <PanelSection
      title="调用链"
      icon={<Activity className="h-4 w-4 text-blue-600" />}
    >
      <ScrollArea className="h-64 rounded-md border border-gray-200 bg-gray-100 shadow-sm">
        <div className="p-2 space-y-3">
          {loading && traces.length === 0 && (
            <p className="text-center text-zinc-500 p-3 text-xs">加载中…</p>
          )}
          {err && (
            <p className="text-center text-red-600 p-3 text-xs">{err}</p>
          )}
          {!loading && traces.length === 0 && !err && (
            <p className="text-center text-zinc-500 p-3 text-xs">
              该会话暂无调用链
            </p>
          )}
          {trees.map(({ trace, roots }) => (
            <div
              key={trace.trace_id}
              className="bg-white rounded-md border border-gray-200 shadow-sm"
            >
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
                <span className="text-sm font-medium text-gray-800 truncate">
                  {trace.workflow_name || trace.trace_id}
                </span>
                <span className="text-[10px] font-mono text-gray-400 shrink-0">
                  {trace.trace_id.slice(-8)}
                </span>
              </div>
              <div className="py-1">
                {roots.map((node) => (
                  <SpanRow key={node.span.span_id} node={node} depth={0} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </PanelSection>
  );
}
