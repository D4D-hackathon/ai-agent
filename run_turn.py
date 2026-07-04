#!/usr/bin/env python3
"""run_turn.py — 엔진 한 턴 실행 CLI (백엔드 브리지 · 테스트/데모 공용 진입점).

backend/wargame_bridge.py 가 이 파일을 자체 venv 서브프로세스로 호출한다:
    .venv/bin/python run_turn.py [--decision "..."] [--signals signals.json] [scenario.json]

시나리오 입력:  위치인자 파일 → 없으면 stdin(JSON).
지형 신호:      --signals 파일(JSON, 선택). 없으면 mapdata 무키 실측 자동 수집.
결심:           --decision (선택).

출력:  engine.run_turn(...) 결과를 **stdout 에 JSON 한 덩어리**로만 낸다(브리지가 파싱).
종료코드:  ok → 0, 필수 필드 누락(ok=false, missing) → 2, 그 외 오류 → 1.
진단/경고는 전부 stderr 로 보내 stdout JSON 을 오염시키지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys

from scripts import engine


def _load_json(path: str | None, *, what: str) -> dict:
    """path 가 있으면 파일에서, 없으면 stdin 에서 JSON 을 읽는다."""
    if path:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError(f"{what} 입력이 비어 있습니다.")
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="워게임 엔진 한 턴 실행 (stdin/파일 → 판정 JSON)")
    ap.add_argument("scenario", nargs="?", default=None,
                    help="시나리오 JSON 파일 (생략 시 stdin 에서 읽음)")
    ap.add_argument("--signals", default=None,
                    help="지형 신호 JSON 파일 (생략 시 mapdata 무키 실측 자동 수집)")
    ap.add_argument("--decision", default=None,
                    help="이번 턴 지휘관 결심(문자열). 있으면 doctrine 게이트 평가.")
    ap.add_argument("--no-persist", action="store_true",
                    help="game.json/log.jsonl 저장을 건너뜀(순수 계산만).")
    args = ap.parse_args(argv)

    try:
        scenario = _load_json(args.scenario, what="시나리오")
        signals = _load_json(args.signals, what="지형 신호") if args.signals else None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        json.dump({"ok": False, "error": f"입력 파싱 실패: {type(e).__name__}: {e}"},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1

    try:
        out = engine.run_turn(
            scenario,
            terrain_signals=signals,
            decision=args.decision,
            persist=not args.no_persist,
        )
    except Exception as e:  # 엔진 내부 오류도 stdout JSON 으로 보고(브리지가 파싱 가능하도록)
        json.dump({"ok": False, "error": f"엔진 실행 오류: {type(e).__name__}: {e}"},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1

    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()
    # 필수 필드 누락은 실패가 아닌 '되묻기' 신호 → 종료코드 2 (브리지 규약: 0/2 모두 JSON)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
