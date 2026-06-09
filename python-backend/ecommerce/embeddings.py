"""Shared embedding client for the knowledge-base RAG.

Uses Alibaba Cloud Model Studio (百炼 / DashScope) text-embedding-v4 via its
OpenAI-compatible endpoint. Documents are embedded once (build_embeddings.py)
and cached on disk; only the user query is embedded at request time.

Requires env var DASHSCOPE_API_KEY (kept in python-backend/.env).
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

EMB_MODEL = "text-embedding-v4"
EMB_DIM = 1024
EMB_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_BATCH = 10  # text-embedding-v4 OpenAI-compatible batch size limit


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY not set")
    return OpenAI(api_key=key, base_url=EMB_BASE_URL)


def embed_texts(texts: list[str], text_type: str = "document") -> np.ndarray:
    """Embed texts and return an L2-normalized float32 matrix (len(texts), EMB_DIM).

    text_type: "document" for knowledge passages, "query" for the user query
    (text-embedding-v4 optimizes each side differently).
    """
    client = _client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        chunk = texts[start : start + _BATCH]
        resp = client.embeddings.create(
            model=EMB_MODEL,
            input=chunk,
            dimensions=EMB_DIM,
            encoding_format="float",
            extra_body={"text_type": text_type},
        )
        for item in sorted(resp.data, key=lambda d: d.index):
            vectors.append(item.embedding)

    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms
