"""client.py — 팀원 백엔드 ↔ 워게임 엔진 연동 어댑터.

⚠️ 실제 연동은 이미 구축됨(backend 브리지 방식):
    CloudTAK 프론트 → POST /wargame/resolve (~/backend, FastAPI :8000)
        → ~/backend/wargame_bridge.py 가 프론트 payload(units/attacks)를 엔진 스키마로 변환
        → 이 저장소 엔진(run_turn.py)을 자체 venv 서브프로세스로 실행 → 판정 결과 반환.
    프론트 payload → 엔진 스키마 변환의 **정본**은 ~/backend/wargame_bridge.py 다.

이 client.py 는 "에이전트가 백엔드에서 시나리오를 pull 해 실행"하는 대안 흐름용 **템플릿**이며,
현재 파이프라인엔 불필요하다. pull 방식을 쓰려면 아래 3곳을 실제 규격에 맞춰라:
  (1) BASE_URL / 엔드포인트 경로
  (2) adapt_scenario()   : 백엔드 응답 JSON → 엔진 입력 스키마
  (3) adapt_result()     : 엔진 출력 → 백엔드가 기대하는 결과 JSON

엔진 입력 스키마(CLAUDE.md):
  {objective, weather(clear|rain|fog|night), terrain(open|forest|ridge|urban),
   blue:{tanks,troops,lat,lon,posture}, red:{...}}
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests

from scripts import engine

# 실제 백엔드는 ~/backend FastAPI(:8000). 현재 통합 경로는 POST /wargame/resolve 이며
# 프론트 payload→엔진 변환은 backend/wargame_bridge.py 가 담당한다(아래 pull 경로는 미사용 템플릿).
BASE_URL = os.environ.get("WARGAME_BACKEND_URL", "http://localhost:8000")
FETCH_PATH = "/api/scenario/next"      # (미구현) pull 방식 쓸 때 실제 경로로 교체
SUBMIT_PATH = "/api/scenario/result"   # (미구현) pull 방식 쓸 때 실제 경로로 교체


# ── (2) 백엔드 응답 → 엔진 입력 스키마 매핑 (규격 확정 시 여기만 수정) ──
def adapt_scenario(raw: Dict[str, Any]) -> Dict[str, Any]:
    """팀원 백엔드의 시나리오 JSON을 엔진 입력 스키마로 변환.
    기본 구현은 '이미 엔진 스키마와 동일'하다고 가정한 항등 매핑 + 흔한 별칭 처리.
    실제 필드명이 다르면 여기서 매핑하라 (예: raw['friendly'] → blue)."""
    def unit(u):
        u = u or {}
        pos = (u.get("위치") or u.get("position") or {})
        return {
            "tanks": u.get("tanks", u.get("전차")),
            "troops": u.get("troops", u.get("병력")),
            "lat": u.get("lat", u.get("위도", pos.get("lat"))),
            "lon": u.get("lon", u.get("경도", pos.get("lon"))),
            "posture": u.get("posture", u.get("태세")),
        }
    return {
        "objective": raw.get("objective", raw.get("작전목표")),
        "weather": raw.get("weather", raw.get("날씨")),
        "terrain": raw.get("terrain", raw.get("지형")),
        "blue": unit(raw.get("blue", raw.get("아군", raw.get("friendly")))),
        "red": unit(raw.get("red", raw.get("적군", raw.get("enemy")))),
    }


# ── (3) 엔진 출력 → 백엔드 결과 JSON (규격 확정 시 여기만 수정) ────────
def adapt_result(engine_out: Dict[str, Any]) -> Dict[str, Any]:
    """엔진 결과를 백엔드가 기대하는 형태로 축약/변환."""
    if not engine_out.get("ok"):
        return {"status": "need_more_input", "missing": engine_out.get("missing")}
    res = engine_out["resolution"]["result"]
    return {
        "status": "resolved",
        "scenario_id": engine_out["scenario_id"],
        "outcome": res["outcome"],
        "effective_ratio": res["effective_ratio"],
        "p_attacker_success": res["p_attacker_success"],
        "losses": res["losses"],
        "forces_after": engine_out["resolution"]["forces_after"],
        "doctrine_flags": [f["id"] for f in (engine_out.get("doctrine_gate") or {}).get("fired", [])],
        "enemy_action": engine_out["enemy_action"].get("suggested_action"),
        "citations": engine_out["enemy_action"].get("doctrine_basis"),
        "terminated": engine_out["resolution"]["terminated"],
    }


# ── (1) HTTP 입출력 (엔드포인트는 규격 확정 시 교체) ────────────────────
def fetch_scenario(base_url: str = BASE_URL, timeout: float = 10.0) -> Dict[str, Any]:
    r = requests.get(base_url.rstrip("/") + FETCH_PATH, timeout=timeout)
    r.raise_for_status()
    return r.json()


def submit_result(result: Dict[str, Any], base_url: str = BASE_URL, timeout: float = 10.0):
    r = requests.post(base_url.rstrip("/") + SUBMIT_PATH, json=result, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def run_from_backend(base_url: str = BASE_URL, terrain_signals: Optional[Dict] = None,
                     decision: Optional[str] = None) -> Dict[str, Any]:
    """백엔드에서 시나리오 받아 → 엔진 실행 → 결과 반환(+백엔드 제출).
    terrain_signals 는 에이전트가 Google Maps MCP 로 수집해 주입한다."""
    raw = fetch_scenario(base_url)
    scenario = adapt_scenario(raw)
    out = engine.run_turn(scenario, terrain_signals=terrain_signals, decision=decision)
    result = adapt_result(out)
    try:
        submit_result(result, base_url)
    except Exception as e:
        result["_submit_error"] = str(e)
    return result


if __name__ == "__main__":
    print("연동 어댑터. 백엔드 규격 확정 후 BASE_URL/경로/adapt_* 를 채우세요.")
    print(json.dumps({"BASE_URL": BASE_URL, "FETCH_PATH": FETCH_PATH,
                      "SUBMIT_PATH": SUBMIT_PATH}, ensure_ascii=False, indent=2))
