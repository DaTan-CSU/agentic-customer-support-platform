"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Loader2 } from "lucide-react";

interface VoiceInputProps {
  onTranscribed: (text: string) => void;
}

type Status = "idle" | "recording" | "transcribing" | "error";

const MIN_RECORD_MS = 600;
// dashscope paraformer expects 16kHz mono PCM; everything else returns empty
// transcripts. We decode the browser's webm/opus locally, downsample, and
// upload a plain WAV — no backend deps (no ffmpeg / no pydub).
const TARGET_RATE = 16000;

// -- WAV encoding helpers ---------------------------------------------------

function encodeWav16k(samples: Float32Array, sampleRate: number): Blob {
  // 16-bit PCM mono WAV. samples are -1..1 Float32 from AudioBuffer.
  const dataLen = samples.length * 2;
  const buf = new ArrayBuffer(44 + dataLen);
  const view = new DataView(buf);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataLen, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits
  writeStr(36, "data");
  view.setUint32(40, dataLen, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }
  return new Blob([buf], { type: "audio/wav" });
}

async function blobToWav16k(blob: Blob): Promise<Blob> {
  // decodeAudioData understands whatever the browser MediaRecorder emitted
  // (webm/opus on Chrome, ogg/opus on Firefox, mp4/aac on Safari).
  const arr = await blob.arrayBuffer();
  const ctx = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await ctx.decodeAudioData(arr.slice(0));
  } finally {
    void ctx.close();
  }
  // Downmix to mono and resample to 16kHz via OfflineAudioContext.
  const lengthAtTarget = Math.ceil(decoded.duration * TARGET_RATE);
  const offline = new OfflineAudioContext(1, lengthAtTarget, TARGET_RATE);
  const src = offline.createBufferSource();
  // Re-pack as mono if stereo.
  if (decoded.numberOfChannels > 1) {
    const mono = offline.createBuffer(1, decoded.length, decoded.sampleRate);
    const monoData = mono.getChannelData(0);
    const chs: Float32Array[] = [];
    for (let c = 0; c < decoded.numberOfChannels; c++) chs.push(decoded.getChannelData(c));
    for (let i = 0; i < decoded.length; i++) {
      let v = 0;
      for (let c = 0; c < chs.length; c++) v += chs[c][i];
      monoData[i] = v / chs.length;
    }
    src.buffer = mono;
  } else {
    src.buffer = decoded;
  }
  src.connect(offline.destination);
  src.start(0);
  const rendered = await offline.startRendering();
  return encodeWav16k(rendered.getChannelData(0), TARGET_RATE);
}

// -- Component --------------------------------------------------------------

export function VoiceInput({ onTranscribed }: VoiceInputProps) {
  const [status, setStatus] = useState<Status>("idle");
  const [errMsg, setErrMsg] = useState<string>("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);

  useEffect(() => {
    return () => {
      try {
        recorderRef.current?.stop();
      } catch {}
      recorderRef.current?.stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const start = useCallback(async () => {
    if (status === "recording" || status === "transcribing") return;
    setErrMsg("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        const heldMs = Date.now() - startedAtRef.current;
        stream.getTracks().forEach((t) => t.stop());
        if (heldMs < MIN_RECORD_MS || chunksRef.current.length === 0) {
          setStatus("idle");
          return;
        }
        const sourceBlob = new Blob(chunksRef.current, {
          type: rec.mimeType || "audio/webm",
        });
        chunksRef.current = [];
        setStatus("transcribing");
        try {
          const wav = await blobToWav16k(sourceBlob);
          const form = new FormData();
          form.append("audio", wav, "clip.wav");
          const res = await fetch("/stt", { method: "POST", body: form });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          const text = typeof data.text === "string" ? data.text.trim() : "";
          if (text) {
            onTranscribed(text);
            setStatus("idle");
          } else {
            setErrMsg(data.error || "未识别到内容");
            setStatus("error");
          }
        } catch (e: any) {
          setErrMsg(String(e?.message ?? e));
          setStatus("error");
        }
      };
      recorderRef.current = rec;
      rec.start();
      startedAtRef.current = Date.now();
      setStatus("recording");
    } catch (e: any) {
      setErrMsg("无法访问麦克风：" + String(e?.message ?? e));
      setStatus("error");
    }
  }, [status, onTranscribed]);

  const stop = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state === "recording") {
      try {
        rec.stop();
      } catch {}
    }
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    void start();
  };
  const onPointerUp = (e: React.PointerEvent) => {
    e.preventDefault();
    stop();
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.code === "Space" && (status === "idle" || status === "error")) {
      e.preventDefault();
      void start();
    }
  };
  const onKeyUp = (e: React.KeyboardEvent) => {
    if (e.code === "Space" && status === "recording") {
      e.preventDefault();
      stop();
    }
  };

  let label = "按住说话";
  let cls = "bg-blue-600 hover:bg-blue-700";
  if (status === "recording") {
    label = "松开发送…";
    cls = "bg-rose-600 hover:bg-rose-700 animate-pulse";
  } else if (status === "transcribing") {
    label = "识别中…";
    cls = "bg-amber-500";
  } else if (status === "error") {
    label = "重试";
    cls = "bg-gray-500 hover:bg-gray-600";
  }

  return (
    <div className="flex items-center gap-3 px-3 py-2 border-t border-gray-200 bg-white">
      <button
        type="button"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        disabled={status === "transcribing"}
        className={`inline-flex items-center gap-2 text-white text-sm px-3 py-1.5 rounded-full transition ${cls} disabled:opacity-60 select-none`}
      >
        {status === "transcribing" ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
        <span>{label}</span>
      </button>
      {status === "error" && errMsg && (
        <span className="text-xs text-rose-600 truncate flex-1">{errMsg}</span>
      )}
    </div>
  );
}
