"""engine.py — 워게임 시뮬레이션 엔진 (import 가능한 단일 진입점).

백엔드·프론트엔드는 팀원이 구현 → 이 엔진은 그들이 넘겨준 시나리오를 받아 실행한다.
연동 방식(HTTP 호출/인프로세스 import/파일)에 무관하게 동작하도록, 전 과정을 순수 함수로 노출.

한 턴 프로토콜(CLAUDE.md)을 함수로 1:1 매핑:
  normalize(payload)           입력 정규화 (누락 필드는 missing 으로 되묻기)
  attach_terrain(state, sig)   Google Maps MCP 신호 → 지형 분류(에이전트가 signals 수집)
  situation(state)             상황분석 지표(전력비 등) — 정답 결심은 노출 안 함
  doctrine_gate(state, dec)    결심 평가 → 피드백 게이트(교범 근거)
  enemy_action(state)          KPA 교리 행동 + TTR 인용
  resolve_turn(state, ...)     결정론 전투 판정 → 전력 갱신 + log
  report(scenario_id)          1장 리포트용 구조화 데이터
  run_turn(payload, ...)       위 단계를 한 번에 실행하는 편의 함수(일회성/배치용)

숫자는 resolve_combat.py 만이 만든다. 서술(한국어)은 에이전트가 이 출력을 인용해 생성한다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from scripts import doctrine, mapdata, resolve_combat, retrieval, state, terrain
except ImportError:  # 스크립트 디렉터리에서 직접 실행 시
    import doctrine, mapdata, resolve_combat, retrieval, state, terrain


# ── 1) 입력 정규화 ─────────────────────────────────────────────────────
def normalize(payload: Dict[str, Any], persist: bool = True) -> Tuple[Optional[Dict], List[str]]:
    """입력 스키마 → 정규화 state. 누락 시 (None, missing). CLAUDE.md: 지어내지 말고 되묻기."""
    st, missing = state.normalize(payload)
    if st and persist:
        state.save(st)
    return st, missing


# ── 2) 지형 인지 (Google Maps MCP 신호 → 분류) ─────────────────────────
def attach_terrain(st: Dict, signals: Optional[Dict] = None,
                   auto_fetch: bool = True, persist: bool = True) -> Dict:
    """지형 분류를 state 에 부착.

    signals 우선순위:
      1) 인자로 받은 signals (예: 에이전트가 Google Maps MCP 로 수집)
      2) auto_fetch=True 면 mapdata 로 **무키 실측**(OpenTopoData 표고 + OSM + OSRM) 자동 수집
      3) 둘 다 없으면 시나리오의 terrain 값(사용자 입력)으로 진행(기복/협로 drivers 비활성)
    """
    if signals is None and auto_fetch:
        try:
            signals = mapdata.fetch_signals(
                st["blue"]["lat"], st["blue"]["lon"],
                to=(st["red"]["lat"], st["red"]["lon"]),
                approach_bearing=st.get("approach_bearing_deg"))
        except Exception as e:
            signals = {"fetch_errors": [f"mapdata: {type(e).__name__}: {e}"]}
    sig = dict(signals or {})
    sig.setdefault("user_terrain", st.get("terrain"))
    tc = terrain.classify(sig)
    if sig.get("sources"):
        tc["sources"] = sig["sources"]
    if sig.get("fetch_errors"):
        tc.setdefault("notes", []).extend(sig["fetch_errors"])
    st["terrain"] = tc["terrain"]
    st["chokepoint"] = tc["chokepoint"]
    st["terrain_detail"] = tc
    st["seed"] = state.seed_for(st)
    if persist:
        state.save(st)
    return tc


# ── 3) 상황분석 지표 (정답 결심은 노출하지 않음) ───────────────────────
def situation(st: Dict) -> Dict:
    """전력비 등 상황분석 지표. 전력비는 resolve_combat 가 계산(LLM 금지)."""
    preview = resolve_combat.resolve(state.combat_payload(st))
    return {
        "mode": st["mode"],
        "terrain": st["terrain"],
        "weather": st["weather"],
        "chokepoint": st.get("chokepoint", False),
        "distance_km": st["distance_km"],
        "approach_bearing_deg": st.get("approach_bearing_deg"),
        "effective_ratio": preview["effective_ratio"],
        "p_attacker_success": preview["p_attacker_success"],
        "drivers": preview["drivers"],
        "terrain_detail": st.get("terrain_detail"),
    }


# ── 4) 결심 평가 → 피드백 게이트 ───────────────────────────────────────
def doctrine_gate(st: Dict, decision: str) -> Dict:
    preview = resolve_combat.resolve(state.combat_payload(st))
    return doctrine.check(st, decision,
                          effective_ratio=preview["effective_ratio"],
                          chokepoint=st.get("chokepoint"))


# ── 5) 적(KPA) 행동 — 교리 기반 ────────────────────────────────────────
def enemy_action(st: Dict, hint: Optional[str] = None) -> Dict:
    terr, wx = st["terrain"], st["weather"]
    kpa_attacking = st["mode"] == "blue_defend"
    if kpa_attacking:
        label = ("야간/악천후 경보병 능선 침투 후 종심 타격"
                 if wx in ("night", "fog") or terr == "ridge"
                 else "제파식 공격: 정찰→화력준비→돌파→종심 확대")
        query = (f"KPA offensive light infantry infiltration night inclement weather ridgeline "
                 f"avenue of approach penetration fire support {terr}")
    else:
        label = "AT 방어 6단계(장애물→화력계획→AT진지→교전지역→기동예비→역습)"
        query = (f"KPA defense anti-tank engagement area obstacle plan counterattack "
                 f"forward defensive positions {terr}")
    hits = retrieval.search(query + (f" {hint}" if hint else ""), manual="NK-TTR", k=3)
    if not hits:
        return {"warning": "TTR 근거 없음 — 적 행동 창작 금지(판단 근거 불충분).",
                "kpa_posture": "attack" if kpa_attacking else "defend"}
    return {
        "kpa_posture": "attack" if kpa_attacking else "defend",
        "suggested_action": label,
        "doctrine_basis": hits,
        "note": "적 행동은 TTR 근거(위) 범위 안에서만 서술. 근거 없는 창작 금지.",
    }


# ── 6) 전투 판정 + 전력 갱신 (결정론) ──────────────────────────────────
def resolve_turn(st: Dict, decision: Optional[str] = None, enemy: Optional[str] = None,
                 enemy_citations: Optional[List[Dict]] = None,
                 doctrine_result: Optional[Dict] = None,
                 persist: bool = True, log: bool = True) -> Tuple[Dict, Dict]:
    payload = state.combat_payload(st)
    result = resolve_combat.resolve(payload)     # 숫자의 유일한 출처
    turn_no = st["turn"] + 1
    state.apply_losses(st, result["losses"])     # losses 로만 전력 갱신
    st["turn"] = turn_no
    st["seed"] = state.seed_for(st)
    if persist:
        state.save(st)
    if log:
        state.append_log({
            "event": "resolve", "scenario_id": st["id"], "turn": turn_no,
            "mode": st["mode"], "decision": decision, "enemy_action": enemy,
            "combat_input": payload, "result": result, "citations": enemy_citations,
            "doctrine": doctrine_result,
            "forces_after": {"blue": st["blue"], "red": st["red"]},
        })
    return st, {
        "turn": turn_no, "result": result,
        "forces_after": {"blue": st["blue"], "red": st["red"]},
        "status": st["status"], "terminated": st["status"] != "active",
    }


# ── 7) 리포트 ──────────────────────────────────────────────────────────
def report(scenario_id: str) -> Dict:
    return state.report_data(scenario_id)


# ── 편의: 한 턴 전체 실행 (일회성/배치/백엔드 원샷용) ──────────────────
def run_turn(payload: Dict[str, Any], terrain_signals: Optional[Dict] = None,
             decision: Optional[str] = None, enemy_hint: Optional[str] = None,
             persist: bool = True) -> Dict:
    """정규화 → 지형 → 상황 → (결심 게이트) → 적 행동 → 판정 → 리포트 를 한 번에.

    반환에 gate 와 resolution 을 모두 담아, 교육형(게이트 먼저)·원샷형 모두 대응 가능.
    """
    st, missing = normalize(payload, persist=persist)
    if missing:
        return {"ok": False, "missing": missing,
                "message": "필수 항목 누락 — 백엔드/사용자에게 되물어야 함(값 창작 금지)."}

    tc = attach_terrain(st, terrain_signals, persist=persist)
    sit = situation(st)
    gate = doctrine_gate(st, decision) if decision else None
    enemy = enemy_action(st, enemy_hint)
    st, resolution = resolve_turn(
        st, decision=decision,
        enemy=enemy.get("suggested_action"),
        enemy_citations=enemy.get("doctrine_basis"),
        doctrine_result=gate, persist=persist)

    return {
        "ok": True,
        "scenario_id": st["id"],
        "situation": sit,
        "terrain": tc,
        "doctrine_gate": gate,
        "enemy_action": enemy,
        "resolution": resolution,
        "report": report(st["id"]) if persist else None,
        "state": st,
    }
