"use client";

import { ChatKit, useChatKit } from "@openai/chatkit-react";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { UserRound, TicketCheck, ShieldAlert } from "lucide-react";
import { VoiceInput } from "./voice-input";

type ChatKitPanelProps = {
  initialThreadId?: string | null;
  threadId?: string | null;
  onThreadChange?: (threadId: string | null) => void;
  onResponseEnd?: () => void;
  onRunnerUpdate?: () => void;
  onRunnerEventDelta?: (events: any[]) => void;
  onRunnerBindThread?: (threadId: string) => void;
  context?: Record<string, any>;
};

const CHATKIT_DOMAIN_KEY =
  process.env.NEXT_PUBLIC_CHATKIT_DOMAIN_KEY ?? "domain_pk_localhost_dev";

export function ChatKitPanel({
  initialThreadId,
  threadId,
  onThreadChange,
  onResponseEnd,
  onRunnerUpdate,
  onRunnerEventDelta,
  onRunnerBindThread,
  context,
}: ChatKitPanelProps) {
  const escalated = context?.human_mode === "escalated";
  const ticketId = (context?.ticket_id as string | null) ?? null;

  // Poll /approvals every 2s while a thread is active so we can surface a
  // banner that explains the spinner ("the model is paused waiting for a
  // human approval", which otherwise looks identical to a stuck network).
  const [pendingApprovals, setPendingApprovals] = useState<
    { id: string; summary: string }[]
  >([]);
  const lastThreadRef = useRef<string | null>(null);
  useEffect(() => {
    if (!threadId) {
      setPendingApprovals([]);
      return;
    }
    lastThreadRef.current = threadId;
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch(`/approvals?thread_id=${encodeURIComponent(threadId)}`);
        const d = await r.json();
        if (cancelled || lastThreadRef.current !== threadId) return;
        const pending = (Array.isArray(d.approvals) ? d.approvals : []).filter(
          (a: any) => a.status === "pending"
        );
        setPendingApprovals(
          pending.map((a: any) => ({ id: a.id, summary: a.summary || "" }))
        );
      } catch {
        // fail-quiet — banner just stays as last known
      }
    };
    void poll();
    const t = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [threadId]);
  const chatkit = useChatKit({
    api: {
      url: "/chatkit",
      domainKey: CHATKIT_DOMAIN_KEY,
      uploadStrategy: { type: "two_phase" },
    },
    composer: {
      placeholder: "输入消息…",
      attachments: {
        enabled: true,
        maxCount: 1,
        maxSize: 10 * 1024 * 1024,
        accept: {
          "image/*": [".png", ".jpg", ".jpeg", ".webp", ".gif"],
        },
      },
    },
    history: {
      enabled: true,
    },
    theme: {
      colorScheme: "light",
      radius: "round",
      density: "normal",
      color: {
        accent: {
          primary: "#2563eb",
          level: 1,
        },
      },
    },
    initialThread: initialThreadId ?? null,
    startScreen: {
      greeting: "你好！我是京东购物助手，可以帮你查订单、查物流、查售后政策。",
      prompts: [
        {
          label: "查订单",
          prompt: "帮我查一下订单 DMO20260101 的信息",
        },
        {
          label: "查物流",
          prompt: "订单 DMO20260101 的物流到哪了？",
        },
        {
          label: "退货政策",
          prompt: "京东自营的7天无理由退货规则是怎样的？",
        },
      ],
    },
    threadItemActions: {
      feedback: false,
    },
    onThreadChange: ({ threadId }) => onThreadChange?.(threadId ?? null),
    onResponseEnd: () => onResponseEnd?.(),
    onError: ({ error }) => {
      console.error("ChatKit error", error);
    },
    onEffect: async ({ name, data }) => {
      const effectData = data as { events?: any[]; thread_id?: string } | undefined;
      if (name === "runner_state_update") {
        onRunnerUpdate?.();
      }
      if (name === "runner_event_delta") {
        onRunnerEventDelta?.(effectData?.events ?? []);
      }
      if (name === "runner_bind_thread") {
        const tid = effectData?.thread_id;
        if (tid) {
          onRunnerBindThread?.(tid);
        }
      }
    },
  });

  // Voice-input → composer plumbing. ChatKit React exposes `setComposerValue`
  // on the hook return; we hand it the STT transcript so the text lands in
  // the composer textarea for the user to review and send manually.
  const handleVoiceTranscribed = useCallback(
    (text: string) => {
      try {
        chatkit.setComposerValue?.({ text });
        chatkit.focusComposer?.();
      } catch (err) {
        console.error("setComposerValue failed", err);
      }
    },
    [chatkit]
  );

  return (
    <div className="flex flex-col h-full flex-1 bg-white shadow-sm border border-gray-200 border-t-0 rounded-xl relative">
      <div className="bg-blue-600 text-white h-12 px-4 flex items-center gap-3 rounded-t-xl">
        <UserRound className="h-5 w-5" />
        <h2 className="font-semibold text-sm sm:text-base lg:text-lg">
          Customer View
        </h2>
        <span className="ml-auto flex items-center gap-2 text-xs font-light tracking-wide opacity-90">
          {escalated && ticketId && (
            <span className="inline-flex items-center gap-1 bg-amber-500/90 text-white rounded-full px-2 py-0.5 text-[11px]">
              <TicketCheck className="h-3 w-3" />
              已升级人工 · 工单 {ticketId}
            </span>
          )}
          京东客服
        </span>
      </div>
      {pendingApprovals.length > 0 && (
        <div className="flex items-center gap-2 bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-2 text-xs">
          <ShieldAlert className="h-4 w-4 text-amber-600 flex-shrink-0" />
          <span className="font-medium">等待人工审批：</span>
          <span className="truncate flex-1">
            {pendingApprovals[0].summary}
          </span>
          <span className="text-amber-700 whitespace-nowrap">
            请到右侧 Agent View · Approvals 面板点 Approve / Reject
          </span>
        </div>
      )}
      <div className="flex-1 overflow-hidden">
        <ChatKit
          control={chatkit.control}
          className="block h-full w-full"
          style={{ height: "100%", width: "100%" }}
        />
      </div>
      <VoiceInput onTranscribed={handleVoiceTranscribed} />
    </div>
  );
}
