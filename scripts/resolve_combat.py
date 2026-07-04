"""resolve_combat.py — 결정론 전투 판정 (숫자의 유일한 출처).

CLAUDE.md 절대원칙 1: "숫자는 코드, 서술은 LLM."
전투 결과·손실·전력비는 반드시 이 스크립트가 계산한다. LLM은 이 출력의 *범위 안에서만*
해석·서술한다. 모델은 전부 투명하고 감사 가능해야 하며, seed 고정으로 재현 가능하다.

계약(CLAUDE.md):
  입력(JSON): {
    attacker:{side,tanks,troops,posture}, defender:{...},
    terrain, weather, distance_km, seed
  }
  출력(JSON): {
    outcome: attacker_decisive|attacker_marginal|stalemate|defender_marginal|defender_decisive,
    p_attacker_success: float, effective_ratio: float,
    losses:{friendly:{tanks,troops}, enemy:{tanks,troops}},
    drivers:[ ... ]
  }
losses.friendly / losses.enemy 는 아군(Blue)/적(Red) 기준이다. attacker/defender 중
side 가 blue 계열인 쪽이 friendly 가 된다.
"""
from __future__ import annotations

import json
import math
import random
import sys
from typing import Dict, Any

# ── 전투력 상수 (한 단위의 상대 가치, 감사 가능) ──────────────────────────
TANK_V = 10.0    # 전차 1대 = 10 전투력 단위
TROOP_V = 0.10   # 병력 1명 = 0.1 → 병력 100명 ≈ 전차 1대

# ── 지형 배수: 방어자 총전력 배수(def_mult)와 전차 효과 배수(tank_mult) ──
#    forest/ridge/urban → 방어자 유리 & 전차 효과 저하 / open → 중립
TERRAIN = {
    "open":   {"def_mult": 1.00, "tank_mult": 1.00, "tank_vuln": 1.00},
    "forest": {"def_mult": 1.35, "tank_mult": 0.70, "tank_vuln": 1.20},
    "ridge":  {"def_mult": 1.50, "tank_mult": 0.75, "tank_vuln": 1.15},
    "urban":  {"def_mult": 1.60, "tank_mult": 0.60, "tank_vuln": 1.35},
}

# ── 기상 배수: 전차·원거리 효과 저하, 방어·침투 유리 (night 가장 강함) ──
WEATHER = {
    "clear": {"tank_mult": 1.00, "def_mult": 1.00, "infil_mult": 1.00},
    "rain":  {"tank_mult": 0.85, "def_mult": 1.05, "infil_mult": 1.10},
    "fog":   {"tank_mult": 0.75, "def_mult": 1.10, "infil_mult": 1.20},
    "night": {"tank_mult": 0.65, "def_mult": 1.15, "infil_mult": 1.35},
}

# ── 태세(방어자) 배수 ──
POSTURE_DEF = {"defend": 1.15, "delay": 1.05, "attack": 1.00, "hold": 1.15}

# ── p_attacker_success 곡선: effective_ratio 의 단조 증가 로지스틱 ──
#    R0=1.5 에서 50%. (전력비 우위가 있어야 공격 성공. 준비된 방어 상대 명목 3:1 →
#    지형·태세 배수 반영 후 effective_ratio ≈ 1.5 → 승률 50% 근처가 되도록 설계.)
RATIO_HALF = 1.5
LOGISTIC_K = 2.2

# ── 결과 밴드별 기본 손실률 (att_loss_frac, def_loss_frac) ──
OUTCOME_LOSS = {
    "attacker_decisive": (0.10, 0.45),
    "attacker_marginal": (0.20, 0.30),
    "stalemate":         (0.25, 0.25),
    "defender_marginal": (0.30, 0.18),
    "defender_decisive": (0.45, 0.10),
}

BLUE_SIDES = {"blue", "friendly", "아군", "rok", "rokaf", "blufor", "us", "un"}
EPS = 1e-6


def _terrain(t: str) -> Dict[str, float]:
    return TERRAIN.get((t or "open").lower(), TERRAIN["open"])


def _weather(w: str) -> Dict[str, float]:
    return WEATHER.get((w or "clear").lower(), WEATHER["clear"])


def _side_power(unit: Dict[str, Any], terr, wx, *, is_defender: bool,
                distance_km: float, posture: str) -> Dict[str, float]:
    """한 부대의 유효 전투력을 지형·기상·태세로 보정해 반환."""
    tanks = float(unit.get("tanks", 0) or 0)
    troops = float(unit.get("troops", 0) or 0)

    # 전차: 지형 거칠기 + 기상(가시선/원거리)으로 효과 저하
    tank_power = tanks * TANK_V * terr["tank_mult"] * wx["tank_mult"]
    # 개활지에서 clear 원거리 교전은 전차 표준화력, 근접일수록 이점 감소(경미)
    if terr is TERRAIN["open"] and wx is WEATHER["clear"]:
        tank_power *= 1.0 + min(0.15, max(0.0, (distance_km - 1.0)) * 0.03)

    # 보병: 악천후·거친지형에서 침투 이점 (공격 측에 주로 유효)
    infil = wx["infil_mult"] if not is_defender else 1.0
    if not is_defender and terr is not TERRAIN["open"]:
        infil *= 1.10
    troop_power = troops * TROOP_V * infil

    power = tank_power + troop_power
    if is_defender:
        power *= terr["def_mult"] * wx["def_mult"] * POSTURE_DEF.get((posture or "defend").lower(), 1.0)
    return {"power": power, "tank_power": tank_power, "troop_power": troop_power}


def _p_success(ratio: float) -> float:
    return 1.0 / (1.0 + math.exp(-LOGISTIC_K * (math.log(max(ratio, EPS)) - math.log(RATIO_HALF))))


def _outcome_from_score(s: float) -> str:
    if s > 0.30:
        return "attacker_decisive"
    if s > 0.05:
        return "attacker_marginal"
    if s >= -0.05:
        return "stalemate"
    if s >= -0.30:
        return "defender_marginal"
    return "defender_decisive"


def _losses(unit, frac, terr, rng) -> Dict[str, int]:
    tanks = int(unit.get("tanks", 0) or 0)
    troops = int(unit.get("troops", 0) or 0)
    jt = 1.0 + rng.uniform(-0.15, 0.15)
    jp = 1.0 + rng.uniform(-0.15, 0.15)
    tank_frac = min(1.0, frac * terr["tank_vuln"] * jt)
    troop_frac = min(1.0, frac * jp)
    return {
        "tanks": max(0, min(tanks, round(tanks * tank_frac))),
        "troops": max(0, min(troops, round(troops * troop_frac))),
    }


def resolve(payload: Dict[str, Any]) -> Dict[str, Any]:
    att = payload.get("attacker", {})
    dfd = payload.get("defender", {})
    terr = _terrain(payload.get("terrain"))
    wx = _weather(payload.get("weather"))
    distance_km = float(payload.get("distance_km", 0) or 0)
    seed = int(payload.get("seed", 0) or 0)
    rng = random.Random(seed)

    ap = _side_power(att, terr, wx, is_defender=False, distance_km=distance_km,
                     posture=att.get("posture", "attack"))
    dp = _side_power(dfd, terr, wx, is_defender=True, distance_km=distance_km,
                     posture=dfd.get("posture", "defend"))

    effective_ratio = ap["power"] / max(dp["power"], EPS)
    p = _p_success(effective_ratio)

    roll = rng.random()
    score = p - roll
    outcome = _outcome_from_score(score)

    att_frac, dfd_frac = OUTCOME_LOSS[outcome]
    att_losses = _losses(att, att_frac, terr, rng)
    dfd_losses = _losses(dfd, dfd_frac, terr, rng)

    # friendly(아군=Blue) / enemy(적=Red) 매핑
    att_is_blue = str(att.get("side", "")).lower() in BLUE_SIDES
    dfd_is_blue = str(dfd.get("side", "")).lower() in BLUE_SIDES
    if dfd_is_blue and not att_is_blue:
        friendly, enemy = dfd_losses, att_losses
    else:  # 기본: 공격 측을 아군으로 (side 불명확 시)
        friendly, enemy = att_losses, dfd_losses

    tname = (payload.get("terrain") or "open").lower()
    wname = (payload.get("weather") or "clear").lower()
    drivers = [
        f"force_ratio {effective_ratio:.2f}:1",
        f"p_attacker_success {p:.2f}",
        f"terrain {tname}: def x{terr['def_mult']:.2f}, armor x{terr['tank_mult']:.2f}",
    ]
    if wname != "clear":
        note = "favors infiltration" if wname in ("fog", "night") else "degraded armor/optics"
        drivers.append(f"weather {wname}: armor x{wx['tank_mult']:.2f}, {note} x{wx['infil_mult']:.2f}")
    dpos = (dfd.get("posture") or "defend").lower()
    if POSTURE_DEF.get(dpos, 1.0) != 1.0:
        drivers.append(f"defender {dpos} +x{POSTURE_DEF.get(dpos, 1.0):.2f}")
    if distance_km:
        drivers.append(f"engagement {distance_km:.2f} km")

    return {
        "outcome": outcome,
        "p_attacker_success": round(p, 4),
        "effective_ratio": round(effective_ratio, 4),
        "losses": {"friendly": friendly, "enemy": enemy},
        "drivers": drivers,
        # 감사용 부가 정보(계약 외, 서술 근거 추적용)
        "audit": {
            "attacker_power": round(ap["power"], 2),
            "defender_power": round(dp["power"], 2),
            "score": round(score, 4),
            "seed": seed,
        },
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read())
    print(json.dumps(resolve(data), ensure_ascii=False, indent=2))
