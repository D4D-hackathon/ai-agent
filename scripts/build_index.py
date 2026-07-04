"""build_index.py — 교범 PDF를 페이지 단위 텍스트 인덱스로 추출 (1회 실행).

CLAUDE.md: 교범 근거는 벡터DB 없이 파일 읽기로 해당 절을 찾아 **파일명+페이지**로 인용한다.
이 스크립트는 각 PDF를 페이지별로 추출해 검색 가능한 JSONL 인덱스를 만든다.
retrieval.py 가 이 인덱스를 BM25로 검색한다.

산출물:
  doctrine/index/<stem>.pages.jsonl   각 줄 = {"file","page","text"}  (page=1-base)
  doctrine/index/manifest.json        인덱스된 파일 목록/페이지수
  doctrine/nk-ttr.txt                 NK TTR 전문 (CLAUDE.md 가 참조)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANUALS = ROOT / "manuals"
INDEX_DIR = ROOT / "doctrine" / "index"

# 파일명 → 짧은 식별자(인용 표기용) + 교리 진영
MANUAL_META = {
    "ARN38160-FM_3-90-000-WEB-1.pdf": {"id": "FM3-90", "doctrine": "blue",
                                       "title": "FM 3-90 Tactics"},
    "USArmy-NorthKoreaTactics.pdf":   {"id": "NK-TTR", "doctrine": "red",
                                       "title": "TRADOC Threat Tactics Report: North Korea"},
}


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build() -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pypdf 미설치. `pip install -r requirements.txt` 후 다시 실행하세요.")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"manuals": []}

    for pdf in sorted(MANUALS.glob("*.pdf")):
        meta = MANUAL_META.get(pdf.name, {"id": pdf.stem, "doctrine": "unknown",
                                          "title": pdf.stem})
        reader = PdfReader(str(pdf))
        out = INDEX_DIR / f"{meta['id']}.pages.jsonl"
        pages_written = 0
        full_text_parts = []
        with out.open("w", encoding="utf-8") as fh:
            for i, page in enumerate(reader.pages, start=1):
                try:
                    text = _clean(page.extract_text() or "")
                except Exception as e:  # 손상 페이지는 건너뛰되 페이지번호 유지
                    text = ""
                    print(f"  ! {pdf.name} p{i} 추출 실패: {e}", file=sys.stderr)
                fh.write(json.dumps({"file": meta["id"], "manual": pdf.name,
                                     "page": i, "text": text}, ensure_ascii=False) + "\n")
                full_text_parts.append(f"\n\n===== [{meta['id']} p.{i}] =====\n{text}")
                pages_written += 1
        print(f"  ✓ {meta['id']}: {pages_written} pages → {out.name}")
        manifest["manuals"].append({**meta, "file_name": pdf.name,
                                    "pages": pages_written, "index": out.name})

        # NK TTR 는 전문 텍스트도 저장 (CLAUDE.md 가 doctrine/nk-ttr.txt 를 참조)
        if meta["id"] == "NK-TTR":
            (ROOT / "doctrine" / "nk-ttr.txt").write_text(
                "".join(full_text_parts), encoding="utf-8")
            print("  ✓ doctrine/nk-ttr.txt 생성")

    (INDEX_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print("교범 인덱스 생성 중...")
    m = build()
    total = sum(x["pages"] for x in m["manuals"])
    print(f"완료: {len(m['manuals'])}개 교범, 총 {total} 페이지.")
