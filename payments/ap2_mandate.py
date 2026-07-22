"""AP2 (Agent Payments Protocol) — 결제 권한/한도 레이어.

해커톤 핵심 요구사항인 "사람 승인 없이 정해진 한도 내에서 자율 결제"를
암호 서명된 mandate 로 표현한다.

  - OpenPaymentMandate  : 사용자가 미리 설정하는 제약(예산·건별 한도·허용 자산/종목).
                          사용자 키로 서명 → 위임 근거.
  - ClosedPaymentMandate: 특정 거래 1건에 대한 결제 승인. 에이전트가 한도 내에서
                          자율 생성·서명 → 부인 불가(non-repudiable) 감사 기록.

실제 ed25519 서명(solders)을 사용하므로 검증 가능하다. 오프라인 동작.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class OpenPaymentMandate:
    """사용자가 설정하는 자율 결제 한도."""
    user_pubkey: str
    allowed_asset: str            # USDC 민트
    budget_total_usdc: Decimal
    per_trade_max_usdc: Decimal
    allowed_symbols: List[str]
    signature: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))

    def _payload(self) -> dict:
        return {
            "type": "open-payment-mandate",
            "user": self.user_pubkey,
            "asset": self.allowed_asset,
            "budgetTotal": str(self.budget_total_usdc),
            "perTradeMax": str(self.per_trade_max_usdc),
            "symbols": sorted(self.allowed_symbols),
            "createdAt": self.created_at,
        }

    def sign(self, user_kp: Keypair) -> "OpenPaymentMandate":
        sig = user_kp.sign_message(_canonical(self._payload()))
        self.signature = str(sig)
        return self

    def verify(self) -> bool:
        if not self.signature:
            return False
        try:
            pub = Pubkey.from_string(self.user_pubkey)
            sig = Signature.from_string(self.signature)
            return sig.verify(pub, _canonical(self._payload()))
        except Exception:
            return False


@dataclass
class ClosedPaymentMandate:
    """특정 거래 1건에 대한 결제 승인(서명됨)."""
    order_id: str
    symbol: str
    amount_usdc: Decimal
    pay_to: str
    open_mandate_sig: str          # 근거가 된 open mandate 서명
    signature: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))

    def _payload(self) -> dict:
        return {
            "type": "closed-payment-mandate",
            "orderId": self.order_id,
            "symbol": self.symbol,
            "amount": str(self.amount_usdc),
            "payTo": self.pay_to,
            "openMandate": self.open_mandate_sig,
            "createdAt": self.created_at,
        }

    def sign(self, agent_kp: Keypair) -> "ClosedPaymentMandate":
        self.signature = str(agent_kp.sign_message(_canonical(self._payload())))
        return self


class MandateError(Exception):
    pass


class PaymentAuthorizer:
    """Open mandate 를 들고 다니며 거래 요청을 한도 내에서 승인/거부."""

    def __init__(self, open_mandate: OpenPaymentMandate, agent_kp: Keypair):
        if not open_mandate.verify():
            raise MandateError("open mandate 서명이 유효하지 않습니다.")
        self.open = open_mandate
        self.agent_kp = agent_kp
        self.spent_usdc: Decimal = Decimal(0)

    @property
    def remaining_usdc(self) -> Decimal:
        return self.open.budget_total_usdc - self.spent_usdc

    def authorize(self, order_id: str, symbol: str, amount_usdc: Decimal, pay_to: str) -> ClosedPaymentMandate:
        """한도 검사 후 통과 시 서명된 closed mandate 반환, 실패 시 MandateError."""
        if symbol not in self.open.allowed_symbols:
            raise MandateError(f"허용되지 않은 종목: {symbol}")
        if amount_usdc > self.open.per_trade_max_usdc:
            raise MandateError(
                f"건별 한도 초과: {amount_usdc} > {self.open.per_trade_max_usdc}"
            )
        if amount_usdc > self.remaining_usdc:
            raise MandateError(
                f"총 예산 초과: {amount_usdc} > 잔여 {self.remaining_usdc}"
            )
        closed = ClosedPaymentMandate(
            order_id=order_id, symbol=symbol, amount_usdc=amount_usdc,
            pay_to=pay_to, open_mandate_sig=self.open.signature or "",
        ).sign(self.agent_kp)
        self.spent_usdc += amount_usdc
        return closed

    def credit_sale(self, amount_usdc: Decimal) -> None:
        """매도 대금 환입 — 예산(budget_total)은 '순투입 한도'로 해석한다.

        판 만큼 spent 가 줄어 다시 매수에 쓸 수 있다. 0 밑으로는 내려가지 않음
        (매수 원금보다 비싸게 팔아도 한도가 늘어나지는 않는다)."""
        self.spent_usdc = max(Decimal(0), self.spent_usdc - amount_usdc)
