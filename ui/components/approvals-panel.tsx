"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Check, X, Clock } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PanelSection } from "./panel-section";

type ApprovalKind = "refund" | "order_cancel" | "price_protection";
type ApprovalStatus = "pending" | "approved" | "rejected";

interface ApprovalRequest {
  id: string;
  thread_id: string;
  kind: ApprovalKind;
  tool_name: string;
  args: Record<string, any>;
  summary: string;
  status: ApprovalStatus;
  created_at: number;
  decided_at: number | null;
  operator_note: string | null;
  execution_result: string | null;
}

interface Props {
  threadId: string | null;
  refetchKey?: number;
  onDecided?: () => void;
}

const KIND_LABEL: Record<ApprovalKind, string> = {
  refund: "退款",
  order_cancel: "订单取消",
  price_protection: "价保理赔",
};

const KIND_BADGE: Record<ApprovalKind, string> = {
  refund: "bg-rose-50 text-rose-700 border-rose-200",
  order_cancel: "bg-amber-50 text-amber-700 border-amber-200",
  price_protection: "bg-violet-50 text-violet-700 border-violet-200",
};

function timeStr(ts: number | null): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ApprovalsPanel({ threadId, refetchKey, onDecided }: Props) {
  const [items, setItems] = useState<ApprovalRequest[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const fetchList = useCallback(async () => {
    if (!threadId) return;
    try {
      const r = await fetch(`/approvals?thread_id=${encodeURIComponent(threadId)}`);
      const d = await r.json();
      setItems(Array.isArray(d.approvals) ? d.approvals : []);
    } catch {
      // fail-quiet: panel just stays as last known.
    }
  }, [threadId]);

  useEffect(() => {
    if (!threadId) {
      setItems([]);
      return;
    }
    void fetchList();
  }, [threadId, refetchKey, fetchList]);

  const decide = useCallback(
    async (approval: ApprovalRequest, decision: "approve" | "reject") => {
      setBusyId(approval.id);
      try {
        await fetch(`/approvals/${approval.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        });
        await fetchList();
        onDecided?.();
      } finally {
        setBusyId(null);
      }
    },
    [fetchList, onDecided]
  );

  const pendingCount = items.filter((i) => i.status === "pending").length;

  return (
    <PanelSection
      title={pendingCount > 0 ? `Pending Approvals (${pendingCount})` : "Approvals"}
      icon={<ShieldCheck className="h-4 w-4 text-blue-600" />}
    >
      <ScrollArea className="h-56 rounded-md border border-gray-200 bg-gray-100 shadow-sm">
        <div className="p-2 space-y-2">
          {items.length === 0 && (
            <p className="text-center text-zinc-500 p-3 text-xs">No approvals yet</p>
          )}
          {items.map((it) => {
            const isPending = it.status === "pending";
            const badge = KIND_BADGE[it.kind] ?? "bg-gray-50 text-gray-700 border-gray-200";
            return (
              <div
                key={it.id}
                className={`bg-white rounded-md border shadow-sm p-3 ${
                  isPending ? "border-blue-200" : "border-gray-200"
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <span
                    className={`text-[10px] uppercase tracking-wide border px-1.5 py-0.5 rounded ${badge}`}
                  >
                    {KIND_LABEL[it.kind] ?? it.kind}
                  </span>
                  <span className="text-[11px] font-mono text-gray-400 truncate flex-1">
                    {it.id}
                  </span>
                  {it.status === "pending" && (
                    <span className="flex items-center gap-1 text-[11px] text-blue-600">
                      <Clock className="h-3 w-3" />
                      待审批
                    </span>
                  )}
                  {it.status === "approved" && (
                    <span className="text-[11px] text-emerald-700">已批准</span>
                  )}
                  {it.status === "rejected" && (
                    <span className="text-[11px] text-rose-700">已拒绝</span>
                  )}
                </div>
                <div className="text-sm text-gray-800 leading-relaxed mb-1">
                  {it.summary}
                </div>
                <div className="flex items-center gap-3 text-[11px] text-gray-500 mb-2">
                  <span>提交 {timeStr(it.created_at)}</span>
                  {it.decided_at && <span>处理 {timeStr(it.decided_at)}</span>}
                </div>
                {it.execution_result && (
                  <div className="text-[12px] bg-emerald-50 text-emerald-800 border border-emerald-100 rounded px-2 py-1 mb-2">
                    {it.execution_result}
                  </div>
                )}
                {isPending && (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busyId === it.id}
                      onClick={() => decide(it, "approve")}
                      className="flex-1 inline-flex items-center justify-center gap-1 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-md transition"
                    >
                      <Check className="h-4 w-4" />
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busyId === it.id}
                      onClick={() => decide(it, "reject")}
                      className="flex-1 inline-flex items-center justify-center gap-1 text-sm bg-white hover:bg-gray-50 disabled:opacity-50 text-gray-700 border border-gray-300 px-3 py-1.5 rounded-md transition"
                    >
                      <X className="h-4 w-4" />
                      Reject
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </PanelSection>
  );
}
