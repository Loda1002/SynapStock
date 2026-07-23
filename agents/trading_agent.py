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
    """매매 전략 — condition(조건형, 임계값+Gemini 판단) / dca(적립형, 주기 정액 매수).

    B7: 적립형은 판단 없이 주기마다 정액 매수만 한다(매도 없음).
    주기 기준(dca_unit)은 사람이 고른다 — ticks(N틱마다) / minutes(N분마다) /
    daily(매일 지정 시각). AP2 mandate 검사는 어느 모드든 같은 결제 경로를 지난다."""
    buy_below: Decimal
    sell_above: Decimal
    spend_per_trade_usdc: Decimal
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
        self.HISTORY_MAX = 30              # MA20 계산 + 여유분
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

    # 1) 판단 — 적립형이면 스케줄 매수, 조건형이면 Gemini(있으면) → 실패 시 규칙 폴백
    def decide(self, symbol: str, price: Decimal) -> Decision:
        self.position.symbol = symbol
        history = list(self._history)
        self._history.append(price)
        if len(self._history) > self.HISTORY_MAX:
            self._history.pop(0)

        if self.strategy.mode == "dca":
            return self._decide_dca()

        if self.brain is not None:
            try:
                d = self.brain.decide(
                    symbol, price, history[-8:], self.strategy,
                    self.auth.remaining_usdc, self.position,
                    fee_bps=self.fee_bps,
                )
                return self._sanitize(d)
            except Exception as e:
                d = self._decide_by_rule(symbol, price)
                d.source = "rule-fallback"
                detail = str(e).replace("\n", " ")[:100]  # 실제 원인 표면화 (예: 429 쿼터 초과)
                d.reason += f" — Gemini 호출 실패({type(e).__name__}: {detail}) → 규칙 폴백"
                return d
        return self._decide_by_rule(symbol, price)

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

    def _decide_by_rule(self, symbol: str, price: Decimal) -> Decision:
        if price <= self.strategy.buy_below and self.auth.remaining_usdc > 0:
            spend = min(self.strategy.spend_per_trade_usdc, self.auth.remaining_usdc)
            return Decision("buy", f"가격 {price} ≤ 매수기준 {self.strategy.buy_below}", spend)
        if price >= self.strategy.sell_above and self.position.quantity > 0:
            return Decision("sell", f"가격 {price} ≥ 매도기준 {self.strategy.sell_above}")
        return Decision("hold", f"조건 미충족 (가격 {price})")

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
