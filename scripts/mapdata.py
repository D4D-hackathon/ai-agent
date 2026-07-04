"""mapdata.py — 무키(no-key) 실측 지형 신호 수집기.

위경도로 실제 지형 데이터를 API 키 없이 가져와 terrain.classify 가 먹는 signals 로 조립한다.
  · 표고(능선):   OpenTopoData(aster30m) → 실패 시 Open-Elevation
  · 도로/건물/숲: OSM Overpass (반경 내 집계)
  · 협로(chokepoint): OSRM 데모 라우팅(도로거리/직선거리) — best-effort

모든 호출은 best-effort 다. 네트워크 실패 시 해당 신호만 비우고 fetch_errors 에 남긴다
(엔진은 이때 시나리오의 terrain 입력값으로 진행). 숫자(relief/slope/분류)는 terrain.py 가 만든다.

주의: 공용 무료 API 라 rate limit 이 있다(OpenTopoData 1req/s·1000/day, Overpass/OSRM fair-use).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from scripts.geo import bearing, haversine, sample_ring
except ImportError:
    from geo import bearing, haversine, sample_ring

UA = {"User-Agent": "wargame-agent/1.0 (terrain recon; educational wargame)",
      "Accept": "application/json"}
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter"]


def fetch_elevation(points: List[Tuple[float, float]], timeout: float = 20.0):
    """[(lat,lon)] → [elevation m]. OpenTopoData 우선, 실패 시 Open-Elevation."""
    locs = "|".join(f"{a:.6f},{b:.6f}" for a, b in points)
    # 1) OpenTopoData
    try:
        r = requests.get(f"https://api.opentopodata.org/v1/aster30m?locations={locs}",
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        res = r.json().get("results") or []
        elevs = [x.get("elevation") for x in res if x.get("elevation") is not None]
        if elevs:
            return elevs, "opentopodata"
    except Exception:
        pass
    # 2) Open-Elevation
    try:
        r = requests.get(f"https://api.open-elevation.com/api/v1/lookup?locations={locs}",
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        res = r.json().get("results") or []
        elevs = [x.get("elevation") for x in res if x.get("elevation") is not None]
        if elevs:
            return elevs, "open-elevation"
    except Exception:
        pass
    return None, None


def fetch_osm_counts(lat: float, lon: float, radius_m: int = 500, timeout: float = 40.0):
    """반경 내 숲/도로/건물 way 수 집계 (urban·forest 근거)."""
    q = (f"[out:json][timeout:25];"
         f"(way(around:{radius_m},{lat},{lon})[natural=wood];"
         f"way(around:{radius_m},{lat},{lon})[landuse=forest];);out count;"
         f"(way(around:{radius_m},{lat},{lon})[highway];);out count;"
         f"(way(around:{radius_m},{lat},{lon})[building];"
         f"node(around:{radius_m},{lat},{lon})[building];);out count;")
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data=q.encode("utf-8"), headers=UA, timeout=timeout)
            r.raise_for_status()
            counts = [int(e.get("tags", {}).get("total", 0))
                      for e in r.json().get("elements", []) if e.get("type") == "count"]
            if len(counts) >= 3:
                return {"forest": counts[0], "roads": counts[1], "buildings": counts[2]}, "overpass"
        except Exception:
            continue
    return None, None


def fetch_route(a: Tuple[float, float], b: Tuple[float, float], timeout: float = 20.0):
    """A→B 도로 경로(OSRM 데모). straight_km/road_km/alternatives → 협로 근거. best-effort."""
    straight = haversine(a[0], a[1], b[0], b[1])
    url = (f"https://router.project-osrm.org/route/v1/driving/"
           f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}?overview=false&alternatives=true")
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        routes = r.json().get("routes") or []
        if routes:
            road_km = min(rt["distance"] for rt in routes) / 1000.0
            return {"straight_km": round(straight, 3), "road_km": round(road_km, 3),
                    "alternatives": len(routes)}, "osrm"
    except Exception:
        pass
    return None, None


def _defile_from_ring(center_elev, ring_elevs, n, approach_bearing):
    """접근축(approach_bearing)에 수직인 양 측면 표고가 통로(center)보다 얼마나 높은지.
    양쪽 다 높으면 협로(defile). ring 은 sample_ring 과 동일한 방위 순서로 가정."""
    azimuths = [(360.0 / n) * i for i in range(n)]

    def nearest(az):
        return min(range(n), key=lambda i: min(abs(azimuths[i] - az), 360 - abs(azimuths[i] - az)))

    p1, p2 = (approach_bearing + 90) % 360, (approach_bearing - 90) % 360
    i1, i2 = nearest(p1), nearest(p2)
    rise = min(ring_elevs[i1], ring_elevs[i2]) - center_elev
    return {"flank_rise_m": round(rise, 1),
            "perp_azimuths": [round(p1), round(p2)],
            "flank_elevs": [ring_elevs[i1], ring_elevs[i2]], "corridor_elev": center_elev}


def fetch_signals(lat: float, lon: float, to: Optional[Tuple[float, float]] = None,
                  approach_bearing: Optional[float] = None, include_route: bool = False,
                  ring_radius_m: int = 400, n: int = 8,
                  timeout: float = 20.0) -> Dict[str, Any]:
    """위경도 → terrain.classify 용 signals 조립 (실측, 무키).

    협로(chokepoint)는 표고 형태(defile: 접근축 양측면 융기)로 판정한다 — approach_bearing 필요.
    include_route=True 면 OSRM 라우팅도 보조로 시도(공용 데모라 불안정, 실패해도 무방)."""
    sources: List[str] = []
    errors: List[str] = []
    signals: Dict[str, Any] = {"ring_radius_m": ring_radius_m}

    # 표고: 중심 + 링
    pts = [(lat, lon)] + sample_ring(lat, lon, ring_radius_m / 1000.0, n)
    elevs, src = fetch_elevation(pts, timeout)
    if elevs:
        signals["center_elevation"] = elevs[0]
        signals["elevations"] = elevs
        sources.append(src)
        # 협로(defile): 접근 방위 수직 양측면 융기 (실측 표고)
        if approach_bearing is not None and len(elevs) >= n + 1:
            signals["defile"] = _defile_from_ring(elevs[0], elevs[1:1 + n], n, approach_bearing)
    else:
        errors.append("elevation: 표고 API 실패(능선/협로 판정 비활성)")

    # OSM 도로/건물/숲
    osm, src = fetch_osm_counts(lat, lon, radius_m=max(ring_radius_m, 500), timeout=timeout + 20)
    if osm:
        signals["nearby"] = {"total": osm["buildings"], "natural": osm["forest"]}
        signals["roads"] = osm["roads"]
        # 도시성 힌트: 건물 밀집 시 route 타입 부여(간이 reverse geocode 대용)
        types = []
        if osm["buildings"] >= 20:
            types.append("locality")
        if osm["forest"] > 0:
            types.append("natural_feature")
        if types:
            signals["reverse_geocode"] = {"types": types}
        sources.append(src)
    else:
        errors.append("osm: Overpass 실패(도시/삼림 근거 축소)")

    # 협로 보조: 적 위치까지 도로 경로 (opt-in; 공용 OSRM 데모라 불안정)
    if include_route and to:
        route, src = fetch_route(to, (lat, lon), timeout)  # 적→아군 접근로
        if route:
            signals["route"] = route
            sources.append(src)
        else:
            errors.append("route: OSRM 실패(협로는 defile 표고 판정 사용)")

    signals["sources"] = sources
    if errors:
        signals["fetch_errors"] = errors
    return signals


if __name__ == "__main__":
    import json
    import sys
    lat, lon = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) >= 3 else (38.2030, 127.2100)
    to = (float(sys.argv[3]), float(sys.argv[4])) if len(sys.argv) >= 5 else None
    ab = bearing(to[0], to[1], lat, lon) if to else None  # 적→아군 접근 방위
    print(json.dumps(fetch_signals(lat, lon, to=to, approach_bearing=ab),
                     ensure_ascii=False, indent=2))
