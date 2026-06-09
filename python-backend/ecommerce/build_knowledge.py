"""One-time data-prep: clean the four JD source datasets into a single
knowledge base JSONL consumed by the search_policy RAG tool.

Sources (under AgenticMultimodalRAG/data/jd_closed_loop_rag):
  1. raw_issue_detail_v3/detail_pages  -> real Q&A (question + answer)   [kind=qa]
  2. focus_chunks/policy_chunks.jsonl  -> JD help index pages (titles)   [kind=index]
  3. focus_clean/policy/**             -> same help list pages (titles)  [kind=index]
  4. external_datasets/ChineseEcomQA.jsonl -> product knowledge Q&A      [kind=qa]

Run:  python -m ecommerce.build_knowledge
Output: ecommerce/knowledge/knowledge_base.jsonl
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from bs4 import BeautifulSoup

# --- paths -----------------------------------------------------------------
SRC_ROOT = Path(
    r"C:\Users\Administrator\Desktop\AgenticMultimodalRAG-main\data\jd_closed_loop_rag"
)
OUT_DIR = Path(__file__).with_name("knowledge")
OUT_PATH = OUT_DIR / "knowledge_base.jsonl"

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WS_RE = re.compile(r"[ \t　]+")


def _norm(text: str) -> str:
    text = _WS_RE.sub(" ", text or "")
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text


def _eid(*parts: str) -> str:
    return "kb_" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


# --- source 1: issue detail pages (real Q&A) -------------------------------
def from_detail_pages() -> Iterable[Dict[str, Any]]:
    pages = sorted((SRC_ROOT / "raw_issue_detail_v3" / "detail_pages").glob("*.html"))
    for p in pages:
        soup = BeautifulSoup(_decode(p), "html.parser")
        q_el = soup.find("div", class_="help-tit1")
        c_el = soup.find("div", class_="contxt")
        if not q_el or not c_el:
            continue
        question = _norm(q_el.get_text(" ", strip=True))
        body = _norm(c_el.get_text("\n", strip=True))
        # answer = contxt minus the question and the trailing date
        answer = body.replace(question, "", 1)
        answer = _DATE_RE.sub("", answer).strip(" \n·-")
        if not question or len(answer) < 8:
            continue
        url_m = re.search(r"(help\.jd\.com[^_]*\.html)", p.name)
        url = ("https://" + url_m.group(1)) if url_m else ""
        yield {
            "id": _eid("detail", question),
            "source": "jd_issue_detail",
            "kind": "qa",
            "category": "jd_help",
            "title": question,
            "text": f"问题：{question}\n答案：{answer}",
            "url": url,
        }


# --- source 2: policy_chunks.jsonl (index pages) ---------------------------
def from_policy_chunks() -> Iterable[Dict[str, Any]]:
    fp = SRC_ROOT / "focus_chunks" / "policy_chunks.jsonl"
    if not fp.exists():
        return
    for line in fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        text = _norm(r.get("text", ""))
        if not text:
            continue
        yield {
            "id": _eid("policychunk", r.get("chunk_id", text[:30])),
            "source": "jd_policy_index",
            "kind": "index",
            "category": r.get("category", "jd_policy"),
            "title": (r.get("theme") or "policy_index"),
            "text": text,
            "url": r.get("url", ""),
        }


# --- source 3: focus_clean/policy (index list pages) -----------------------
def from_focus_clean(seen_titles: set) -> Iterable[Dict[str, Any]]:
    root = SRC_ROOT / "focus_clean" / "policy"
    if not root.exists():
        return
    for p in sorted(root.rglob("*.txt")):
        raw = _norm(_decode(p))
        # keep only bullet lines (the actual question-title list); drop nav chrome
        bullets = [ln.strip("·• \t") for ln in raw.splitlines() if ln.strip().startswith("·")]
        bullets = [b for b in bullets if len(b) > 4 and b not in seen_titles]
        if not bullets:
            continue
        category = p.parent.name
        text = "\n".join(f"· {b}" for b in bullets)
        yield {
            "id": _eid("focusclean", p.name),
            "source": "jd_focus_clean",
            "kind": "index",
            "category": category,
            "title": f"{category}_常见问题清单",
            "text": text,
            "url": "",
        }


# --- source 4: ChineseEcomQA.jsonl (product knowledge Q&A) -----------------
_QUERY_RE = re.compile(r"query[\*\s)）]*[:：]\s*(.+?)(?:[\*\s]*答案|\*{2,}|$)", re.S)


def from_chinese_ecom_qa() -> Iterable[Dict[str, Any]]:
    fp = SRC_ROOT / "external_datasets" / "ChineseEcomQA.jsonl"
    if not fp.exists():
        return
    for line in fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        prompt = r.get("prompt", "")
        gt = _norm(str(r.get("gt", "")))
        m = _QUERY_RE.search(prompt)
        query = _norm(m.group(1)) if m else _norm(prompt)
        if not query or not gt:
            continue
        yield {
            "id": _eid("ceqa", query, gt[:20]),
            "source": "chinese_ecom_qa",
            "kind": "qa",
            "category": "product_knowledge",
            "title": query[:60],
            "text": f"问题：{query}\n答案：{gt}",
            "url": "",
        }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    seen_ids: set = set()

    detail = list(from_detail_pages())
    detail_titles = {r["title"] for r in detail}
    sources = [
        ("jd_issue_detail", detail),
        ("jd_policy_index", list(from_policy_chunks())),
        ("jd_focus_clean", list(from_focus_clean(detail_titles))),
        ("chinese_ecom_qa", list(from_chinese_ecom_qa())),
    ]

    counts: Dict[str, int] = {}
    for name, recs in sources:
        kept = 0
        for r in recs:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            records.append(r)
            kept += 1
        counts[name] = kept

    with OUT_PATH.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"wrote {len(records)} records -> {OUT_PATH}")
    for name, c in counts.items():
        print(f"  {name}: {c}")


if __name__ == "__main__":
    main()
