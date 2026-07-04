# S2 — 야간 침투 (Night Infiltration)

**작전목표:** 야간에 능선·삼림 접근로를 방어해 KPA 경보병의 침투와 종심 타격을 저지한다.
**상황:** 칠흑 같은 야간. 아군은 능선 일대를 방어. KPA 경보병 대대가 능선을 접근로로
도보 침투해 지휘소·화력자산을 노린다(TTR: 야간·악천후 경보병 침투 교리).

- 날씨: `night`  · 지형: `ridge`
- 아군(Blue): 전차 6, 병력 300, 위치 (38.1520, 127.5010), 태세 `defend`
- 적군(Red):  전차 4, 병력 700, 위치 (38.1280, 127.5180), 태세 `attack`

## 교육 포인트
- 야간에 가시선·원거리 직사화력을 전제하면 안 된다(전차 효과 급감, night 배수 강함).
- 능선을 내주고 계곡만 방어하면 침투를 초대한다 → `weather_terrain_ignored` flag.
- 근접전 대비 조명·매복·경계, 능선 관측 유지가 핵심.

## POST /scenario 페이로드
```json
{
  "objective": "야간 능선 접근로 방어로 KPA 경보병 침투 저지",
  "weather": "night",
  "terrain": "ridge",
  "blue": {"tanks": 6, "troops": 300, "lat": 38.1520, "lon": 127.5010, "posture": "defend"},
  "red":  {"tanks": 4, "troops": 700, "lat": 38.1280, "lon": 127.5180, "posture": "attack"}
}
```
