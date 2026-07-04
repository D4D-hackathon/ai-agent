"""state.py — 상태 정규화 + game.json/log.jsonl 관리 + 리포트 데이터 조립.

CLAUDE.md:
  - 입력을 받으면 즉시 state/game.json 으로 정규화하고 geo 로 교전거리를 계산해 둔다.
  - 누락 필드는 지어내지 말고 사용자에게 되묻는다 → normalize() 는 missing 목록을 반환한다.
  - 결심·적행동·판정·근거를 state/log.jsonl 에 1줄 append.
  - 전력 갱신은 resolve_combat 의 losses 로만 한다.

멀티 시나리오 지원: 권위 저장소는 state/games/<id>.json, 활성 스냅샷은 state/game.json(계약 유지),
로그는 state/log.jsonl(한 줄=한 이벤트, scenario_id 포함).
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

try:
    from scripts.geo import haversine, bearing
except ImportError:
    from geo import haversine, bearing

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
GAMES_DIR = STATE_DIR / "games"
ACTIVE = STATE_DIR / "game.json"          # CLAUDE.md 계약 파일 (활성 스냅샷)
LOG = STATE_DIR / "log.jsonl"

VALID_WEATHER = {"clear", "rain", "fog", "night"}
VALID_TERRAIN = {"open", "forest", "ridge", "urban"}
VALID_POSTURE = {"attack", "defend", "delay"}


def _unit(raw: Dict, side: str, missing: List[str], label: str) -> Dict:
    raw = raw or {}
    for key, path in (("tanks", "전차"), ("troops", "병력"), ("posture", "태세")):
        if raw.get(key) is None:
            missing.append(f"{label}.{path}")
    lat = raw.get("lat", (raw.get("위치") or {}).get("lat") if isinstance(raw.get("위치"), dict) else None)
    lon = raw.get("lon", (raw.get("위치") or {}).get("lon") if isinstance(raw.get("위치"), dict) else None)
    if lat is None:
        missing.append(f"{label}.위치.위도")
    if lon is None:
        missing.append(f"{label}.위치.경도")
    return {
        "side": side,
        "tanks": int(raw["tanks"]) if raw.get("tanks") is not None else None,
        "troops": int(raw["troops"]) if raw.get("troops") is not None else None,
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "posture": (raw.get("posture") or "").lower() or None,
    }


def normalize(payload: Dict[str, Any]) -> Tuple[Optional[Dict], List[str]]:
    """입력 스키마 → 정규화 state. 누락 필드가 있으면 (None, missing) 반환."""
    missing: List[str] = []
    if not payload.get("objective") and not payload.get("작전목표"):
        missing.append("작전목표")
    weather = (payload.get("weather") or payload.get("날씨") or "").lower()
    terrain = (payload.get("terrain") or payload.get("지형") or "").lower()
    if weather not in VALID_WEATHER:
        missing.append("날씨(clear|rain|fog|night)")
    if terrain not in VALID_TERRAIN:
        missing.append("지형(open|forest|ridge|urban)")

    blue = _unit(payload.get("blue") or payload.get("아군"), "blue", missing, "아군")
    red = _unit(payload.get("red") or payload.get("적군"), "red", missing, "적군")
    for u, lbl in ((blue, "아군"), (red, "적군")):
        if u["posture"] and u["posture"] not in VALID_POSTURE:
            missing.append(f"{lbl}.태세(attack|defend|delay)")

    if missing:
        return None, sorted(set(missing))

    # 태세로 공격/방어 모드 결정 (CLAUDE.md: 한 턴, 공격 또는 방어 하나)
    mode = "blue_attack" if blue["posture"] == "attack" else "blue_defend"
    if blue["posture"] == "attack":
        red["posture"] = red["posture"] or "defend"
    else:
        red["posture"] = "attack"   # 방어 턴: 적이 공격해온다

    dist = round(haversine(blue["lat"], blue["lon"], red["lat"], red["lon"]), 4)
    brg = round(bearing(red["lat"], red["lon"], blue["lat"], blue["lon"]), 1)  # 적→아군 접근 방위

    state = {
        "id": uuid4().hex[:12],
        "turn": 0,
        "objective": payload.get("objective") or payload.get("작전목표"),
        "terrain": terrain,
        "weather": weather,
        "chokepoint": bool(payload.get("chokepoint", False)),
        "mode": mode,
        "blue": blue,
        "red": red,
        "distance_km": dist,
        "approach_bearing_deg": brg,
        "terrain_detail": None,
        "status": "active",
    }
    state["seed"] = seed_for(state)
    return state, []


def seed_for(state: Dict) -> int:
    """id+turn 으로 결정론적 seed (재현 가능). resolve_combat 에 넘긴다."""
    return zlib.crc32(f"{state['id']}:{state.get('turn', 0)}".encode()) & 0x7FFFFFFF


def combat_payload(state: Dict) -> Dict:
    """resolve_combat 입력 페이로드 구성 (attacker/defender 는 mode 로 결정)."""
    if state["mode"] == "blue_attack":
        attacker, defender = state["blue"], state["red"]
    else:
        attacker, defender = state["red"], state["blue"]
    return {
        "attacker": {k: attacker[k] for k in ("side", "tanks", "troops", "posture")},
        "defender": {k: defender[k] for k in ("side", "tanks", "troops", "posture")},
        "terrain": state["terrain"],
        "weather": state["weather"],
        "distance_km": state["distance_km"],
        "seed": seed_for(state),
    }


def apply_losses(state: Dict, losses: Dict) -> Dict:
    """resolve_combat 의 losses 로만 전력 갱신 (숫자 창작 금지). 소멸 판정 포함."""
    for side_key, side in (("friendly", "blue"), ("enemy", "red")):
        l = losses.get(side_key, {})
        u = state[side]
        u["tanks"] = max(0, (u["tanks"] or 0) - int(l.get("tanks", 0)))
        u["troops"] = max(0, (u["troops"] or 0) - int(l.get("troops", 0)))
    state["status"] = termination(state)
    return state


def termination(state: Dict) -> str:
    def dead(u):
        return (u["tanks"] or 0) <= 0 and (u["troops"] or 0) <= 0
    if dead(state["blue"]) and dead(state["red"]):
        return "mutual_destruction"
    if dead(state["blue"]):
        return "blue_eliminated"
    if dead(state["red"]):
        return "red_eliminated"
    return "active"


# ── 영속화 ─────────────────────────────────────────────────────────────
def save(state: Dict, make_active: bool = True) -> None:
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    (GAMES_DIR / f"{state['id']}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if make_active:
        ACTIVE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load(scenario_id: str) -> Optional[Dict]:
    p = GAMES_DIR / f"{scenario_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def append_log(record: Dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log(scenario_id: Optional[str] = None) -> List[Dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if scenario_id is None or rec.get("scenario_id") == scenario_id:
            out.append(rec)
    return out


def report_data(scenario_id: str) -> Dict:
    """log.jsonl 을 순회해 1장 리포트용 구조화 데이터 조립 (프로즈는 에이전트가 작성)."""
    state = load(scenario_id)
    events = read_log(scenario_id)
    resolves = [e for e in events if e.get("event") == "resolve"]
    # turning point: 손실이 가장 컸던 판정
    def loss_mag(e):
        l = e.get("result", {}).get("losses", {}).get("friendly", {})
        return (l.get("tanks", 0) or 0) * 10 + (l.get("troops", 0) or 0)
    turning = max(resolves, key=loss_mag) if resolves else None
    return {
        "scenario_id": scenario_id,
        "state": state,
        "status": state.get("status") if state else None,
        "timeline": [
            {
                "turn": e.get("turn"),
                "decision": e.get("decision"),
                "enemy_action": e.get("enemy_action"),
                "outcome": e.get("result", {}).get("outcome"),
                "losses": e.get("result", {}).get("losses"),
                "drivers": e.get("result", {}).get("drivers"),
                "citations": e.get("citations"),
            }
            for e in resolves
        ],
        "fired_flags": [f for e in events for f in (e.get("doctrine", {}) or {}).get("fired", [])],
        "turning_point": {
            "turn": turning.get("turn"),
            "outcome": turning.get("result", {}).get("outcome"),
            "drivers": turning.get("result", {}).get("drivers"),
        } if turning else None,
    }
