"""FastAPI 서버 — 대시보드 정적 서빙 + 상태 API + SSE 실시간 이벤트 (P1 A1).

실행: python -m web.server   (기본 http://127.0.0.1:8000, .env 의 WEB_PORT)
Cloud Run 배포(P2)에서는 WEB_HOST=0.0.0.0, PORT 환경변수를 쓰게 된다.
"""
from __future__ import annotations
import asyncio
import html
import mimetypes
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import secrets as _secrets
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import CFG, env_num
from web.auth import SESSION_COOKIE, AuthError, WalletAuth
from web.broker_service import router as broker_router
from web.engine import TradingEngine, EngineError
from web.events import EventBus
from web.store import build_store

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# `.webp` 를 파이썬 표준 mimetypes 가 아는 것은 3.11 부터다(윈도우에서는 레지스트리를 읽어
# 3.11 이어도 모를 수 있다). 등록하지 않으면 StaticFiles 가 `application/octet-stream` 으로
# 내보내는데(로컬에서 실측했다), 개발자 소개 페이지의 그림 8개가 전부 webp 다.
# 브라우저는 대개 내용을 보고 알아서 그리지만 인터프리터·OS 에 따라 달라지는 것을 남길
# 이유가 없어 명시한다.
mimetypes.add_type("image/webp", ".webp")

bus = EventBus()
store = build_store()          # Firestore(FIRESTORE_ENABLED=1) 또는 no-op
engine = TradingEngine(bus, store)
# 지갑 연결 = 로그인. 체인 표기는 세션 네트워크를 따른다(Phantom 팝업에 그대로 보인다).
wallet_auth = WalletAuth(chain_id=CFG.network.replace("solana-", "") or "devnet")


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


class StrictBody(BaseModel):
    """요청 본문 공통 규칙 — 선언되지 않은 필드는 422 로 거부한다.

    기본값(무시)이면 오타난 필드가 조용히 먹혀 "설정했는데 반영이 안 되는" 버그가 되고,
    제거된 필드(예: feed.file)를 그대로 보내도 성공처럼 보인다. 둘 다 겪은 문제라 막는다."""
    model_config = {"extra": "forbid"}


app = FastAPI(title="SynapStock — 402 Guard(에이전트 지출 승인 게이트)", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# 브로커 x402 자원 서버(G5) — POST /broker/orders · GET /.well-known/x402.
# 로컬 시연은 별도 프로세스(python -m web.broker_service --port 8402)로 띄우지만,
# Cloud Run 은 컨테이너당 포트를 하나만 노출하므로 배포 URL 에서도 같은 402 를 확인할 수
# 있도록 여기에 함께 마운트한다. 조작 API 가 아니라 판매자 측 자원이라 토큰 게이트는 없다
# (결제 없이는 402 로 막히고, 정산은 서명 검증을 통과해야 한다).
app.include_router(broker_router)


@app.middleware("http")
async def _revalidate_ui(request: Request, call_next):
    """UI 자산은 브라우저가 쓰기 전에 항상 서버에 확인하게 한다.

    Cache-Control 이 없으면 브라우저가 **휴리스틱 캐시**를 적용한다(Last-Modified 기준으로
    임의 기간 보관). 2026-07-27 에 실제로 터졌다 — `/` 를 대시보드에서 랜딩으로 바꿔
    재배포했는데, 이전에 열어 본 브라우저가 옛 대시보드 HTML 을 계속 보여줬다.

    심사에서 이건 두 가지로 위험하다: ①심사위원이 URL 을 한 번 열어 둔 뒤 우리가
    재배포하면 옛 화면을 계속 본다 ②HTML 만 새것이고 `/static/js/app.js` 가 옛것이면
    상태 스키마가 어긋나 화면이 깨진다.

    `no-cache` 는 '저장하지 마라'가 아니라 '쓰기 전에 재검증하라'다 — ETag 가 그대로라면
    304 로 응답하므로 대역폭 이점은 유지된다. API·SSE 는 건드리지 않는다."""
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/app", "/login", "/connect", "/developers") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(404)
async def _not_found(request: Request, exc):
    """브라우저에는 사람이 읽는 404 를, API 클라이언트에는 기존 JSON 을 준다.

    심사위원이 주소를 잘못 치면 마지막으로 보는 화면이 `{"detail":"Not Found"}` 였다.
    돌아갈 링크도 없어서 막다른 길이다. API 경로(`/api/*`)와 JSON 을 원한다고 밝힌
    요청은 그대로 두어 클라이언트 파싱을 깨지 않는다."""
    path = request.url.path
    wants_json = (path.startswith("/api/") or path.startswith("/broker/")
                  or "application/json" in request.headers.get("accept", ""))
    if wants_json:
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    body = (
        "<!doctype html><meta charset='utf-8'><title>404 — SynapStock</title>"
        "<style>body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#0f1115;"
        "color:#e6e8ee;display:grid;place-items:center;min-height:100vh;margin:0;text-align:center}"
        "a{color:#7ee2b8}code{color:#9aa4b2}</style>"
        "<div><h1 style='margin:0 0 .4em'>404</h1>"
        "<p>이 주소에는 페이지가 없습니다.</p>"
        f"<p><code>{html.escape(path)}</code></p>"
        "<p style='margin-top:1.6em'><a href='/'>소개 페이지</a> · "
        "<a href='/app'>대시보드</a> · <a href='/developers'>만든 사람들</a> · "
        "<a href='/connect'>지갑 연결</a></p></div>"
    )
    return HTMLResponse(body, status_code=404)


@app.get("/")
async def landing() -> FileResponse:
    """첫 화면 = 소개(랜딩) 페이지.

    예전에는 여기서 곧바로 대시보드를 서빙했다. 처음 들어온 사람(심사위원 포함)이
    제품 설명 없이 조작 화면부터 마주치면 "무엇을 하는 서비스인지"를 스스로 추론해야 한다.
    랜딩을 앞에 두고 대시보드는 /app 으로 옮긴다(docs/frontend_backend_split.md §2.1 표).
    """
    return FileResponse(os.path.join(STATIC_DIR, "landing.html"))


@app.get("/app")
async def dashboard() -> FileResponse:
    """대시보드(운영 화면). 예전 경로 `/static/index.html` 도 StaticFiles 로 계속 열린다 —
    랜딩의 '데모 대시보드 보기' 링크가 그 경로를 쓰고 있어 깨지지 않는다."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/developers")
async def developers() -> FileResponse:
    """만든 사람들 — 상단 바 '개발자' 탭이 가리키는 화면.

    ⚠ 이 경로를 위 `_revalidate_ui` 의 목록에도 넣어야 한다. 빼면 재배포 뒤에도
    브라우저가 옛 화면을 계속 보여준다(2026-07-27 에 `/` 에서 실제로 겪은 결함이다).
    ⚠ 오른쪽 개발자 서랍(`.dev-dock`, `?lab=1`)과는 무관한 화면이다 — 이름만 겹친다."""
    return FileResponse(os.path.join(STATIC_DIR, "developers.html"))


@app.get("/connect")
async def connect_page() -> FileResponse:
    """지갑 연결 페이지 = 이 제품의 로그인."""
    return FileResponse(os.path.join(STATIC_DIR, "connect.html"))


@app.get("/login")
async def login() -> FileResponse:
    """`/login` 도 지갑 연결로 보낸다.

    이메일·비밀번호 자리표시 페이지(`static/login.html`)를 서빙하던 자리다. self-custody 를
    주장하는 제품이 비밀번호를 보관하는 계정 체계를 함께 파는 것은 앞뒤가 안 맞고, 로그인
    한 번 + 지갑 연결 한 번은 사용자가 같은 일을 두 번 하는 것이다. 지갑 서명이 곧 소유
    증명이므로 그 한 단계로 합친다. 옛 페이지 파일은 지우지 않았고 `/static/login.html`
    로 계속 열린다(프론트 작업자의 Phase 5 대상 파일이라 건드리지 않는다)."""
    return FileResponse(os.path.join(STATIC_DIR, "connect.html"))


# ---------- 지갑 로그인 (Phantom 등 Solana 지갑) ----------

class ChallengeBody(StrictBody):
    pubkey: str


class VerifyBody(StrictBody):
    pubkey: str
    message: str
    signature: str          # base64(64바이트 ed25519 서명)


def _session_of(request: Request) -> Optional[dict]:
    return wallet_auth.session(request.cookies.get(SESSION_COOKIE))


@app.post("/api/auth/challenge")
async def auth_challenge(body: ChallengeBody, request: Request):
    """서명할 로그인 메시지를 발급한다. 클라이언트는 이 문자열을 그대로 지갑에 넘긴다."""
    origin = str(request.base_url).rstrip("/")
    try:
        return wallet_auth.challenge(body.pubkey, domain=request.url.hostname or "402guard",
                                     uri=origin)
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/verify")
async def auth_verify(body: VerifyBody, request: Request, response: Response):
    """지갑 서명을 검증하고 세션 쿠키를 심는다."""
    try:
        token = wallet_auth.verify(body.pubkey, body.message, body.signature)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,                              # JS 가 못 읽는다(XSS 로 탈취 불가)
        samesite="lax",
        secure=(request.url.scheme == "https"),     # 로컬 http 개발에서도 동작하게
        max_age=12 * 3600, path="/",
    )
    return {"ok": True, "pubkey": body.pubkey}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """현재 연결된 지갑. 미연결이면 connected=false (401 이 아니다 — 데모는 열려 있다)."""
    s = _session_of(request)
    return {"connected": bool(s), "pubkey": (s or {}).get("pubkey", "")}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    wallet_auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# ---------- 연결된 지갑의 온체인 잔액 ----------
#
# 세션 쿠키의 지갑만 조회한다 — 임의의 주소를 받지 않는다. 공개 정보(체인에 이미 있다)라
# 보안상 민감하진 않지만, 주소를 파라미터로 받으면 이 서버가 남의 지갑을 훑는 무료
# RPC 프록시가 된다(공용 devnet RPC 의 429 를 우리 요금으로 사는 셈).
_BAL_TTL_SEC = 20.0                       # 헤더가 주기적으로 물어보므로 짧게 캐시한다
_bal_cache: dict = {}                     # pubkey -> (만료시각, 결과 dict)


async def _read_wallet_balance(pubkey: str) -> dict:
    """SOL + 결제 자산(USDC) 잔액을 읽는다. 실패는 예외가 아니라 error 필드로 돌려준다.

    잔액 표시는 부가 정보다 — RPC 가 흔들린다고 헤더가 깨지거나 대시보드가 멈추면 안 된다.
    드라이런(샌드박스) 세션에서도 값이 나온다: 조회 대상은 엔진이 아니라 체인이고,
    연결된 지갑은 세션 모드와 무관하게 실재하는 주소다.
    """
    from solders.pubkey import Pubkey
    from payments import x402_solana as x

    owner = Pubkey.from_string(pubkey)
    mint = Pubkey.from_string(CFG.usdc_mint)   # CFG 는 문자열로 들고 있다
    client = await x.get_client(CFG.rpc_url)
    try:
        sol = await x.get_sol_balance(client, owner)
        usdc = await x.get_token_balance_ui(client, owner, mint)
    finally:
        await client.close()
    return {"sol": f"{sol:.4f}", "usdc": usdc,
            "usdc_mint": CFG.usdc_mint, "network": CFG.network}


@app.get("/api/wallet/balance")
async def wallet_balance(request: Request):
    """연결된 지갑의 온체인 잔액. 미연결이면 connected=false (401 이 아니다)."""
    s = _session_of(request)
    if not s:
        return {"connected": False}
    pubkey = s.get("pubkey", "")

    now = asyncio.get_running_loop().time()
    hit = _bal_cache.get(pubkey)
    if hit and hit[0] > now:
        return {"connected": True, "pubkey": pubkey, "cached": True, **hit[1]}

    try:
        data = await _read_wallet_balance(pubkey)
    except Exception as e:
        # 조회 실패도 캐시한다 — RPC 가 죽어 있을 때 폴링이 그대로 재시도 폭풍이 된다.
        data = {"error": f"{type(e).__name__}: {e}"[:120]}
    if len(_bal_cache) > 200:
        # 지갑 하나당 한 칸이라 정상 사용에서는 안 찬다. 세션을 대량 생성하면 자라므로
        # 만료된 것부터 비운다(그래도 넘치면 통째로) — 캐시일 뿐이라 잃어도 안전하다.
        for k in [k for k, v in _bal_cache.items() if v[0] <= now]:
            _bal_cache.pop(k, None)
        if len(_bal_cache) > 200:
            _bal_cache.clear()
    _bal_cache[pubkey] = (now + _BAL_TTL_SEC, data)
    return {"connected": True, "pubkey": pubkey, "cached": False, **data}


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

class StrategyBody(StrictBody):
    """B7 전략 선택 — condition(조건형) / dca(적립형: 주기마다 정액 매수)
    / trend(추세추종: 상승세 전량 보유·하락세 전량 매도).

    적립 주기 기준(dca_unit): ticks(N틱마다) / minutes(N분마다) / daily(매일 HH:MM).
    decision_mode: 조건형의 Gemini 재량 — strict(규칙 그대로) / trend(보류 재량).
    trend_signal: 추세추종 판단 방식 — pxma20(가격≥MA20) / cross_5_20(골든크로스5/20).
    brain: 판단 두뇌 — auto(키 있으면 Gemini) / rule(규칙만) / gemini(강제, 키 없으면 400).
           조건형에서만 의미가 있고, 같은 데이터로 규칙 vs AI 를 비교 실행하기 위한 스위치다."""
    type: str = "condition"
    brain: str = "auto"
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
      테스트용 커스텀 파일은 .env REPLAY_FILE 로만 지정한다.

    멀티 종목(동시 매수, 드라이 전용): symbols 에 티커 목록(예: ["AAPL","TSLA"])을 주면
    한 예산·한 가드 아래 각 종목을 독립 포지션으로 굴린다. 비우면 단일(symbol/기본).
    경로 주입·존재 여부·개수(최대 5) 검증은 엔진(_resolve_symbols/_build_symbol_feed)이 한다."""
    type: str = ""         # "" / mock / replay
    symbol: str = ""       # replay: data/market/{SYMBOL}_{dataset}.csv (영문 대문자 1~5자)
    symbols: list[str] = []  # 멀티 종목 티커 목록 (비우면 단일). 각 티커는 대문자 1~5자.
    dataset: str = "daily"  # daily(상승장 일봉) / bear(2022 폭락+2023 회복, 추세추종 데모)
    sub_bars: int = 1       # 1=일봉, >1=하루당 N개 합성 인트라바(더 짧은 간격 재현). 엔진이 1~12 클램프
    start: str = ""        # 재생 시작일 YYYY-MM-DD
    end: str = ""          # 재생 종료일 YYYY-MM-DD


class StartBody(StrictBody):
    mode: str = "dry"      # dry / live
    strategy: StrategyBody = StrategyBody()
    feed: FeedBody = FeedBody()
    tick_interval_sec: float | None = None  # 재생 속도(틱 간격 초). 미지정=.env 기본. 엔진이 안전범위로 클램프


class ActorBody(StrictBody):
    actor: str = "human"   # A2 정지 주체 기록 (P3 리스크가드가 "risk-guard" 로 재사용)


@app.post("/api/engine/start", dependencies=[Depends(require_control)])
async def engine_start(body: StartBody):
    try:
        return await engine.start(body.mode, body.strategy.model_dump(),
                                  body.feed.model_dump(),
                                  tick_interval_sec=body.tick_interval_sec)
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
            # 첫 바이트를 즉시 흘린다. 이게 없으면 엔진이 대기 중(=이벤트 히스토리가 비어
            # 있는 첫 방문)일 때 아래 wait_for(timeout=15) 가 끝날 때까지 본문이 한 바이트도
            # 안 나간다 — 배포본 실측 ttfb 15.06초. 그동안 대시보드는 '서버 연결 대기…'로
            # 멈춰 있어서, 심사위원이 URL 을 열고 처음 15초를 죽은 화면으로 본다.
            # SSE 주석줄(': ')은 규격상 무시되므로 클라이언트 코드 변경이 필요 없고,
            # 중간 프록시의 응답 버퍼도 함께 밀어낸다.
            yield ": connected\n\n"
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
    # env_num 을 쓰는 이유는 config 의 숫자 필드와 같다 — PORT= 처럼 빈 값이 들어오면
    # int("") 가 터져 서버가 뜨지 않는데, 그 트레이스백에는 어느 변수가 문제인지 안 나온다.
    default_port = env_num("PORT", str(CFG.web_port), int)
    ap = argparse.ArgumentParser(description="SynapStock 대시보드 서버")
    ap.add_argument("--port", type=int, default=default_port,
                    help=f"포트 (기본 {default_port}) — 8000 점유 시 --port 8010 등")
    args = ap.parse_args()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    # forwarded_allow_ips="*" — Cloud Run 은 TLS 를 앞단에서 끊고 컨테이너로는 평문 http 로
    # 넘긴다. uvicorn 기본값은 127.0.0.1 에서 온 요청의 X-Forwarded-* 만 신뢰하므로,
    # 프록시를 거친 요청은 헤더가 무시되고 request.url.scheme 이 "http" 로 남는다.
    # 실측 피해 2건: ①지갑 서명 팝업의 로그인 메시지에 "URI: http://synapstock-…" 이 찍힌다
    # (https 사이트에서 http 를 서명해 달라는 화면 — 사용자가 피싱으로 의심할 자리다)
    # ②세션 쿠키의 secure 플래그가 꺼진 채로 발급된다(server.py 의 set_cookie).
    # 컨테이너 포트는 Google 프론트엔드를 통해서만 도달 가능하고 그 프론트엔드가 해당
    # 헤더를 덮어쓰므로, 이 경로에서 "*" 는 스푸핑 위험을 새로 만들지 않는다.
    # 로컬 개발은 프록시 헤더 자체가 없어 그대로 http 로 동작한다.
    uvicorn.run(app, host=host, port=args.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
