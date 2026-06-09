"""One-time data-prep: precompute dense embeddings for the knowledge base so
search_policy can do semantic (vector) retrieval instead of keyword overlap.

Model: text-embedding-v4 via Alibaba Cloud Model Studio (百炼/DashScope),
OpenAI-compatible endpoint. Each record is embedded from `title + "\n" + text`
with text_type="document"; vectors are L2-normalized so cosine similarity
reduces to a dot product at query time.

Run (from python-backend):  python -m ecommerce.build_embeddings
Requires env var DASHSCOPE_API_KEY (loaded from .env).
Output:
  ecommerce/knowledge/knowledge_emb.npy        float32 (N, D), L2-normalized
  ecommerce/knowledge/knowledge_emb_meta.json  {model, dim, count, built_at}
The row order matches knowledge_base.jsonl line order (aligned by index).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from .embeddings import EMB_DIM, EMB_MODEL, embed_texts

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
JSONL_PATH = KNOWLEDGE_DIR / "knowledge_base.jsonl"
EMB_PATH = KNOWLEDGE_DIR / "knowledge_emb.npy"
META_PATH = KNOWLEDGE_DIR / "knowledge_emb_meta.json"


def _load_records() -> list[dict]:
    records: list[dict] = []
    with JSONL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _doc_text(record: dict) -> str:
    title = record.get("title", "") or ""
    text = record.get("text", "") or ""
    return f"{title}\n{text}".strip()


def main() -> None:
    records = _load_records()
    docs = [_doc_text(r) for r in records]
    print(f"Embedding {len(docs)} records with {EMB_MODEL} (dim={EMB_DIM}) ...")

    t0 = time.time()
    vectors = embed_texts(docs, text_type="document")
    np.save(EMB_PATH, vectors)

    meta = {
        "model": EMB_MODEL,
        "dim": int(vectors.shape[1]),
        "count": int(vectors.shape[0]),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {vectors.shape} -> {EMB_PATH.name} in {time.time() - t0:.1f}s")
    print(f"Meta: {meta}")


if __name__ == "__main__":
    main()
