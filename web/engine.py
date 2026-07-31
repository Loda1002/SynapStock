"""TradingEngine — run_demo.py 매매 사이클의 웹 서비스화 (P1: A1·A2·A5·A6·A7).

run_demo 의 print 흐름을 EventBus 발행으로 바꾼 백그라운드 asyncio 태스크.
기존 검증 코드(agents/·payments/·market/)는 수정 없이 그대로 재사용하고,
run_demo.py 는 데모데이 CLI 폴백으로 유지한다.

A2 긴급정지: trading_enabled 플래그 — 끄면 신규 판단·결제가 중단된다.
단일 태스크 루프라 진행 중인 정산 1건은 자연히 마무리된 뒤 멈춘다(스펙 그대로).
시세 틱은 계속 흘러 화면은 살아 있다.

포지션·예산은 세션(start~stop) 단위다. 라이브 모드의 온체인 잔액은
세션 시작/종료 스냅샷으로 교차 검증하고 artifacts/tx/ 에 아카이브한다.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Dict, List, Optional

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import CFG, to_base_units
from market.price_feed import Bar, IntradayReplayFeed, MockPriceFeed, PriceFeed, ReplayPriceFeed
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from payments.guard import Guard, GuardError
from payments.invoice_semantics import GUARD_LLM_UNVERIFIED, InvoiceSemanticChecker
from payments.x402_http import optional_client
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy, Decision
from run_demo import (_load_or_new, _load_or_create_user_key, explorer_tx_url,
                      snapshot_balances, usdc_net_out, USDC_OUT_WALLETS)
from web import events as ev
from web.briefing import generate_briefing_text
from web.events import EventBus
from web.store import BaseStore, jsonable

# 데모 규칙 기본값 — 지표 기준 (매수: MA5 −%, 매도: 평단 +%).
# 2026-07-25 전략 고도화: 검증(scripts/explore_strategy.py) 최고 수익·최저 꼬리위험 조합으로
# dip3/profit5 채택(분산 포트폴리오 흑자율 85.8%·평균 2.11%). 시간청산은 CFG.max_hold_bars.
DEFAULT_RULES = {"buy_dip_pct": "3", "take_profit_pct": "5"}

# 1회 매수 금액은 절대값이 아니라 **예산 대비 비율**이다(2026-07-31 확정 —
# docs/reports/strategy_validation.md 부록의 백테스트 144회).
#   · 절대값(옛 30)이면 예산이 바뀌어도 노출도가 안 따라온다. devnet 처럼 예산이 20 USDC 면
#     30 은 예산의 150% 라 **한 건도 못 산다**(실제로 걸려서 run_demo 에 --spend 를 만들었다).
#   · 비율은 예산에 대해 불변임을 실측했다 — 같은 비율에서 예산 50/100/200 의 수익률 편차가
#     20% 이상 구간에서 0.01~0.20%p 다.
#   · 30% 는 시험한 6단계(5·10·20·30·40·50%) 중 평균 수익률(4.55%)·초과수익(8.87%p)·
#     위험조정(0.256) 모두 1위였고, **기본 예산 100 에서 옛 상수 30 과 같은 값**이라
#     지금까지의 동작·문서 수치가 하나도 바뀌지 않는다.
#   ⚠ 이 값은 '최적해'가 아니라 노출도 다이얼이다 — 상승장은 클수록, 하락장은 작을수록
#     유리해서 장세를 알아야 정해진다. 표본 6데이터셋이라 30% 가 최적이라고 주장하지 않는다.
SPEND_PCT = Decimal("30")

MAX_DECISIONS = 500   # A6 타임라인 메모리 상한
MAX_PRICE_POINTS = 120

# 캔들차트: 목 시세는 틱당 단일 가격이라 N틱을 묶어 하나의 캔들(OHLC)로 집계한다.
# 2틱(기본 8초 틱 → 캔들 16초)이면 데모 길이에서 이동평균선이 눈에 보인다.
TICKS_PER_CANDLE = 2
# 화면 표시 전용 버퍼다 — 매매 판단은 에이전트가 따로 들고 있는 봉 이력을 쓰므로 이 값은
# 자금 경로에 닿지 않는다. 90 이던 것을 늘린 이유: 대시보드가 차트를 과거로 스크롤하고
# MA100·MA200 을 그리려면 이만큼의 이력이 필요한데, 세션 도중 새로고침하면 클라이언트가
# 쌓아 둔 이력이 사라지고 여기서 받은 것만 남는다. (/api/state 응답 크기와 맞바꾼 값이다.)
MAX_CANDLES = 300

# 드라이런 전용 자리표시 스톡 민트 — STOCK_MINT 미설정(devnet 셋업 전 = 배포 직후 상태)에도
# 매도 레그가 동작해야 한다. 온체인 전송이 없는 드라이런에서만 쓰이고, 라이브는 세션 시작에서
# fail-fast 하므로 이 값이 체인에 닿는 경로는 없다.
DRY_STOCK_MINT = Pubkey.default()

# 긴급정지 주체의 화면 표시명 (CODE-06). 저장소에 실제로 존재하는 액터만 넣는다 —
# `pause()` 를 부르는 곳은 HTTP 핸들러(human, 기본값)와 엔진의 가드 경로 3곳(guard)뿐이다.
# 모르는 값이 오면 원문을 그대로 보여 준다(없는 액터를 지어내지 않는다).
PAUSE_ACTOR_LABEL = {"human": "사용자", "guard": "402 Guard"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EngineError(Exception):
    """엔진 조작 오류 — API 레이어에서 4xx 로 변환된다."""


class TradingEngine:
    def __init__(self, bus: EventBus, store: Optional[BaseStore] = None):
        self.bus = bus
        self.store = store or BaseStore()    # 영속화 (기본 no-op — 로컬 무변경)
        self.session_id: str = ""            # 영속 문서 키 (start 에서 발급)
        self._store_warned = False           # 저장 실패 경고는 1회만 (스팸 방지)
        self.status: str = "idle"            # idle / running / stopping
        self.mode: str = ""                  # dry / live
        self.trading_enabled: bool = True    # A2 긴급정지 플래그
        self.pause_info: Optional[Dict[str, str]] = None
        self.tick_interval: float = CFG.web_tick_interval_sec
        self.last_archive_path: str = ""

        # A3 유효 한도 — .env 기본값에서 시작, 한도 설정 화면으로 변경 가능
        self.budget_total: Decimal = CFG.budget_usdc
        self.per_trade_max: Decimal = CFG.per_trade_max_usdc
        # 세션에서 실제 적용되는 건별 한도 — 조건형/적립형은 per_trade_max 그대로,
        # 추세추종(올인/올아웃)은 '가진 현금 전량 매수'라 건별 한도가 총자산까지 열려야 해서
        # 세션 동안만 상한(max_budget)으로 확장한다. 표시·mandate·가드가 이 값을 쓴다.
        self._session_per_trade: Decimal = CFG.per_trade_max_usdc
        self.mandate_history: List[Dict[str, Any]] = []  # 세션 중 변경 이력 (아카이브 포함)

        # 세션 상태
        self.tick = 0
        self.symbols: List[str] = []                # 세션 종목 목록 (단일=길이 1, 멀티=N)
        self.decisions: List[Dict[str, Any]] = []   # A6 판단 타임라인 (전 종목, 행마다 symbol)
        self.trades: List[Dict[str, Any]] = []      # A5 거래 내역 (전 종목)
        # 종목별 시세 이력·캔들 (멀티 종목: dict[sym]). 집계·차트는 종목 단위로 분리한다.
        self._price_history: Dict[str, List[Dict[str, str]]] = {}
        self._candles: Dict[str, List[Dict[str, Any]]] = {}
        self.realized_pnl = Decimal(0)              # A7 실현손익 (전 종목 합산)
        self.cum_buy_usdc = Decimal(0)              # A7 수익률 분모(누적 매수금액, 합산)
        self.total_fees = Decimal(0)                # A8 누적 브로커 수수료 (합산, 수익모델 증명)
        self.started_at: str = ""
        self.brain_label: str = ""
        self.strategy_info: Dict[str, Any] = {"type": "condition"}  # B7 세션 전략
        self.feed_info: Dict[str, Any] = {"type": "", "label": ""}  # 시세 피드 (세션 공통 type/label)
        self.reject_count = 0                       # B2 브리핑용: AP2 거부 횟수
        self.pause_count = 0                        # B2 브리핑용: 긴급정지 횟수
        self.guard_block_count = 0                  # 402 Guard check_demand 차단 횟수 (첫 화면 KPI)
        self._guard_checked = 0                     # 가드를 태운 청구서 수 (KPI 분모 — 매수·매도 양 레그)
        self._guard_blocked_buy = 0                 # 그중 매수 레그 차단 (레그별 분해)
        self.guard_unverified_count = 0             # 그중 '검사 불가'(쿼터·응답 실패) 보류 — 판정 차단과 구별
        self.guard_leak_usdc = Decimal(0)           # 가드 통과 후 유출된 USDC (정상 0.00)
        # 판단 출처·행동 누적 카운터 (축② AI 활용 증빙). self.decisions 는 메모리 상한(500)으로
        # 앞부분이 잘리므로, 세션 전체 집계는 반드시 이 카운터를 쓴다.
        self.source_counts: Dict[str, int] = {}
        self.action_counts: Dict[str, int] = {}
        self.last_briefing: Optional[Dict[str, Any]] = None  # B2 최근 브리핑
        self._last_daily_briefing_date = ""         # B2 장 마감 자동 생성 중복 방지

        # 세션 구성물 (start 에서 생성)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._client = None
        # 종목별(멀티): 각자 position·이력·시간청산 카운터를 가진 TradingAgent 와 피드.
        # 모두 같은 auth·guard·broker(공유) 를 쓴다 → 하나의 예산·하나의 가드로 N종목 통제.
        self.agents: Dict[str, TradingAgent] = {}
        self.feeds: Dict[str, PriceFeed] = {}
        self._feeds_info: Dict[str, Dict[str, Any]] = {}  # 종목별 피드 상세 (label·file·bars)
        self._broker: Optional[BrokerAgent] = None        # 공유 브로커 (드라이 1개로 충분)
        # G5 브로커 HTTP 402 클라이언트 — BROKER_HTTP_URL 설정 시에만 생성(기본 None=인프로세스)
        self._broker_http = None
        self._prev_close: Dict[str, Optional[Decimal]] = {}  # 종목별 직전 봉 종가 (등락 기준)
        self._change_ref: Dict[str, Optional[Decimal]] = {}  # 종목별 마지막 틱 등락 기준값
        self._auth: Optional[PaymentAuthorizer] = None
        self._guard: Optional[Guard] = None
        self._mandate: Optional[OpenPaymentMandate] = None
        self._user_kp: Optional[Keypair] = None
        self._trading_kp: Optional[Keypair] = None
        self._broker_kp: Optional[Keypair] = None
        self._usdc_mint: Optional[Pubkey] = None
        self._stock_mint: Optional[Pubkey] = None
        self._snap_before: Optional[dict] = None
        self._snap_last: Optional[dict] = None

    # ---------- 시세 피드 (mock / replay) ----------

    @staticmethod
    def default_replay_path() -> str:
        """재생 CSV 기본 경로 — REPLAY_FILE > REPLAY_SYMBOL > 종목명 유도(tAAPL→AAPL)."""
        if CFG.replay_file:
            return CFG.replay_file
        sym = CFG.replay_symbol.upper() if CFG.replay_symbol else ""
        if not sym:
            s = CFG.stock_symbol
            # 토큰 표기 관례: 소문자 t + 실제 티커 (tAAPL). 아니면 그대로 사용.
            sym = s[1:] if len(s) > 1 and s[0] == "t" and s[1:].isupper() else s.upper()
        return os.path.join("data", "market", f"{sym}_daily.csv")

    def _replay_feed(self, path: str, fcfg: Dict[str, Any], err_prefix: str = ""):
        """재생 피드 생성 — sub_bars>1 이면 하루당 sub 개의 합성 인트라바로 확장한다.
        일 단위 궤적(종가)은 실데이터 그대로고 하위 경로만 합성이다. 실패는 EngineError."""
        try:
            sub = int(fcfg.get("sub_bars") or 1)
        except (TypeError, ValueError):
            sub = 1
        sub = min(max(sub, 1), 12)          # 과도한 봉 수(세션 지연) 방지
        start = str(fcfg.get("start") or CFG.replay_start)
        end = str(fcfg.get("end") or CFG.replay_end)
        try:
            if sub > 1:
                return IntradayReplayFeed(path, start=start, end=end,
                                          warmup=CFG.replay_warmup, sub=sub)
            return ReplayPriceFeed(path, start=start, end=end, warmup=CFG.replay_warmup)
        except (FileNotFoundError, ValueError) as e:
            raise EngineError(f"{err_prefix}실데이터 재생 준비 실패 — {e}")

    def _build_feed(self, feed_cfg: Optional[Dict[str, Any]]):
        """세션 피드 구성 — (feed, feed_info). 실패는 EngineError 로 사용자에게 안내."""
        fcfg = feed_cfg or {}
        ftype = fcfg.get("type") or CFG.price_feed
        if ftype not in ("mock", "replay"):
            raise EngineError("feed.type 은 'mock' 또는 'replay' 여야 합니다.")
        if ftype == "mock":
            return MockPriceFeed(), {"type": "mock", "label": "목 시세 (10스텝 데모 패턴)"}
        # 데이터셋 — daily(상승장 최근 일봉, 기본) / bear(2022 폭락+2023 회복, 추세추종 데모).
        # 화이트리스트라 경로 주입이 불가능하다(임의 접미사 차단).
        dataset = str(fcfg.get("dataset") or "daily")
        if dataset not in ("daily", "bear"):
            raise EngineError("데이터셋은 'daily' 또는 'bear' 여야 합니다.")
        # 경로 주입 차단: API 로는 심볼만 받고(정규식 검증) 경로는 서버가 조립한다.
        # 임의 CSV 경로를 받으면 컨테이너의 아무 파일이나 열게 되고, 파싱 오류 메시지에
        # 파일 내용이 실려 400 응답으로 새어나간다. 테스트용 직접 지정은 .env REPLAY_FILE 만.
        if fcfg.get("symbol"):
            sym = str(fcfg["symbol"]).upper()
            if not re.fullmatch(r"[A-Z]{1,5}", sym):
                raise EngineError("종목 코드는 영문 대문자 1~5자여야 합니다.")
            path = os.path.join("data", "market", f"{sym}_{dataset}.csv")
            market_dir = os.path.realpath(os.path.join("data", "market"))
            if os.path.commonpath([os.path.realpath(path), market_dir]) != market_dir:
                raise EngineError("허용되지 않은 시세 파일 경로입니다.")
        elif dataset == "bear":
            # 심볼 미지정 + bear 데이터셋 — 기본 종목의 _bear.csv 로 유도
            base = os.path.basename(self.default_replay_path())
            sym = base.split("_")[0]
            path = os.path.join("data", "market", f"{sym}_bear.csv")
        else:
            path = self.default_replay_path()
        feed = self._replay_feed(path, fcfg)
        info = {
            "type": "replay",
            "dataset": dataset,      # daily / bear (추세추종 폭락회피 데모)
            "label": feed.source_label,
            "file": path,
            "source": ("yfinance 조정 일봉 (2022 폭락+2023 회복, fetch_bear_data.py)"
                       if dataset == "bear" else "Alpha Vantage 일봉 (fetch_market_data.py)"),
            "sub_bars": getattr(feed, "sub", 1),   # 1=일봉, >1=합성 인트라바(하루당 봉 수)
            "bars_total": feed.total_bars,
            "warmup_bars": len(feed.warmup_bars),
        }
        return feed, info

    # ---------- 멀티 종목 (동시 매수) ----------

    @property
    def _focus(self) -> str:
        """상태 스냅샷 top-level 이 가리키는 대표(포커스) 종목 — 첫 종목. N=1 이면 그 종목."""
        return self.symbols[0] if self.symbols else CFG.stock_symbol

    def _resolve_symbols(self, feed_cfg: Optional[Dict[str, Any]]) -> tuple[List[str], bool]:
        """세션 종목 목록과 '멀티 여부'를 정한다.

        - feed.symbols(리스트, 대문자 티커)가 있으면 멀티 모드 — 거래 심볼 = 티커,
          피드 CSV = data/market/{티커}_{dataset}.csv. (경로 주입 차단: 정규식 검증만 통과)
        - 없으면 레거시 단일 — 거래 심볼 = CFG.stock_symbol, 기존 _build_feed 경로(feed.symbol 은
          CSV 선택만, 거래 라벨은 STOCK_SYMBOL). 하위호환 위해 그대로 둔다.
        반환: (symbols, multi)."""
        fcfg = feed_cfg or {}
        raw = fcfg.get("symbols") or []
        if isinstance(raw, str):
            raw = [s for s in raw.split(",")]
        syms: List[str] = []
        for s in raw:
            s = str(s).strip().upper()
            if not s:
                continue
            if not re.fullmatch(r"[A-Z]{1,5}", s):
                raise EngineError(f"종목 코드는 영문 대문자 1~5자여야 합니다: {s!r}")
            if s not in syms:
                syms.append(s)          # 중복 제거(순서 유지)
        if not syms:
            return [CFG.stock_symbol], False
        if len(syms) > 5:
            raise EngineError("동시 매수 종목은 최대 5개까지 지원합니다.")
        return syms, True

    def _build_symbol_feed(self, ticker: str, feed_cfg: Optional[Dict[str, Any]]):
        """멀티 종목용 — 명시 티커의 피드를 만든다.

        replay: data/market/{ticker}_{dataset}.csv (없으면 EngineError). mock: 종목별 목 시세.
        _build_feed(레거시 단일)와 달리 티커를 그대로 거래 심볼로 쓴다(tAAPL 유도 없음)."""
        fcfg = feed_cfg or {}
        ftype = fcfg.get("type") or CFG.price_feed
        if ftype not in ("mock", "replay"):
            raise EngineError("feed.type 은 'mock' 또는 'replay' 여야 합니다.")
        if ftype == "mock":
            return MockPriceFeed(), {"type": "mock", "label": f"목 시세 ({ticker})"}
        dataset = str(fcfg.get("dataset") or "daily")
        if dataset not in ("daily", "bear"):
            raise EngineError("데이터셋은 'daily' 또는 'bear' 여야 합니다.")
        path = os.path.join("data", "market", f"{ticker}_{dataset}.csv")
        market_dir = os.path.realpath(os.path.join("data", "market"))
        if os.path.commonpath([os.path.realpath(path), market_dir]) != market_dir:
            raise EngineError("허용되지 않은 시세 파일 경로입니다.")
        feed = self._replay_feed(path, fcfg, f"{ticker} ")
        info = {
            "type": "replay", "dataset": dataset, "label": feed.source_label, "file": path,
            "source": ("yfinance 조정 일봉 (2022 폭락+2023 회복)"
                       if dataset == "bear" else "Alpha Vantage 일봉"),
            "sub_bars": getattr(feed, "sub", 1),
            "bars_total": feed.total_bars, "warmup_bars": len(feed.warmup_bars),
        }
        return feed, info

    # ---------- 영속화 (Firestore — 실패해도 매매 루프는 계속) ----------

    def _persist(self, coro) -> None:
        """스토어 쓰기 fire-and-forget. 저장 실패는 1회만 ERROR 이벤트로 알리고
        이후엔 store.last_error 에만 남긴다 — 영속화 장애가 매매를 멈추지 않는다."""
        if not self.store.enabled:
            coro.close()  # 미실행 코루틴 경고 방지
            return

        async def run():
            try:
                await coro
            except Exception as e:
                self.store.last_error = f"{type(e).__name__}: {e}"
                if not self._store_warned:
                    self._store_warned = True
                    self.bus.emit(ev.ERROR, {
                        "message": f"영속 저장 실패(매매는 계속): {self.store.last_error}"})

        asyncio.create_task(run())

    async def restore_from_store(self) -> None:
        """서버 부팅 시 1회 — 한도 기본값·최근 브리핑을 Firestore 에서 복원한다.
        (세션·거래 이력은 /api/history 로 조회 — 현재 세션 화면과 섞지 않는다)"""
        if not self.store.enabled:
            return
        try:
            await asyncio.wait_for(self.store.ping(), timeout=10)
            d = await self.store.load_defaults()
            if d:
                try:
                    self.budget_total = Decimal(str(d["budget_total_usdc"]))
                    self.per_trade_max = Decimal(str(d["per_trade_max_usdc"]))
                except (KeyError, InvalidOperation):
                    pass  # 필드 없거나 형식 이상 — .env 기본값 유지
            b = await self.store.load_last_briefing()
            if b:
                self.last_briefing = {
                    k: b[k] for k in ("ts", "trigger", "source", "text", "archive",
                                      "fallback_detail")
                    if k in b}
                self.last_briefing["restored"] = True  # 재시작 복원본 표시
            # 콘솔 print 는 cp949(한국어 Windows)에서도 안전하게 ASCII 구두점만 쓴다
            print(f"[store] 부팅 복원 완료: 한도 {self.budget_total}/{self.per_trade_max} USDC, "
                  f"브리핑 {'있음' if self.last_briefing else '없음'}")
        except Exception as e:
            self.store.last_error = f"{type(e).__name__}: {e}"
            print(f"[store] 부팅 복원 실패(기본값으로 계속): {self.store.last_error}")

    # ---------- 라이프사이클 ----------

    async def start(self, mode: str,
                    strategy_cfg: Optional[Dict[str, Any]] = None,
                    feed_cfg: Optional[Dict[str, Any]] = None,
                    tick_interval_sec: Optional[float] = None,
                    autostart: bool = True) -> Dict[str, Any]:
        # autostart=False 는 테스트 시드 — 세션을 구성하되 백그라운드 루프를 띄우지 않는다.
        # (테스트가 _tick_once/_finalize 를 결정론적으로 직접 스텝한다. 운영은 항상 True.)
        if self.status != "idle":
            raise EngineError("엔진이 이미 실행 중입니다 — 먼저 세션을 종료하세요.")
        if mode not in ("dry", "live"):
            raise EngineError("mode 는 'dry' 또는 'live' 여야 합니다.")
        live = mode == "live"
        # 배포 환경 이중 안전장치 — 라이브(실제 온체인 전송)는 명시적으로 켠 경우에만.
        # 시연 직전 `gcloud run services update --update-env-vars ALLOW_LIVE_FROM_WEB=1` 로 연다.
        if live and not CFG.allow_live_from_web:
            raise EngineError("이 서버는 웹에서 라이브 세션 시작이 차단돼 있습니다 "
                              "(ALLOW_LIVE_FROM_WEB=1 필요).")

        # B7 전략 선택 — condition(조건형) / dca(적립형, 주기 정액) / trend(추세추종, 올인·올아웃)
        scfg = strategy_cfg or {}
        strat_type = scfg.get("type", "condition")
        if strat_type not in ("condition", "dca", "trend"):
            raise EngineError("strategy.type 은 'condition' / 'dca' / 'trend' 중 하나여야 합니다.")
        # 추세추종 판단 방식 — pxma20(가격≥MA20) / cross_5_20(골든크로스5/20). trend 에서만 의미.
        trend_signal = scfg.get("trend_signal") or "pxma20"
        if trend_signal not in ("pxma20", "cross_5_20", "cross_1_5", "cross_5_20_1_5"):
            raise EngineError("추세 신호는 'pxma20' / 'cross_5_20' / 'cross_1_5' / "
                              "'cross_5_20_1_5' 중 하나여야 합니다.")
        # Gemini 재량 모드 — strict(규칙 그대로) / trend(보류 재량). 조건형에서만 의미 있음.
        decision_mode = scfg.get("decision_mode") or "strict"
        if decision_mode not in ("strict", "trend"):
            raise EngineError("판단 모드는 'strict' 또는 'trend' 여야 합니다.")
        # 판단 두뇌 선택 — auto(키 있으면 Gemini) / rule(규칙만) / gemini(강제).
        # 조건형에서만 의미가 있다(적립형·추세추종은 결정론적 규칙이라 두뇌를 안 쓴다).
        # rule 을 명시하면 키가 있어도 Gemini 를 호출하지 않는다 — 같은 데이터에서 규칙 vs AI 를
        # 나란히 돌려 비교하기 위한 스위치다(심사 축② 판단 출처 대조).
        brain_pref = scfg.get("brain") or "auto"
        if brain_pref not in ("auto", "rule", "gemini"):
            raise EngineError("판단 두뇌는 'auto' / 'rule' / 'gemini' 중 하나여야 합니다.")
        # TA 보강(매매 기준 개선) — MA 배열·크로스·지지/저항·패턴을 판단 근거로 주입.
        # 백테스트 검증 전 기본 OFF, 실데이터 재생에서 의미 있음(목 시세는 퇴화 봉).
        ta_mode = bool(scfg.get("ta_mode", False))
        # 적립 주기 기준 — ticks(N틱마다) / minutes(N분마다) / daily(매일 HH:MM)
        dca_unit = scfg.get("dca_unit", "ticks")
        if dca_unit not in ("ticks", "minutes", "daily"):
            raise EngineError("적립 주기는 'ticks' / 'minutes' / 'daily' 중 하나여야 합니다.")
        dca_at_time = str(scfg.get("dca_at_time", "09:00"))
        try:
            dca_every = int(scfg.get("dca_every_ticks", 5))
            dca_minutes = int(scfg.get("dca_every_minutes", 60))
            dca_amount = Decimal(str(scfg.get("dca_amount_usdc", "10")))
        except (ValueError, InvalidOperation):
            raise EngineError("적립식 파라미터가 숫자 형식이 아닙니다.")
        if strat_type == "dca":
            if dca_amount <= 0:
                raise EngineError("적립식 회당 금액은 0보다 커야 합니다.")
            if dca_unit == "ticks" and dca_every < 1:
                raise EngineError("적립 주기는 1틱 이상이어야 합니다.")
            if dca_unit == "minutes" and dca_minutes < 1:
                raise EngineError("적립 주기는 1분 이상이어야 합니다.")
            if dca_unit == "daily":
                try:
                    hh, mm = (int(v) for v in dca_at_time.split(":"))
                    if not (0 <= hh < 24 and 0 <= mm < 60):
                        raise ValueError
                except ValueError:
                    raise EngineError("적립 시각은 HH:MM 형식(00:00~23:59)이어야 합니다.")

        # 종목 목록 — feed.symbols(멀티) 또는 레거시 단일(STOCK_SYMBOL).
        symbols, multi = self._resolve_symbols(feed_cfg)
        if live and len(symbols) > 1:
            raise EngineError("라이브(온체인) 세션은 여러 종목을 동시에 지원하지 않습니다 — "
                              "멀티 종목은 드라이 전용입니다. 종목을 하나만 선택하세요.")
        if live:
            symbols, multi = [CFG.stock_symbol], False   # 라이브는 항상 레거시 단일(민트=STOCK_MINT)
        # 추세추종 멀티는 종목별 예산 슬라이스(예산/N)로 각자 올인·복리한다(아래 per-symbol auth)
        # — 공유 예산이 아니라 독립 예산이라 한 종목이 전액을 독식하거나 다른 종목 예산을
        # 잠식하지 못한다. 라이브(온체인)는 위에서 단일로 강제되므로 멀티 추세는 드라이 전용이다.
        # 목 시세는 종목별 기준가가 없어 전 종목이 동일 가격 경로가 된다(완전 상관 → 분산 무의미).
        # 멀티는 실데이터 재생만 지원한다(live/trend 멀티 거부와 같은 관례).
        if len(symbols) > 1 and ((feed_cfg or {}).get("type") or CFG.price_feed) == "mock":
            raise EngineError("목 시세는 멀티 종목을 지원하지 않습니다 — 멀티 종목은 실데이터 재생(replay)만 "
                              "지원합니다. 종목을 하나만 고르거나 시세를 실데이터 재생으로 바꾸세요.")

        # 시세 피드 — 레거시 단일은 기존 _build_feed(tAAPL 유도·bear 기본·feed.symbol 처리 포함),
        # 멀티는 종목별로 _build_symbol_feed 로 조립한다(아래 종목 루프에서).
        feed0 = feed0_info = None
        if not multi:
            feed0, feed0_info = self._build_feed(feed_cfg)

        usdc_mint = Pubkey.from_string(CFG.usdc_mint)
        if live and not CFG.stock_mint:
            raise EngineError("STOCK_MINT 미설정 — 먼저 scripts/setup_devnet.py 를 실행하세요.")
        # 드라이런은 실제 민트가 없어도 매도 견적이 만들어져야 한다. 예전에는 None 이 그대로
        # 브로커로 넘어가 매 틱 "stock_mint 미설정 — 매도 견적 불가"(make_stock_required)로 터졌고,
        # 매수만 되고 익절이 전혀 안 됐다(Cloud Run 첫 배포에서 발견 — 로컬은 .env 에 값이 있어
        # 드러나지 않았다). 자리표시를 써도 가드의 자산 대조(check_stock_transfer)는 그대로 작동한다.
        stock_mint = (Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint
                      else DRY_STOCK_MINT)

        # 라이브 세션은 키가 없으면 즉시 실패한다(무증상 랜덤 지갑 방지 — run_demo._load_or_new 주석)
        wd = CFG.wallet_dir
        # 사용자(위임자) 키 — open mandate 서명자. 에이전트 키와 분리(결함 G 제거).
        user_kp = _load_or_create_user_key(os.path.join(wd, "user.json"),
                                           env_json=CFG.user_keypair_json)
        try:
            trading_kp = _load_or_new(os.path.join(wd, "trading.json"), required=live,
                                      env_json=CFG.trading_keypair_json)
            broker_kp = _load_or_new(os.path.join(wd, "broker.json"), required=live,
                                     env_json=CFG.broker_keypair_json)
        except (FileNotFoundError, ValueError) as e:
            raise EngineError(str(e))

        # 판단 두뇌 — run_demo 와 동일한 선택 로직 (Gemini, 실패 시 규칙 폴백)
        # 적립형(dca)은 판단 없이 스케줄 매수라 Gemini 를 쓰지 않는다
        schedule_label = ({"minutes": f"{dca_minutes}분마다",
                           "daily": f"매일 {dca_at_time}"}
                          .get(dca_unit, f"{dca_every}틱마다"))
        signal_label = {"pxma20": "가격>MA20", "cross_5_20": "골든크로스5/20",
                        "cross_1_5": "1/5크로스(가격>MA5)",
                        "cross_5_20_1_5": "5/20+1/5 결합"}[trend_signal]
        brain = None
        if strat_type == "dca":
            brain_label = f"적립식 스케줄 ({schedule_label} {dca_amount} USDC, Gemini 미사용)"
        elif strat_type == "trend":
            # 추세추종은 결정론적 규칙 신호(검증이 그대로 재현되도록) — Gemini 를 쓰지 않는다
            brain_label = f"추세추종 규칙 ({signal_label} 신호 · 올인/올아웃 · Gemini 미사용)"
        elif brain_pref == "rule":
            # 사용자가 규칙 모드를 명시 — 키가 있어도 Gemini 를 호출하지 않는다.
            brain_label = "규칙 기반 (사용자 지정 — Gemini 미사용)"
        elif not CFG.gemini_api_key:
            # 명시적으로 gemini 를 골랐는데 키가 없으면 조용히 규칙으로 떨어뜨리지 않는다
            # (설정했는데 반영이 안 되는 부류의 버그를 만들지 않기 위해).
            if brain_pref == "gemini":
                raise EngineError("Gemini 두뇌를 선택했지만 GEMINI_API_KEY 가 설정돼 있지 않습니다.")
            brain_label = "규칙 기반 (GEMINI_API_KEY 미설정)"
        else:
            try:
                from agents.gemini_decider import GeminiDecider
                brain = GeminiDecider(CFG.gemini_api_key, CFG.gemini_model, CFG.gemini_mode)
                brain_label = f"Gemini ({CFG.gemini_model}, {brain.mode} 모드, 실패 시 규칙 폴백)"
            except Exception as e:
                if brain_pref == "gemini":
                    raise EngineError(f"Gemini 두뇌 초기화 실패: {type(e).__name__}: {e}")
                brain_label = f"규칙 기반 (Gemini 초기화 실패: {type(e).__name__})"

        # 세션 건별 한도 — 추세추종은 '가진 현금 전량 매수'(올인)라 건별 한도가 총자산까지
        # 열려야 한다(복리로 예산 초과 매수 가능). 이때 실질 방어선은 '수취인 allowlist +
        # 청구=합의견적 + 의도지출(올인 전액) 상한 + 자산/종목'이고(가드 그대로 작동),
        # 총 사용자 자금 노출은 여전히 예산(첫 진입 상한)까지다. 조건형/적립형은 그대로.
        is_trend = strat_type == "trend"
        self._session_per_trade = CFG.max_budget_usdc if is_trend else self.per_trade_max

        # ---- 공유 레이어 (세션 1개): mandate·auth·guard·broker ----
        # 하나의 예산 한도(mandate) 아래 N종목이 경쟁 소비하고, 하나의 가드가 모든 서명을 검문한다.
        # 이게 "다중 지출을 하나의 402 Guard 로 통제" 서사의 핵심(멀티=조건형/적립형만, 위에서 강제).
        n = len(symbols)
        # AP2 mandate — 허용 종목은 세션 전 종목. 사용자가 설정한 한도에 서명(예산=순투입 한도).
        mandate = OpenPaymentMandate(
            user_pubkey=str(user_kp.pubkey()),          # 위임자(사용자) 키 — 에이전트 키와 분리
            allowed_asset=str(usdc_mint),
            budget_total_usdc=self.budget_total,
            per_trade_max_usdc=self._session_per_trade,
            allowed_symbols=list(symbols),              # 전 종목 허용
        ).sign(user_kp)                                 # 사용자가 한도에 서명(위임 근거)
        auth = PaymentAuthorizer(mandate, agent_kp=trading_kp)  # 에이전트는 한도 내 결제만 서명
        broker = BrokerAgent(
            broker_kp, usdc_mint, CFG.usdc_decimals, stock_mint, CFG.stock_decimals, CFG.network,
            fee_bps=CFG.broker_fee_bps)
        # 402 Guard — 신뢰 수취인은 A2A 협의를 마친 브로커뿐. 전 종목 에이전트가 공유한다.
        # 두뇌가 있으면 의미 대조 계층(두 번째 AI 레이어)을 함께 붙인다. 두뇌가 없으면
        # (brain=rule·추세추종·적립식) 계층 자체가 없어 하드 검사만 돈다 — 그래야 AI 없는
        # 세션에서 '검사 불가 = 매수 차단' 정책이 제품을 멈추는 일이 생기지 않는다.
        semantic = InvoiceSemanticChecker(brain) if brain is not None else None
        guard = Guard(mandate, [str(broker_kp.pubkey())], CFG.usdc_decimals, semantic=semantic)

        # 1회 매수 금액은 종목 수로 나눈다 — 한 종목이 예산을 독식하지 않게(검증 포트폴리오와 동형).
        # 단일(N=1)이면 나눗셈이 항등이라 기존 동작 그대로. 적립형 회당 금액도 동일하게 분할한다.
        cent = Decimal("0.01")
        base_spend = self._spend_per_trade()
        # ⚠ 예산이 아주 작으면 비율 계산이 0 으로 내려앉는다. 0 이면 수량이 0.0000 으로
        # 내림돼 청구액도 0 이 되고, 그 상태로 모든 계층을 통과해 **가짜 체결**이 쌓인다
        # (BUG-12 와 같은 자리). 여기서 끊어야 가드·AP2 의 KPI 가 더러워지지 않는다.
        if base_spend <= 0:
            raise EngineError(
                f"예산 {self.budget_total} USDC 의 {SPEND_PCT}% 가 0 이 됩니다 — 예산을 올리세요.")
        spend_per_symbol = (base_spend / n).quantize(cent) if n > 1 else base_spend
        per_symbol_dca = (dca_amount / n).quantize(cent) if n > 1 else dca_amount
        if strat_type == "dca" and per_symbol_dca <= 0:
            raise EngineError("적립식 회당 금액이 종목 수로 나누면 0이 됩니다 — 금액을 올리거나 종목을 줄이세요.")

        # 종목별 authorizer 배정. 단일·조건형/적립형 멀티는 공유 예산(auth 1개)을 쓰고,
        # 추세추종 멀티(올인/올아웃)만 종목별 예산 슬라이스(예산/N)로 독립 auth 를 준다 —
        # 공유 예산이면 먼저 상승세로 돌아선 종목이 전액을 올인해 독식하기 때문. 각 슬라이스는
        # 자기 매도 대금으로 복리(allow_surplus)하고, 한 종목의 손실은 제 슬라이스에만 갇힌다.
        # 가드는 세션 mandate 하나로 전 종목의 서명을 검문(수취인·자산·종목·건별·의도 상한).
        multi_trend = is_trend and n > 1
        if multi_trend:
            slice_budget = (self.budget_total / n).quantize(cent)
            symbol_auths: Dict[str, PaymentAuthorizer] = {}
            for i, sym in enumerate(symbols):
                sb = self.budget_total - slice_budget * (n - 1) if i == n - 1 else slice_budget
                sm = OpenPaymentMandate(
                    user_pubkey=str(user_kp.pubkey()), allowed_asset=str(usdc_mint),
                    budget_total_usdc=sb, per_trade_max_usdc=self._session_per_trade,
                    allowed_symbols=[sym]).sign(user_kp)
                symbol_auths[sym] = PaymentAuthorizer(sm, agent_kp=trading_kp)
            auth = None   # 공유 auth 없음 — 종목별. 예산 보고는 _total_remaining/_total_spent 가 합산.
        else:
            symbol_auths = {sym: auth for sym in symbols}

        # ---- 종목별 레이어: TradingAgent·피드 (position·이력·시간청산 독립) ----
        agents: Dict[str, TradingAgent] = {}
        feeds: Dict[str, PriceFeed] = {}
        feeds_info: Dict[str, Dict[str, Any]] = {}
        for sym in symbols:
            f, info = (self._build_symbol_feed(sym, feed_cfg) if multi else (feed0, feed0_info))
            warm = list(getattr(f, "warmup_bars", []))
            strat = Strategy(
                buy_dip_pct=Decimal(DEFAULT_RULES["buy_dip_pct"]),
                take_profit_pct=Decimal(DEFAULT_RULES["take_profit_pct"]),
                spend_per_trade_usdc=spend_per_symbol,
                decision_mode=decision_mode, ta_mode=ta_mode, mode=strat_type,
                trend_signal=trend_signal, dca_unit=dca_unit, dca_every_ticks=dca_every,
                dca_every_minutes=dca_minutes, dca_at_time=dca_at_time,
                dca_amount_usdc=per_symbol_dca,
                # 시간청산(안전레일)은 조건형에만 — 추세추종은 추세가 살아 있는 한 태운다
                max_hold_bars=0 if is_trend else CFG.max_hold_bars,
            )
            ag = TradingAgent(trading_kp, symbol_auths[sym], strat, CFG.usdc_decimals, CFG.network,
                              brain=brain, fee_bps=CFG.broker_fee_bps)
            if warm:
                ag.preload_bars(warm)   # 첫 틱부터 MA/TA 성립하게 봉(OHLC)째 주입
            ag.guard = guard            # 전 종목이 같은 게이트를 통과
            agents[sym], feeds[sym], feeds_info[sym] = ag, f, info

        # 세션 공통 피드 정보 (top-level 표시·아카이브용) — 멀티는 종목 목록을 합쳐 라벨링
        if multi:
            ftype = feeds_info[symbols[0]]["type"]
            feed_info = {
                "type": ftype,
                "dataset": feeds_info[symbols[0]].get("dataset"),
                "label": (f"{n}종목 " + ("재생" if ftype == "replay" else "목 시세")
                          + ": " + "·".join(symbols)),
                "source": feeds_info[symbols[0]].get("source", ""),
                "symbols": list(symbols),
                "per_symbol": {s: {k: feeds_info[s].get(k)
                                   for k in ("label", "file", "bars_total")} for s in symbols},
            }
        else:
            feed_info = feed0_info

        client = None
        snap_before = None
        if live:
            try:
                client = await x.get_client(CFG.rpc_url)
                snap_before = await snapshot_balances(
                    client, trading_kp.pubkey(), broker_kp.pubkey(), usdc_mint, stock_mint,
                    user_pk=user_kp.pubkey())
            except Exception as e:
                if client is not None:
                    await client.close()
                raise EngineError(
                    f"RPC 연결/잔액 조회 실패({CFG.rpc_url}) — 검증기가 켜져 있나요? "
                    f"[{type(e).__name__}]")

        # 세션 상태 리셋 (긴급정지는 세션 단위 — 새 세션은 매매 활성으로 시작)
        self.mode = mode
        self.status = "running"
        self.trading_enabled = True
        self.pause_info = None
        self.tick = 0
        self.symbols = list(symbols)
        self.decisions = []
        self.trades = []
        # 종목별 시세 이력·캔들·등락기준 초기화. 워밍업 봉은 차트 사전 이력으로 미리 그린다(반투명).
        self._price_history = {s: [] for s in symbols}
        self._candles = {}
        self._prev_close = {}
        self._change_ref = {}
        for s in symbols:
            warm = list(getattr(feeds[s], "warmup_bars", []))
            self._candles[s] = [{
                "ts": b.date, "open": str(b.open), "high": str(b.high),
                "low": str(b.low), "close": str(b.close),
                "count": TICKS_PER_CANDLE, "warmup": True,
            } for b in warm][-MAX_CANDLES:]
            self._prev_close[s] = warm[-1].close if warm else None
            self._change_ref[s] = None
        self.realized_pnl = Decimal(0)
        self.cum_buy_usdc = Decimal(0)
        self.total_fees = Decimal(0)
        self.mandate_history = []
        self.reject_count = 0
        self.pause_count = 0
        self.guard_block_count = 0
        self._guard_checked = 0
        self._guard_blocked_buy = 0
        self.guard_unverified_count = 0
        self.guard_leak_usdc = Decimal(0)
        self.source_counts = {}
        self.action_counts = {}
        self.started_at = _now()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{mode}"
        self.brain_label = brain_label
        self.strategy_info = {
            "type": strat_type,
            "symbols": list(symbols),         # 세션 종목 목록 (멀티=N개)
            "multi": multi,                   # 멀티 종목 세션 여부
            "spend_per_symbol_usdc": str(spend_per_symbol),  # 조건형 종목별 1회 매수(총 spend/N)
            "dca_amount_per_symbol_usdc": str(per_symbol_dca),  # 적립형 종목별 회당(총 amount/N)
            "decision_mode": decision_mode,   # strict(엄격) / trend(추세 재량) — 조건형 전용
            "trend_signal": trend_signal,     # 추세추종 신호 (pxma20 / cross_5_20)
            "trend_signal_label": signal_label,  # 사람이 읽는 신호 문구
            "all_in": is_trend,               # 추세추종: 올인/올아웃 (건별 한도 = 총자산)
            "ta_mode": ta_mode,               # TA 보강(이동평균 배열·패턴 근거 판단)
            # 시간청산(안전레일) — 조건형만 적용, 추세추종은 0(미적용)
            "max_hold_bars": 0 if is_trend else CFG.max_hold_bars,
            "dca_unit": dca_unit,
            "dca_every_ticks": dca_every,
            "dca_every_minutes": dca_minutes,
            "dca_at_time": dca_at_time,
            "dca_amount_usdc": str(dca_amount),
            "schedule_label": schedule_label,   # 사람이 읽는 주기 문구 (UI 공용)
        }
        self.feed_info = feed_info
        # 재생 속도 — UI 가 넘긴 틱 간격(초)을 안전 범위[0.05, 60]로 클램프. 미지정이면 .env 기본.
        _ti = tick_interval_sec if tick_interval_sec is not None else CFG.web_tick_interval_sec
        try:
            _ti = float(_ti)
        except (TypeError, ValueError):
            _ti = CFG.web_tick_interval_sec
        self.tick_interval = min(max(_ti, 0.05), 60.0)
        self.last_archive_path = ""
        self._usdc_mint, self._stock_mint = usdc_mint, stock_mint
        self._user_kp = user_kp
        self._trading_kp, self._broker_kp = trading_kp, broker_kp
        self._mandate, self._auth = mandate, auth
        self._guard = guard
        self.agents, self.feeds, self._feeds_info = agents, feeds, feeds_info
        self._broker = broker
        # G5 — BROKER_HTTP_URL 이 설정된 세션만 매수 레그를 실제 HTTP 402 왕복으로 보낸다.
        # 미설정(기본)이면 None 이라 기존 인프로세스 A2A 경로가 바이트 그대로 유지된다.
        self._broker_http = optional_client(CFG.broker_http_url)
        self._client = client
        self._snap_before = self._snap_last = snap_before
        self._stop_event = asyncio.Event()

        self.bus.emit(ev.ENGINE_STARTED, {
            "mode": mode, "network": CFG.network,
            "symbol": self._focus, "symbols": list(symbols),
            "brain": brain_label,
            "feed": feed_info,
            "budget_total_usdc": str(self.budget_total),
            "per_trade_max_usdc": str(self.per_trade_max),
            "fee_bps": CFG.broker_fee_bps,
            "rules": self._rules_snapshot(),
            "strategy": self.strategy_info,
            "mandate_verified": mandate.verify(),
            "wallets": {"user": str(user_kp.pubkey()), "trading": str(trading_kp.pubkey()), "broker": str(broker_kp.pubkey())},
            "tick_interval_sec": self.tick_interval,
        })
        if snap_before is not None:
            self.bus.emit(ev.BALANCES, {"stage": "before", "balances": snap_before})

        if autostart:
            self._task = asyncio.create_task(self._run_loop())
        return self.state_snapshot()

    async def stop(self) -> Dict[str, Any]:
        if self.status != "running" or self._task is None:
            raise EngineError("실행 중인 세션이 없습니다.")
        self.status = "stopping"
        self._stop_event.set()
        await self._task            # _run_loop 의 finally 에서 _finalize 수행
        return self.state_snapshot()

    # ---------- A2 긴급정지 ----------

    def pause(self, actor: str = "human", reason: str = "") -> Dict[str, Any]:
        """긴급정지. `reason` 은 402 Guard 가 멈춘 이유를 화면에 남기기 위한 것이다(CODE-06).

        사람이 누른 정지는 이유가 자명하지만, 가드가 스스로 멈추면 화면에
        `🛑 매매 정지됨 (guard)` 만 남아 **왜 멈췄는지가 어디에도 없었다** —
        이 제품에서 가장 중요한 순간의 설명이 비어 있던 셈이다.
        """
        # 정지 상태는 세션(start~stop) 안에서만 의미가 있다 — 대기 중 정지는 거부한다
        # (버그: 대기 중 정지가 남아 다음 접속에도 "정지됨" 배지가 보였다)
        if self.status != "running":
            raise EngineError("실행 중인 세션이 없습니다 — 긴급정지는 세션 실행 중에만 가능합니다.")
        if self.trading_enabled:
            self.trading_enabled = False
            self.pause_info = {"actor": actor, "actor_label": PAUSE_ACTOR_LABEL.get(actor, actor),
                               "reason": reason, "ts": _now()}
            self.pause_count += 1
            self.bus.emit(ev.TRADING_PAUSED, dict(self.pause_info))
        return self.state_snapshot()

    def resume(self, actor: str = "human") -> Dict[str, Any]:
        if self.status != "running":
            raise EngineError("실행 중인 세션이 없습니다 — 매매 재개는 세션 실행 중에만 가능합니다.")
        if not self.trading_enabled:
            self.trading_enabled = True
            self.pause_info = None
            self.bus.emit(ev.TRADING_RESUMED, {"actor": actor})
        return self.state_snapshot()

    # ---------- A3 한도 설정 (새 mandate 재서명) ----------

    def _spend_per_trade(self, budget: Optional[Decimal] = None) -> Decimal:
        """1회 매수 금액 = 예산 × SPEND_PCT%. 센트 미만은 내린다(올림하면 마지막 매수가
        예산을 1센트 넘겨 AP2 가 거부한다). 인자를 주면 그 예산 기준으로 계산한다 —
        한도 변경이 적용되기 **전에** 새 값을 미리 재는 데 쓴다."""
        b = self.budget_total if budget is None else budget
        return (b * SPEND_PCT / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def _rules_snapshot(self) -> Dict[str, str]:
        """화면·이벤트로 나가는 규칙 묶음. spend_per_trade 는 **계산된 실제 값**이다 —
        고정 문자열을 내보내면 화면이 '30 USDC 어치 매수'라고 하는데 엔진은 다른 금액을
        쓰는 어긋남이 생긴다(이 저장소가 반복해서 밟은 부류의 결함)."""
        return {**DEFAULT_RULES,
                "spend_pct": str(SPEND_PCT),
                "spend_per_trade": str(self._spend_per_trade())}

    def update_limits(self, budget_total: Decimal, per_trade_max: Decimal,
                      actor: str = "human") -> Dict[str, Any]:
        """예산/건별 한도 변경. 실행 중에는 긴급정지 상태에서만 즉시 적용(레이스 방지),
        대기 상태에서는 다음 세션 기본값으로 저장한다. 즉시 적용 시 새 mandate 를
        재서명하고 사용액(spent)을 이월한다 — 예산=순투입 한도 해석 유지."""
        # 입력 검증 — Decimal("Infinity")·NaN 은 InvalidOperation 을 던지지 않고 만들어지므로
        # is_finite() 로 먼저 걸러야 한다. Infinity 는 `<= 0` 검사를 통과해 예산 무한대가 되고,
        # NaN 은 비교에서 예외를 내 500 으로 새어나간다.
        for name, v in (("예산", budget_total), ("건별 한도", per_trade_max)):
            if not v.is_finite():
                raise EngineError(f"{name} 값이 유효한 숫자가 아닙니다.")
        if budget_total <= 0 or per_trade_max <= 0:
            raise EngineError("예산과 건별 한도는 0보다 큰 숫자여야 합니다.")
        # 서버측 상한 — 외부에서 한도를 무한대로 올려 AP2 방어선을 무력화하는 것을 차단
        if budget_total > CFG.max_budget_usdc:
            raise EngineError(f"예산은 최대 {CFG.max_budget_usdc} USDC 까지 설정할 수 있습니다.")
        if per_trade_max > budget_total:
            raise EngineError("건별 한도는 총예산보다 클 수 없습니다.")
        old = {"budget_total_usdc": str(self.budget_total),
               "per_trade_max_usdc": str(self.per_trade_max)}
        applied = "next-session"

        # 추세추종(올인) 세션이면 건별 한도는 총자산까지 열려 있어야 한다 — 사용자가 준
        # per_trade 값 대신 세션 실효 한도(max_budget)로 재서명한다(재진입 올인이 안 막히게).
        is_trend = (getattr(self, "strategy_info", None) or {}).get("type") == "trend"

        if self.status == "running":
            if self.trading_enabled:
                raise EngineError("실행 중에는 긴급정지 상태에서만 한도를 변경할 수 있습니다.")
            spent = self._total_spent()   # 공유 예산은 auth 1개, 추세추종 멀티는 종목별 합산
            if budget_total < spent:
                raise EngineError(
                    f"새 예산({budget_total})이 이미 사용한 금액({spent})보다 작습니다.")
            # ⚠ 총액 검사만으로는 부족하다(bug-dept BUG-07). 추세추종 멀티는 예산을 종목별로
            # 쪼개 쓰는데, 한 종목이 많이 쓴 상태에서 예산을 낮추면 그 몫이 그 종목의 사용액보다
            # 작아져 슬라이스 잔여가 음수가 된다. 음수 몫은 _total_remaining 합산에서 다른
            # 종목의 양수와 상계되고, 각 종목의 authorize 는 제 슬라이스만 보므로 결국
            # **세션 총 사용액이 새 예산을 넘는 매수가 승인된다**(재현: 예산 100→60 으로 낮춘 뒤
            # 총 75 사용 = 15 초과). "한도를 낮추면 그만큼 줄어든다"는 이 제품의 헤드라인
            # 주장이라 통과시키면 안 된다. 검사는 반드시 mandate 재서명 **앞**이다 —
            # 뒤에서 던지면 세션 mandate 만 새 예산으로 바뀌고 종목별 auth 는 옛 몫으로 남는다.
            slices = (self._budget_slices(self.symbols, budget_total)
                      if is_trend and len(self.symbols) > 1 else {})
            over = [f"{s}(몫 {slices[s]} < 이미 사용 {self.agents[s].auth.spent_usdc})"
                    for s in slices if slices[s] < self.agents[s].auth.spent_usdc]
            if over:
                raise EngineError(
                    f"새 예산({budget_total})을 종목 {len(slices)}개로 나누면 이미 사용한 금액보다 "
                    f"작아지는 종목이 있습니다 — {' · '.join(over)}")
            eff_per_trade = CFG.max_budget_usdc if is_trend else per_trade_max
            # 세션 가드는 항상 이 mandate 하나로 검문한다(허용종목=전 종목, 건별=실효 한도).
            new_mandate = OpenPaymentMandate(
                user_pubkey=str(self._user_kp.pubkey()),   # 위임자(사용자) 키로 재서명
                allowed_asset=str(self._usdc_mint),
                budget_total_usdc=budget_total,
                per_trade_max_usdc=eff_per_trade,
                allowed_symbols=list(self.symbols),        # 세션 전 종목 (멀티 유지)
            ).sign(self._user_kp)
            self._mandate = new_mandate
            # 공유 가드도 새 mandate 로 정합시킨다 — 안 하면 Guard 의 한도(per_trade)·허용종목
            # 검사가 옛 mandate 를 계속 봐서 활성 서명 mandate 와 어긋난다(헤드라인 가드 정합).
            self._guard.mandate = new_mandate
            if slices:
                # 추세추종 멀티 — 종목별 예산 슬라이스(새 예산/N)를 재산정하고 사용액을 이월한다.
                # 몫은 위 검사와 **같은 값**을 쓴다(따로 계산하면 둘이 갈라질 수 있다).
                for sym in self.symbols:
                    sm = OpenPaymentMandate(
                        user_pubkey=str(self._user_kp.pubkey()),
                        allowed_asset=str(self._usdc_mint),
                        budget_total_usdc=slices[sym], per_trade_max_usdc=eff_per_trade,
                        allowed_symbols=[sym]).sign(self._user_kp)
                    sa = PaymentAuthorizer(sm, agent_kp=self._trading_kp)
                    sa.spent_usdc = self.agents[sym].auth.spent_usdc   # 종목별 사용액 이월
                    self.agents[sym].auth = sa
                self._auth = None
            else:
                new_auth = PaymentAuthorizer(new_mandate, agent_kp=self._trading_kp)
                new_auth.spent_usdc = spent  # 사용액 이월 (공유 예산이라 전 종목 합산치)
                self._auth = new_auth
                for a in self.agents.values():   # 모든 종목 에이전트가 새 공유 auth 를 쓴다
                    a.auth = new_auth
            self._session_per_trade = eff_per_trade
            # 1회 매수 금액이 예산 대비 비율이므로 예산이 바뀌면 함께 다시 잰다.
            # 안 하면 세션 중 예산을 내려도 지출은 옛 예산 기준으로 남아, 화면이 말하는
            # 금액(_rules_snapshot 은 새 예산으로 계산한다)과 엔진이 쓰는 금액이 갈라진다.
            # ⚠ 종목 수로 나누는 것은 시작 때와 같은 규칙이다(한 종목이 독식하지 않게).
            new_spend = self._spend_per_trade(budget_total)
            if new_spend > 0:
                cent = Decimal("0.01")
                ns = len(self.symbols)
                per_sym = (new_spend / ns).quantize(cent) if ns > 1 else new_spend
                if per_sym > 0:
                    for a in self.agents.values():
                        a.strategy.spend_per_trade_usdc = per_sym
            applied = "immediate"

        self.budget_total = budget_total
        # per_trade_max 는 사용자가 지정한 '기본 건별 한도'로 항상 추적한다(다음 비추세 세션·
        # 재시작 복원의 기준). 추세추종 세션의 실효 한도(올인 캡)는 별도로 _session_per_trade 에
        # 담겨 mandate 에만 쓰이므로, 여기서 self.per_trade_max 를 인메모리·영속 모두 같은 값으로
        # 갱신해야 둘이 갈라지지 않는다(과거: 인메모리만 스킵돼 재시작 유무로 결과가 달라짐).
        self.per_trade_max = per_trade_max
        rec = {
            "ts": _now(), "actor": actor, "applied": applied,
            "old": old,
            "new": {"budget_total_usdc": str(budget_total),
                    # 즉시 적용이면 실제 재서명된 실효 한도(추세추종은 올인 캡)를 기록한다
                    "per_trade_max_usdc": str(self._session_per_trade
                                              if applied == "immediate" else per_trade_max)},
            "signature": self._mandate.signature if applied == "immediate" else "",
            "mandate_verified": self._mandate.verify() if applied == "immediate" else None,
        }
        if applied == "immediate":
            self.mandate_history.append(rec)  # 세션 아카이브에 포함될 변경 이력
        self.bus.emit(ev.MANDATE_UPDATED, rec)
        # 한도는 재시작 후에도 유지 — 다음 부팅 때 restore_from_store 가 복원
        self._persist(self.store.save_defaults({
            "budget_total_usdc": str(budget_total),
            "per_trade_max_usdc": str(per_trade_max),
            "updated_by": actor,
        }))
        return self.state_snapshot()

    # ---------- 판단 출처 계측 (심사 축② AI 활용 증빙) ----------

    def _ai_stats(self) -> Dict[str, Any]:
        """세션 판단의 출처 집계 — "이 세션이 정말 AI 로 구동됐는가"의 정량 증빙.

        tx 아티팩트·세션 요약(Firestore)·상태 스냅샷이 모두 이 함수 하나를 쓴다.
        온체인 증빙만 있고 판단 출처가 없으면 "9건의 tx 중 Gemini 관여분이 몇 건인지"
        확인할 수 없다는 심사 지적(judging_latest.md 축② 갭)을 닫는 계측이다.

        출처별 의미:
          gemini        — Gemini 응답이 그대로 집행된 판단
          rule-gate     — Gemini 가 규칙 밖 개시를 시도해 규칙 게이트가 보류로 강등
          rule-fallback — Gemini 호출 실패(쿼터·네트워크) → 규칙 판단으로 대체
          rule / dca    — 애초에 AI 를 부르지 않는 결정론 경로(추세추종·적립·워밍업)
        gemini_calls = 앞 세 개의 합 = 실제로 Gemini API 를 부른 틱 수.

        invoice_semantics 블록은 **두 번째 AI 레이어**다(판단이 아니라 청구서 심사).
        판단 레이어의 rule-gate 와 같은 원리로 LLM 에 거부권만 준다 —
        상세는 payments/invoice_semantics.py 모듈 독스트링."""
        by_source = dict(self.source_counts)
        used = by_source.get("gemini", 0)
        gated = by_source.get("rule-gate", 0)
        fallbacks = by_source.get("rule-fallback", 0)
        total = sum(by_source.values())
        share = (Decimal(used) / total * 100).quantize(Decimal("0.01")) if total else Decimal(0)
        # 체결(온체인/드라이)이 어느 판단에서 나왔는지 — tx 단위 AI 관여 증빙
        by_trade: Dict[str, int] = {}
        for t in self.trades:
            src = t.get("decision_source") or "unknown"
            by_trade[src] = by_trade.get(src, 0) + 1
        return {
            "brain": self.brain_label,
            "decisions_total": total,
            "by_source": by_source,
            "by_action": dict(self.action_counts),
            "gemini_calls": used + gated + fallbacks,
            "gemini_decisions": used,
            "gemini_gated": gated,          # 규칙 게이트가 막은 AI 의 규칙 밖 개시
            "rule_fallbacks": fallbacks,
            "gemini_share_pct": str(share),
            "trades_by_decision_source": by_trade,
            "invoice_semantics": self._semantic_stats(),
        }

    def replace_brain(self, brain) -> None:
        """세션의 판단 두뇌를 통째로 교체한다 (테스트에서 가짜 두뇌를 꽂을 때).

        같은 인스턴스가 **두 곳**에 물려 있다 — 종목별 TradingAgent(판단)와 Guard 의 의미
        대조기(청구서 심사). 한쪽만 바꾸면 다른 쪽이 실제 Gemini API 를 계속 부른다.
        테스트가 `engine.agents[s].brain = fake` 만 하던 시절에 그게 실제로 일어났고,
        무료 티어 일일 500건이 조용히 소모됐다. 교체 지점을 여기 하나로 모은다."""
        for agent in self.agents.values():
            agent.brain = brain
        if self._guard is not None and getattr(self._guard, "semantic", None) is not None:
            self._guard.semantic.brain = brain

    def _emit_semantic(self, side: str, order_id: str, symbol: str) -> None:
        """직전 청구서 의미 대조 판정을 로그로 흘린다 (축④ 실행 이력).

        통과·차단·검사불가를 모두 남긴다 — '검사가 돌았다'는 사실 자체가 증빙이고,
        차단만 기록하면 평소에 이 계층이 일하고 있다는 게 화면에 안 보인다."""
        v = getattr(self._guard, "last_semantic", None) if self._guard is not None else None
        if v is None:
            return
        # ⚠ 이 주문의 판정일 때만 방출한다. Guard 는 세션 1개를 전 종목이 공유하므로,
        # 하드 검사에서 차단돼 의미 대조가 **돌지도 않은** 건에 직전 주문의 '통과' 판정이
        # 붙어 나가던 결함이 있었다(bug-dept BUG-04). 같은 order_id 로 서로 모순되는
        # 두 줄('차단'과 '의미 대조 통과')이 로그에 남았다.
        if v.order_id and v.order_id != str(order_id):
            return
        self.bus.emit(ev.GUARD_SEMANTIC, {
            "side": side, "order_id": order_id, "symbol": symbol, **v.as_event(),
        })

    def _semantic_stats(self) -> Dict[str, Any]:
        """청구서 의미 대조(두 번째 AI 레이어) 집계. 계층이 없으면 enabled=false."""
        checker = getattr(self._guard, "semantic", None) if self._guard is not None else None
        if checker is None:
            return {"enabled": False, "checked": 0, "llm_calls": 0, "cache_hits": 0,
                    "passed": 0, "blocked": 0,
                    "unverified_blocked": 0, "unverified_skipped": 0}
        return {"enabled": True, **checker.stats.as_dict()}

    # ---------- B2 데일리 브리핑 ----------

    def _briefing_stats(self) -> Dict[str, Any]:
        """브리핑 근거 데이터 — 현재(실행 중) 또는 직전 세션의 집계 (전 종목 합산)."""
        settled = [t for t in self.trades if t["status"] == "settled"]
        buys = [t for t in settled if t["side"] == "buy"]
        sells = [t for t in settled if t["side"] == "sell"]
        remaining = self._total_remaining()
        val = self._valuation(remaining)
        return_pct = self._display_return_pct(val)
        ai = self._ai_stats()   # 세션 전체 누적 (타임라인 상한과 무관)
        # 종목별 보유 요약 + 합산 수량 (멀티는 종목이 섞여 단일 평단이 없어 리스트로 함께 준다)
        positions = [{"symbol": s, "quantity": str(self.agents[s].position.quantity),
                      "avg_price_usdc": str(self.agents[s].position.avg_price_usdc)}
                     for s in self.symbols]
        total_qty = sum((self.agents[s].position.quantity for s in self.symbols), Decimal(0))
        focus_pos = self.agents[self._focus].position if self.symbols else None
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "session_started_at": self.started_at,
            "engine_status": self.status,
            "mode": self.mode, "network": CFG.network,
            "symbol": "·".join(self.symbols) if self.symbols else CFG.stock_symbol,
            "symbols": list(self.symbols),
            "strategy": self.strategy_info,
            "ticks": self.tick,
            "buy_count": len(buys), "sell_count": len(sells),
            "buy_total_usdc": str(sum((Decimal(t["total_usdc"]) for t in buys), Decimal(0))),
            "sell_total_usdc": str(sum((Decimal(t["total_usdc"]) for t in sells), Decimal(0))),
            "realized_pnl_usdc": str(self.realized_pnl),
            "return_pct": str(return_pct),
            "unrealized_pnl_usdc": val["unrealized_pnl_usdc"],   # 평가손익(미실현, 합산)
            "position_value_usdc": val["position_net_value_usdc"],
            "total_asset_usdc": val["total_asset_usdc"],
            "budget_total_usdc": str(self.budget_total),
            "budget_remaining_usdc": str(remaining),
            "cum_fee_usdc": str(self.total_fees),
            "ap2_reject_count": self.reject_count,
            "pause_count": self.pause_count,
            "position_qty": str(total_qty),
            "position_avg_usdc": str(focus_pos.avg_price_usdc) if focus_pos else "0",
            "positions": positions,   # 종목별 보유 (멀티 브리핑 근거)
            "last_price_usdc": self._price_history.get(self._focus, [{}])[-1].get("price")
                               if self._price_history.get(self._focus) else None,
            "decisions_by_action": ai["by_action"],
            "decisions_by_source": ai["by_source"],
            "ai": ai,   # 판단 출처 계측 (브리핑이 AI 관여도를 근거로 쓸 수 있게)
        }

    async def generate_briefing(self, trigger: str = "manual") -> Dict[str, Any]:
        """브리핑 생성 → BRIEFING 이벤트 + artifacts/briefings/ 저장.
        trigger: manual(버튼) / session-end(세션 종료) / market-close(장 마감 시각)"""
        if not self.trades and not self.decisions:
            raise EngineError("브리핑할 데이터가 없습니다 — 먼저 세션을 실행하세요.")
        stats = self._briefing_stats()
        # Gemini 호출은 blocking — 이벤트 루프를 막지 않게 워커 스레드에서
        text, source, fallback_detail = await asyncio.to_thread(generate_briefing_text, stats)
        rec: Dict[str, Any] = {"ts": _now(), "trigger": trigger, "source": source, "text": text}
        # 폴백 사유는 본문에 섞지 않고 별도 필드로 올린다(web/briefing.py 독스트링 참조).
        # 화면은 이 값을 title 툴팁처럼 눈에 띄지 않는 자리에 두면 된다 — 진단은 남기되
        # 심사위원이 읽는 문장에 raw 예외 문자열이 들어가지 않게.
        if fallback_detail:
            rec["fallback_detail"] = fallback_detail
        try:
            rec["archive"] = self._save_briefing(rec, stats)
        except Exception as e:
            rec["archive"] = ""
            self.bus.emit(ev.ERROR, {"message": f"브리핑 저장 실패: {type(e).__name__}: {e}"})
        self.last_briefing = rec
        self.bus.emit(ev.BRIEFING, rec)
        self._persist(self.store.save_briefing(rec, stats))
        return rec

    def _save_briefing(self, rec: Dict[str, Any], stats: Dict[str, Any]) -> str:
        os.makedirs(os.path.join("artifacts", "briefings"), exist_ok=True)
        # 초 단위 타임스탬프 — 같은 분에 여러 번(수동+자동) 생성해도 덮어쓰지 않게
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("artifacts", "briefings", f"{ts}_briefing.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 데일리 브리핑 {stats['date']}\n\n")
            f.write(f"- 생성: {rec['ts']} / 트리거: {rec['trigger']} / 출처: {rec['source']}\n\n")
            f.write(rec["text"] + "\n\n")
            f.write("## 근거 데이터\n\n```json\n")
            f.write(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
            f.write("\n```\n")
        return path

    async def _auto_briefing(self, trigger: str) -> None:
        try:
            await self.generate_briefing(trigger)
        except EngineError:
            pass  # 데이터 없음 — 조용히 스킵
        except Exception as e:
            self.bus.emit(ev.ERROR, {"message": f"자동 브리핑 실패: {type(e).__name__}: {e}"})

    async def maybe_daily_briefing(self) -> None:
        """장 마감 시각(DAILY_BRIEFING_TIME) 이후 하루 1회 자동 생성 — 서버 루프가 주기 호출."""
        try:
            hh, mm = CFG.daily_briefing_time.split(":")
            now = datetime.now()
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except ValueError:
            return  # 형식 오류면 자동 생성 비활성
        today = now.strftime("%Y-%m-%d")
        if now < target or self._last_daily_briefing_date == today:
            return
        self._last_daily_briefing_date = today  # 데이터가 없어도 오늘은 1회로 간주
        if self.trades or self.decisions:
            await self._auto_briefing("market-close")

    # ---------- 루프 ----------

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick_once()
                except Exception as e:
                    self.bus.emit(ev.ERROR, {"message": f"틱 처리 실패: {type(e).__name__}: {e}"})
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._finalize()

    def _append_candle(self, candles: List[Dict[str, Any]], price: Decimal) -> None:
        """틱 가격을 (종목별) 캔들 리스트에 반영 — 진행 중 캔들이 차면 새 캔들을 연다(시가=첫 틱가)."""
        cur = candles[-1] if candles else None
        if cur is not None and cur["count"] < TICKS_PER_CANDLE:
            cur["high"] = str(max(Decimal(cur["high"]), price))
            cur["low"] = str(min(Decimal(cur["low"]), price))
            cur["close"] = str(price)
            cur["count"] += 1
            return
        candles.append({
            "ts": _now(), "open": str(price), "high": str(price),
            "low": str(price), "close": str(price), "count": 1,
        })
        if len(candles) > MAX_CANDLES:
            candles.pop(0)

    async def _tick_once(self) -> None:
        # 재생 피드가 전부 소진 → 세션 자동 종료. 일부만 소진되면 그 종목만 건너뛰고 나머지는 계속.
        replay = [f for f in self.feeds.values() if isinstance(f, ReplayPriceFeed)]
        if replay and all(f.exhausted for f in replay):
            played = max((f.played_bars for f in replay), default=0)
            last = next((f.last_bar.date for f in replay if f.last_bar), "")
            self.bus.emit(ev.REPLAY_ENDED, {
                "message": "실데이터 재생 완료 — 세션을 자동 종료합니다",
                "bars_played": played, "last_date": last,
                "symbols": list(self.symbols),
            })
            self._stop_event.set()
            return

        self.tick += 1
        for sym in self.symbols:
            feed = self.feeds[sym]
            if isinstance(feed, ReplayPriceFeed) and feed.exhausted:
                continue                          # 이 종목만 소진 — 나머지 진행
            try:
                await self._process_symbol(sym, feed)
            except Exception as e:                # 한 종목 오류 격리 — 나머지 계속
                self.bus.emit(ev.ERROR, {
                    "symbol": sym,
                    "message": f"틱 처리 실패({sym}): {type(e).__name__}: {e}"})

    async def _process_symbol(self, symbol: str, feed: PriceFeed) -> None:
        """한 종목의 1틱 처리 — 시세 반영·판단·매매 사이클. 종목별 상태(agents/dict)만 만진다."""
        agent = self.agents[symbol]
        ph = self._price_history[symbol]
        candles = self._candles[symbol]
        price = feed.get_price(symbol)
        bar: Optional[Bar] = feed.last_bar if isinstance(feed, ReplayPriceFeed) else None
        prev = self._prev_close.get(symbol)
        ph.append({"ts": bar.date if bar else _now(), "price": str(price)})
        if len(ph) > MAX_PRICE_POINTS:
            ph.pop(0)

        if bar is not None:
            # 실데이터: 1틱 = 1봉 — 시가·고가·저가·종가를 그대로 캔들로
            candles.append({
                "ts": bar.date, "open": str(bar.open), "high": str(bar.high),
                "low": str(bar.low), "close": str(bar.close), "count": TICKS_PER_CANDLE,
            })
            if len(candles) > MAX_CANDLES:
                candles.pop(0)
        else:
            self._append_candle(candles, price)

        payload: Dict[str, Any] = {"tick": self.tick, "symbol": symbol, "price": str(price)}
        if prev is not None:
            payload["prev_close"] = str(prev)
        if bar is not None:
            payload["date"] = bar.date
            payload["bar"] = {"ts": bar.date, "open": str(bar.open), "high": str(bar.high),
                              "low": str(bar.low), "close": str(bar.close)}
            payload["progress"] = {"played": feed.played_bars, "total": feed.total_bars}
        self.bus.emit(ev.PRICE_TICK, payload)
        self._change_ref[symbol] = prev
        self._prev_close[symbol] = price

        if not self.trading_enabled:
            return  # A2 긴급정지 — 시세만 흐르고 신규 판단·결제 없음

        # Gemini 호출은 동기(blocking) — 서버 이벤트 루프를 막지 않게 워커 스레드에서.
        # (종목 순회는 순차라 같은 brain 을 동시에 부르지 않는다 — 429 충돌 없음.)
        decision = await asyncio.to_thread(agent.decide, symbol, price, bar)
        drec = {
            "ts": _now(), "tick": self.tick, "symbol": symbol, "price": str(price),
            "action": decision.action, "source": decision.source, "reason": decision.reason,
            "spend_usdc": str(decision.spend_usdc),
        }
        self.decisions.append(drec)
        # 세션 전체 집계 — 타임라인은 500건에서 잘리므로 카운터를 따로 누적한다
        self.source_counts[decision.source] = self.source_counts.get(decision.source, 0) + 1
        self.action_counts[decision.action] = self.action_counts.get(decision.action, 0) + 1
        if len(self.decisions) > MAX_DECISIONS:
            self.decisions.pop(0)
        self.bus.emit(ev.DECISION, drec)

        if decision.action == "sell" and agent.position.quantity > 0:
            await self._sell_cycle(symbol, agent, price, decision)
        elif decision.action == "buy":
            await self._buy_cycle(symbol, agent, price, decision)

    # ---------- 매수 사이클 (run_demo 이식) ----------

    async def _buy_cycle(self, symbol: str, agent: TradingAgent,
                         price: Decimal, decision: Decision) -> None:
        live = self.mode == "live"
        quote = self._broker.quote(symbol, decision.spend_usdc, price)
        self.bus.emit(ev.QUOTE, {
            "side": "buy",
            "request": f"'{symbol} 을 {decision.spend_usdc} USDC 어치 견적 줘'",
            "symbol": symbol, "quantity": str(quote.quantity),
            "price_usdc": str(quote.price_usdc), "total_usdc": str(quote.total_usdc),
            "subtotal_usdc": str(quote.subtotal_usdc), "fee_usdc": str(quote.fee_usdc),
            "fee_bps": quote.fee_bps,
        })

        # 지불액이 1단위 미만이면 수량이 0.0000 으로 내림되고 청구액도 0 이 된다. 그 0원
        # 청구서는 가드·AP2·정산을 전부 지나 `settled` 로 남는데 예산은 줄지 않아, 남은 예산이
        # dust 인 세션에서 **틱마다 무한 반복**됐다(bug-dept BUG-12 — 481틱 세션 실측).
        # 여기서 끊는 이유: 아래 계층(가드·AP2)에도 하한을 넣었지만 그쪽이 먼저 잡으면
        # 자기가 만든 잡음이 '가드 차단'·'한도 거부' KPI 로 잡혀 대표 지표가 더러워진다.
        # HTTP 경로(web/broker_service.py)는 이미 같은 조건을 400 으로 막고 있었다 —
        # 인프로세스 경로만 비대칭으로 열려 있던 것을 맞춘다.
        if quote.quantity <= 0:
            self.bus.emit(ev.ERROR, {
                "message": f"{symbol} 매수 건너뜀 — 지불액 {decision.spend_usdc} USDC 가 1단위 "
                           f"미만이라 견적 수량이 0 입니다(남은 예산으로는 살 수 없습니다)"})
            return

        # 청구서 수령 — 기본은 인프로세스 A2A, BROKER_HTTP_URL 이 설정되면 실제 HTTP 402 왕복.
        # 어느 전송이든 아래 402 Guard 검증은 동일하게 지난다(전송이 바뀌어도 방어는 그대로).
        http = self._broker_http
        if http is not None:
            try:
                required = await http.request_order(
                    symbol, decision.spend_usdc, price, mode="live" if live else "dry")
            except Exception as e:
                self.bus.emit(ev.ERROR, {
                    "message": f"브로커 HTTP 402 청구서 요청 실패 — 매수 보류: {type(e).__name__}: {e}"})
                return
        else:
            required = self._broker.make_payment_required(quote)
        self.bus.emit(ev.X402_REQUIRED, {
            "side": "buy", "order_id": required.order_id,
            "amount_base": required.requirements.amount,
            "pay_to": required.requirements.pay_to,
            "resource": required.requirements.resource,
            "transport": "http-402" if http is not None else "a2a-inprocess",
        })

        # 402 Guard(청구서 검증) + AP2 한도 검사 — 위반이면 서명 자체가 일어나지 않는다(유출 0)
        self._guard_checked += 1   # KPI 분모: 가드를 태운 청구서(결과와 무관하게 여기서 센다)
        try:
            blockhash = await x.get_latest_blockhash(self._client) if live else Hash.default()
            submitted = agent.build_payment(
                required, blockhash, quote, max_spend_usdc=decision.spend_usdc,
                expected_symbol=symbol)   # 엔진이 '지금 주문한' 종목 — 청구서 종목 바꿔치기 차단
        except GuardError as e:
            self._count_guard_block(e.result.code, buy=True)
            self._emit_semantic("buy", required.order_id, symbol)
            self.bus.emit(ev.GUARD_BLOCKED, {
                "side": "buy", "order_id": required.order_id, **e.result.as_event(),
            })
            return
        except MandateError as e:
            self.reject_count += 1
            self.bus.emit(ev.MANDATE_REJECTED, {
                "side": "buy", "order_id": required.order_id, "reason": str(e),
            })
            return
        self._emit_semantic("buy", required.order_id, symbol)
        self.bus.emit(ev.X402_SUBMITTED, {
            "side": "buy", "order_id": required.order_id,
            "remaining_usdc": str(agent.auth.remaining_usdc),
            "payload_b64_len": len(submitted.payment.serialized_transaction),
        })

        # 라이브: 정산 전 구매자 주식 잔액을 캡처(배송 재조회의 기준점).
        # 기준선(before)을 못 읽으면 delta 오라클이 오염(0 기준선 = 미배송 오탐 통과)되므로,
        # 결제를 진행하지 않고 예약을 원복 + 세션 정지한다(유출 0, BUG-01).
        before_stock = 0
        if live and self._client is not None and self._stock_mint is not None:
            try:
                before_stock = await x.get_token_balance_base(
                    self._client, self._trading_kp.pubkey(), self._stock_mint)
            except Exception as e:
                agent.auth.release(required.order_id)
                self.bus.emit(ev.GUARD_PENDING, {
                    "side": "buy", "order_id": required.order_id, "ok": False,
                    "code": "GUARD_BASELINE_UNREAD",
                    "detail": f"정산 전 주식 잔액 기준선 조회 실패 — 배송 검증 불가로 매수 보류: {type(e).__name__}",
                    "where": "engine.py:_buy_cycle", "expected": "", "actual": "",
                })
                if self.trading_enabled:
                    self.pause(actor="guard",
                               reason=f"{symbol} 매수 — 정산 전 주식 잔액 기준선을 읽지 못해 "
                                      "배송 검증이 불가능합니다(GUARD_BASELINE_UNREAD)")
                return

        completed = None
        settled = False
        try:
            if http is not None:
                completed = await http.submit_payment(
                    required, submitted, decision.spend_usdc, mode="live" if live else "dry")
            else:
                completed = await self._broker.settle(
                    submitted, required.requirements, quote.quantity,
                    live=live, client=self._client)

            # 라이브 정산이면 온체인 재조회로 실제 배송을 확인한다
            # (결함 I: 결제는 확정됐는데 주식 전달이 실패해도 settled 로 기록되던 문제).
            #
            # 진입 조건에 partial 을 포함한다 — 매도 레그(_sell_cycle:1345)와 같은 규칙이다.
            # 브로커는 '대금은 확정 수령했는데 주식 전달 tx 가 확정되지 않음'이면 스스로
            # partial 을 돌려주는데(broker_agent.py `elif confirmed and not delivered`),
            # settled 만 검사하면 **그 건이 검증 블록에 아예 못 들어와** 유출 KPI 와
            # GUARD_PENDING 에서 통째로 빠졌다. USDC 는 이미 온체인에서 떠난 뒤인데
            # 첫 화면은 유출 0.00 을 계속 표시한다 = de9e000 으로 닫았던 '유출 KPI 자해'가
            # 매수 레그에서만 되살아난 모양이었다(CODE-01, BUG-01 수정의 비대칭 잔여).
            #
            # 판정은 여기서 하지 않고 check_delivery 에 맡긴다 — 배송 tx 가 늦게 확정된
            # 경우까지 유출로 찍으면 그것도 거짓이다(오탐).
            if (completed.status in ("settled", "partial") and live and self._client is not None
                    and self._stock_mint is not None):
                expected_inc = to_base_units(quote.quantity, CFG.stock_decimals)

                async def _reader():
                    return await x.get_token_balance_base(
                        self._client, self._trading_kp.pubkey(), self._stock_mint)

                delivery = await self._guard.check_delivery(
                    completed, signed_order_id=required.order_id, balance_reader=_reader,
                    before_units=before_stock, expected_increase_units=expected_inc,
                    retries=2, retry_delay_sec=1.0)
                if not delivery.ok:
                    # pending_delivery — 포지션 미반영·한도 원복·세션 정지·미결(partial) 기록.
                    # USDC 는 이미 온체인에서 떠났고 회수 경로가 없다 → KPI '유출' 에 가산.
                    completed.status = "partial"
                    self._record_leak(quote.total_usdc)
                    self.bus.emit(ev.GUARD_PENDING, {
                        "side": "buy", "order_id": required.order_id,
                        "leak_usdc": str(quote.total_usdc), **delivery.as_event(),
                    })

            settled = completed.status == "settled"
            # 평단은 수수료 포함 실효 단가(total/qty)로 반영 — 실현손익이 수수료 차감 후 순손익
            eff_price = ((quote.total_usdc / quote.quantity).quantize(Decimal("0.01"))
                         if quote.quantity > 0 else price)
            agent.on_completed(completed, symbol, quote.quantity, eff_price, quote.total_usdc)
            if settled:
                self.cum_buy_usdc += quote.total_usdc
                self.total_fees += quote.fee_usdc
            self._complete_trade("buy", symbol, quote.quantity, quote, completed, decision)
        finally:
            # 결함 H: settled 가 아니면 AP2 예약분을 원복해 한도를 되돌린다(실패해도 예산 소진 방지)
            if settled:
                agent.auth.settle(required.order_id)
            else:
                agent.auth.release(required.order_id)

        # 배송 미확인(partial)이면 세션을 정지한다 — 반복 결제로 손실이 누적되지 않게
        if completed is not None and completed.status == "partial" and self.trading_enabled:
            self.pause(actor="guard",
                       reason=f"{symbol} 매수 — USDC 는 나갔는데 주식 도착이 확인되지 않았습니다"
                              f"(주문 {required.order_id} · {quote.total_usdc} USDC). "
                              "같은 손실이 반복되지 않게 멈춥니다")

    # ---------- 매도 사이클 (run_demo 이식 + A7 실현손익) ----------

    async def _sell_cycle(self, symbol: str, agent: TradingAgent,
                          price: Decimal, decision: Decision) -> None:
        live = self.mode == "live"
        qty = agent.position.quantity
        avg_before = agent.position.avg_price_usdc  # 실현손익 계산용 평단 캡처
        quote = self._broker.sell_quote(symbol, qty, price)
        self.bus.emit(ev.QUOTE, {
            "side": "sell",
            "request": f"'{symbol} {qty} 주 되사줘'",
            "symbol": symbol, "quantity": str(quote.quantity),
            "price_usdc": str(quote.price_usdc), "total_usdc": str(quote.total_usdc),
            "subtotal_usdc": str(quote.subtotal_usdc), "fee_usdc": str(quote.fee_usdc),
            "fee_bps": quote.fee_bps,
        })

        required = self._broker.make_stock_required(quote)
        self.bus.emit(ev.X402_REQUIRED, {
            "side": "sell", "order_id": required.order_id,
            "amount_base": required.requirements.amount,
            "pay_to": required.requirements.pay_to,
            "resource": required.requirements.resource,
        })

        # 402 Guard(매도 청구서 검증) — 자산(합의 주식 민트)·수취인(브로커)·수량을 엔진의 독립
        # 기준(self._stock_mint·보유 수량 qty)과 대조한다. 위반이면 서명 자체가 일어나지 않는다(유출 0).
        blockhash = await x.get_latest_blockhash(self._client) if live else Hash.default()
        self._guard_checked += 1   # 매수와 같은 분모에 넣는다 — 양 레그 대칭이 지표에도 성립해야 한다
        try:
            submitted = agent.build_stock_transfer(
                required, blockhash,
                expected_stock_mint=self._stock_mint,
                expected_quantity=qty,
                stock_decimals=CFG.stock_decimals,
                expected_symbol=symbol,   # 매수 레그와 같은 종목 동일성 검사(대칭)
                quote=quote)              # 의미 대조 입력(매도는 검사 불가 시 진행)
        except GuardError as e:
            self._count_guard_block(e.result.code, buy=False)
            self._emit_semantic("sell", required.order_id, symbol)
            self.bus.emit(ev.GUARD_BLOCKED, {
                "side": "sell", "order_id": required.order_id, **e.result.as_event(),
            })
            return
        self._emit_semantic("sell", required.order_id, symbol)
        self.bus.emit(ev.X402_SUBMITTED, {
            "side": "sell", "order_id": required.order_id,
            "payload_b64_len": len(submitted.payment.serialized_transaction),
        })

        # 라이브: 정산 전 판매자 USDC 잔액을 캡처(대금 도착 재조회의 기준점). 기준선을
        # 못 읽으면 delta 오라클이 오염되므로 pending 으로 취급한다(BUG-01 과 동일 원칙).
        before_usdc = 0
        baseline_ok = True
        if live and self._client is not None and self._usdc_mint is not None:
            try:
                before_usdc = await x.get_token_balance_base(
                    self._client, self._trading_kp.pubkey(), self._usdc_mint)
            except Exception:
                baseline_ok = False

        completed = await self._broker.settle_sale(
            submitted, required.requirements, quote.total_usdc, live=live, client=self._client)

        # 매도 대금(USDC) 온체인 도착 재조회 — 매수측 check_delivery 의 매도 대칭(BUG-02).
        # 주식은 넘어갔는데 대금이 안 들어오면 partial 로 강등(포지션·예산·실현손익 미반영) + 세션 정지.
        # 진입 조건에 partial 을 포함한다. 브로커는 '주식은 확정 수령했는데 USDC 지급 tx 가
        # 실패' 하면 스스로 partial 을 돌려준다(broker_agent.py `elif confirmed and not paid`).
        # 예전에는 settled 만 검사해서 **그 경우가 검증 블록에 아예 못 들어왔고**, 결과적으로
        # 매도 손실의 가장 대표적인 경로(BUG-02 가 정의한 바로 그 상황)가 유출 계측에서 빠졌다.
        if (completed.status in ("settled", "partial") and live and self._client is not None
                and self._usdc_mint is not None):
            expected_inc = to_base_units(quote.total_usdc, CFG.usdc_decimals)
            if not baseline_ok:
                # 주식은 이미 나갔는데 대금 도착을 '확인할 수 없다'. 안전 지표는 낙관적으로
                # 세지 않는다 — 확인 못 한 것은 도착하지 않은 것으로 계상한다(보수적).
                completed.status = "partial"
                self._record_leak(quote.total_usdc)
                self.bus.emit(ev.GUARD_PENDING, {
                    "side": "sell", "order_id": required.order_id, "ok": False,
                    "code": "GUARD_BASELINE_UNREAD",
                    "detail": "정산 전 USDC 잔액 기준선 조회 실패 — 대금 도착 검증 불가로 매도 보류",
                    "where": "engine.py:_sell_cycle", "expected": "", "actual": "",
                    "leak_usdc": str(quote.total_usdc),
                })
            else:
                async def _reader():
                    return await x.get_token_balance_base(
                        self._client, self._trading_kp.pubkey(), self._usdc_mint)

                delivery = await self._guard.check_delivery(
                    completed, signed_order_id=required.order_id, balance_reader=_reader,
                    before_units=before_usdc, expected_increase_units=expected_inc,
                    retries=2, retry_delay_sec=1.0)
                if not delivery.ok:
                    # 주식은 나갔는데 대금이 안 왔다 — 회수 경로 없음 → KPI '유출' 에 가산.
                    completed.status = "partial"
                    self._record_leak(quote.total_usdc)
                    self.bus.emit(ev.GUARD_PENDING, {
                        "side": "sell", "order_id": required.order_id,
                        "leak_usdc": str(quote.total_usdc), **delivery.as_event(),
                    })

        agent.on_sale_completed(completed, symbol, qty, price, quote.total_usdc)

        realized = None
        if completed.status == "settled":
            # 수령액(수수료 차감)과 실효 평단(수수료 포함) 기준 → 순손익
            realized = (quote.total_usdc - avg_before * qty).quantize(Decimal("0.01"))
            self.realized_pnl += realized
            self.total_fees += quote.fee_usdc

        self._complete_trade("sell", symbol, qty, quote, completed, decision, realized)

        # 대금 미확인(partial)이면 세션을 정지한다 — 반복 매도로 손실이 누적되지 않게(매수측 미러)
        if completed.status == "partial" and self.trading_enabled:
            self.pause(actor="guard",
                       reason=f"{symbol} 매도 — 주식은 나갔는데 USDC 도착이 확인되지 않았습니다"
                              f"(수량 {qty} · {quote.total_usdc} USDC). "
                              "같은 손실이 반복되지 않게 멈춥니다")

    def _count_guard_block(self, code: str, *, buy: bool) -> None:
        """가드 차단 1건을 첫 화면 KPI 에 계상한다 (매수·매도 공통).

        '검사 불가'(GUARD_LLM_UNVERIFIED)를 그 안의 부분집합으로 따로 센다. 결제를 막은
        것은 맞지만 **악성 청구서를 판정으로 막은 것과는 전혀 다른 사건**이다 — Gemini
        쿼터가 소진되거나 응답이 실패해서 의미 대조를 못 했고, 그래서 매수를 보류한 것이다
        (설계상 LLM 은 차단만 가능하고 통과 권한이 없다 → invoice_semantics 참조).
        이 제품은 계층을 합치지 않는 것이 미덕인데(red_team 은 blocked_by_policy 로 이미
        분리해 표기한다) 대표 지표가 둘을 같은 칸에 더하면 그 미덕을 스스로 어긴다.

        총계(blocked)는 그대로 두고 부분집합으로만 방출한다 — KPI 칸 수를 늘리지 않기 위해서다.
        """
        self.guard_block_count += 1
        if buy:
            self._guard_blocked_buy += 1
        if code == GUARD_LLM_UNVERIFIED:
            self.guard_unverified_count += 1

    def _record_leak(self, amount: Decimal) -> None:
        """서명 후 온체인으로 나갔는데 대가 도착을 확인하지 못한 금액을 KPI 에 가산한다.

        첫 화면 KPI 의 '유출'이 이 값이다. **예전에는 초기화·리셋·읽기 3곳만 있고 증가시키는
        코드가 저장소에 한 줄도 없어서, 실제로 자산이 사라져도 화면은 계속 0.00 을 표시했다**
        (측정치가 아니라 상수). 소스를 여는 사람에게는 그 자체가 주장을 무너뜨리는 지점이라
        실제 가산으로 배선한다 — 못 막은 것도 숫자로 표시하는 것이 이 KPI 의 신뢰다.

        여기에 오는 건 '막지 못한 것'뿐이다. 서명 전 차단(check_demand·check_stock_transfer)은
        트랜잭션 자체가 만들어지지 않으므로 이 경로를 타지 않는다 — 그 계층의 유출이 0 인 것은
        측정 결과가 아니라 구조적 사실이고, 둘을 같은 숫자로 합치면 안 된다.

        회수 경로는 없다(환불·에스크로 미구현). 그래서 이 값이 오르면 호출측이 세션을 멈춘다.
        드라이런은 check_delivery 자체가 라이브에서만 돌므로 이 경로에 오지 않는다.

        ⚠ 알려진 한계 (2026-07-27 적대 검증에서 실증으로 적출 — 아직 안 닫힘).
        "이 숫자가 곧 실제 유출"이라고 단정하지 말 것:
          1) 전송 뒤 예외로 빠지면 계상되지 않는다. submit_and_confirm 은 트랜잭션을
             제출한 뒤 confirm 단계에서 예외를 던질 수 있는데(rpc_retry 소진),
             _buy_cycle 의 try 에는 except 가 없고 _sell_cycle 의 settle_sale 은 try 밖이다.
             그 경우 유출도 안 세고 **세션 정지도 안 걸린다**(틱 루프는 계속 돈다).
          2) HTTP 402 원격 경로는 상대가 보낸 status 문자열을 그대로 신뢰한다
             (x402_http.parse_settlement). 원격이 "ok"·"pending" 처럼 우리가 안 보는 값을
             돌려주면 검증도 정지도 건너뛴다 — 계측 스위치를 상대가 쥔다.
          3) 매도 계상액이 quote.total_usdc(= subtotal − fee)라 나간 주식의 시가보다
             수수료만큼 낮다(과소 계상).
          4) '확인된 손실'과 '조회 실패로 확인 불가'를 한 숫자에 합친다. RPC 가 한 번
             흔들리면 실제로는 도착한 건이 유출로 찍힐 수 있다(보수적이지만 부정확).
          5) 이 값은 인메모리 전용이라 artifacts/tx 아카이브·세션 요약에 남지 않는다.
          6) 유출 계상 경로에서도 finally 가 auth.release() 를 불러 AP2 잔여 한도가
             유출액만큼 부풀려진다 — 정지를 해제하면 실제 온체인 지출이 한도를 넘을 수 있다.
        """
        if amount > 0:
            self.guard_leak_usdc += amount

    def _complete_trade(self, side, symbol, qty, quote, completed, decision, realized=None) -> None:
        self.bus.emit(ev.X402_COMPLETED, {
            "side": side, "order_id": completed.order_id, "status": completed.status,
            "confirmed": completed.confirmed,
            "payment_tx": completed.tx_signature,
            "delivery_tx": completed.delivery_tx_signature,
        })
        row = {
            "ts": _now(),
            "order_id": completed.order_id,
            "side": side,
            "decision_source": decision.source,
            "decision_reason": decision.reason,
            "symbol": symbol,
            "quantity": str(qty),
            "price_usdc": str(quote.price_usdc),
            "subtotal_usdc": str(quote.subtotal_usdc),
            "fee_usdc": str(quote.fee_usdc),
            "fee_bps": quote.fee_bps,
            "total_usdc": str(quote.total_usdc),
            "status": completed.status,
            "confirmed": completed.confirmed,
            "payment_tx": completed.tx_signature,
            "delivery_tx": completed.delivery_tx_signature,
            "explorer_payment": explorer_tx_url(completed.tx_signature) if completed.confirmed else "",
            "explorer_delivery": explorer_tx_url(completed.delivery_tx_signature)
                                 if completed.delivery_tx_signature else "",
            "realized_pnl_usdc": str(realized) if realized is not None else "",
        }
        self.trades.append(row)
        self.bus.emit(ev.TRADE, row)
        self._persist(self.store.save_trade(self.session_id, row))

    # ---------- 세션 마무리 (라이브: 교차검증 + 아카이브) ----------

    async def _finalize(self) -> None:
        live = self.mode == "live"
        archive_path = ""
        cross: Optional[Dict[str, Any]] = None
        try:
            if live and self._client is not None and self._snap_before is not None:
                snap_after = await snapshot_balances(
                    self._client, self._trading_kp.pubkey(), self._broker_kp.pubkey(),
                    self._usdc_mint, self._stock_mint,
                    user_pk=self._user_kp.pubkey() if self._user_kp else None)
                self._snap_last = snap_after
                self.bus.emit(ev.BALANCES, {"stage": "after", "balances": snap_after})
                archive_path, cross = self._archive(snap_after)
        except Exception as e:
            self.bus.emit(ev.ERROR, {"message": f"세션 마무리 실패: {type(e).__name__}: {e}"})
        finally:
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:
                    pass
                self._client = None
            if self._broker_http is not None:
                try:
                    await self._broker_http.aclose()   # G5 HTTP 연결 정리
                except Exception:
                    pass
                self._broker_http = None
            self.last_archive_path = archive_path
            # 긴급정지는 세션 단위 상태 — 세션이 끝나면 해제한다.
            # (해제하지 않으면 대기 화면·다음 접속에도 "🛑 매매 정지됨" 배지가 남는다)
            was_paused = not self.trading_enabled
            self.trading_enabled = True
            self.pause_info = None
            self.bus.emit(ev.ENGINE_STOPPED, {
                "trades": len(self.trades), "ticks": self.tick,
                "archive": archive_path, "cross_check": cross,
                "was_paused": was_paused,
            })
            # B2: 세션 종료 시 자동 브리핑 — 백그라운드로 생성해 stop 응답을 막지 않는다
            if self.trades or self.decisions:
                asyncio.create_task(self._auto_briefing("session-end"))
            # 세션 요약 영속화 (dry 포함) — 재시작·재배포 후에도 /api/history 로 남는다.
            # 세션 문서는 핵심 증빙이라 fire-and-forget 이 아니라 짧게 기다려 확정한다.
            if self.store.enabled and (self.tick or self.trades or self.decisions):
                try:
                    await asyncio.wait_for(
                        self.store.save_session(
                            self.session_id, self._session_summary(archive_path, cross)),
                        timeout=5)
                except Exception as e:
                    self.store.last_error = f"{type(e).__name__}: {e}"
                    self.bus.emit(ev.ERROR, {
                        "message": f"세션 영속 저장 실패: {self.store.last_error}"})
            # ★ BUG-09: 엔진 소유권을 놓는 것은 반드시 마지막이다.
            # `status == "idle"` 이 다음 세션의 유일한 관문(start():`self.status != "idle"`)이라,
            # 이 두 줄 뒤에서 전역 상태를 만지면 **이미 시작된 다음 세션**을 덮어쓴다.
            # 예전 순서는 status 를 먼저 idle 로 내리고 영속 저장을 await 했다 — 그 5초 창에
            # 새 세션이 시작되면 끝난 세션의 마무리 코드가 그 세션의 긴급정지를 해제하고
            # (trading_enabled=True·pause_info=None) 남의 실적으로 ENGINE_STOPPED 를 쐈다.
            # stop() 은 어차피 `await self._task` 로 이 함수가 끝날 때까지 기다리므로
            # 응답이 느려지지 않는다. 재생 소진으로 자동 종료될 때만 idle 표시가 저장 시간만큼
            # 늦어지는데, 그동안 세션은 실제로 아직 안 끝났으므로 그쪽이 정확하다.
            self._task = None
            self.status = "idle"

    def _archive(self, snap_after: dict) -> tuple[str, Dict[str, Any]]:
        """run_demo 와 동일한 순변화 교차검증 + artifacts/tx/ 증빙 아카이브."""
        confirmed = [t for t in self.trades if t["confirmed"]]
        buys = [t for t in confirmed if t["side"] == "buy"]
        sells = [t for t in confirmed if t["side"] == "sell"]
        net_spent = (sum((Decimal(t["total_usdc"]) for t in buys), Decimal(0))
                     - sum((Decimal(t["total_usdc"]) for t in sells), Decimal(0)))
        net_qty = (sum((Decimal(t["quantity"]) for t in buys), Decimal(0))
                   - sum((Decimal(t["quantity"]) for t in sells), Decimal(0)))
        usdc_out = usdc_net_out(self._snap_before, snap_after)
        stock_in = Decimal(snap_after["trading"]["stock"]) - Decimal(self._snap_before["trading"]["stock"])
        cross = {
            "usdc_net_out_onchain": str(usdc_out), "usdc_net_out_expected": str(net_spent),
            "usdc_ok": usdc_out == net_spent,
            "stock_net_in_onchain": str(stock_in), "stock_net_in_expected": str(net_qty),
            "stock_ok": stock_in == net_qty,
            # 어느 지갑을 합산한 값인지 — 나중에 이 아카이브를 읽는 사람이 알아야 한다.
            "usdc_wallets": [w for w in USDC_OUT_WALLETS if w in self._snap_before],
        }

        os.makedirs(os.path.join("artifacts", "tx"), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        path = os.path.join("artifacts", "tx", f"{ts}_{CFG.network}_web_session.json")
        archive = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "web-dashboard",
            "network": CFG.network,
            "rpc_url": CFG.rpc_url,
            "wallets": {"user": str(self._user_kp.pubkey()), "trading": str(self._trading_kp.pubkey()), "broker": str(self._broker_kp.pubkey())},
            "mints": {"usdc": str(self._usdc_mint), "stock": str(self._stock_mint),
                      "stock_symbol": CFG.stock_symbol},
            "strategy": getattr(self, "strategy_info", None),
            "brain": self.brain_label,   # 판단 두뇌 (Gemini 모델명 / 규칙)
            # 축② 증빙: 이 세션의 온체인 거래가 어느 판단에서 나왔는가
            # (gemini / rule-gate / rule-fallback / rule). 거래 행마다 decision_source 도 있다.
            "ai": self._ai_stats(),
            "feed": self.feed_info,   # 시세 출처 (mock/replay·구간) — 재현 조건 증빙
            "mandate": {
                "user_pubkey": self._mandate.user_pubkey,   # 위임자(서명자) — 에이전트와 분리 증빙
                "budget_total_usdc": str(self._mandate.budget_total_usdc),
                "per_trade_max_usdc": str(self._mandate.per_trade_max_usdc),
                "signature": self._mandate.signature,
            },
            "mandate_history": self.mandate_history,  # A3 세션 중 한도 변경 이력
            "broker_fee": {  # A8: 수수료 합계 = 브로커 수익 (수익모델 증빙)
                "fee_bps": CFG.broker_fee_bps,
                "total_fees_usdc": str(self.total_fees),
            },
            # 402 Guard KPI — 화면(state.guard)과 같은 값을 파일에도 남긴다.
            # 예전에는 아카이브에 이 블록이 없어서, 세션이 끝난 뒤 tx 증빙 파일만으로는
            # '유출 0' 을 사후에 증명할 수 없었다(_record_leak 독스트링의 알려진 한계 5).
            # 심사·발표에서 화면 스크린샷 대신 파일을 근거로 쓸 수 있어야 한다.
            "guard": {
                "attempts": self._guard_checked,
                "blocked": self.guard_block_count,
                "blocked_buy": self._guard_blocked_buy,
                "blocked_sell": self.guard_block_count - self._guard_blocked_buy,
                "blocked_unverified": self.guard_unverified_count,
                "ap2_rejected": self.reject_count,
                "leak_usdc": str(self.guard_leak_usdc),
            },
            "balances_before": self._snap_before,
            "balances_after": snap_after,
            "trades": self.trades,
            "decisions": self.decisions,
            "cross_check": cross,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2, default=str)
        return path, cross

    def _session_summary(self, archive_path: str, cross: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Firestore sessions 문서 — artifacts/tx 아카이브의 DB판 (dry 세션 포함).
        판단 로그는 문서 1MB 한도 보호를 위해 최근 300건까지만 담는다."""
        remaining = self._total_remaining()
        return_pct = self._display_return_pct(self._valuation(remaining))
        positions = [{"symbol": s, "quantity": str(self.agents[s].position.quantity),
                      "avg_price_usdc": str(self.agents[s].position.avg_price_usdc)}
                     for s in self.symbols]
        total_qty = sum((self.agents[s].position.quantity for s in self.symbols), Decimal(0))
        focus_pos = self.agents[self._focus].position if self.symbols else None
        return jsonable({
            "session_id": self.session_id,
            "mode": self.mode, "network": CFG.network,
            "symbol": "·".join(self.symbols) if self.symbols else CFG.stock_symbol,
            "symbols": list(self.symbols),
            "started_at": self.started_at, "ended_at": _now(),
            "ticks": self.tick, "brain": self.brain_label,
            "strategy": self.strategy_info, "feed": self.feed_info,
            "budget_total_usdc": str(self.budget_total),
            "per_trade_max_usdc": str(self.per_trade_max),  # 사용자 기본 한도(추세추종 올인은 strategy.all_in 로 표시)
            "realized_pnl_usdc": str(self.realized_pnl),
            "return_pct": str(return_pct),
            "cum_buy_usdc": str(self.cum_buy_usdc),
            "total_fees_usdc": str(self.total_fees),
            "fee_bps": CFG.broker_fee_bps,
            "reject_count": self.reject_count, "pause_count": self.pause_count,
            "guard_block_count": self.guard_block_count,   # 402 Guard check_demand 차단
            "ai": self._ai_stats(),   # 판단 출처 계측 (축② AI 관여 증빙)
            "position_qty": str(total_qty),
            "position_avg_usdc": str(focus_pos.avg_price_usdc) if focus_pos else "0",
            "positions": positions,   # 종목별 최종 보유 (멀티)
            "trade_count": len(self.trades), "decision_count": len(self.decisions),
            "trades": self.trades,
            "decisions": self.decisions[-300:],
            "mandate_history": self.mandate_history,
            "archive_path": archive_path,       # 라이브 세션의 로컬 증빙 파일 경로
            "cross_check": cross,               # 라이브: 온체인 순변화 교차검증 결과
        })

    # ---------- 상태 스냅샷 (GET /api/state) ----------

    def _symbol_last_price(self, sym: str) -> Decimal:
        ph = self._price_history.get(sym) or []
        return Decimal(ph[-1]["price"]) if ph else Decimal(0)

    def _position_value(self, pos, price: Decimal) -> Dict[str, Decimal]:
        """한 종목의 평가 지표(현금 제외). 지금 전량 매도 시 수령액(수수료 차감) − 실효 평단×수량."""
        qty = pos.quantity if pos else Decimal(0)
        fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
        gross = (qty * price).quantize(Decimal("0.01"))
        net = (qty * price * (1 - fee_rate)).quantize(Decimal("0.01"))
        cost = ((pos.avg_price_usdc if pos else Decimal(0)) * qty).quantize(Decimal("0.01"))
        return {"gross": gross, "net": net, "cost": cost, "unrealized": net - cost}

    @staticmethod
    def _budget_slices(symbols: List[str], budget_total: Decimal) -> Dict[str, Decimal]:
        """추세추종 멀티의 종목별 예산 몫 — 예산/N(센트 반올림), 나머지는 마지막 종목이 받는다.
        세션 시작 시의 초기 슬라이스와 같은 식이다(start 의 slice_budget)."""
        n = len(symbols)
        unit = (budget_total / n).quantize(Decimal("0.01"))
        return {sym: (budget_total - unit * (n - 1) if i == n - 1 else unit)
                for i, sym in enumerate(symbols)}

    def _session_auths(self) -> List[PaymentAuthorizer]:
        """세션에 실재하는 '구별되는' authorizer 목록. 공유 예산(단일·조건형/적립형 멀티)은
        전 종목이 같은 객체 1개를 참조하므로 중복 제거하면 1개, 추세추종 멀티는 종목별 N개."""
        seen: set = set()
        out: List[PaymentAuthorizer] = []
        for ag in self.agents.values():
            if id(ag.auth) not in seen:
                seen.add(id(ag.auth))
                out.append(ag.auth)
        return out

    def _total_remaining(self) -> Decimal:
        """세션 잔여 예산 — 공유는 그 auth 의 잔여, 추세추종 멀티는 종목별 슬라이스 합산.

        음수 몫은 0 으로 바닥 처리한다(bug-dept BUG-07 방어) — 한 종목의 마이너스가 다른
        종목의 양수와 상계되면 화면의 '가용 현금'·총자산이 실제로 쓸 수 있는 돈보다 커진다.
        각 authorize 는 제 슬라이스만 보므로 음수 몫에서는 어차피 한 푼도 못 쓴다."""
        auths = self._session_auths()
        if auths:
            return sum((max(Decimal(0), a.remaining_usdc) for a in auths), Decimal(0))
        return self._auth.remaining_usdc if self._auth else self.budget_total

    def _total_spent(self) -> Decimal:
        """세션 사용액 — 공유는 그 auth 의 사용액, 추세추종 멀티는 종목별 합산."""
        auths = self._session_auths()
        if auths:
            return sum((a.spent_usdc for a in auths), Decimal(0))
        return self._auth.spent_usdc if self._auth else Decimal(0)

    def _symbol_valuation(self, sym: str) -> Dict[str, Any]:
        """종목 1개의 평가손익 블록 (현금 없음 — 현금은 세션 공유라 aggregate 에만)."""
        agent = self.agents.get(sym)
        price = self._symbol_last_price(sym)
        pv = self._position_value(agent.position if agent else None, price)
        pct = (pv["unrealized"] / pv["cost"] * 100).quantize(Decimal("0.01")) if pv["cost"] > 0 else Decimal(0)
        return {
            "market_price_usdc": str(price),
            "position_value_usdc": str(pv["gross"]),
            "position_net_value_usdc": str(pv["net"]),
            "cost_basis_usdc": str(pv["cost"]),
            "unrealized_pnl_usdc": str(pv["unrealized"]),
            "unrealized_pct": str(pct),
        }

    def _valuation(self, remaining: Decimal) -> Dict[str, Any]:
        """세션 전체(전 종목 합산) 평가손익 · 총자산.

        총자산 = 가용 현금(공유 AP2 잔여 예산) + 전 종목 보유 주식의 매도 예상 수령액(수수료 차감).
        N=1 이면 단일 종목 평가와 동일하다(하위호환). market_price 는 멀티에서 단일가가 없어
        비워 두고, 종목별 가격은 per_symbol 을 참조한다."""
        gross = net = cost = unreal = Decimal(0)
        for sym in self.symbols:
            pv = self._position_value(self.agents[sym].position, self._symbol_last_price(sym))
            gross += pv["gross"]; net += pv["net"]; cost += pv["cost"]; unreal += pv["unrealized"]
        pct = (unreal / cost * 100).quantize(Decimal("0.01")) if cost > 0 else Decimal(0)
        single = len(self.symbols) == 1
        return {
            "market_price_usdc": str(self._symbol_last_price(self._focus)) if single else "",
            "position_value_usdc": str(gross),
            "position_net_value_usdc": str(net),     # 지금 전량 매도 시 수령 예상액(전 종목)
            "cost_basis_usdc": str(cost),
            "unrealized_pnl_usdc": str(unreal),
            "unrealized_pct": str(pct),
            "cash_usdc": str(remaining),             # 가용 현금 = 공유 AP2 잔여 예산
            "total_asset_usdc": str(remaining + net),
            "onchain_usdc": (self._snap_last["trading"]["usdc"]
                             if self._snap_last else None),  # 라이브(N=1): 최근 스냅샷
        }

    def _symbol_price_block(self, sym: str) -> Dict[str, Any]:
        """종목 1개의 시세 블록 (차트·등락 표시용) — state_snapshot per_symbol 및 top-level 공용."""
        ph = self._price_history.get(sym) or []
        candles = self._candles.get(sym) or []
        change_ref = self._change_ref.get(sym)
        return {
            "current": ph[-1]["price"] if ph else None,
            "session_open": ph[0]["price"] if ph else None,
            "prev_close": str(change_ref) if change_ref is not None else None,
            "change_basis": ("prev-close" if self.feed_info.get("type") == "replay"
                             else "session-open"),
            "history": ph[-60:],
            "candles": candles[-MAX_CANDLES:],
            "ticks_per_candle": TICKS_PER_CANDLE,
        }

    def _is_trend(self) -> bool:
        return (getattr(self, "strategy_info", None) or {}).get("type") == "trend"

    def _display_return_pct(self, valuation: Dict[str, Any]) -> Decimal:
        """세션 수익률(%). 추세추종은 올인/올아웃(복리)이라 '초기자본 대비 총자산'이 정직한
        수익률이고(realized/cum_buy 는 분할매수 기준이라 올인엔 왜곡), 조건형/적립형은 기존대로
        실현손익/누적매수액을 쓴다. valuation 은 _valuation() 결과."""
        if self._is_trend() and self.budget_total > 0:
            return ((Decimal(valuation["total_asset_usdc"]) - self.budget_total)
                    / self.budget_total * 100).quantize(Decimal("0.01"))
        if self.cum_buy_usdc > 0:
            return (self.realized_pnl / self.cum_buy_usdc * 100).quantize(Decimal("0.01"))
        return Decimal(0)

    def state_snapshot(self) -> Dict[str, Any]:
        focus_agent = self.agents.get(self._focus)
        pos = focus_agent.position if focus_agent else None
        spent = self._total_spent()
        remaining = self._total_remaining()
        valuation = self._valuation(remaining)   # 전 종목 합산 (총자산·평가손익)
        is_trend = self._is_trend()
        return_pct = self._display_return_pct(valuation)
        # top-level price/position 은 포커스(첫) 종목 — N=1 이면 기존과 동일. 멀티는 per_symbol 참조.
        price_block = self._symbol_price_block(self._focus)
        price_block["feed"] = self.feed_info
        per_symbol = {
            s: {
                "price": self._symbol_price_block(s),
                "position": {"quantity": str(self.agents[s].position.quantity),
                             "avg_price_usdc": str(self.agents[s].position.avg_price_usdc)},
                "valuation": self._symbol_valuation(s),
                "feed": self._feeds_info.get(s, {}),
            } for s in self.symbols
        }
        return {
            "engine": {
                "status": self.status, "mode": self.mode, "network": CFG.network,
                "tick": self.tick, "tick_interval_sec": self.tick_interval,
                "started_at": self.started_at, "brain": self.brain_label,
                "session_id": self.session_id, "symbols": list(self.symbols),
            },
            "persistence": {  # Firestore 영속화 상태 (Cloud Run 재시작 대비)
                "enabled": self.store.enabled,
                "backend": self.store.backend,
                "detail": self.store.detail,
                "last_error": self.store.last_error,
            },
            "trading_enabled": self.trading_enabled,
            "pause_info": self.pause_info,
            "symbol": self._focus,                    # 포커스(대표) 종목 — 멀티는 symbols/per_symbol 참조
            "symbols": list(self.symbols),            # 세션 종목 목록
            "replay_available": os.path.exists(self.default_replay_path()),
            "price": price_block,                     # 포커스 종목 시세 (멀티: per_symbol[sym].price)
            "per_symbol": per_symbol,                 # 종목별 시세·포지션·평가·피드
            "position": {                             # 포커스 종목 포지션
                "quantity": str(pos.quantity) if pos else "0",
                "avg_price_usdc": str(pos.avg_price_usdc) if pos else "0",
            },
            "budget": {
                "total_usdc": str(self.budget_total),
                "spent_usdc": str(spent),
                "remaining_usdc": str(remaining),
                # 건별 한도(숫자)는 항상 사용자 '기본값'을 노출한다 — A3 한도변경 폼의 사전채움에
                # 쓰이므로 올인 캡(max_budget)을 노출하면 per_trade>budget 검증에 걸려 제출이 막힌다.
                # 추세추종의 '올인'은 숫자가 아니라 all_in 플래그로 표시한다(mandate 는 _session_per_trade).
                "per_trade_max_usdc": str(self.per_trade_max),
                "all_in": is_trend,
            },
            "pnl": {  # 수익률 — 조건형: 실현손익/누적매수 · 추세추종: 초기자본 대비 총자산
                "realized_usdc": str(self.realized_pnl),
                "return_pct": str(return_pct),
                "cum_buy_usdc": str(self.cum_buy_usdc),
                "basis": "initial-capital" if is_trend else "cum-buy",
            },
            "valuation": valuation,  # 평가손익(미실현) · 총자산
            "fees": {  # A8 수수료 투명화 — 브로커 수익모델 증명
                "fee_bps": CFG.broker_fee_bps,
                "cum_fee_usdc": str(self.total_fees),
            },
            "rules": self._rules_snapshot(),
            "strategy": getattr(self, "strategy_info", None) or {"type": "condition"},
            "last_briefing": self.last_briefing,  # B2 최근 브리핑 (새로고침 복원용)
            "counts": {"trades": len(self.trades), "decisions": len(self.decisions)},
            "guard": {  # 첫 화면 KPI — 시도·차단·유출·오탐 (수익률이 아니라 지출 통제)
                # 분모는 '가드를 태운 청구서 전체'다(_guard_checked). 예전에는
                # guard_block_count + 매수 체결 수 로 계산해서 **모집단이 섞여 있었다** —
                # 매도 차단은 분자에 들어가는데 매도 성공은 분모에 없었고,
                # GUARD_BASELINE_UNREAD 로 중단된 매수는 어느 쪽에도 없었다. 오차 방향이
                # 하필 제품에 유리했다(분모 과소 → 차단율 과대, bug-dept BUG-08).
                # 양 레그 대칭이 이 제품의 차별점인데 대표 지표가 매도를 비대칭으로 세면
                # 그 자체가 반증 자료가 된다.
                "attempts": self._guard_checked,
                "blocked": self.guard_block_count,
                "blocked_buy": self._guard_blocked_buy,     # 레그별 분해 — 대칭이 숫자로 보이게
                "blocked_sell": self.guard_block_count - self._guard_blocked_buy,
                # blocked 의 부분집합: 판정으로 막은 게 아니라 '검사를 못 해서' 보류한 건수.
                # 악성 청구서 차단과 같은 칸으로 합쳐 보이면 대표 지표가 정직하지 않다.
                "blocked_unverified": self.guard_unverified_count,
                "ap2_rejected": self.reject_count,
                "leak_usdc": str(self.guard_leak_usdc),
            },
            # 축② 판단 출처 계측 — 이 세션에서 AI 가 실제로 몇 건을 판단했고
            # 규칙 게이트가 몇 건을 되돌렸는지. (프론트 계약에 새로 추가되는 필드)
            "ai": self._ai_stats(),
            "wallets": {
                "user": str(self._user_kp.pubkey()) if self._user_kp else "",
                "trading": str(self._trading_kp.pubkey()) if self._trading_kp else "",
                "broker": str(self._broker_kp.pubkey()) if self._broker_kp else "",
            },
            "balances": self._snap_last,
        }
