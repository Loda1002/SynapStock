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
        # 주문별 예약분 — 정산 실패 시 원복(release)용. authorize 가 즉시 spent 에 반영하되,
        # 결제가 settled 되지 못하면 이 예약을 되돌려 한도를 원상복구한다(결함 H).
        self._reservations: dict[str, Decimal] = {}

    @property
    def remaining_usdc(self) -> Decimal:
        return self.open.budget_total_usdc - self.spent_usdc

    def authorize(self, order_id: str, symbol: str, amount_usdc: Decimal, pay_to: str,
                  asset: Optional[str] = None) -> ClosedPaymentMandate:
        """한도 검사 후 통과 시 서명된 closed mandate 반환, 실패 시 MandateError.

        asset 이 주어지면 mandate 가 허용한 결제 자산인지 검사한다 — allowed_asset 를
        실제로 읽는 유일한 지점(결함 C: 직렬화만 되고 검증엔 안 쓰이던 죽은 필드를 살린다).
        """
        # 하한 검사 — 예전에는 0 이하가 그냥 통과했다(bug-dept BUG-12). 수량 0 견적이 만든
        # 0원 청구서가 여기서 승인되고 `settled` 로 남는데 spent 는 그대로라, 잔여 예산이
        # 줄지 않아 **틱마다 무한히 반복**됐다(481틱 세션에서 실측). 정상 흐름은 엔진이
        # 먼저 거르므로 이 검사는 발동하지 않는다 — 어느 호출자가 오든 성립시키는 방어선이다.
        if amount_usdc <= 0:
            raise MandateError(f"결제 금액이 0 이하입니다: {amount_usdc}")
        if asset is not None and str(asset) != str(self.open.allowed_asset):
            raise MandateError(f"허용되지 않은 결제 자산: {asset} (허용 {self.open.allowed_asset})")
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
        self._reservations[order_id] = self._reservations.get(order_id, Decimal(0)) + amount_usdc
        return closed

    def release(self, order_id: str) -> Decimal:
        """정산 실패 시 예약분 원복 — 사용액(spent)을 되돌려 한도를 원상복구한다(결함 H).

        결제가 온체인에서 settled 되지 못했는데도 예산이 소진된 채로 남던 문제를 막는다.
        멱등(idempotent) — 같은 주문을 두 번 release 해도 한 번만 되돌린다."""
        amt = self._reservations.pop(order_id, None)
        if amt is None:
            return Decimal(0)
        # ⚠ 여기에 0 클램프를 두면 안 된다(bug-dept BUG-03). 추세추종은 아래 credit_sale
        # (allow_surplus=True)로 spent 를 의도적으로 음수까지 내려 '실현이익을 운용현금으로
        # 재투자'하는 복리를 표현한다. 그 상태에서 매수 1건이 release 되면 음수 spent 가 0 으로
        # 올라붙어, 벌어 놓은 운용 한도가 세션이 끝날 때까지 조용히 사라졌다(로그도 안 남는다).
        # 멱등성은 위 pop 이 담당하고 amt 는 항상 authorize 가 더한 값이므로, 비잉여 모드에서
        # 클램프는 애초에 발동할 수 없는 죽은 코드였다.
        self.spent_usdc -= amt
        return amt

    def settle(self, order_id: str) -> Decimal:
        """정산 성공 확정 — 예약을 확정 소비로 전환한다(이후 release 로 되돌지 않게)."""
        return self._reservations.pop(order_id, None) or Decimal(0)

    def credit_sale(self, amount_usdc: Decimal, allow_surplus: bool = False) -> None:
        """매도 대금 환입 — 예산(budget_total)은 '순투입 한도'로 해석한다.

        판 만큼 spent 가 줄어 다시 매수에 쓸 수 있다.

        allow_surplus=False (기본, 적립형): spent 는 0 밑으로 내려가지 않는다
        (매수 원금보다 비싸게 팔아도 한도가 늘어나지 않음 — 순투입은 예산까지).
        ⚠ 2026-08-03 부터 **조건형은 여기서 빠졌다**(→ allow_surplus=True). 실현이익이
          현금에 남지 않아, 포지션이 비는 순간 화면의 총자산이 늘 예산값으로 되돌아왔다
          (`agents/trading_agent.py` on_sale_completed 주석 참고). 적립형은 매수만 하는
          전략이라 이 경로 자체가 없어 기존 정의를 그대로 둔다.

        allow_surplus=True (매도가 있는 전략 — 추세추종 올인/올아웃 · 조건형): 매도 대금 전액을 환입해 spent 가
        음수까지 내려갈 수 있다(remaining = budget − spent > budget). 이는 '실현이익을
        운용현금으로 재투자'하는 복리를 표현한다. 사용자 예산(초기 자본)은 여전히 첫
        진입의 상한이고(remaining 초기값 = budget), 이후 매수는 '가진 현금(remaining)'을
        넘지 못한다(authorize 의 amount ≤ remaining 검사). 즉 사용자 지갑에서 새로 끌어오는
        순금액은 예산을 넘지 않고, 초과분은 시장에서 번 이익의 재투자다."""
        self.spent_usdc = self.spent_usdc - amount_usdc
        if not allow_surplus:
            self.spent_usdc = max(Decimal(0), self.spent_usdc)
