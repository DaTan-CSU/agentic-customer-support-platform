"use client";

import { FileText, AlertCircle } from "lucide-react";
import { PanelSection } from "./panel-section";

export interface CaseSummary {
  intent: string;
  order_id: string | null;
  sentiment: string;
  action_taken: string;
  follow_up_needed: boolean;
  follow_up_note: string;
}

interface Props {
  summary: CaseSummary | null | undefined;
}

const SENTIMENT_STYLE: Record<string, string> = {
  满意: "bg-emerald-50 text-emerald-700 border-emerald-200",
  中立: "bg-slate-50 text-slate-700 border-slate-200",
  不满: "bg-amber-50 text-amber-700 border-amber-200",
  投诉: "bg-rose-50 text-rose-700 border-rose-200",
};

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[11px] text-gray-500">{label}</span>
      <span className="text-sm text-gray-900 truncate">{value}</span>
    </div>
  );
}

export function SummaryPanel({ summary }: Props) {
  return (
    <PanelSection
      title="会话摘要"
      icon={<FileText className="h-4 w-4 text-blue-600" />}
    >
      {summary ? (
        <div className="rounded-md border border-gray-200 bg-white shadow-sm p-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 mb-2">
            <Field
              label="意图"
              value={
                <span className="inline-flex items-center text-[12px] border border-blue-200 bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                  {summary.intent}
                </span>
              }
            />
            <Field
              label="情绪"
              value={
                <span
                  className={`inline-flex items-center text-[12px] border px-2 py-0.5 rounded ${
                    SENTIMENT_STYLE[summary.sentiment] ?? "bg-gray-50 text-gray-700 border-gray-200"
                  }`}
                >
                  {summary.sentiment}
                </span>
              }
            />
            <Field
              label="订单号"
              value={
                <span className="font-mono text-[12px] text-gray-800">
                  {summary.order_id ?? "—"}
                </span>
              }
            />
            <Field
              label="后续跟进"
              value={
                summary.follow_up_needed ? (
                  <span className="inline-flex items-center gap-1 text-[12px] text-amber-700">
                    <AlertCircle className="h-3 w-3" />
                    需要
                  </span>
                ) : (
                  <span className="text-[12px] text-gray-500">不需要</span>
                )
              }
            />
          </div>
          <div className="border-t border-gray-100 pt-2 mt-1 space-y-1">
            <div>
              <span className="text-[11px] text-gray-500">已执行</span>
              <p className="text-sm text-gray-900 mt-0.5">{summary.action_taken}</p>
            </div>
            {summary.follow_up_needed && summary.follow_up_note && (
              <div>
                <span className="text-[11px] text-gray-500">跟进备注</span>
                <p className="text-sm text-amber-800 mt-0.5">{summary.follow_up_note}</p>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="rounded-md border border-gray-200 bg-gray-50 shadow-sm p-3 text-xs text-gray-500 text-center">
          摘要将在助手回复后自动生成
        </div>
      )}
    </PanelSection>
  );
}
