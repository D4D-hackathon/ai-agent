"""retrieval.py — 교범 RAG 검색 (페이지 인덱스 → BM25 → 파일+페이지 인용).

CLAUDE.md: 모든 주장은 교범 근거(파일+페이지)로 뒷받침. 벡터DB 없이 페이지 텍스트를 검색한다.
검색 품질을 위해 BM25 를 쓰되, rank-bm25 미설치 시 내장 BM25 로 자동 폴백한다.

사용:
  from retrieval import search
  hits = search("engagement area kill zone antitank", manual="NK-TTR", k=5)
  # -> [{"file":"NK-TTR","page":18,"score":..,"snippet":"...","citation":"NK-TTR p.18"}]
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "doctrine" / "index"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "be", "by", "with", "as", "at", "this", "that", "from", "it", "its", "will",
}


def _tok(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


# ── 내장 BM25 (rank-bm25 폴백; 의존성 없이도 동작) ──────────────────────
class _BM25:
    def __init__(self, corpus_tokens: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = corpus_tokens
        self.N = len(corpus_tokens)
        self.dl = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.dl) / self.N) if self.N else 0.0
        self.tf: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for d in corpus_tokens:
            counts: Dict[str, int] = {}
            for t in d:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        self.idf = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }

    def scores(self, query_tokens: List[str]) -> List[float]:
        out = [0.0] * self.N
        for i in range(self.N):
            counts = self.tf[i]
            if not counts:
                continue
            dl = self.dl[i]
            s = 0.0
            for t in query_tokens:
                f = counts.get(t, 0)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out[i] = s
        return out


class _Index:
    def __init__(self):
        self.records: List[dict] = []
        self.tokens: List[List[str]] = []
        self.bm25 = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        files = sorted(p for p in INDEX_DIR.glob("*.pages.jsonl"))
        if not files:
            raise FileNotFoundError(
                f"인덱스 없음: {INDEX_DIR}. 먼저 `python scripts/build_index.py` 를 실행하세요."
            )
        for f in files:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    self.records.append(rec)
                    self.tokens.append(_tok(rec.get("text", "")))
        # rank-bm25 있으면 사용, 없으면 내장 BM25
        try:
            from rank_bm25 import BM25Okapi
            self.bm25 = BM25Okapi(self.tokens)
            self._impl = "rank_bm25"
        except Exception:
            self.bm25 = _BM25(self.tokens)
            self._impl = "builtin"
        self._loaded = True

    def score(self, query_tokens: List[str]) -> List[float]:
        if hasattr(self.bm25, "get_scores"):   # rank_bm25
            return list(self.bm25.get_scores(query_tokens))
        return self.bm25.scores(query_tokens)   # 내장


_IDX = _Index()


def _snippet(text: str, q_tokens: set, width: int = 320) -> str:
    """질의어가 가장 많이 몰린 구간을 잘라 스니펫으로 반환."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= 60:
        return text[:width].strip()
    best_i, best_hits = 0, -1
    win = 45
    for i in range(0, max(1, len(words) - win), 10):
        seg = words[i:i + win]
        hits = sum(1 for w in seg if _TOKEN_RE.findall(w.lower()) and
                   any(tok in q_tokens for tok in _TOKEN_RE.findall(w.lower())))
        if hits > best_hits:
            best_hits, best_i = hits, i
    seg = " ".join(words[best_i:best_i + win])
    return ("… " if best_i else "") + seg[:width].strip() + " …"


def search(query: str, manual: Optional[str] = None, k: int = 5,
           min_chars: int = 40) -> List[dict]:
    """교범 페이지를 BM25 로 검색. manual 로 특정 교범(id)만 제한 가능.

    반환: [{"file","manual","page","score","snippet","citation"}] (점수 내림차순)
    """
    _IDX.load()
    q_tokens = _tok(query)
    if not q_tokens:
        return []
    scores = _IDX.score(q_tokens)
    q_set = set(q_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    out: List[dict] = []
    for i in ranked:
        rec = _IDX.records[i]
        if scores[i] <= 0:
            break
        if manual and rec["file"].lower() != manual.lower():
            continue
        if len(rec.get("text", "")) < min_chars:
            continue
        out.append({
            "file": rec["file"],
            "manual": rec.get("manual"),
            "page": rec["page"],
            "score": round(float(scores[i]), 4),
            "snippet": _snippet(rec["text"], q_set),
            "citation": f"{rec['file']} p.{rec['page']}",
        })
        if len(out) >= k:
            break
    return out


def stats() -> dict:
    _IDX.load()
    files: Dict[str, int] = {}
    for r in _IDX.records:
        files[r["file"]] = files.get(r["file"], 0) + 1
    return {"impl": getattr(_IDX, "_impl", "?"), "pages_total": len(_IDX.records),
            "by_manual": files}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "engagement area kill zone antitank obstacle"
    print("index:", stats())
    print(f"\nquery: {q!r}\n")
    for h in search(q, k=6):
        print(f"[{h['citation']}] score={h['score']}")
        print("   ", h["snippet"][:200], "\n")
