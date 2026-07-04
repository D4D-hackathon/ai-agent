"""geo.py — 위경도 기반 거리/방위 계산 헬퍼.

CLAUDE.md: 입력받은 아군/적군 위치(위도,경도)로 교전거리를 계산해 둔다.
LLM이 거리를 지어내지 않도록, 거리는 반드시 이 모듈이 계산한다.
표준 라이브러리만 사용(외부 의존성 없음).
"""
from __future__ import annotations

import math
from typing import Tuple

EARTH_RADIUS_KM = 6371.0088  # 평균 지구 반지름(km)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 사이의 대권 거리(km). haversine 공식."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """1->2 방위각(도, 0=북, 시계방향). 적 접근 방향 판단에 사용."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat: float, lon: float, bearing_deg: float, distance_km: float) -> Tuple[float, float]:
    """시작점에서 방위/거리만큼 이동한 지점의 (위도,경도).
    지형 신호 수집용 링(주변 표고 샘플점) 생성에 사용한다."""
    d = distance_km / EARTH_RADIUS_KM
    br = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def sample_ring(lat: float, lon: float, radius_km: float, n: int = 8):
    """중심점 주변 n방위의 (위도,경도) 링. 표고 샘플 → 기복/경사 계산용."""
    return [destination(lat, lon, (360.0 / n) * i, radius_km) for i in range(n)]


if __name__ == "__main__":
    import json
    import sys

    args = json.loads(sys.stdin.read())
    print(json.dumps({
        "distance_km": round(haversine(args["lat1"], args["lon1"], args["lat2"], args["lon2"]), 4),
        "bearing_deg": round(bearing(args["lat1"], args["lon1"], args["lat2"], args["lon2"]), 1),
    }))
