# 지상전 전술 결심 워게임 — 시뮬레이션 엔진 (War-game AI Tutor)

정해진 시나리오에서 **지휘관 결심**을 훈련시키는 교육용 워게임의 **시뮬레이션 엔진**.
백엔드·프론트엔드는 팀원이 구현했고, 이 저장소는 그 백엔드에서 **시나리오(상황·병력 위경도·
공격/수비 옵션)를 받아** → **위경도로 실제 지형 데이터(무키 표고·OSM API)를 가져와 지형을 인지** →
**교범 RAG 근거**로 → **결정론 전투 판정**을 하고, 에이전트(Claude Code)가 근거를 인용해 한국어로 서술한다.

> 절대 원칙(CLAUDE.md): **숫자는 코드**(`resolve_combat.py`), **모든 주장은 교범 근거(파일+페이지) 인용**,
> **출력은 한국어**, **AI 단독 판정 금지**.

## 범위
- ✅ 이 저장소: 워게임 **엔진** — 전투 판정 · 지형 분류 · 교범 RAG · 교리 피드백.
- ⛔ 이 저장소 밖(팀원 담당): **백엔드 서버 · 프론트엔드**. 여기선 그 백엔드에서 **받아오기만** 한다.

## 아키텍처

```
[팀원 백엔드/프론트엔드]  ──시나리오(상황·위경도·태세)──▶  client.py (연동 어댑터)
                                                              │
   Claude Code (에이전트)                                     ▼
   · 엔진 결과(전력비·교범근거·판정)를 ────────────────▶  scripts/engine.py  (run_turn)
     교범 근거 인용해 한국어로 서술                            │  호출
                                                              ├─▶ mapdata.py ──무키 실측──▶ 표고 API / OSM
                                                              ▼   (위경도 → 지형 신호)
                                          결정론 코어 (scripts/)
                                          resolve_combat · terrain · retrieval · doctrine · state
```

지형 **수집**은 `mapdata.py` 가 위경도로 실제 데이터를 API 키 없이 가져오고, 지형 **분류**
(신호→open/forest/ridge/urban + 협로)는 `terrain.py` 가 결정론적으로 한다.
(선택) Google Maps MCP 로 신호를 넣고 싶으면 `engine.attach_terrain(state, signals=...)` 에 직접 전달 가능.

## 구성요소
| 파일 | 역할 |
|---|---|
| `scripts/engine.py` | **엔진 진입점.** 한 턴 프로토콜을 import 가능한 함수로 노출(`run_turn` 등) |
| `scripts/resolve_combat.py` | **숫자의 유일한 출처.** 결정론 전투 판정(전력비·확률·손실), seed 재현 |
| `scripts/geo.py` | 위경도 → 교전거리(haversine)·방위·표고 샘플 링 |
| `scripts/mapdata.py` | **무키 실측 지형 수집.** 위경도 → 표고(OpenTopoData/Open-Elevation) + OSM(도로·건물·숲) → 신호 |
| `scripts/terrain.py` | 지형 신호 → 분류(open/forest/ridge/urban) + 협로(defile) + 기복/경사 |
| `scripts/build_index.py` | 교범 PDF → 페이지 텍스트 인덱스(`doctrine/index/*.jsonl`) + `nk-ttr.txt` |
| `scripts/retrieval.py` | 교범 RAG(BM25) → **파일+페이지** 인용 |
| `scripts/doctrine.py` | `doctrine_check`: 결심 평가 flag + 교범 근거(피드백 게이트) |
| `scripts/state.py` | 입력 정규화, `game.json`/`log.jsonl`, 전력 갱신·종료 판정, 리포트 조립 |
| `client.py` | 팀원 **백엔드 ↔ 엔진 연동 어댑터**(규격 확정 시 이 파일만 수정) |
| `run_turn.py` | 백엔드 없이 엔진을 돌려보는 **CLI**(테스트/데모, 파일·stdin 연동) |

## 설치
```bash
cd wargame-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/build_index.py      # 교범 인덱스 생성(1회, ~15초)
```

### 지형 데이터 (무키 실측 — 기본)
`mapdata.py` 가 위경도로 **API 키 없이** 실제 지형을 가져온다. 별도 설정 불필요(인터넷만 필요):
- 표고(능선·협로): **OpenTopoData**(aster30m) → 실패 시 **Open-Elevation**
- 도로·건물·숲(도시·삼림): **OSM Overpass**
- 협로(chokepoint)는 접근축 수직 양측면 융기(**defile**)를 실측 표고로 판정.

> 공용 무료 API 라 rate limit 존재(OpenTopoData 1req/s·1000/day 등). 실패 시 해당 신호만 비우고
> 시나리오 `terrain` 값으로 진행한다(graceful). **forest(식생)** 는 OSM 태깅이 성겨 약하므로
> 시나리오 `terrain` 입력을 우선한다.
>
> (선택) Google Maps MCP 를 쓰려면: `export GOOGLE_MAPS_API_KEY=…` 후 `.mcp.json` 의
> `@modelcontextprotocol/server-google-maps` 로드 → 에이전트가 `maps_*` 로 신호를 모아
> `engine.attach_terrain(state, signals=...)` 에 넘기면 된다.

## 사용법

### A. 팀원 백엔드와 연동 (`client.py`)
백엔드 API **규격이 확정되면** `client.py` 상단 3곳만 수정:
`BASE_URL`/경로, `adapt_scenario()`(백엔드 JSON→엔진 스키마), `adapt_result()`(엔진→백엔드).
```python
from client import run_from_backend
result = run_from_backend(terrain_signals=signals, decision="능선 확보·역습 준비")
```

### B. 인프로세스 import (백엔드가 엔진을 직접 호출)
```python
from scripts import engine
out = engine.run_turn(scenario, terrain_signals=signals, decision="…")
#   또는 단계별: engine.normalize → attach_terrain → situation → doctrine_gate
#                → enemy_action → resolve_turn → report
```

### C. CLI (백엔드 없이 테스트)
```bash
.venv/bin/python run_turn.py scenario.json --signals signals.json \
    --decision "능선(핵심지형)을 확보·유지하고 기동예비를 남겨 역습한다"
cat scenario.json | .venv/bin/python run_turn.py         # stdin
```

## 엔진 입력 스키마 (= CLAUDE.md 입력 스키마)
```json
{
  "objective": "협로 통제 능선 방어",
  "weather": "night",                     // clear|rain|fog|night
  "terrain": "ridge",                     // open|forest|ridge|urban
  "blue": {"tanks": 8,  "troops": 250, "lat": 38.2030, "lon": 127.2100, "posture": "defend"},
  "red":  {"tanks": 14, "troops": 600, "lat": 38.1740, "lon": 127.2240, "posture": "attack"}
}
```
누락 필드는 **지어내지 않고** `missing` 목록으로 되묻는다(CLAUDE.md). 예: `["적군.병력","적군.태세"]`.

## 한 턴 프로토콜 (CLAUDE.md ↔ 엔진 함수)
| 단계 | CLAUDE.md | 엔진 함수 / MCP |
|---|---|---|
| 입력 | 입력 정규화 | `engine.normalize(payload)` |
| 지형 인지 | 위경도 지형 인지 | `engine.attach_terrain(state)` — `mapdata.py` 가 표고·OSM 실측 자동 수집(무키). (선택) MCP 신호는 `signals=` 로 주입 |
| ① 상황분석·결심 유도 | 전력비 인용, 정답 미노출 | `engine.situation(state)` (전력비=`effective_ratio`) |
| ② 피드백 게이트 | doctrine flag | `engine.doctrine_gate(state, decision)` |
| ③ 적 행동 | KPA 교리 인용 | `engine.enemy_action(state)` (NK-TTR 검색) |
| ④ 전투 판정·갱신 | losses 로만 갱신 | `engine.resolve_turn(state, …)` → `game.json`/`log.jsonl` |
| ⑤ 종료·리포트 | 1장 리포트 | `engine.report(scenario_id)` → 에이전트가 100자 md 저장 |

## 설계 노트
- **결정론:** 같은 `seed`(=`crc32(id:turn)`) → 같은 결과. LLM 은 값을 **인용만** 한다.
- **인용 페이지:** 물리 PDF 페이지(1-base). 인쇄 페이지와 1p 내외 오프셋 가능.
- **지형 실측:** 표고·도로·건물은 무키 API 실측(결정론 분류). 협로는 접근축 수직 양측면 융기(defile)로 판정 —
  값이 낮으면 협로를 **날조하지 않고** chokepoint=False 로 둔다.
- **forest 한계:** 무료 landcover 가 없어(공식 Google Maps MCP 도 동일) forest 는 약함. `terrain`(사용자 입력)을
  우선하고 실측은 기복/도시/협로 drivers 로 보강, 불일치 시 경고.
- **하지 말 것:** LLM 이 전력비·손실·확률 계산 / 교범 근거 없는 적 행동·피드백 / 정답 사전 노출.
