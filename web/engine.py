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
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import CFG, to_base_units
from market.price_feed import Bar, MockPriceFeed, PriceFeed, ReplayPriceFeed
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from payments.guard import Guard, GuardError
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy, Decision
from run_demo import _load_or_new, _load_or_create_user_key, explorer_tx_url, snapshot_balances
from web import events as ev
from web.briefing import generate_briefing_text
from web.events import EventBus
from web.store import BaseStore, jsonable

# 데모 규칙 기본값 — 지표 기준 (매수: MA5 −%, 매도: 평단 +%). run_demo.py 와 동일.
DEFAULT_RULES = {"buy_dip_pct": "2", "take_profit_pct": "3", "spend_per_trade": "30"}

MAX_DECISIONS = 500   # A6 타임라인 메모리 상한
MAX_PRICE_POINTS = 120

# 캔들차트: 목 시세는 틱당 단일 가격이라 N틱을 묶어 하나의 캔들(OHLC)로 집계한다.
# 2틱(기본 8초 틱 → 캔들 16초)이면 데모 길이에서 이동평균선이 눈에 보인다.
TICKS_PER_CANDLE = 2
MAX_CANDLES = 90


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
        self.mandate_history: List[Dict[str, Any]] = []  # 세션 중 변경 이력 (아카이브 포함)

        # 세션 상태
        self.tick = 0
        self.decisions: List[Dict[str, Any]] = []   # A6 판단 타임라인
        self.trades: List[Dict[str, Any]] = []      # A5 거래 내역
        self.price_history: List[Dict[str, str]] = []
        self.candles: List[Dict[str, Any]] = []     # 캔들차트용 OHLC (N틱 = 1캔들)
        self.realized_pnl = Decimal(0)              # A7 실현손익
        self.cum_buy_usdc = Decimal(0)              # A7 수익률 분모(누적 매수금액)
        self.total_fees = Decimal(0)                # A8 누적 브로커 수수료 (수익모델 증명)
        self.started_at: str = ""
        self.brain_label: str = ""
        self.strategy_info: Dict[str, Any] = {"type": "condition"}  # B7 세션 전략
        self.feed_info: Dict[str, Any] = {"type": "", "label": ""}  # 시세 피드 (mock/replay)
        self.reject_count = 0                       # B2 브리핑용: AP2 거부 횟수
        self.pause_count = 0                        # B2 브리핑용: 긴급정지 횟수
        self.guard_block_count = 0                  # 402 Guard check_demand 차단 횟수 (첫 화면 KPI)
        self.guard_leak_usdc = Decimal(0)           # 가드 통과 후 유출된 USDC (정상 0.00)
        self.last_briefing: Optional[Dict[str, Any]] = None  # B2 최근 브리핑
        self._last_daily_briefing_date = ""         # B2 장 마감 자동 생성 중복 방지

        # 세션 구성물 (start 에서 생성)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._client = None
        self._trading: Optional[TradingAgent] = None
        self._broker: Optional[BrokerAgent] = None
        self._feed: Optional[PriceFeed] = None
        self._prev_close: Optional[Decimal] = None   # 직전 봉 종가 (등락 표시 기준)
        self._change_ref: Optional[Decimal] = None   # 마지막 틱에서 쓴 등락 기준값
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

    def _build_feed(self, feed_cfg: Optional[Dict[str, Any]]):
        """세션 피드 구성 — (feed, feed_info). 실패는 EngineError 로 사용자에게 안내."""
        fcfg = feed_cfg or {}
        ftype = fcfg.get("type") or CFG.price_feed
        if ftype not in ("mock", "replay"):
            raise EngineError("feed.type 은 'mock' 또는 'replay' 여야 합니다.")
        if ftype == "mock":
            return MockPriceFeed(), {"type": "mock", "label": "목 시세 (8스텝 데모 패턴)"}
        # 경로 주입 차단: API 로는 심볼만 받고(정규식 검증) 경로는 서버가 조립한다.
        # 임의 CSV 경로를 받으면 컨테이너의 아무 파일이나 열게 되고, 파싱 오류 메시지에
        # 파일 내용이 실려 400 응답으로 새어나간다. 테스트용 직접 지정은 .env REPLAY_FILE 만.
        if fcfg.get("symbol"):
            sym = str(fcfg["symbol"]).upper()
            if not re.fullmatch(r"[A-Z]{1,5}", sym):
                raise EngineError("종목 코드는 영문 대문자 1~5자여야 합니다.")
            path = os.path.join("data", "market", f"{sym}_daily.csv")
            market_dir = os.path.realpath(os.path.join("data", "market"))
            if os.path.commonpath([os.path.realpath(path), market_dir]) != market_dir:
                raise EngineError("허용되지 않은 시세 파일 경로입니다.")
        else:
            path = self.default_replay_path()
        try:
            feed = ReplayPriceFeed(
                path,
                start=str(fcfg.get("start") or CFG.replay_start),
                end=str(fcfg.get("end") or CFG.replay_end),
                warmup=CFG.replay_warmup,
            )
        except (FileNotFoundError, ValueError) as e:
            raise EngineError(f"실데이터 재생 준비 실패 — {e}")
        info = {
            "type": "replay",
            "label": feed.source_label,
            "file": path,
            "source": "Alpha Vantage 일봉 (fetch_market_data.py)",
            "bars_total": feed.total_bars,
            "warmup_bars": len(feed.warmup_bars),
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
                    k: b[k] for k in ("ts", "trigger", "source", "text", "archive")
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
                    feed_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

        # B7 전략 선택 — condition(조건형, 현행) / dca(적립형, N틱마다 정액 매수)
        scfg = strategy_cfg or {}
        strat_type = scfg.get("type", "condition")
        if strat_type not in ("condition", "dca"):
            raise EngineError("strategy.type 은 'condition' 또는 'dca' 여야 합니다.")
        # Gemini 재량 모드 — strict(규칙 그대로) / trend(보류 재량). 조건형에서만 의미 있음.
        decision_mode = scfg.get("decision_mode") or "strict"
        if decision_mode not in ("strict", "trend"):
            raise EngineError("판단 모드는 'strict' 또는 'trend' 여야 합니다.")
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

        # 시세 피드 — UI 선택(mock/replay) 우선, 미지정 시 .env(PRICE_FEED)
        feed, feed_info = self._build_feed(feed_cfg)
        warmup_bars: List[Bar] = list(getattr(feed, "warmup_bars", []))

        usdc_mint = Pubkey.from_string(CFG.usdc_mint)
        if live and not CFG.stock_mint:
            raise EngineError("STOCK_MINT 미설정 — 먼저 scripts/setup_devnet.py 를 실행하세요.")
        stock_mint = Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint else None

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
        brain = None
        if strat_type == "dca":
            brain_label = f"적립식 스케줄 ({schedule_label} {dca_amount} USDC, Gemini 미사용)"
        elif CFG.gemini_api_key:
            try:
                from agents.gemini_decider import GeminiDecider
                brain = GeminiDecider(CFG.gemini_api_key, CFG.gemini_model, CFG.gemini_mode)
                brain_label = f"Gemini ({CFG.gemini_model}, {brain.mode} 모드, 실패 시 규칙 폴백)"
            except Exception as e:
                brain_label = f"규칙 기반 (Gemini 초기화 실패: {type(e).__name__})"
        else:
            brain_label = "규칙 기반 (GEMINI_API_KEY 미설정)"

        # AP2 mandate — 사용자가 설정한 한도에 서명 (예산=순투입 한도, A3 로 변경 가능)
        mandate = OpenPaymentMandate(
            user_pubkey=str(user_kp.pubkey()),          # 위임자(사용자) 키 — 에이전트 키와 분리
            allowed_asset=str(usdc_mint),
            budget_total_usdc=self.budget_total,
            per_trade_max_usdc=self.per_trade_max,
            allowed_symbols=[CFG.stock_symbol],
        ).sign(user_kp)                                 # 사용자가 한도에 서명(위임 근거)
        auth = PaymentAuthorizer(mandate, agent_kp=trading_kp)  # 에이전트는 한도 내 결제만 서명

        strategy = Strategy(
            buy_dip_pct=Decimal(DEFAULT_RULES["buy_dip_pct"]),
            take_profit_pct=Decimal(DEFAULT_RULES["take_profit_pct"]),
            spend_per_trade_usdc=Decimal(DEFAULT_RULES["spend_per_trade"]),
            decision_mode=decision_mode,
            ta_mode=ta_mode,
            mode=strat_type,
            dca_unit=dca_unit,
            dca_every_ticks=dca_every,
            dca_every_minutes=dca_minutes,
            dca_at_time=dca_at_time,
            dca_amount_usdc=dca_amount,
        )
        trading = TradingAgent(
            trading_kp, auth, strategy, CFG.usdc_decimals, CFG.network, brain=brain,
            fee_bps=CFG.broker_fee_bps)
        if warmup_bars:
            # 재생 피드 워밍업 — 첫 틱부터 MA/TA 지표가 계산되게 봉(OHLC)째로 주입
            trading.preload_bars(warmup_bars)
        broker = BrokerAgent(
            broker_kp, usdc_mint, CFG.usdc_decimals, stock_mint, CFG.stock_decimals, CFG.network,
            fee_bps=CFG.broker_fee_bps)
        # 402 Guard — 신뢰 수취인은 A2A 협의를 마친 브로커뿐. 구매 에이전트가 서명 직전 통과.
        guard = Guard(mandate, [str(broker_kp.pubkey())], CFG.usdc_decimals)
        trading.guard = guard

        client = None
        snap_before = None
        if live:
            try:
                client = await x.get_client(CFG.rpc_url)
                snap_before = await snapshot_balances(
                    client, trading_kp.pubkey(), broker_kp.pubkey(), usdc_mint, stock_mint)
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
        self.decisions = []
        self.trades = []
        self.price_history = []
        # 워밍업 봉은 차트 사전 이력으로 미리 그린다 (UI 에서 반투명 표시)
        self.candles = [{
            "ts": b.date, "open": str(b.open), "high": str(b.high),
            "low": str(b.low), "close": str(b.close),
            "count": TICKS_PER_CANDLE, "warmup": True,
        } for b in warmup_bars][-MAX_CANDLES:]
        self._prev_close = warmup_bars[-1].close if warmup_bars else None
        self._change_ref = None
        self.realized_pnl = Decimal(0)
        self.cum_buy_usdc = Decimal(0)
        self.total_fees = Decimal(0)
        self.mandate_history = []
        self.reject_count = 0
        self.pause_count = 0
        self.guard_block_count = 0
        self.guard_leak_usdc = Decimal(0)
        self.started_at = _now()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{mode}"
        self.brain_label = brain_label
        self.strategy_info = {
            "type": strat_type,
            "decision_mode": decision_mode,   # strict(엄격) / trend(추세 재량)
            "ta_mode": ta_mode,               # TA 보강(이동평균 배열·패턴 근거 판단)
            "dca_unit": dca_unit,
            "dca_every_ticks": dca_every,
            "dca_every_minutes": dca_minutes,
            "dca_at_time": dca_at_time,
            "dca_amount_usdc": str(dca_amount),
            "schedule_label": schedule_label,   # 사람이 읽는 주기 문구 (UI 공용)
        }
        self.feed_info = feed_info
        self.tick_interval = CFG.web_tick_interval_sec
        self.last_archive_path = ""
        self._usdc_mint, self._stock_mint = usdc_mint, stock_mint
        self._user_kp = user_kp
        self._trading_kp, self._broker_kp = trading_kp, broker_kp
        self._mandate, self._auth = mandate, auth
        self._guard = guard
        self._trading, self._broker, self._feed = trading, broker, feed
        self._client = client
        self._snap_before = self._snap_last = snap_before
        self._stop_event = asyncio.Event()

        self.bus.emit(ev.ENGINE_STARTED, {
            "mode": mode, "network": CFG.network, "symbol": CFG.stock_symbol,
            "brain": brain_label,
            "feed": feed_info,
            "budget_total_usdc": str(self.budget_total),
            "per_trade_max_usdc": str(self.per_trade_max),
            "fee_bps": CFG.broker_fee_bps,
            "rules": DEFAULT_RULES,
            "strategy": self.strategy_info,
            "mandate_verified": mandate.verify(),
            "wallets": {"user": str(user_kp.pubkey()), "trading": str(trading_kp.pubkey()), "broker": str(broker_kp.pubkey())},
            "tick_interval_sec": self.tick_interval,
        })
        if snap_before is not None:
            self.bus.emit(ev.BALANCES, {"stage": "before", "balances": snap_before})

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

    def pause(self, actor: str = "human") -> Dict[str, Any]:
        # 정지 상태는 세션(start~stop) 안에서만 의미가 있다 — 대기 중 정지는 거부한다
        # (버그: 대기 중 정지가 남아 다음 접속에도 "정지됨" 배지가 보였다)
        if self.status != "running":
            raise EngineError("실행 중인 세션이 없습니다 — 긴급정지는 세션 실행 중에만 가능합니다.")
        if self.trading_enabled:
            self.trading_enabled = False
            self.pause_info = {"actor": actor, "ts": _now()}
            self.pause_count += 1
            self.bus.emit(ev.TRADING_PAUSED, {"actor": actor})
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

        if self.status == "running":
            if self.trading_enabled:
                raise EngineError("실행 중에는 긴급정지 상태에서만 한도를 변경할 수 있습니다.")
            spent = self._auth.spent_usdc
            if budget_total < spent:
                raise EngineError(
                    f"새 예산({budget_total})이 이미 사용한 금액({spent})보다 작습니다.")
            new_mandate = OpenPaymentMandate(
                user_pubkey=str(self._user_kp.pubkey()),   # 위임자(사용자) 키로 재서명
                allowed_asset=str(self._usdc_mint),
                budget_total_usdc=budget_total,
                per_trade_max_usdc=per_trade_max,
                allowed_symbols=[CFG.stock_symbol],
            ).sign(self._user_kp)
            new_auth = PaymentAuthorizer(new_mandate, agent_kp=self._trading_kp)
            new_auth.spent_usdc = spent  # 사용액 이월
            self._mandate, self._auth = new_mandate, new_auth
            self._trading.auth = new_auth
            applied = "immediate"

        self.budget_total, self.per_trade_max = budget_total, per_trade_max
        rec = {
            "ts": _now(), "actor": actor, "applied": applied,
            "old": old,
            "new": {"budget_total_usdc": str(budget_total),
                    "per_trade_max_usdc": str(per_trade_max)},
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

    # ---------- B2 데일리 브리핑 ----------

    def _briefing_stats(self) -> Dict[str, Any]:
        """브리핑 근거 데이터 — 현재(실행 중) 또는 직전 세션의 집계."""
        settled = [t for t in self.trades if t["status"] == "settled"]
        buys = [t for t in settled if t["side"] == "buy"]
        sells = [t for t in settled if t["side"] == "sell"]
        pos = self._trading.position if self._trading else None
        return_pct = (
            (self.realized_pnl / self.cum_buy_usdc * 100).quantize(Decimal("0.01"))
            if self.cum_buy_usdc > 0 else Decimal(0))
        val = self._valuation(pos, self._auth.remaining_usdc if self._auth else self.budget_total)
        by_action: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for d in self.decisions:
            by_action[d["action"]] = by_action.get(d["action"], 0) + 1
            by_source[d["source"]] = by_source.get(d["source"], 0) + 1
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "session_started_at": self.started_at,
            "engine_status": self.status,
            "mode": self.mode, "network": CFG.network, "symbol": CFG.stock_symbol,
            "strategy": self.strategy_info,
            "ticks": self.tick,
            "buy_count": len(buys), "sell_count": len(sells),
            "buy_total_usdc": str(sum((Decimal(t["total_usdc"]) for t in buys), Decimal(0))),
            "sell_total_usdc": str(sum((Decimal(t["total_usdc"]) for t in sells), Decimal(0))),
            "realized_pnl_usdc": str(self.realized_pnl),
            "return_pct": str(return_pct),
            "unrealized_pnl_usdc": val["unrealized_pnl_usdc"],   # 평가손익(미실현)
            "position_value_usdc": val["position_net_value_usdc"],
            "total_asset_usdc": val["total_asset_usdc"],
            "budget_total_usdc": str(self.budget_total),
            "budget_remaining_usdc": str(self._auth.remaining_usdc if self._auth else self.budget_total),
            "cum_fee_usdc": str(self.total_fees),
            "ap2_reject_count": self.reject_count,
            "pause_count": self.pause_count,
            "position_qty": str(pos.quantity) if pos else "0",
            "position_avg_usdc": str(pos.avg_price_usdc) if pos else "0",
            "last_price_usdc": self.price_history[-1]["price"] if self.price_history else None,
            "decisions_by_action": by_action,
            "decisions_by_source": by_source,
        }

    async def generate_briefing(self, trigger: str = "manual") -> Dict[str, Any]:
        """브리핑 생성 → BRIEFING 이벤트 + artifacts/briefings/ 저장.
        trigger: manual(버튼) / session-end(세션 종료) / market-close(장 마감 시각)"""
        if not self.trades and not self.decisions:
            raise EngineError("브리핑할 데이터가 없습니다 — 먼저 세션을 실행하세요.")
        stats = self._briefing_stats()
        # Gemini 호출은 blocking — 이벤트 루프를 막지 않게 워커 스레드에서
        text, source = await asyncio.to_thread(generate_briefing_text, stats)
        rec: Dict[str, Any] = {"ts": _now(), "trigger": trigger, "source": source, "text": text}
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

    def _append_candle(self, price: Decimal) -> None:
        """틱 가격을 캔들에 반영 — 진행 중 캔들이 차면 새 캔들을 연다(시가=첫 틱가)."""
        cur = self.candles[-1] if self.candles else None
        if cur is not None and cur["count"] < TICKS_PER_CANDLE:
            cur["high"] = str(max(Decimal(cur["high"]), price))
            cur["low"] = str(min(Decimal(cur["low"]), price))
            cur["close"] = str(price)
            cur["count"] += 1
            return
        self.candles.append({
            "ts": _now(), "open": str(price), "high": str(price),
            "low": str(price), "close": str(price), "count": 1,
        })
        if len(self.candles) > MAX_CANDLES:
            self.candles.pop(0)

    async def _tick_once(self) -> None:
        symbol = CFG.stock_symbol
        feed = self._feed

        # 재생 피드 소진 → 세션 자동 종료 (마지막 봉까지 처리한 다음 틱에서)
        if isinstance(feed, ReplayPriceFeed) and feed.exhausted:
            self.bus.emit(ev.REPLAY_ENDED, {
                "message": "실데이터 재생 완료 — 세션을 자동 종료합니다",
                "bars_played": feed.played_bars,
                "last_date": feed.last_bar.date if feed.last_bar else "",
            })
            self._stop_event.set()
            return

        price = feed.get_price(symbol)
        bar: Optional[Bar] = feed.last_bar if isinstance(feed, ReplayPriceFeed) else None
        prev = self._prev_close
        self.tick += 1
        self.price_history.append({"ts": bar.date if bar else _now(), "price": str(price)})
        if len(self.price_history) > MAX_PRICE_POINTS:
            self.price_history.pop(0)

        if bar is not None:
            # 실데이터: 1틱 = 1봉 — 시가·고가·저가·종가를 그대로 캔들로
            self.candles.append({
                "ts": bar.date, "open": str(bar.open), "high": str(bar.high),
                "low": str(bar.low), "close": str(bar.close), "count": TICKS_PER_CANDLE,
            })
            if len(self.candles) > MAX_CANDLES:
                self.candles.pop(0)
        else:
            self._append_candle(price)

        payload: Dict[str, Any] = {"tick": self.tick, "symbol": symbol, "price": str(price)}
        if prev is not None:
            payload["prev_close"] = str(prev)
        if bar is not None:
            payload["date"] = bar.date
            payload["bar"] = {"ts": bar.date, "open": str(bar.open), "high": str(bar.high),
                              "low": str(bar.low), "close": str(bar.close)}
            payload["progress"] = {"played": feed.played_bars, "total": feed.total_bars}
        self.bus.emit(ev.PRICE_TICK, payload)
        self._change_ref = prev
        self._prev_close = price

        if not self.trading_enabled:
            return  # A2 긴급정지 — 시세만 흐르고 신규 판단·결제 없음

        # Gemini 호출은 동기(blocking) — 서버 이벤트 루프를 막지 않게 워커 스레드에서.
        # 재생 피드는 봉(OHLC)을 함께 전달해 TA(캔들·패턴) 근거를 살린다.
        decision = await asyncio.to_thread(self._trading.decide, symbol, price, bar)
        drec = {
            "ts": _now(), "tick": self.tick, "symbol": symbol, "price": str(price),
            "action": decision.action, "source": decision.source, "reason": decision.reason,
            "spend_usdc": str(decision.spend_usdc),
        }
        self.decisions.append(drec)
        if len(self.decisions) > MAX_DECISIONS:
            self.decisions.pop(0)
        self.bus.emit(ev.DECISION, drec)

        if decision.action == "sell" and self._trading.position.quantity > 0:
            await self._sell_cycle(symbol, price, decision)
        elif decision.action == "buy":
            await self._buy_cycle(symbol, price, decision)

    # ---------- 매수 사이클 (run_demo 이식) ----------

    async def _buy_cycle(self, symbol: str, price: Decimal, decision: Decision) -> None:
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

        required = self._broker.make_payment_required(quote)
        self.bus.emit(ev.X402_REQUIRED, {
            "side": "buy", "order_id": required.order_id,
            "amount_base": required.requirements.amount,
            "pay_to": required.requirements.pay_to,
            "resource": required.requirements.resource,
        })

        # 402 Guard(청구서 검증) + AP2 한도 검사 — 위반이면 서명 자체가 일어나지 않는다(유출 0)
        try:
            blockhash = await x.get_latest_blockhash(self._client) if live else Hash.default()
            submitted = self._trading.build_payment(
                required, blockhash, quote, max_spend_usdc=decision.spend_usdc)
        except GuardError as e:
            self.guard_block_count += 1
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
        self.bus.emit(ev.X402_SUBMITTED, {
            "side": "buy", "order_id": required.order_id,
            "remaining_usdc": str(self._auth.remaining_usdc),
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
                self._auth.release(required.order_id)
                self.bus.emit(ev.GUARD_PENDING, {
                    "side": "buy", "order_id": required.order_id, "ok": False,
                    "code": "GUARD_BASELINE_UNREAD",
                    "detail": f"정산 전 주식 잔액 기준선 조회 실패 — 배송 검증 불가로 매수 보류: {type(e).__name__}",
                    "where": "engine.py:_buy_cycle", "expected": "", "actual": "",
                })
                if self.trading_enabled:
                    self.pause(actor="guard")
                return

        completed = None
        settled = False
        try:
            completed = await self._broker.settle(
                submitted, required.requirements, quote.quantity, live=live, client=self._client)

            # 라이브 정산 성공이면 온체인 재조회로 실제 배송을 확인한다
            # (결함 I: 결제는 확정됐는데 주식 전달이 실패해도 settled 로 기록되던 문제).
            if (completed.status == "settled" and live and self._client is not None
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
                    # pending_delivery — 포지션 미반영·한도 원복·세션 정지·미결(partial) 기록
                    completed.status = "partial"
                    self.bus.emit(ev.GUARD_PENDING, {
                        "side": "buy", "order_id": required.order_id, **delivery.as_event(),
                    })

            settled = completed.status == "settled"
            # 평단은 수수료 포함 실효 단가(total/qty)로 반영 — 실현손익이 수수료 차감 후 순손익
            eff_price = ((quote.total_usdc / quote.quantity).quantize(Decimal("0.01"))
                         if quote.quantity > 0 else price)
            self._trading.on_completed(completed, symbol, quote.quantity, eff_price, quote.total_usdc)
            if settled:
                self.cum_buy_usdc += quote.total_usdc
                self.total_fees += quote.fee_usdc
            self._complete_trade("buy", symbol, quote.quantity, quote, completed, decision)
        finally:
            # 결함 H: settled 가 아니면 AP2 예약분을 원복해 한도를 되돌린다(실패해도 예산 소진 방지)
            if settled:
                self._auth.settle(required.order_id)
            else:
                self._auth.release(required.order_id)

        # 배송 미확인(partial)이면 세션을 정지한다 — 반복 결제로 손실이 누적되지 않게
        if completed is not None and completed.status == "partial" and self.trading_enabled:
            self.pause(actor="guard")

    # ---------- 매도 사이클 (run_demo 이식 + A7 실현손익) ----------

    async def _sell_cycle(self, symbol: str, price: Decimal, decision: Decision) -> None:
        live = self.mode == "live"
        qty = self._trading.position.quantity
        avg_before = self._trading.position.avg_price_usdc  # 실현손익 계산용 평단 캡처
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

        blockhash = await x.get_latest_blockhash(self._client) if live else Hash.default()
        submitted = self._trading.build_stock_transfer(required, blockhash)
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
        if (completed.status == "settled" and live and self._client is not None
                and self._usdc_mint is not None):
            expected_inc = to_base_units(quote.total_usdc, CFG.usdc_decimals)
            if not baseline_ok:
                completed.status = "partial"
                self.bus.emit(ev.GUARD_PENDING, {
                    "side": "sell", "order_id": required.order_id, "ok": False,
                    "code": "GUARD_BASELINE_UNREAD",
                    "detail": "정산 전 USDC 잔액 기준선 조회 실패 — 대금 도착 검증 불가로 매도 보류",
                    "where": "engine.py:_sell_cycle", "expected": "", "actual": "",
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
                    completed.status = "partial"
                    self.bus.emit(ev.GUARD_PENDING, {
                        "side": "sell", "order_id": required.order_id, **delivery.as_event(),
                    })

        self._trading.on_sale_completed(completed, symbol, qty, price, quote.total_usdc)

        realized = None
        if completed.status == "settled":
            # 수령액(수수료 차감)과 실효 평단(수수료 포함) 기준 → 순손익
            realized = (quote.total_usdc - avg_before * qty).quantize(Decimal("0.01"))
            self.realized_pnl += realized
            self.total_fees += quote.fee_usdc

        self._complete_trade("sell", symbol, qty, quote, completed, decision, realized)

        # 대금 미확인(partial)이면 세션을 정지한다 — 반복 매도로 손실이 누적되지 않게(매수측 미러)
        if completed.status == "partial" and self.trading_enabled:
            self.pause(actor="guard")

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
                    self._usdc_mint, self._stock_mint)
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
            self.status = "idle"
            self._task = None
            self.last_archive_path = archive_path
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

    def _archive(self, snap_after: dict) -> tuple[str, Dict[str, Any]]:
        """run_demo 와 동일한 순변화 교차검증 + artifacts/tx/ 증빙 아카이브."""
        confirmed = [t for t in self.trades if t["confirmed"]]
        buys = [t for t in confirmed if t["side"] == "buy"]
        sells = [t for t in confirmed if t["side"] == "sell"]
        net_spent = (sum((Decimal(t["total_usdc"]) for t in buys), Decimal(0))
                     - sum((Decimal(t["total_usdc"]) for t in sells), Decimal(0)))
        net_qty = (sum((Decimal(t["quantity"]) for t in buys), Decimal(0))
                   - sum((Decimal(t["quantity"]) for t in sells), Decimal(0)))
        usdc_out = Decimal(self._snap_before["trading"]["usdc"]) - Decimal(snap_after["trading"]["usdc"])
        stock_in = Decimal(snap_after["trading"]["stock"]) - Decimal(self._snap_before["trading"]["stock"])
        cross = {
            "usdc_net_out_onchain": str(usdc_out), "usdc_net_out_expected": str(net_spent),
            "usdc_ok": usdc_out == net_spent,
            "stock_net_in_onchain": str(stock_in), "stock_net_in_expected": str(net_qty),
            "stock_ok": stock_in == net_qty,
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
        pos = self._trading.position if self._trading else None
        return_pct = (
            (self.realized_pnl / self.cum_buy_usdc * 100).quantize(Decimal("0.01"))
            if self.cum_buy_usdc > 0 else Decimal(0))
        return jsonable({
            "session_id": self.session_id,
            "mode": self.mode, "network": CFG.network, "symbol": CFG.stock_symbol,
            "started_at": self.started_at, "ended_at": _now(),
            "ticks": self.tick, "brain": self.brain_label,
            "strategy": self.strategy_info, "feed": self.feed_info,
            "budget_total_usdc": str(self.budget_total),
            "per_trade_max_usdc": str(self.per_trade_max),
            "realized_pnl_usdc": str(self.realized_pnl),
            "return_pct": str(return_pct),
            "cum_buy_usdc": str(self.cum_buy_usdc),
            "total_fees_usdc": str(self.total_fees),
            "fee_bps": CFG.broker_fee_bps,
            "reject_count": self.reject_count, "pause_count": self.pause_count,
            "guard_block_count": self.guard_block_count,   # 402 Guard check_demand 차단
            "position_qty": str(pos.quantity) if pos else "0",
            "position_avg_usdc": str(pos.avg_price_usdc) if pos else "0",
            "trade_count": len(self.trades), "decision_count": len(self.decisions),
            "trades": self.trades,
            "decisions": self.decisions[-300:],
            "mandate_history": self.mandate_history,
            "archive_path": archive_path,       # 라이브 세션의 로컬 증빙 파일 경로
            "cross_check": cross,               # 라이브: 온체인 순변화 교차검증 결과
        })

    # ---------- 상태 스냅샷 (GET /api/state) ----------

    def _valuation(self, pos, remaining: Decimal) -> Dict[str, Any]:
        """평가손익(미실현) · 총자산 — 실현손익과 같은 기준으로 계산한다.

        지금 전량 매도하면 받을 금액(수수료 차감) − 실효 평단 × 보유수량.
        평단이 이미 매수 수수료를 포함하므로 이 값이 곧 수수료 반영 후 순손익이다.
        총자산 = 가용 현금(AP2 잔여 예산) + 보유 주식의 매도 예상 수령액."""
        qty = pos.quantity if pos else Decimal(0)
        price = Decimal(self.price_history[-1]["price"]) if self.price_history else Decimal(0)
        fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
        gross = (qty * price).quantize(Decimal("0.01"))
        net = (qty * price * (1 - fee_rate)).quantize(Decimal("0.01"))
        cost = ((pos.avg_price_usdc if pos else Decimal(0)) * qty).quantize(Decimal("0.01"))
        unrealized = net - cost
        pct = (unrealized / cost * 100).quantize(Decimal("0.01")) if cost > 0 else Decimal(0)
        return {
            "market_price_usdc": str(price),
            "position_value_usdc": str(gross),       # 현재가 × 보유수량
            "position_net_value_usdc": str(net),     # 지금 매도 시 수령 예상액
            "cost_basis_usdc": str(cost),
            "unrealized_pnl_usdc": str(unrealized),
            "unrealized_pct": str(pct),
            "cash_usdc": str(remaining),             # 가용 현금 = AP2 잔여 예산
            "total_asset_usdc": str(remaining + net),
            "onchain_usdc": (self._snap_last["trading"]["usdc"]
                             if self._snap_last else None),  # 라이브: 최근 스냅샷
        }

    def state_snapshot(self) -> Dict[str, Any]:
        pos = self._trading.position if self._trading else None
        spent = self._auth.spent_usdc if self._auth else Decimal(0)
        remaining = self._auth.remaining_usdc if self._auth else self.budget_total
        return_pct = (
            (self.realized_pnl / self.cum_buy_usdc * 100).quantize(Decimal("0.01"))
            if self.cum_buy_usdc > 0 else Decimal(0)
        )
        return {
            "engine": {
                "status": self.status, "mode": self.mode, "network": CFG.network,
                "tick": self.tick, "tick_interval_sec": self.tick_interval,
                "started_at": self.started_at, "brain": self.brain_label,
                "session_id": self.session_id,
            },
            "persistence": {  # Firestore 영속화 상태 (Cloud Run 재시작 대비)
                "enabled": self.store.enabled,
                "backend": self.store.backend,
                "detail": self.store.detail,
                "last_error": self.store.last_error,
            },
            "trading_enabled": self.trading_enabled,
            "pause_info": self.pause_info,
            "symbol": CFG.stock_symbol,
            "replay_available": os.path.exists(self.default_replay_path()),
            "price": {
                "current": self.price_history[-1]["price"] if self.price_history else None,
                "session_open": self.price_history[0]["price"] if self.price_history else None,
                "prev_close": str(self._change_ref) if self._change_ref is not None else None,
                "change_basis": ("prev-close" if self.feed_info.get("type") == "replay"
                                 else "session-open"),
                "feed": self.feed_info,
                "history": self.price_history[-60:],
                "candles": self.candles[-60:],           # 캔들차트 (OHLC)
                "ticks_per_candle": TICKS_PER_CANDLE,
            },
            "position": {
                "quantity": str(pos.quantity) if pos else "0",
                "avg_price_usdc": str(pos.avg_price_usdc) if pos else "0",
            },
            "budget": {
                "total_usdc": str(self.budget_total),
                "spent_usdc": str(spent),
                "remaining_usdc": str(remaining),
                "per_trade_max_usdc": str(self.per_trade_max),
            },
            "pnl": {  # A7 라이트: 수익률 = 실현손익 / 누적 매수금액
                "realized_usdc": str(self.realized_pnl),
                "return_pct": str(return_pct),
                "cum_buy_usdc": str(self.cum_buy_usdc),
            },
            "valuation": self._valuation(pos, remaining),  # 평가손익(미실현) · 총자산
            "fees": {  # A8 수수료 투명화 — 브로커 수익모델 증명
                "fee_bps": CFG.broker_fee_bps,
                "cum_fee_usdc": str(self.total_fees),
            },
            "rules": DEFAULT_RULES,
            "strategy": getattr(self, "strategy_info", None) or {"type": "condition"},
            "last_briefing": self.last_briefing,  # B2 최근 브리핑 (새로고침 복원용)
            "counts": {"trades": len(self.trades), "decisions": len(self.decisions)},
            "guard": {  # 첫 화면 KPI — 시도·차단·유출·오탐 (수익률이 아니라 지출 통제)
                "attempts": self.guard_block_count + len([t for t in self.trades if t["side"] == "buy"]),
                "blocked": self.guard_block_count,
                "ap2_rejected": self.reject_count,
                "leak_usdc": str(self.guard_leak_usdc),
            },
            "wallets": {
                "user": str(self._user_kp.pubkey()) if self._user_kp else "",
                "trading": str(self._trading_kp.pubkey()) if self._trading_kp else "",
                "broker": str(self._broker_kp.pubkey()) if self._broker_kp else "",
            },
            "balances": self._snap_last,
        }
