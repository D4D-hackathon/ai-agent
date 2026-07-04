"""doctrine.py — 결심 평가 & 피드백 게이트 (doctrine_check).

CLAUDE.md 한 턴 프로토콜 2): 사용자 결심을 받으면 관련 교범 절을 검색해 doctrine flag 를
판정한다. 하나라도 켜지면 그 즉시(턴 결과 전에) 피드백한다.

각 flag = 상황조건(코드로 판정) + 교범 근거(retrieval.search 로 파일+페이지 인용).
근거 없는 훈계 금지 → 켜진 flag 는 반드시 교범 인용을 동반한다.

effective_ratio 는 숫자이므로 resolve_combat.py 가 계산한 값을 인자로 받는다(직접 계산 금지).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

try:
    from scripts.retrieval import search
except ImportError:  # 스크립트 직접 실행 시
    from retrieval import search

ROUGH_TERRAIN = {"forest", "ridge", "urban"}
DEGRADED_WX = {"night", "fog"}
RATIO_MIN = 1.5   # p_attacker_success 50% 지점. 공격엔 이 이상의 전력비 우위 필요.

# 결심 텍스트에서 의도를 감지하는 키워드 (한/영 혼용)
INTENT_KEYWORDS: Dict[str, List[str]] = {
    "frontal":        ["정면", "돌격", "정면돌격", "직접 공격", "정면 공격", "밀어붙", "정면으로",
                       "frontal", "charge", "direct assault", "head-on"],
    "into_chokepoint": ["협로", "골짜기", "계곡", "고개", "통로로", "협곡", "킬존", "교전지역",
                        "kill zone", "engagement area", "협도", "좁은 길"],
    "flank":          ["우회", "측방", "측면", "포위", "양익", "flank", "envelop", "우측으로", "좌측으로"],
    "commit_all":     ["전 병력", "총공격", "모두 투입", "전원 투입", "예비 없이", "전력 투입",
                       "올인", "all-in", "일제", "전부 투입"],
    "reserve":        ["예비대", "예비", "역습", "반격", "reserve", "counterattack", "counter-attack",
                       "예비 전력", "기동예비"],
    "give_up_high":   ["능선 포기", "능선을 내주", "계곡 방어", "저지대 방어", "고지 포기",
                       "능선 포기하고", "능선 대신", "give up the ridge", "cede the high ground"],
    "night_los":      ["가시선", "관측 하", "주간처럼", "시야 확보", "원거리 사격", "조명 없이",
                       "line of sight", "직사 화력으로", "먼 거리에서"],
    "secure_key":     ["확보", "점령", "선점", "고지 유지", "능선 유지", "핵심지형", "핵심 지형",
                       "key terrain", "seize the ridge", "retain the high ground", "hold the ridge"],
}


def detect_intents(decision: str) -> Set[str]:
    if not decision:
        return set()
    low = decision.lower()
    found = set()
    for intent, kws in INTENT_KEYWORDS.items():
        for kw in kws:
            if kw in decision or kw.lower() in low:
                found.add(intent)
                break
    return found


def _cite(query: str, manual: Optional[str], k: int = 2) -> List[dict]:
    hits = search(query, manual=manual, k=k)
    return [{"citation": h["citation"], "file": h["file"], "page": h["page"],
             "snippet": h["snippet"]} for h in hits]


# ── FLAG 정의: (id, title, severity, query, manual, condition, guidance) ──
def _flags():
    return [
        {
            "id": "frontal_assault_killzone",
            "title": "방어된 협로/킬존으로의 정면 돌격",
            "severity": "block",
            "query": "anti-tank engagement area kill zone obstacle plan forward defensive positions",
            "manual": "NK-TTR",
            "cond": lambda c: (
                c["blue_posture"] in ("attack",)
                and (c["chokepoint"] or c["terrain"] in ROUGH_TERRAIN)
                and ("frontal" in c["intents"] or "into_chokepoint" in c["intents"])
                and "flank" not in c["intents"]
            ),
            "guidance": "정면 돌격 대신 우회/측방 기동으로 킬존을 회피하거나, 화력으로 AT 진지를 "
                        "제압한 뒤 진입하라. 협로 정면은 KPA의 AT 교전지역 설계에 그대로 말려든다.",
        },
        {
            "id": "insufficient_ratio",
            "title": "전력비 부족 상태의 공격",
            "severity": "block",
            "query": "attacker force ratio superiority three to one attacking prepared defense combat power",
            "manual": "FM3-90",
            "cond": lambda c: (
                c["blue_posture"] == "attack"
                and c["effective_ratio"] is not None
                and c["effective_ratio"] < RATIO_MIN
            ),
            "guidance": "effective_ratio 가 {ratio} 로 공격 성공 임계(≈1.5:1) 미달이다. 화력 집중·"
                        "국지적 전투력 우세 확보(조공/양공, 예비 투입) 후 결정적 지점에 비율 우위를 만들어라.",
        },
        {
            "id": "weather_terrain_ignored",
            "title": "기상·지형 무시 (야간/안개 가시선 전제 또는 능선 포기)",
            "severity": "warn",
            "query": "light infantry infiltration night inclement weather poor visibility ridgeline avenue of approach",
            "manual": "NK-TTR",
            "cond": lambda c: (
                c["weather"] in DEGRADED_WX
                and ("night_los" in c["intents"] or "give_up_high" in c["intents"])
            ),
            "guidance": "KPA 경보병은 야간·악천후에 능선을 접근로로 침투한다(TTR). 야간에 가시선을 전제한 "
                        "원거리 사격계획이나 능선을 내주고 계곡만 방어하는 것은 침투를 초대한다. "
                        "능선/고지에 관측·경계, 근접전 대비 조명·매복을 배치하라.",
        },
        {
            "id": "no_counterattack_reserve",
            "title": "예비대/역습 미보유 (예비대 조기 소진)",
            "severity": "warn",
            "query": "retain reserve mobile reserve counterattack defense in depth commit reserve decisive point",
            "manual": "FM3-90",
            "cond": lambda c: (
                c["blue_posture"] in ("defend", "delay")
                and ("commit_all" in c["intents"] or "reserve" not in c["intents"])
            ),
            "guidance": "종심방어는 돌파 시 역습할 예비대를 반드시 남긴다. 전 병력을 전방에 붙이면 "
                        "돌파구를 봉쇄·격멸할 수단이 없다. 기동 가능한 예비를 지정하고 역습 계획을 준비하라.",
        },
        {
            "id": "key_terrain_not_secured",
            "title": "핵심지형(고지/능선) 미확보",
            "severity": "warn",
            "query": "key terrain decisive terrain seize retain control dominant high ground observation",
            "manual": "FM3-90",
            "cond": lambda c: (
                c["terrain"] == "ridge"
                and "secure_key" not in c["intents"]
            ),
            "guidance": "능선/고지는 관측·사격을 지배하는 핵심지형이다. 이를 먼저 확보·유지하지 않으면 "
                        "적이 선점해 전 지역을 통제한다. 핵심지형 확보를 결심에 명시하라.",
        },
    ]


def check(state: Dict, decision: str, effective_ratio: Optional[float] = None,
          chokepoint: Optional[bool] = None) -> Dict:
    """결심을 교범 기준으로 판정. 켜진 flag(근거 포함) + 전체 평가 반환."""
    intents = detect_intents(decision)
    ctx = {
        "terrain": (state.get("terrain") or "open").lower(),
        "weather": (state.get("weather") or "clear").lower(),
        "chokepoint": bool(state.get("chokepoint") if chokepoint is None else chokepoint),
        "blue_posture": (state.get("blue", {}).get("posture") or "attack").lower(),
        "red_posture": (state.get("red", {}).get("posture") or "defend").lower(),
        "effective_ratio": effective_ratio,
        "intents": intents,
    }
    fired, evaluated = [], []
    for f in _flags():
        try:
            on = bool(f["cond"](ctx))
        except Exception:
            on = False
        evaluated.append({"id": f["id"], "fired": on})
        if on:
            guidance = f["guidance"].replace(
                "{ratio}", f"{effective_ratio:.2f}:1" if effective_ratio else "N/A")
            fired.append({
                "id": f["id"],
                "title": f["title"],
                "severity": f["severity"],
                "guidance": guidance,
                "citations": _cite(f["query"], f["manual"]),
            })
    return {
        "fired": fired,
        "evaluated": evaluated,
        "intents": sorted(intents),
        "effective_ratio": effective_ratio,
        "gate_open": len(fired) > 0,   # True 면 턴 결과 전에 즉시 피드백
    }


if __name__ == "__main__":
    import json
    import sys
    payload = json.loads(sys.stdin.read())
    out = check(payload["state"], payload.get("decision", ""),
                payload.get("effective_ratio"), payload.get("chokepoint"))
    print(json.dumps(out, ensure_ascii=False, indent=2))
