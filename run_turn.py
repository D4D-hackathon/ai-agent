"""run_turn.py — 백엔드 없이 엔진을 돌려보는 CLI (테스트/데모 겸 파일·stdin 연동).

사용:
  cat scenario.json | python run_turn.py
  python run_turn.py scenario.json --decision "능선 확보·역습 준비" --signals signals.json

scenario.json 은 엔진 입력 스키마(=CLAUDE.md 입력 스키마). --signals 는 에이전트가 Google Maps
MCP 로 수집한 지형 신호(JSON). 결과는 stdout 으로 JSON 출력.
"""
from __future__ import annotations

import argparse
import json
import sys

from scripts import engine


def main():
    ap = argparse.ArgumentParser(description="워게임 한 턴 실행")
    ap.add_argument("scenario", nargs="?", help="시나리오 JSON 파일 (없으면 stdin)")
    ap.add_argument("--decision", help="지휘관 결심(한국어). 주면 doctrine 게이트 평가 포함")
    ap.add_argument("--signals", help="Google Maps MCP 지형 신호 JSON 파일(선택)")
    ap.add_argument("--enemy-hint", help="적 행동 검색 힌트(선택)")
    ap.add_argument("--no-persist", action="store_true", help="state 파일에 저장하지 않음")
    args = ap.parse_args()

    raw = open(args.scenario, encoding="utf-8").read() if args.scenario else sys.stdin.read()
    scenario = json.loads(raw)
    signals = json.load(open(args.signals, encoding="utf-8")) if args.signals else None

    out = engine.run_turn(scenario, terrain_signals=signals, decision=args.decision,
                          enemy_hint=args.enemy_hint, persist=not args.no_persist)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out.get("ok") else 2)


if __name__ == "__main__":
    main()
