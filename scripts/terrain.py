"""terrain.py — 지형 인지 (Google Maps MCP 신호 → open|forest|ridge|urban 분류).

CLAUDE.md: 위경도로 지형을 인지한다. MCP 도구는 에이전트(Claude Code)가 호출하고, 그 원자료
(signals)를 이 순수 함수에 넘긴다 → 결정론적으로 분류. 서버 프로세스는 MCP를 직접 못 부르므로
지형 '분류'만 코드가 담당하고 '수집'은 에이전트가 담당한다.

신호 수집(에이전트가 MCP로):
  - maps_elevation : 병력 위치 + 주변 링(geo.sample_ring) 표고 → 기복(relief)/경사(slope) → ridge
  - maps_reverse_geocode + maps_search_places : 도로/건물/POI 밀도 → urban
  - maps_directions / maps_distance_matrix : 도로/직선거리 비, 대체경로 수 → chokepoint(협로)

forest 는 Google Maps 공식 서버에 landcover 가 없어 근사한다(자연/공원 place type + 저urban +
중기복). 정확도 위해 CLAUDE.md 입력의 지형 필드(user_terrain)가 있으면 그것을 우선하고,
MCP 추론은 검증/보강(drivers)으로만 쓴다. 불일치 시 경고를 남긴다.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# ── 분류 임계값 (감사 가능, 상단 문서화) ──
RIDGE_RELIEF_M = 60.0    # 링 반경 내 표고차 ≥ 60m → 능선/고지 경향
RIDGE_SLOPE_DEG = 12.0   # 중심→링 경사 ≥ 12° → 능선/고지
URBAN_SCORE_HI = 0.50    # urban_score ≥ 0.5 → urban
FOREST_SCORE_HI = 0.40   # forest_score ≥ 0.4 → forest
CHOKE_ROAD_RATIO = 1.6   # 도로거리/직선거리 ≥ 1.6 → 우회 강제(협로 경향)
DEFILE_FLANK_M = 40.0    # 접근축 양 측면이 통로보다 ≥ 40m 높으면 협로(defile)
DEFAULT_RING_M = 400.0   # 링 반경 미상 시 가정 반경

URBAN_TYPES = {"route", "street_address", "premise", "subpremise", "locality",
               "sublocality", "neighborhood", "postal_code", "intersection",
               "transit_station", "shopping_mall"}
URBAN_POI = {"restaurant", "store", "cafe", "bank", "school", "hospital",
             "building", "lodging", "supermarket", "gas_station", "pharmacy"}
NATURAL_TYPES = {"natural_feature", "park", "campground", "national_park",
                 "forest", "hiking_area"}


def _elev_values(signals: Dict[str, Any]) -> List[float]:
    """다양한 형태의 표고 입력에서 숫자 표고 리스트를 뽑아낸다."""
    vals: List[float] = []
    for e in (signals.get("elevations") or []):
        if isinstance(e, (int, float)):
            vals.append(float(e))
        elif isinstance(e, dict):
            for key in ("elevation", "elev", "value"):
                if isinstance(e.get(key), (int, float)):
                    vals.append(float(e[key]))
                    break
    ce = signals.get("center_elevation")
    if isinstance(ce, (int, float)):
        vals.append(float(ce))
    return vals


def _relief_slope(signals):
    vals = _elev_values(signals)
    if len(vals) < 2:
        return None, None
    relief = max(vals) - min(vals)
    radius = float(signals.get("ring_radius_m") or DEFAULT_RING_M)
    slope = math.degrees(math.atan2(relief, radius))
    return round(relief, 1), round(slope, 1)


def _urban_score(signals) -> (float, list):
    drivers = []
    score = 0.0
    rg = signals.get("reverse_geocode") or {}
    types = set(t.lower() for t in (rg.get("types") or []))
    hit_types = types & URBAN_TYPES
    if hit_types:
        score += 0.35
        drivers.append(f"geocode types {sorted(hit_types)}")
    nearby = signals.get("nearby") or {}
    total = nearby.get("total")
    if total is None and isinstance(nearby.get("counts_by_type"), dict):
        total = sum(nearby["counts_by_type"].values())
    if isinstance(total, (int, float)):
        dens = min(0.55, total / 40.0)   # POI 40개 ≈ 포화
        score += dens
        drivers.append(f"{int(total)} nearby POIs (density +{dens:.2f})")
    return min(1.0, round(score, 2)), drivers


def _forest_score(signals, urban_score) -> (float, list):
    drivers = []
    score = 0.0
    rg = signals.get("reverse_geocode") or {}
    types = set(t.lower() for t in (rg.get("types") or []))
    nearby = signals.get("nearby") or {}
    nat = nearby.get("natural")
    if nat is None:
        cbt = nearby.get("counts_by_type") or {}
        nat = sum(v for t, v in cbt.items() if t.lower() in NATURAL_TYPES)
    if types & NATURAL_TYPES:
        score += 0.35
        drivers.append(f"natural geocode types {sorted(types & NATURAL_TYPES)}")
    if isinstance(nat, (int, float)) and nat > 0:
        score += min(0.4, nat / 10.0)
        drivers.append(f"{int(nat)} natural/park features nearby")
    score *= (1.0 - urban_score)   # 도심일수록 forest 가능성↓
    return min(1.0, round(score, 2)), drivers


def _chokepoint(signals) -> (Optional[bool], list):
    """협로 판정. (1) 표고 형태(defile: 접근축 양측면이 통로보다 높음) 우선,
    (2) 도로 라우팅(도로/직선 비, 대체경로 수) 보조."""
    drivers = []
    choke: Optional[bool] = None

    # (1) defile: 접근축에 수직인 양 측면이 통로보다 얼마나 솟았는가 (실측 표고)
    defile = signals.get("defile")
    if isinstance(defile, dict) and isinstance(defile.get("flank_rise_m"), (int, float)):
        rise = defile["flank_rise_m"]
        drivers.append(f"defile flank rise {rise:.0f}m (approach-perpendicular)")
        choke = bool(rise >= DEFILE_FLANK_M)

    # (2) 도로 라우팅 보조(있을 때만)
    route = signals.get("route")
    if isinstance(route, dict):
        straight, road, alts = route.get("straight_km"), route.get("road_km"), route.get("alternatives")
        if isinstance(straight, (int, float)) and isinstance(road, (int, float)) and straight > 0:
            ratio = road / straight
            drivers.append(f"road/straight {ratio:.2f}x")
            if ratio >= CHOKE_ROAD_RATIO:
                choke = True
        if isinstance(alts, (int, float)):
            drivers.append(f"{int(alts)} route alternative(s)")
            if alts <= 1 and choke is None:
                choke = False
    return choke, drivers


def classify(signals: Dict[str, Any]) -> Dict[str, Any]:
    """Maps 신호 → 지형 분류. user_terrain 이 있으면 우선(불일치 시 경고)."""
    drivers: List[str] = []
    notes: List[str] = []

    relief, slope = _relief_slope(signals)
    if relief is None:
        notes.append("표고 신호 없음 → 능선 판정(기복/경사) 불가. maps_elevation 링을 수집하면 정확해짐.")
    else:
        drivers.append(f"relief {relief}m / slope ~{slope}° over {int(signals.get('ring_radius_m') or DEFAULT_RING_M)}m ring")

    urban_score, ud = _urban_score(signals)
    drivers += [f"urban: {d}" for d in ud]
    forest_score, fd = _forest_score(signals, urban_score)
    drivers += [f"forest: {d}" for d in fd]
    choke, cd = _chokepoint(signals)
    drivers += [f"chokepoint: {d}" for d in cd]

    # MCP 신호만으로 추론
    mcp = "open"
    if urban_score >= URBAN_SCORE_HI:
        mcp = "urban"
    elif relief is not None and (relief >= RIDGE_RELIEF_M or slope >= RIDGE_SLOPE_DEG):
        mcp = "ridge"
    elif forest_score >= FOREST_SCORE_HI:
        mcp = "forest"

    user_terrain = (signals.get("user_terrain") or "").lower() or None
    if user_terrain:
        terrain = user_terrain
        source = "user"
        agreement = (user_terrain == mcp) if relief is not None or urban_score or forest_score else None
        if agreement is False:
            notes.append(f"불일치: 사용자 지정 '{user_terrain}' vs MCP 추론 '{mcp}'. "
                         f"사용자 값 우선 사용. 위경도/지형 재확인 권장.")
        elif agreement is True:
            source = "mcp+user"
    else:
        terrain = mcp
        source = "mcp"
        agreement = None
        if relief is None and not urban_score and not forest_score:
            notes.append("MCP 신호가 비어 있어 지형을 open 으로 가정. user_terrain 제공 권장.")

    return {
        "terrain": terrain,
        "source": source,
        "mcp_inferred": mcp,
        "agreement": agreement,
        "chokepoint": bool(choke) if choke is not None else False,
        "chokepoint_known": choke is not None,
        "relief_m": relief,
        "slope_deg": slope,
        "urban_score": urban_score,
        "forest_score": forest_score,
        "drivers": drivers,
        "notes": notes,
    }


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(classify(json.loads(sys.stdin.read())), ensure_ascii=False, indent=2))
