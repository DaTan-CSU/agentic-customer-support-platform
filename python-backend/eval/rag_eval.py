"""RAG retrieval acceptance evaluation.

Run from python-backend/:
    .venv/Scripts/python.exe -m eval.rag_eval

Computes 5 metrics over eval/eval_set.jsonl:
  - Recall@1, Recall@3        (any expected_keyword found in top-K doc)
  - MRR                        (1 / rank of first matching doc)
  - Mean retrieval latency     (embed + cosine sim)
  - Rejection accuracy         (off-topic queries should have TOP-1 sim < threshold)
  - Faithfulness (sample 5)    (LLM-as-judge: are answer claims supported by context)

The 4 functional categories (policy/plus/logistics/product) are scored on the
first 3 metrics; the reject category is scored on rejection accuracy alone.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from ecommerce.embeddings import embed_texts  # noqa: E402
from ecommerce.tools import _MIN_SIMILARITY  # noqa: E402

EVAL_PATH = Path(__file__).with_name("eval_set.jsonl")
KB_DIR = BACKEND_DIR / "ecommerce" / "knowledge"
JSONL_PATH = KB_DIR / "knowledge_base.jsonl"
EMB_PATH = KB_DIR / "knowledge_emb.npy"

TOP_K = 3
REJECT_THRESHOLD = _MIN_SIMILARITY  # 0.45 — same as production search_policy


def _load_kb() -> tuple[list[dict[str, Any]], np.ndarray]:
    records = [json.loads(l) for l in JSONL_PATH.open("r", encoding="utf-8") if l.strip()]
    matrix = np.load(EMB_PATH)
    assert matrix.shape[0] == len(records), "KB / embeddings out of sync"
    return records, matrix


def _load_eval() -> list[dict[str, Any]]:
    return [json.loads(l) for l in EVAL_PATH.open("r", encoding="utf-8") if l.strip()]


def _has_any_kw(doc: dict[str, Any], kws: list[str]) -> bool:
    """Match if ANY expected keyword appears in title+text (case-insensitive)."""
    if not kws:
        return False
    blob = (str(doc.get("title", "")) + " " + str(doc.get("text", ""))).lower()
    return any(kw.lower() in blob for kw in kws)


def evaluate_retrieval(eval_items, records, matrix):
    # Embed all queries in one batch (much faster than one-by-one).
    queries = [item["query"] for item in eval_items]
    t0 = time.time()
    q_mat = embed_texts(queries, text_type="query")
    embed_total = time.time() - t0

    results = []
    search_total = 0.0
    for item, qv in zip(eval_items, q_mat):
        s = time.time()
        sims = matrix @ qv
        top_idx = np.argsort(-sims)[:TOP_K]
        search_total += time.time() - s

        top_docs = [records[i] for i in top_idx]
        top_sims = [float(sims[i]) for i in top_idx]

        if item["expected_reject"]:
            # off-topic — pass if TOP-1 sim falls below the production threshold
            passed_reject = top_sims[0] < REJECT_THRESHOLD
            results.append({
                "id": item["id"], "category": item["category"],
                "query": item["query"], "expected_reject": True,
                "top1_sim": top_sims[0], "rejected_correctly": passed_reject,
            })
            continue

        # functional queries — find rank of first doc whose title/text matches any expected_kw
        rank = None
        for r, doc in enumerate(top_docs, start=1):
            if _has_any_kw(doc, item["expected_keywords"]):
                rank = r
                break
        results.append({
            "id": item["id"], "category": item["category"],
            "query": item["query"], "expected_reject": False,
            "rank_in_top3": rank,
            "top1_title": top_docs[0].get("title", "")[:50],
            "top1_sim": top_sims[0],
            "top1_source": top_docs[0].get("source", ""),
        })

    return results, embed_total / len(queries), search_total / len(queries)


def summarize(results):
    functional = [r for r in results if not r["expected_reject"]]
    reject = [r for r in results if r["expected_reject"]]

    n = len(functional)
    hit1 = sum(1 for r in functional if r["rank_in_top3"] == 1)
    hit3 = sum(1 for r in functional if r["rank_in_top3"] is not None)
    rr_sum = sum(1.0 / r["rank_in_top3"] for r in functional if r["rank_in_top3"])

    reject_correct = sum(1 for r in reject if r["rejected_correctly"])

    return {
        "n_functional": n,
        "n_reject": len(reject),
        "recall_at_1": hit1 / n if n else 0.0,
        "recall_at_3": hit3 / n if n else 0.0,
        "mrr": rr_sum / n if n else 0.0,
        "reject_accuracy": reject_correct / len(reject) if reject else 0.0,
    }


def faithfulness_sample(results, records, matrix, k_samples=5):
    """Cheap LLM-as-judge faithfulness over 5 policy queries.

    For each sampled query we retrieve TOP-3 docs, ask the model to answer using
    ONLY those docs, then ask a judge model: 'Are all claims in the answer
    supported by the context?' Returns mean score in [0,1].
    """
    from openai import OpenAI
    import os

    # Try providers in order; first success wins. The default OPENAI_BASE_URL
    # in .env can be out of credit / 503; DeepSeek is the stable fallback for
    # evaluation runs.
    providers = [
        ("default", os.environ.get("OPENAI_API_KEY"), os.environ.get("OPENAI_BASE_URL"), "gpt-5.4-mini", "gpt-5.4-mini"),
        ("deepseek", os.environ.get("DEEPSEEK_API_KEY"), os.environ.get("DEEPSEEK_BASE_URL"), "deepseek-chat", "deepseek-chat"),
    ]
    client = None
    ans_model = judge_model = None
    for name, key, base, am, jm in providers:
        if not (key and base):
            continue
        try:
            c = OpenAI(api_key=key, base_url=base)
            c.chat.completions.create(
                model=am, messages=[{"role": "user", "content": "ok"}], max_tokens=4
            )
            client, ans_model, judge_model = c, am, jm
            print(f"  Using {name} provider ({am} for answer, {jm} for judge)")
            break
        except Exception as exc:
            print(f"  Provider {name} unavailable: {str(exc)[:80]}")
            continue
    if client is None:
        raise RuntimeError("no working LLM provider for faithfulness eval")

    sampled = [r for r in results if r["category"] == "policy" and not r["expected_reject"]][:k_samples]

    scores = []
    for r in sampled:
        # re-embed and retrieve TOP-3
        qv = embed_texts([r["query"]], text_type="query")[0]
        sims = matrix @ qv
        top_idx = np.argsort(-sims)[:3]
        ctx = "\n\n".join(records[i].get("text", "") for i in top_idx)

        # 1) ask answer agent to draft a grounded reply
        ans_resp = client.chat.completions.create(
            model=ans_model,
            messages=[
                {"role": "system", "content": "你是京东客服。严格只用提供的【参考资料】回答用户问题；如果参考资料里没答案，回答'未找到'。回答不超过 80 字。"},
                {"role": "user", "content": f"【参考资料】\n{ctx}\n\n【用户问题】{r['query']}"},
            ],
            max_tokens=150,
        )
        answer = ans_resp.choices[0].message.content or ""

        # 2) judge faithfulness
        judge_resp = client.chat.completions.create(
            model=judge_model,
            messages=[
                {"role": "system", "content": "你是事实核查员。给定【参考资料】和【回答】，判断回答里所有事实声明是否都能从参考资料推出。只输出 SUPPORTED / PARTIAL / UNSUPPORTED 这三个词之一，第二行简短理由。"},
                {"role": "user", "content": f"【参考资料】\n{ctx}\n\n【回答】\n{answer}"},
            ],
            max_tokens=80,
        )
        verdict = (judge_resp.choices[0].message.content or "").strip().upper()
        if verdict.startswith("SUPPORTED"):
            score = 1.0
        elif verdict.startswith("PARTIAL"):
            score = 0.5
        else:
            score = 0.0
        scores.append({
            "id": r["id"], "query": r["query"],
            "answer": answer[:60], "verdict": verdict.split("\n")[0], "score": score,
        })

    mean = sum(s["score"] for s in scores) / len(scores) if scores else 0.0
    return mean, scores


def main():
    print("Loading KB + embeddings...")
    records, matrix = _load_kb()
    print(f"  KB: {len(records)} records, emb: {matrix.shape}")

    eval_items = _load_eval()
    print(f"  Eval set: {len(eval_items)} queries\n")

    print("Running retrieval evaluation...")
    results, embed_avg, search_avg = evaluate_retrieval(eval_items, records, matrix)
    summary = summarize(results)

    print("\n========== Retrieval Metrics ==========")
    print(f"  Recall@1            : {summary['recall_at_1']:.1%}  ({int(summary['recall_at_1']*summary['n_functional'])}/{summary['n_functional']})")
    print(f"  Recall@3            : {summary['recall_at_3']:.1%}  ({int(summary['recall_at_3']*summary['n_functional'])}/{summary['n_functional']})")
    print(f"  MRR                 : {summary['mrr']:.3f}")
    print(f"  Reject Accuracy     : {summary['reject_accuracy']:.1%}  ({int(summary['reject_accuracy']*summary['n_reject'])}/{summary['n_reject']})")
    print(f"  Embed latency / qry : {embed_avg*1000:.0f} ms")
    print(f"  Vector search / qry : {search_avg*1000:.1f} ms")
    print(f"  Total per query     : {(embed_avg+search_avg)*1000:.0f} ms")

    print("\n========== Per-Category Breakdown ==========")
    cats = ["policy", "plus", "logistics", "product"]
    for cat in cats:
        rows = [r for r in results if r["category"] == cat]
        n = len(rows)
        h1 = sum(1 for r in rows if r.get("rank_in_top3") == 1)
        h3 = sum(1 for r in rows if r.get("rank_in_top3") is not None)
        print(f"  {cat:10s}  n={n}  Recall@1={h1/n:.0%}  Recall@3={h3/n:.0%}")

    print("\n========== Faithfulness (LLM judge, 5 policy samples) ==========")
    try:
        faith_mean, faith_rows = faithfulness_sample(results, records, matrix, k_samples=5)
        print(f"  Faithfulness        : {faith_mean:.2f}  (1.0=fully supported, 0.5=partial, 0.0=unsupported)")
        for s in faith_rows:
            print(f"    {s['id']}  verdict={s['verdict']}  score={s['score']}  answer={s['answer']!r}")
    except Exception as exc:
        print(f"  Faithfulness skipped: {exc}")

    print("\n========== Misses (functional, rank_in_top3=None) ==========")
    misses = [r for r in results if not r["expected_reject"] and r.get("rank_in_top3") is None]
    for r in misses:
        print(f"  {r['id']} [{r['category']}] q={r['query']!r}  top1={r['top1_title']!r}  sim={r['top1_sim']:.3f}")

    print("\n========== Reject failures (TOP-1 sim >= threshold) ==========")
    rej_fail = [r for r in results if r["expected_reject"] and not r["rejected_correctly"]]
    for r in rej_fail:
        print(f"  {r['id']}  q={r['query']!r}  top1_sim={r['top1_sim']:.3f}")

    out_path = Path(__file__).with_name("eval_report.json")
    out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDetailed report: {out_path}")


if __name__ == "__main__":
    main()
