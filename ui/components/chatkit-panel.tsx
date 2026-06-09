"use client";

import { ChatKit, useChatKit } from "@openai/chatkit-react";
import React, { useCallback } from "react";
import { VoiceInput } from "./voice-input";

type ChatKitPanelProps = {
  initialThreadId?: string | null;
  onThreadChange?: (threadId: string | null) => void;
  onResponseEnd?: () => void;
  onRunnerUpdate?: () => void;
  onRunnerEventDelta?: (events: any[]) => void;
  onRunnerBindThread?: (threadId: string) => void;
};

const CHATKIT_DOMAIN_KEY =
  process.env.NEXT_PUBLIC_CHATKIT_DOMAIN_KEY ?? "domain_pk_localhost_dev";

export function ChatKitPanel({
  initialThreadId,
  onThreadChange,
  onResponseEnd,
  onRunnerUpdate,
  onRunnerEventDelta,
  onRunnerBindThread,
}: ChatKitPanelProps) {
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
    <div className="flex flex-col h-full flex-1 bg-white shadow-sm border border-gray-200 border-t-0 rounded-xl">
      <div className="bg-blue-600 text-white h-12 px-4 flex items-center rounded-t-xl">
        <h2 className="font-semibold text-sm sm:text-base lg:text-lg">
          Customer View
        </h2>
      </div>
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
