# ngrok으로 공개 배포하기

## 준비

```bash
# 1. ngrok 설치 (이미 완료)
ngrok version

# 2. 의존성 설치
.venv/bin/pip install -q fastapi uvicorn
```

## 실행 (터미널 2개)

### 터미널 1: FastAPI 서버 시작
```bash
cd /Users/hyunwoo/workspace/wargame-agent
.venv/bin/uvicorn server_mini:app --port 8000 --reload
```

**출력 예시:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### 터미널 2: ngrok 터널링
```bash
ngrok http 8000
```

**출력 예시:**
```
Session Status                online
Account                       <email>
Forwarding                    https://xxxx-xx-xxx-xxxx.ngrok.io -> http://localhost:8000
```

## 사용법

### 공개 URL
```
https://xxxx-xx-xxx-xxxx.ngrok.io  ← ngrok 터미널에서 복사
```

### API 문서 (swagger)
```
https://xxxx-xx-xxx-xxxx.ngrok.io/docs
```

### 시나리오 실행 (curl)
```bash
curl -X POST https://xxxx-xx-xxx-xxxx.ngrok.io/run \
  -H "Content-Type: application/json" \
  -d '{
    "objective": "능선 방어",
    "weather": "night",
    "terrain": "ridge",
    "blue": {"tanks": 8, "troops": 250, "lat": 38.2030, "lon": 127.2100, "posture": "defend"},
    "red": {"tanks": 14, "troops": 600, "lat": 38.1740, "lon": 127.2240, "posture": "attack"},
    "decision": "능선을 확보하고 기동예비로 역습한다"
  }'
```

### Python에서
```python
import requests

url = "https://xxxx-xx-xxx-xxxx.ngrok.io/run"
payload = {...}
response = requests.post(url, json=payload)
print(response.json())
```

## 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 서버 정보 |
| GET | `/health` | 헬스 체크 |
| POST | `/run` | 한 턴 실행 (전체: 지형→상황→doctrine→적행동→판정) |
| POST | `/scenario/validate` | 시나리오 검증만 (입력 유효성 + 거리 계산) |

---

**팀원 공유**: ngrok URL을 메모해 둔 후 다른 팀원들에게 URL 공유하면 누구나 사용 가능!
