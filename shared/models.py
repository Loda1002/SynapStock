"""도메인 모델: 주문, 견적, 포지션."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class Quote:
    """Broker가 제시하는 견적."""
    symbol: str
    price_usdc: Decimal          # 주식토큰 1개당 USDC 가격
    quantity: Decimal            # 매수 수량(주식토큰 개수)
    total_usdc: Decimal          # 총 결제액(USDC)


@dataclass
class Position:
    """Trading Agent가 보유한 포지션."""
    symbol: str
    quantity: Decimal = Decimal(0)
    avg_price_usdc: Decimal = Decimal(0)

    def apply_buy(self, qty: Decimal, price: Decimal) -> None:
        new_qty = self.quantity + qty
        if new_qty > 0:
            self.avg_price_usdc = (
                (self.avg_price_usdc * self.quantity + price * qty) / new_qty
            ).quantize(Decimal("0.01"))
        self.quantity = new_qty

    def apply_sell(self, qty: Decimal) -> None:
        self.quantity = max(Decimal(0), self.quantity - qty)


@dataclass
class Receipt:
    """정산 완료 영수증."""
    order_id: str
    symbol: str
    side: str                    # "buy" / "sell"
    quantity: Decimal
    total_usdc: Decimal
    tx_signature: str
    confirmed: bool
    note: str = ""
