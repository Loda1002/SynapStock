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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import CFG
from market.price_feed import MockPriceFeed
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy, Decision
from run_demo import _load_or_new, explorer_tx_url, snapshot_balances
from web import events as ev
from web.briefing import generate_briefing_text
from web.events import EventBus

# 데모 규칙 기본값 — run_demo.py 의 Strategy 와 동일 (한도 설정 화면은 P2 A3)
DEFAULT_RULES = {"buy_below": "178", "sell_above": "185", "spend_per_trade": "30"}

MAX_DECISIONS = 500   # A6 타임라인 메모리 상한
MAX_PRICE_POINTS = 120


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EngineError(Exception):
    """엔진 조작 오류 — API 레이어에서 4xx 로 변환된다."""


class TradingEngine:
    def __init__(self, bus: EventBus):
        self.bus = bus
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
        self.realized_pnl = Decimal(0)              # A7 실현손익
        self.cum_buy_usdc = Decimal(0)              # A7 수익률 분모(누적 매수금액)
        self.total_fees = Decimal(0)                # A8 누적 브로커 수수료 (수익모델 증명)
        self.started_at: str = ""
        self.brain_label: str = ""
        self.strategy_info: Dict[str, Any] = {"type": "condition"}  # B7 세션 전략
        self.reject_count = 0                       # B2 브리핑용: AP2 거부 횟수
        self.pause_count = 0                        # B2 브리핑용: 긴급정지 횟수
        self.last_briefing: Optional[Dict[str, Any]] = None  # B2 최근 브리핑
        self._last_daily_briefing_date = ""         # B2 장 마감 자동 생성 중복 방지

        # 세션 구성물 (start 에서 생성)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._client = None
        self._trading: Optional[TradingAgent] = None
        self._broker: Optional[BrokerAgent] = None
        self._feed: Optional[MockPriceFeed] = None
        self._auth: Optional[PaymentAuthorizer] = None
        self._mandate: Optional[OpenPaymentMandate] = None
        self._trading_kp: Optional[Keypair] = None
        self._broker_kp: Optional[Keypair] = None
        self._usdc_mint: Optional[Pubkey] = None
        self._stock_mint: Optional[Pubkey] = None
        self._snap_before: Optional[dict] = None
        self._snap_last: Optional[dict] = None

    # ---------- 라이프사이클 ----------

    async def start(self, mode: str,
                    strategy_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.status != "idle":
            raise EngineError("엔진이 이미 실행 중입니다 — 먼저 세션을 종료하세요.")
        if mode not in ("dry", "live"):
            raise EngineError("mode 는 'dry' 또는 'live' 여야 합니다.")
        live = mode == "live"

        # B7 전략 선택 — condition(조건형, 현행) / dca(적립형, N틱마다 정액 매수)
        scfg = strategy_cfg or {}
        strat_type = scfg.get("type", "condition")
        if strat_type not in ("condition", "dca"):
            raise EngineError("strategy.type 은 'condition' 또는 'dca' 여야 합니다.")
        try:
            dca_every = int(scfg.get("dca_every_ticks", 5))
            dca_amount = Decimal(str(scfg.get("dca_amount_usdc", "10")))
        except (ValueError, InvalidOperation):
            raise EngineError("적립식 파라미터가 숫자 형식이 아닙니다.")
        if strat_type == "dca" and (dca_every < 1 or dca_amount <= 0):
            raise EngineError("적립식은 주기 1틱 이상, 회당 금액 0 초과여야 합니다.")

        usdc_mint = Pubkey.from_string(CFG.usdc_mint)
        if live and not CFG.stock_mint:
            raise EngineError("STOCK_MINT 미설정 — 먼저 scripts/setup_devnet.py 를 실행하세요.")
        stock_mint = Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint else None

        wd = CFG.wallet_dir
        trading_kp = _load_or_new(os.path.join(wd, "trading.json"))
        broker_kp = _load_or_new(os.path.join(wd, "broker.json"))

        # 판단 두뇌 — run_demo 와 동일한 선택 로직 (Gemini, 실패 시 규칙 폴백)
        # 적립형(dca)은 판단 없이 스케줄 매수라 Gemini 를 쓰지 않는다
        brain = None
        if strat_type == "dca":
            brain_label = f"적립식 스케줄 ({dca_every}틱마다 {dca_amount} USDC, Gemini 미사용)"
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
            user_pubkey=str(trading_kp.pubkey()),
            allowed_asset=str(usdc_mint),
            budget_total_usdc=self.budget_total,
            per_trade_max_usdc=self.per_trade_max,
            allowed_symbols=[CFG.stock_symbol],
        ).sign(trading_kp)
        auth = PaymentAuthorizer(mandate, agent_kp=trading_kp)

        strategy = Strategy(
            buy_below=Decimal(DEFAULT_RULES["buy_below"]),
            sell_above=Decimal(DEFAULT_RULES["sell_above"]),
            spend_per_trade_usdc=Decimal(DEFAULT_RULES["spend_per_trade"]),
            mode=strat_type,
            dca_every_ticks=dca_every,
            dca_amount_usdc=dca_amount,
        )
        trading = TradingAgent(
            trading_kp, auth, strategy, CFG.usdc_decimals, CFG.network, brain=brain,
            fee_bps=CFG.broker_fee_bps)
        broker = BrokerAgent(
            broker_kp, usdc_mint, CFG.usdc_decimals, stock_mint, CFG.stock_decimals, CFG.network,
            fee_bps=CFG.broker_fee_bps)

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
        self.realized_pnl = Decimal(0)
        self.cum_buy_usdc = Decimal(0)
        self.total_fees = Decimal(0)
        self.mandate_history = []
        self.reject_count = 0
        self.pause_count = 0
        self.started_at = _now()
        self.brain_label = brain_label
        self.strategy_info = {
            "type": strat_type,
            "dca_every_ticks": dca_every,
            "dca_amount_usdc": str(dca_amount),
        }
        self.tick_interval = CFG.web_tick_interval_sec
        self.last_archive_path = ""
        self._usdc_mint, self._stock_mint = usdc_mint, stock_mint
        self._trading_kp, self._broker_kp = trading_kp, broker_kp
        self._mandate, self._auth = mandate, auth
        self._trading, self._broker, self._feed = trading, broker, MockPriceFeed()
        self._client = client
        self._snap_before = self._snap_last = snap_before
        self._stop_event = asyncio.Event()

        self.bus.emit(ev.ENGINE_STARTED, {
            "mode": mode, "network": CFG.network, "symbol": CFG.stock_symbol,
            "brain": brain_label,
            "budget_total_usdc": str(self.budget_total),
            "per_trade_max_usdc": str(self.per_trade_max),
            "fee_bps": CFG.broker_fee_bps,
            "rules": DEFAULT_RULES,
            "strategy": self.strategy_info,
            "mandate_verified": mandate.verify(),
            "wallets": {"trading": str(trading_kp.pubkey()), "broker": str(broker_kp.pubkey())},
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
        if budget_total <= 0 or per_trade_max <= 0:
            raise EngineError("예산과 건별 한도는 0보다 큰 숫자여야 합니다.")
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
                user_pubkey=str(self._trading_kp.pubkey()),
                allowed_asset=str(self._usdc_mint),
                budget_total_usdc=budget_total,
                per_trade_max_usdc=per_trade_max,
                allowed_symbols=[CFG.stock_symbol],
            ).sign(self._trading_kp)
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

    async def _tick_once(self) -> None:
        symbol = CFG.stock_symbol
        price = self._feed.get_price(symbol)
        self.tick += 1
        self.price_history.append({"ts": _now(), "price": str(price)})
        if len(self.price_history) > MAX_PRICE_POINTS:
            self.price_history.pop(0)
        self.bus.emit(ev.PRICE_TICK, {"tick": self.tick, "symbol": symbol, "price": str(price)})

        if not self.trading_enabled:
            return  # A2 긴급정지 — 시세만 흐르고 신규 판단·결제 없음

        # Gemini 호출은 동기(blocking) — 서버 이벤트 루프를 막지 않게 워커 스레드에서
        decision = await asyncio.to_thread(self._trading.decide, symbol, price)
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

        try:
            blockhash = await x.get_latest_blockhash(self._client) if live else Hash.default()
            submitted = self._trading.build_payment(required, blockhash)
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

        completed = await self._broker.settle(
            submitted, required.requirements, quote.quantity, live=live, client=self._client)
        # 평단은 수수료 포함 실효 단가(total/qty)로 반영 — 실현손익이 수수료 차감 후 순손익이 된다
        eff_price = ((quote.total_usdc / quote.quantity).quantize(Decimal("0.01"))
                     if quote.quantity > 0 else price)
        self._trading.on_completed(completed, symbol, quote.quantity, eff_price, quote.total_usdc)
        if completed.status == "settled":
            self.cum_buy_usdc += quote.total_usdc
            self.total_fees += quote.fee_usdc

        self._complete_trade("buy", symbol, quote.quantity, quote, completed, decision)

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

        completed = await self._broker.settle_sale(
            submitted, required.requirements, quote.total_usdc, live=live, client=self._client)
        self._trading.on_sale_completed(completed, symbol, qty, price, quote.total_usdc)

        realized = None
        if completed.status == "settled":
            # 수령액(수수료 차감)과 실효 평단(수수료 포함) 기준 → 순손익
            realized = (quote.total_usdc - avg_before * qty).quantize(Decimal("0.01"))
            self.realized_pnl += realized
            self.total_fees += quote.fee_usdc

        self._complete_trade("sell", symbol, qty, quote, completed, decision, realized)

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
            "wallets": {"trading": str(self._trading_kp.pubkey()), "broker": str(self._broker_kp.pubkey())},
            "mints": {"usdc": str(self._usdc_mint), "stock": str(self._stock_mint),
                      "stock_symbol": CFG.stock_symbol},
            "strategy": getattr(self, "strategy_info", None),
            "mandate": {
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

    # ---------- 상태 스냅샷 (GET /api/state) ----------

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
            },
            "trading_enabled": self.trading_enabled,
            "pause_info": self.pause_info,
            "symbol": CFG.stock_symbol,
            "price": {
                "current": self.price_history[-1]["price"] if self.price_history else None,
                "history": self.price_history[-60:],
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
            "fees": {  # A8 수수료 투명화 — 브로커 수익모델 증명
                "fee_bps": CFG.broker_fee_bps,
                "cum_fee_usdc": str(self.total_fees),
            },
            "rules": DEFAULT_RULES,
            "strategy": getattr(self, "strategy_info", None) or {"type": "condition"},
            "last_briefing": self.last_briefing,  # B2 최근 브리핑 (새로고침 복원용)
            "counts": {"trades": len(self.trades), "decisions": len(self.decisions)},
            "wallets": {
                "trading": str(self._trading_kp.pubkey()) if self._trading_kp else "",
                "broker": str(self._broker_kp.pubkey()) if self._broker_kp else "",
            },
            "balances": self._snap_last,
        }
