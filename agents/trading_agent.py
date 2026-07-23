"""Trading (구매) 에이전트 — 사용자 대리.

책임: 시세+규칙으로 매수/매도 판단 → AP2 한도 승인 → 결제 트랜잭션 서명(x402)
→ payment-completed 반영(포지션 갱신).

`decide()` 의 규칙 부분이 이후 Gemini(ADK) 로 교체될 지점이다.
현재는 사용자 정의 임계값 규칙(데모용, 투자 조언 아님).
"""
from __future__ import annotations
from dataclasses import dataclass
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
    """데모용 규칙. (Gemini 로 교체 예정)"""
    buy_below: Decimal
    sell_above: Decimal
    spend_per_trade_usdc: Decimal


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
        self._history: list[Decimal] = []  # 직전 시세 (Gemini 판단 근거)

    @property
    def pubkey(self) -> Pubkey:
        return self.kp.pubkey()

    # 1) 판단 — Gemini(있으면) → 실패 시 규칙 폴백
    def decide(self, symbol: str, price: Decimal) -> Decision:
        self.position.symbol = symbol
        history = list(self._history)
        self._history.append(price)
        if len(self._history) > 8:
            self._history.pop(0)

        if self.brain is not None:
            try:
                d = self.brain.decide(
                    symbol, price, history, self.strategy,
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
