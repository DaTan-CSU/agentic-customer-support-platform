"""Speech-to-text via 腾讯云 ASR "一句话识别" (SentenceRecognition).

Why Tencent over 百炼: 百炼's OpenAI-compat path returns 404 for audio, and
its dashscope realtime model in sync mode produces low-accuracy transcripts
on short clips. Tencent's SentenceRecognition is a REST sync API tuned for
≤60s pushes, with the same engine family that powers 微信输入法 — best
Chinese accuracy of the options that don't need a backend audio pipeline.

Audio in: the frontend already uploads 16kHz mono WAV (encoded client-side
via Web Audio API). Tencent's `EngSerViceType="16k_zh"` matches that
directly. Container conversion stays in the browser because we don't ship
ffmpeg/pydub.

Fail-mode: returns dict with `error` populated; /stt route forwards it to
the UI so the user sees the actual cause (auth / rate / format) instead of
a swallowed "empty transcript".
"""

from __future__ import annotations

import asyncio
import base64
import os

try:
    from tencentcloud.common import credential  # type: ignore
    from tencentcloud.common.profile.client_profile import ClientProfile  # type: ignore
    from tencentcloud.common.profile.http_profile import HttpProfile  # type: ignore
    from tencentcloud.asr.v20190614 import asr_client, models  # type: ignore
    _IMPORT_ERR: str | None = None
except Exception as _exc:  # pragma: no cover
    credential = None  # type: ignore
    asr_client = None  # type: ignore
    models = None  # type: ignore
    _IMPORT_ERR = f"tencentcloud-sdk-python-asr not available: {_exc}"


# "16k_zh" = 16kHz Chinese general model — matches the WAV the frontend
# produces. Plenty accurate for JD customer service utterances.
ENGINE = "16k_zh"
# VoiceFormat enum: 12 = wav (we send 16kHz mono PCM in WAV container).
VOICE_FORMAT_WAV = "wav"


def _sync_recognize(audio_bytes: bytes) -> dict:
    if asr_client is None or models is None or credential is None:
        return {"text": "", "error": _IMPORT_ERR or "tencentcloud sdk missing"}
    sid = os.environ.get("TENCENTCLOUD_SECRET_ID")
    skey = os.environ.get("TENCENTCLOUD_SECRET_KEY")
    if not sid or not skey:
        return {"text": "", "error": "TENCENTCLOUD_SECRET_ID/KEY not set"}

    try:
        cred = credential.Credential(sid, skey)
        http_profile = HttpProfile(endpoint="asr.tencentcloudapi.com")
        client_profile = ClientProfile(httpProfile=http_profile)
        client = asr_client.AsrClient(cred, "", client_profile)

        req = models.SentenceRecognitionRequest()
        req.EngSerViceType = ENGINE
        req.SourceType = 1  # 1 = audio data inline (base64)
        req.VoiceFormat = VOICE_FORMAT_WAV
        req.UsrAudioKey = "jd-cs-demo"
        req.Data = base64.b64encode(audio_bytes).decode("ascii")
        req.DataLen = len(audio_bytes)
        resp = client.SentenceRecognition(req)
    except Exception as exc:
        return {"text": "", "error": f"{type(exc).__name__}: {exc}"}

    text = (getattr(resp, "Result", "") or "").strip()
    if not text:
        return {"text": "", "error": "empty transcript"}
    return {"text": text, "error": None}


async def transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> dict:
    """Transcribe a single short audio blob (16kHz mono WAV) to Chinese text."""
    if not audio_bytes:
        return {"text": "", "error": "empty audio"}
    # Tencent's "一句话识别" hard-caps payload at 1MB base64-encoded.
    if len(audio_bytes) > 750_000:
        return {"text": "", "error": f"audio too large ({len(audio_bytes)} bytes); keep <60s"}
    return await asyncio.to_thread(_sync_recognize, audio_bytes)
