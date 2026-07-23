"""Trading (구매) 에이전트 — 사용자 대리.

책임: 시세+규칙으로 매수/매도 판단 → AP2 한도 승인 → 결제 트랜잭션 서명(x402)
→ payment-completed 반영(포지션 갱신).

`decide()` 의 규칙 부분이 이후 Gemini(ADK) 로 교체될 지점이다.
현재는 사용자 정의 임계값 규칙(데모용, 투자 조언 아님).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash

from config import from_base_units
from market.price_feed import Bar
from market.indicators import ta_summary
from shared.models import Position, Receipt
from shared.a2a_messages import (
    PaymentRequired, PaymentSubmitted, PaymentPayload, PaymentCompleted,
)
from payments import x402_solana as x
from payments.ap2_mandate import PaymentAuthorizer, MandateError


@dataclass
class Decision:
    action: str          # "buy" / "sell" / "hold"
    reason: str
    spend_usdc: Decimal = Decimal(0)
    source: str = "rule"  # "gemini" / "rule" / "rule-fallback"


@dataclass
class Strategy:
    """매매 전략 — condition(조건형, 지표 규칙+Gemini 판단) / dca(적립형, 주기 정액 매수).

    조건형 규칙(2026-07-23 지표 기준 전환 — 절대가 178/185 는 목 시세 전용이었음):
      매수 = 현재가가 5일 이동평균(MA5) 대비 buy_dip_pct% 이상 낮을 때 (싸졌을 때)
      매도 = 현재가가 보유 평균단가 대비 take_profit_pct% 이상 높을 때 (익절)
    어떤 종목·가격대에도 동작하며, % 값은 사용자 설정값이다.

    B7: 적립형은 판단 없이 주기마다 정액 매수만 한다(매도 없음).
    주기 기준(dca_unit)은 사람이 고른다 — ticks(N틱마다) / minutes(N분마다) /
    daily(매일 지정 시각). AP2 mandate 검사는 어느 모드든 같은 결제 경로를 지난다."""
    buy_dip_pct: Decimal = Decimal("2")       # 매수: MA5 대비 −N%
    take_profit_pct: Decimal = Decimal("3")   # 매도: 평단 대비 +N%
    spend_per_trade_usdc: Decimal = Decimal("30")
    # Gemini 재량 범위 — strict(규칙 그대로 판정) / trend(신호가 떠도 추세 근거로 보류 가능).
    # 어느 모드든 신규 매수·매도의 "개시"는 규칙 신호가 필요하고, AP2 한도 검사는 불변.
    decision_mode: str = "strict"
    # TA 보강(2026-07-23 매매 기준 개선): MA 배열·크로스·지지/저항·차트/캔들 패턴을
    # 코드로 계산해 판단(Gemini 프롬프트 + 규칙 폴백)에 주입. 백테스트로 개선이
    # 확인되기 전에는 기본 OFF 를 유지한다(message 가드레일). 실데이터 재생 전용.
    ta_mode: bool = False
    mode: str = "condition"                    # "condition" / "dca"
    dca_unit: str = "ticks"                    # "ticks" / "minutes" / "daily"
    dca_every_ticks: int = 5                   # ticks: N틱마다
    dca_every_minutes: int = 60                # minutes: N분마다
    dca_at_time: str = "09:00"                 # daily: 매일 HH:MM (서버 로컬 시각)
    dca_amount_usdc: Decimal = Decimal("10")   # 적립형: 회당 정액


class TradingAgent:
    def __init__(
        self,
        keypair: Keypair,
        authorizer: PaymentAuthorizer,
        strategy: Strategy,
        usdc_decimals: int,
        network: str,
        brain=None,       # GeminiDecider (없으면 규칙 기반)
        fee_bps: int = 0,  # A8 브로커 수수료 — Gemini 에 실효 가격 근거로 제공
    ):
        self.kp = keypair
        self.auth = authorizer
        self.strategy = strategy
        self.usdc_decimals = usdc_decimals
        self.network = network
        self.position = Position(symbol="")
        self.brain = brain
        self.fee_bps = fee_bps
        self._history: list[Decimal] = []  # 직전 시세 (지표 계산·Gemini 판단 근거)
        self.HISTORY_MAX = 210             # MA200 계산 + 기울기 여유분 (TA 보강)
        self._bars: list[Bar] = []         # OHLC 봉 이력 — 캔들·패턴·지지/저항 탐지용
        self._last_action: Optional[dict] = None  # 직전 매수/매도 회고 (Gemini 프롬프트용)
        self._dca_tick = 0                 # B7 적립형(ticks): 다음 매수까지 틱 카운터
        self._dca_round = 0                # B7 적립형: 누적 회차
        self._dca_next_at: Optional[datetime] = None  # 적립형(minutes): 다음 집행 시각
        self._dca_last_date = ""           # 적립형(daily): 마지막 집행 날짜
        self._now = datetime.now           # 테스트에서 가짜 시계로 교체 가능

    @property
    def pubkey(self) -> Pubkey:
        return self.kp.pubkey()

    def preload_history(self, prices: list[Decimal]) -> None:
        """재생 피드의 워밍업 봉 종가를 주입 — 첫 틱부터 MA5/MA20 이 계산되게 한다."""
        self._history = list(prices)[-self.HISTORY_MAX:]

    def preload_bars(self, bars: list[Bar]) -> None:
        """워밍업 봉을 OHLC 째로 주입 — 종가 이력과 TA 봉 이력을 함께 채운다."""
        self._bars = list(bars)[-self.HISTORY_MAX:]
        self.preload_history([b.close for b in self._bars])

    # ---------- 지표 (규칙·Gemini 판단 공용) ----------

    def _ma(self, period: int) -> Optional[Decimal]:
        """단순 이동평균 — _history(현재가 포함) 마지막 period 개. 부족하면 None."""
        if len(self._history) < period:
            return None
        window = self._history[-period:]
        return (sum(window) / Decimal(period)).quantize(Decimal("0.01"))

    def indicators(self) -> dict:
        """현재 틱 지표 묶음 — decide() 안(현재가 append 이후)에서 호출한다.

        buy_threshold = MA5 × (1 − buy_dip_pct%) / take_profit = 평단 × (1 + take_profit_pct%).
        MA 미성립(워밍업)이나 무보유면 해당 값은 None. 등락률·변동성·평단 손익률은
        Gemini 판단 입력 보강용(퍼센트, 소수 2자리)."""
        s = self.strategy
        ma5, ma20 = self._ma(5), self._ma(20)
        buy_th = ((ma5 * (1 - s.buy_dip_pct / 100)).quantize(Decimal("0.01"))
                  if ma5 is not None else None)
        pos = self.position
        tp = ((pos.avg_price_usdc * (1 + s.take_profit_pct / 100)).quantize(Decimal("0.01"))
              if pos.quantity > 0 and pos.avg_price_usdc > 0 else None)
        price = self._history[-1] if self._history else None

        def pct(now: Decimal, then: Decimal) -> Optional[Decimal]:
            return ((now / then - 1) * 100).quantize(Decimal("0.01")) if then else None

        # 최근 5봉 등락률 (현재가 vs 5봉 전 종가)
        change5 = (pct(price, self._history[-6])
                   if price is not None and len(self._history) >= 6 else None)
        # 변동성: 최근 10개 봉간 수익률의 표준편차(%) — 판단 참고용이라 float 계산으로 충분
        vol = None
        if len(self._history) >= 6:
            closes = [float(v) for v in self._history[-11:]]
            rets = [(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a]
            if len(rets) >= 3:
                mean = sum(rets) / len(rets)
                vol = Decimal(str(round((sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5, 2)))
        # 보유 평단 대비 손익률
        pos_pnl = (pct(price, pos.avg_price_usdc)
                   if price is not None and pos.quantity > 0 and pos.avg_price_usdc > 0 else None)
        ind = {"ma5": ma5, "ma20": ma20, "buy_threshold": buy_th, "take_profit": tp,
               "change5_pct": change5, "volatility_pct": vol, "position_pnl_pct": pos_pnl}
        if s.ta_mode and self._bars:
            # TA 보강 — MA 배열·크로스·지지/저항·패턴을 코드로 계산 (판단자 공용 입력)
            ind["ta"] = ta_summary(self._bars)
        return ind

    def _retrospective(self, price: Decimal) -> str:
        """직전 매수/매도 회고 한 줄 — '학습하는 것처럼' 직전 행동의 결과를 프롬프트에 준다."""
        a = self._last_action
        if not a:
            return "이번 세션 매수·매도 이력 없음"
        a["bars_ago"] += 1
        diff = ((price / a["price"] - 1) * 100).quantize(Decimal("0.01")) if a["price"] else 0
        side = "매수" if a["action"] == "buy" else "매도"
        return (f"{a['bars_ago']}봉 전 {side} @ {a['price']} USDC → 현재가는 그 대비 "
                f"{'+' if diff >= 0 else ''}{diff}%")

    # 1) 판단 — 적립형이면 스케줄 매수, 조건형이면 Gemini(있으면) → 실패 시 규칙 폴백
    def decide(self, symbol: str, price: Decimal, bar: Optional[Bar] = None) -> Decision:
        self.position.symbol = symbol
        self._history.append(price)
        if len(self._history) > self.HISTORY_MAX:
            self._history.pop(0)
        # TA 봉 이력 — 재생 피드는 실제 OHLC, 목 시세는 퇴화 봉(고=저, 캔들 패턴 미탐지)
        self._bars.append(bar if bar is not None
                          else Bar(date="", open=price, high=price, low=price, close=price))
        if len(self._bars) > self.HISTORY_MAX:
            self._bars.pop(0)

        if self.strategy.mode == "dca":
            return self._decide_dca()

        ind = self.indicators()
        if ind["buy_threshold"] is None:
            # MA5 미성립 — 지표 워밍업 (재생 피드는 워밍업 주입으로 첫 틱부터 성립)
            return Decision("hold", f"지표 워밍업 — MA5 계산까지 {5 - len(self._history)}봉 더 필요")

        retro = self._retrospective(price)
        if self.brain is not None:
            try:
                d = self._sanitize(self.brain.decide(
                    symbol, price, self._history[-9:-1], self.strategy,
                    self.auth.remaining_usdc, self.position,
                    fee_bps=self.fee_bps, indicators=ind, retrospective=retro,
                ))
            except Exception as e:
                d = self._decide_by_rule(symbol, price, ind)
                d.source = "rule-fallback"
                detail = str(e).replace("\n", " ")[:100]  # 실제 원인 표면화 (예: 429 쿼터 초과)
                d.reason += f" — Gemini 호출 실패({type(e).__name__}: {detail}) → 규칙 폴백"
        else:
            d = self._decide_by_rule(symbol, price, ind)
        if d.action in ("buy", "sell"):
            self._last_action = {"action": d.action, "price": price, "bars_ago": 0}
        return d

    # B7 적립형 — 가격 판단 없이 주기(틱/분/매일 시각)마다 정액 매수 (매도 없음)
    def _dca_due(self) -> tuple[bool, str]:
        """이번 틱이 적립 시점인지 — (실행 여부, 대기 사유)."""
        s = self.strategy
        if s.dca_unit == "minutes":
            every = max(1, s.dca_every_minutes)
            now = self._now()
            if self._dca_next_at is None:
                self._dca_next_at = now          # 세션 시작 직후 1회차를 바로 집행
            if now < self._dca_next_at:
                left = int((self._dca_next_at - now).total_seconds())
                return False, f"적립 대기 — 다음 정액 매수까지 {left // 60}분 {left % 60}초"
            self._dca_next_at = now + timedelta(minutes=every)
            return True, ""
        if s.dca_unit == "daily":
            now = self._now()
            try:
                hh, mm = (int(v) for v in s.dca_at_time.split(":"))
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                return False, f"적립 보류 — 시각 형식 오류({s.dca_at_time}), HH:MM 이어야 합니다"
            today = now.strftime("%Y-%m-%d")
            if self._dca_last_date == today:
                return False, f"적립 대기 — 오늘({today}) {s.dca_at_time} 정액 매수 완료, 내일 재개"
            if now < target:
                return False, f"적립 대기 — 매일 {s.dca_at_time} 정액 매수 (오늘 아직 미도래)"
            self._dca_last_date = today
            return True, ""
        every = max(1, s.dca_every_ticks)        # 기본: 틱 기준
        self._dca_tick += 1
        if self._dca_tick < every:
            return False, f"적립 대기 — 다음 정액 매수까지 {every - self._dca_tick}틱"
        self._dca_tick = 0
        return True, ""

    def _decide_dca(self) -> Decision:
        amount = self.strategy.dca_amount_usdc
        due, wait_reason = self._dca_due()
        if not due:
            return Decision("hold", wait_reason, source="dca")
        if self.auth.remaining_usdc <= 0:
            return Decision("hold", "적립 보류 — 예산 소진", source="dca")
        if amount > self.auth.remaining_usdc:
            return Decision(
                "hold",
                f"적립 보류 — 잔여 예산 {self.auth.remaining_usdc} < 정액 {amount} USDC",
                source="dca")
        self._dca_round += 1
        return Decision(
            "buy",
            f"적립식 {self._dca_round}회차 — {self.dca_schedule_label()} {amount} USDC 정액 매수",
            amount, source="dca")

    def dca_schedule_label(self) -> str:
        """사람이 읽는 적립 주기 문구 (타임라인·로그·UI 공용)."""
        s = self.strategy
        if s.dca_unit == "minutes":
            return f"{s.dca_every_minutes}분마다"
        if s.dca_unit == "daily":
            return f"매일 {s.dca_at_time}"
        return f"{s.dca_every_ticks}틱마다"

    def _decide_by_rule(self, symbol: str, price: Decimal, ind: dict) -> Decision:
        """지표 규칙 — 익절(매도)을 먼저 검사한다: 급반등 구간에서 매수·매도 조건이
        동시에 성립하면 이익 확정이 우선이다.

        ta_mode 면 TA 피처가 거부권/보류권으로 겹쳐진다(개시 게이트는 기존 규칙 유지):
        - 사지마: 대기 패턴 탐지 / 중기선(MA10) 하락 / TA 매도 신호 우세 → 매수 보류
        - 팔지마: 장기선 상승 + TA 매수 신호 우세 → 익절 보류(추세 살아있음)
        Gemini 실패 시에도 같은 TA 기준으로 폴백 판단한다(무료 티어 쿼터 초과 대비)."""
        s = self.strategy
        tp, buy_th = ind["take_profit"], ind["buy_threshold"]
        ta = ind.get("ta") or {}
        buy_sc, sell_sc = ta.get("buy_score", 0), ta.get("sell_score", 0)
        if tp is not None and price >= tp:
            if ta and ta.get("hold_sell_hint") and buy_sc > sell_sc:
                return Decision(
                    "hold",
                    f"익절 조건 충족({price} ≥ {tp})이지만 장기선(MA{ta['slopes']['long_period']}) "
                    f"상승 + TA 매수 우세(매수합 {buy_sc} vs 매도합 {sell_sc}) — 성급한 매도 보류(팔지마)")
            return Decision(
                "sell",
                f"가격 {price} ≥ 익절기준 {tp} (평단 {self.position.avg_price_usdc} +{s.take_profit_pct}%)")
        if price <= buy_th and self.auth.remaining_usdc > 0:
            if ta:
                if ta.get("wait"):
                    waits = ", ".join(p["name"] for p in ta["patterns"] if p["signal"] == "wait")
                    return Decision(
                        "hold", f"매수 조건 충족이지만 대기 패턴({waits}) — 방향 확정까지 보류")
                if ta.get("veto_buy"):
                    return Decision(
                        "hold", "매수 조건 충족이지만 중기선(MA10) 하락 중 — 매수 보류(사지마)")
                if sell_sc >= buy_sc + 60:
                    return Decision(
                        "hold",
                        f"매수 조건 충족이지만 TA 매도 신호 우세(매도합 {sell_sc} vs 매수합 {buy_sc}) — 보류")
            spend = min(s.spend_per_trade_usdc, self.auth.remaining_usdc)
            reason = f"가격 {price} ≤ 매수기준 {buy_th} (MA5 {ind['ma5']} −{s.buy_dip_pct}%)"
            if buy_sc > 0:
                reason += f" · TA 동조(매수합 {buy_sc})"
            return Decision("buy", reason, spend)
        hold = f"조건 미충족 — 가격 {price} · 매수기준 {buy_th}(MA5−{s.buy_dip_pct}%)"
        hold += f" · 익절기준 {tp}" if tp is not None else " · 보유 없음(매도 조건 없음)"
        return Decision("hold", hold)

    def _sanitize(self, d: Decision) -> Decision:
        """Gemini 응답을 한도 안으로 강제 (AP2 mandate 가 최종 관문이지만 이중 방어)."""
        if d.action == "buy":
            if self.auth.remaining_usdc <= 0:
                return Decision("hold", f"{d.reason} (예산 소진 → 보류)", source=d.source)
            spend = d.spend_usdc if d.spend_usdc > 0 else self.strategy.spend_per_trade_usdc
            d.spend_usdc = min(spend, self.strategy.spend_per_trade_usdc, self.auth.remaining_usdc)
        if d.action == "sell" and self.position.quantity <= 0:
            return Decision("hold", f"{d.reason} (보유 수량 없음 → 보류)", source=d.source)
        return d

    # 2) payment-required → 한도 승인 + 결제 서명 → payment-submitted
    def build_payment(
        self,
        required: PaymentRequired,
        blockhash: Hash,
    ) -> PaymentSubmitted:
        reqs = required.requirements
        amount_usdc = from_base_units(reqs.amount, reqs.decimals)

        # AP2 한도 검사 (초과 시 MandateError → 결제 자체가 일어나지 않음)
        self.auth.authorize(
            order_id=required.order_id, symbol=required.symbol,
            amount_usdc=amount_usdc, pay_to=reqs.pay_to,
        )

        tx = x.build_transfer_transaction(
            payer=self.kp,
            mint=Pubkey.from_string(reqs.asset),
            dest_owner=Pubkey.from_string(reqs.pay_to),
            amount=reqs.amount,
            decimals=reqs.decimals,
            blockhash=blockhash,
        )
        payload = PaymentPayload(
            network=self.network,
            serialized_transaction=x.encode_payload(tx),
        )
        return PaymentSubmitted(order_id=required.order_id, payment=payload)

    # 2') 매도: 주식 전송 트랜잭션 서명 (AP2 는 '지출' 한도이므로 매도엔 미적용)
    def build_stock_transfer(
        self,
        required: PaymentRequired,
        blockhash: Hash,
    ) -> PaymentSubmitted:
        reqs = required.requirements
        tx = x.build_transfer_transaction(
            payer=self.kp,
            mint=Pubkey.from_string(reqs.asset),      # 주식 민트
            dest_owner=Pubkey.from_string(reqs.pay_to),
            amount=reqs.amount,
            decimals=reqs.decimals,
            blockhash=blockhash,
        )
        payload = PaymentPayload(
            network=self.network,
            serialized_transaction=x.encode_payload(tx),
        )
        return PaymentSubmitted(order_id=required.order_id, payment=payload)

    # 3) 정산 완료 반영
    def on_completed(self, completed: PaymentCompleted, quote_symbol: str,
                     quantity: Decimal, price: Decimal, total_usdc: Decimal) -> Receipt:
        if completed.status == "settled":
            self.position.apply_buy(quantity, price)
        return Receipt(
            order_id=completed.order_id, symbol=quote_symbol, side="buy",
            quantity=quantity, total_usdc=total_usdc,
            tx_signature=completed.tx_signature, confirmed=completed.confirmed,
            note="" if completed.confirmed else "dry-run: 미브로드캐스트(로컬 서명만)",
        )

    # 3') 매도 완료 반영 — 포지션 차감 + 매도 대금을 예산에 환입
    def on_sale_completed(self, completed: PaymentCompleted, quote_symbol: str,
                          quantity: Decimal, price: Decimal, total_usdc: Decimal) -> Receipt:
        if completed.status == "settled":
            self.position.apply_sell(quantity)
            self.auth.credit_sale(total_usdc)
        return Receipt(
            order_id=completed.order_id, symbol=quote_symbol, side="sell",
            quantity=quantity, total_usdc=total_usdc,
            tx_signature=completed.tx_signature, confirmed=completed.confirmed,
            note="" if completed.confirmed else "dry-run: 미브로드캐스트(로컬 서명만)",
        )
