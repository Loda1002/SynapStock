"""FastAPI 서버 — 대시보드 정적 서빙 + 상태 API + SSE 실시간 이벤트 (P1 A1).

실행: python -m web.server   (기본 http://127.0.0.1:8000, .env 의 WEB_PORT)
Cloud Run 배포(P2)에서는 WEB_HOST=0.0.0.0, PORT 환경변수를 쓰게 된다.
"""
from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import secrets as _secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import CFG
from web.engine import TradingEngine, EngineError
from web.events import EventBus
from web.store import build_store

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

bus = EventBus()
store = build_store()          # Firestore(FIRESTORE_ENABLED=1) 또는 no-op
engine = TradingEngine(bus, store)


def require_control(request: Request) -> None:
    """조작 API 게이트 — CONTROL_TOKEN 이 설정된 경우에만 X-Control-Token 헤더를 요구한다.

    배포는 --allow-unauthenticated 라 URL 을 아는 누구나 세션을 시작·정지하고 한도를 바꿀 수
    있었다(엔진이 전역 싱글턴 1개라 시연 중 외부 stop 한 번이면 데모가 끊긴다).
    읽기(GET·SSE)는 열어 두어 심사위원이 로그인 없이 관전할 수 있게 하고, 상태를 바꾸는
    POST 만 막는다. 로컬은 CONTROL_TOKEN 미설정 = 무인증이라 기존 개발 흐름이 그대로다.
    """
    expected = CFG.control_token
    if not expected:
        return
    got = request.headers.get("x-control-token", "")
    # 타이밍 공격 방지 — 길이·내용 비교를 상수 시간으로
    if not _secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="조작 권한이 없습니다 (접근 토큰 필요).")


async def _daily_briefing_loop() -> None:
    """B2: 장 마감 시각(DAILY_BRIEFING_TIME) 하루 1회 자동 브리핑 — 30초마다 시각 체크."""
    while True:
        await asyncio.sleep(30)
        try:
            await engine.maybe_daily_briefing()
        except Exception:
            pass  # 루프는 죽지 않는다 — 개별 실패는 엔진이 ERROR 이벤트로 알림


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 부팅 복원 — 한도 기본값·최근 브리핑 (Cloud Run 재시작·재배포 대비)
    await engine.restore_from_store()
    daily_task = asyncio.create_task(_daily_briefing_loop())
    yield
    daily_task.cancel()
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


@app.get("/login")
async def login() -> FileResponse:
    # 로그인/회원가입 자리표시 페이지 — 실제 인증은 제출 후 로드맵.
    # 대시보드 접근을 막지 않으므로 인증 의존성 없이 정적 파일만 서빙한다.
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


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


# ---------- 이력 조회 (Firestore 영속 — 재시작·재배포 후에도 남는 데이터) ----------

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


@app.get("/api/history/sessions")
async def history_sessions(limit: int = 20):
    """지난 세션 요약 목록 (최신순). 영속화 비활성이면 빈 목록 + enabled=false."""
    return {"enabled": store.enabled,
            "sessions": await store.recent_sessions(_clamp(limit, 1, 100))}


@app.get("/api/history/sessions/{session_id}")
async def history_session_detail(session_id: str):
    """세션 상세 — 거래·판단 로그 전체 포함 (artifacts/tx 아카이브의 DB판)."""
    doc = await store.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return doc


@app.get("/api/history/trades")
async def history_trades(limit: int = 50):
    """세션 경계를 넘는 체결 이력 (최신순) — 주간/월별 수익 집계의 데이터 원천."""
    return {"enabled": store.enabled,
            "trades": await store.recent_trades(_clamp(limit, 1, 200))}


@app.get("/api/history/briefings")
async def history_briefings(limit: int = 10):
    return {"enabled": store.enabled,
            "briefings": await store.recent_briefings(_clamp(limit, 1, 50))}


# ---------- 컨트롤 API ----------

class StrictBody(BaseModel):
    """요청 본문 공통 규칙 — 선언되지 않은 필드는 422 로 거부한다.

    기본값(무시)이면 오타난 필드가 조용히 먹혀 "설정했는데 반영이 안 되는" 버그가 되고,
    제거된 필드(예: feed.file)를 그대로 보내도 성공처럼 보인다. 둘 다 겪은 문제라 막는다."""
    model_config = {"extra": "forbid"}


class StrategyBody(StrictBody):
    """B7 전략 선택 — condition(조건형) / dca(적립형: 주기마다 정액 매수)
    / trend(추세추종: 상승세 전량 보유·하락세 전량 매도).

    적립 주기 기준(dca_unit): ticks(N틱마다) / minutes(N분마다) / daily(매일 HH:MM).
    decision_mode: 조건형의 Gemini 재량 — strict(규칙 그대로) / trend(보류 재량).
    trend_signal: 추세추종 판단 방식 — pxma20(가격≥MA20) / cross_5_20(골든크로스5/20)."""
    type: str = "condition"
    decision_mode: str = "strict"
    trend_signal: str = "pxma20"   # 추세추종 신호 (type=trend 에서만 의미)
    ta_mode: bool = False  # TA 보강 — MA 배열·크로스·지지/저항·패턴 근거 판단
    dca_unit: str = "ticks"
    dca_every_ticks: int = 5
    dca_every_minutes: int = 60
    dca_at_time: str = "09:00"
    dca_amount_usdc: str = "10"   # Decimal 정밀 변환용 문자열


class FeedBody(StrictBody):
    """시세 피드 선택 — mock(8스텝 데모) / replay(실데이터 CSV 재생).

    빈값이면 .env(PRICE_FEED·REPLAY_*) 기본을 따른다.
    ※ CSV 경로는 API 로 받지 않는다(경로 주입 차단) — 심볼만 받아 서버가 조립하고,
      테스트용 커스텀 파일은 .env REPLAY_FILE 로만 지정한다."""
    type: str = ""         # "" / mock / replay
    symbol: str = ""       # replay: data/market/{SYMBOL}_{dataset}.csv (영문 대문자 1~5자)
    dataset: str = "daily"  # daily(상승장 일봉) / bear(2022 폭락+2023 회복, 추세추종 데모)
    start: str = ""        # 재생 시작일 YYYY-MM-DD
    end: str = ""          # 재생 종료일 YYYY-MM-DD


class StartBody(StrictBody):
    mode: str = "dry"      # dry / live
    strategy: StrategyBody = StrategyBody()
    feed: FeedBody = FeedBody()


class ActorBody(StrictBody):
    actor: str = "human"   # A2 정지 주체 기록 (P3 리스크가드가 "risk-guard" 로 재사용)


@app.post("/api/engine/start", dependencies=[Depends(require_control)])
async def engine_start(body: StartBody):
    try:
        return await engine.start(body.mode, body.strategy.model_dump(),
                                  body.feed.model_dump())
    except EngineError as e:
        code = 409 if "이미 실행" in str(e) else 400
        raise HTTPException(status_code=code, detail=str(e))


@app.post("/api/engine/stop", dependencies=[Depends(require_control)])
async def engine_stop():
    try:
        return await engine.stop()
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/trading/pause", dependencies=[Depends(require_control)])
async def trading_pause(body: ActorBody):
    try:
        return engine.pause(body.actor)     # 세션 실행 중에만 허용 (정지 상태는 세션 단위)
    except EngineError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/trading/resume", dependencies=[Depends(require_control)])
async def trading_resume(body: ActorBody):
    try:
        return engine.resume(body.actor)
    except EngineError as e:
        raise HTTPException(status_code=409, detail=str(e))


class MandateBody(StrictBody):
    """A3 한도 변경 — 금액은 문자열로 받아 Decimal 정밀 변환 (float 오차 방지)."""
    budget_total_usdc: str
    per_trade_max_usdc: str
    actor: str = "human"


@app.post("/api/mandate", dependencies=[Depends(require_control)])
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


@app.post("/api/briefing", dependencies=[Depends(require_control)])
async def create_briefing():
    """B2 수동 '오늘 요약' — 현재(또는 직전) 세션 데이터로 브리핑 생성."""
    try:
        return await engine.generate_briefing(trigger="manual")
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
    import argparse
    import uvicorn
    # 포트 우선순위: --port 인자 > PORT 환경변수(Cloud Run 관례) > .env WEB_PORT
    default_port = int(os.environ.get("PORT", CFG.web_port))
    ap = argparse.ArgumentParser(description="AutoTrader 대시보드 서버")
    ap.add_argument("--port", type=int, default=default_port,
                    help=f"포트 (기본 {default_port}) — 8000 점유 시 --port 8010 등")
    args = ap.parse_args()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
