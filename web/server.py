"""FastAPI 서버 — 대시보드 정적 서빙 + 상태 API + SSE 실시간 이벤트 (P1 A1).

실행: python -m web.server   (기본 http://127.0.0.1:8000, .env 의 WEB_PORT)
Cloud Run 배포(P2)에서는 WEB_HOST=0.0.0.0, PORT 환경변수를 쓰게 된다.
"""
from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import CFG
from web.engine import TradingEngine, EngineError
from web.events import EventBus

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

bus = EventBus()
engine = TradingEngine(bus)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # 서버 종료 시 실행 중 세션을 정리(라이브면 아카이브까지)
    if engine.status == "running":
        try:
            await engine.stop()
        except Exception:
            pass


app = FastAPI(title="AutoTrader Agent Dashboard", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------- 조회 API ----------

@app.get("/api/state")
async def get_state():
    return engine.state_snapshot()


@app.get("/api/trades")
async def get_trades():
    return {"trades": engine.trades}          # A5 거래 내역


@app.get("/api/decisions")
async def get_decisions():
    return {"decisions": engine.decisions}    # A6 판단 타임라인


# ---------- 컨트롤 API ----------

class StartBody(BaseModel):
    mode: str = "dry"      # dry / live


class ActorBody(BaseModel):
    actor: str = "human"   # A2 정지 주체 기록 (P3 리스크가드가 "risk-guard" 로 재사용)


@app.post("/api/engine/start")
async def engine_start(body: StartBody):
    try:
        return await engine.start(body.mode)
    except EngineError as e:
        code = 409 if "이미 실행" in str(e) else 400
        raise HTTPException(status_code=code, detail=str(e))


@app.post("/api/engine/stop")
async def engine_stop():
    try:
        return await engine.stop()
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/trading/pause")
async def trading_pause(body: ActorBody):
    return engine.pause(body.actor)


@app.post("/api/trading/resume")
async def trading_resume(body: ActorBody):
    return engine.resume(body.actor)


class MandateBody(BaseModel):
    """A3 한도 변경 — 금액은 문자열로 받아 Decimal 정밀 변환 (float 오차 방지)."""
    budget_total_usdc: str
    per_trade_max_usdc: str
    actor: str = "human"


@app.post("/api/mandate")
async def update_mandate(body: MandateBody):
    try:
        budget = Decimal(body.budget_total_usdc)
        per_trade = Decimal(body.per_trade_max_usdc)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="한도 값이 숫자 형식이 아닙니다.")
    try:
        return engine.update_limits(budget, per_trade, body.actor)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- SSE ----------

@app.get("/api/events")
async def sse_events(request: Request) -> StreamingResponse:
    """SSE 스트림. Last-Event-ID 헤더(또는 ?since=) 이후 히스토리를 먼저 재전송하고
    이후 실시간 이벤트를 흘린다 — 새로고침해도 피드가 복원된다."""
    try:
        last_id = int(request.headers.get("last-event-id")
                      or request.query_params.get("since") or 0)
    except ValueError:
        last_id = 0

    async def gen():
        q = bus.subscribe()
        try:
            for e in bus.since(last_id):
                yield e.to_sse()
            while not await request.is_disconnected():
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                    yield e.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 15초 하트비트 (연결 유지·끊김 감지)
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


def main() -> None:
    import uvicorn
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=CFG.web_port, log_level="info")


if __name__ == "__main__":
    main()
