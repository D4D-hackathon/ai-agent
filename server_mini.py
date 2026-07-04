"""server_mini.py — ngrok 공개용 미니 FastAPI 서버.

엔진의 run_turn() 을 HTTP로 노출. 시나리오 → 전체 턴 실행 → 결과 반환.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

from scripts import engine

app = FastAPI(
    title="지상전 워게임 엔진",
    description="위경도 기반 실측 지형 + 교범 RAG + 결정론 전투 판정",
    version="1.0.0",
)


class ScenarioRequest(BaseModel):
    objective: str
    weather: str  # clear|rain|fog|night
    terrain: str  # open|forest|ridge|urban
    blue: Dict[str, Any]
    red: Dict[str, Any]
    decision: Optional[str] = None


@app.get("/")
def index():
    return {
        "name": "지상전 워게임 엔진 (ngrok 공개)",
        "api": "/docs",
        "example": {
            "endpoint": "POST /run",
            "input": {
                "objective": "능선 방어",
                "weather": "night",
                "terrain": "ridge",
                "blue": {"tanks": 8, "troops": 250, "lat": 38.2030, "lon": 127.2100, "posture": "defend"},
                "red": {"tanks": 14, "troops": 600, "lat": 38.1740, "lon": 127.2240, "posture": "attack"},
                "decision": "능선을 확보하고 기동예비로 역습한다",
            }
        }
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run_turn(req: ScenarioRequest):
    """한 턴 실행: 시나리오 → 지형분류 → 상황분석 → doctrine게이트 → 적행동 → 전투판정 → 결과"""
    payload = req.model_dump()
    result = engine.run_turn(payload, persist=False)

    if not result.get("ok"):
        raise HTTPException(400, detail=result)

    return result


@app.post("/scenario/validate")
def validate_scenario(req: ScenarioRequest):
    """시나리오 검증만 (교전거리·지형 계산, 입력 검증)"""
    payload = req.model_dump()
    st, missing = engine.normalize(payload, persist=False)

    if missing or st is None:
        return {"ok": False, "missing": missing or ["invalid_input"]}

    return {
        "ok": True,
        "scenario_id": st["id"],
        "mode": st["mode"],
        "distance_km": st["distance_km"],
        "approach_bearing_deg": st.get("approach_bearing_deg"),
    }


if __name__ == "__main__":
    import uvicorn
    print("🚀 서버 시작: http://localhost:8000")
    print("📚 문서: http://localhost:8000/docs")
    print("🌐 ngrok 공개: ngrok http 8000 (다른 터미널에서)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
