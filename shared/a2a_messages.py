"""A2A + x402 메시지 정의.

a2a-x402 공식 3단계 흐름을 따른다:
  1) payment-required   (Broker → Trading)  "이거 사려면 X USDC 내"
  2) payment-submitted  (Trading → Broker)  서명된 결제 트랜잭션 제출
  3) payment-completed  (Broker → Trading)  온체인 검증 후 상품 전달

각 메시지는 JSON 직렬화 가능한 dict 로 오갈 수 있게 to_dict/from_dict 를 제공한다
(추후 A2A/HTTP 전송에 그대로 사용).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict


X402_VERSION = 1


def _as_str(v: Any, name: str) -> str:
    """문자열 필드는 '문자열일 때만' 받는다.

    str(v) 로 감싸면 {} 가 "{}" 로, 123 이 "123" 으로 **조용히 통과**한다 — 형식이 틀린
    헤더를 정상 결제로 받아들이는 셈이다. 형이 다르면 TypeError 로 거절하고, 호출부
    (payments.x402_http.decode_payment_header)가 X402ProtocolError → 400 으로 바꾼다.
    """
    if not isinstance(v, str):
        raise TypeError(f"{name} 은 문자열이어야 합니다 (받은 형: {type(v).__name__})")
    return v


def _as_int(v: Any, name: str) -> int:
    """정수 필드. bool·float 는 거절한다 — int(1.5) 는 1 로 조용히 절삭된다.

    숫자가 아닌 문자열은 int() 가 ValueError 를 내고, 호출부가 같은 400 으로 바꾼다.
    """
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise TypeError(f"{name} 은 정수여야 합니다 (받은 형: {type(v).__name__})")
    return int(v)


@dataclass
class PaymentRequirements:
    """x402 결제 요구사항 (payment-required 본문)."""
    scheme: str            # "exact"
    network: str           # "solana-devnet"
    asset: str             # 결제 토큰 민트 (USDC)
    amount: int            # base units (정수)
    pay_to: str            # 수취 지갑 pubkey
    resource: str          # 무엇에 대한 결제인지 (예: "STOCK:tAAPL x0.28")
    decimals: int
    x402_version: int = X402_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x402Version": self.x402_version,
            "scheme": self.scheme,
            "network": self.network,
            "asset": self.asset,
            "amount": self.amount,
            "payTo": self.pay_to,
            "resource": self.resource,
            "decimals": self.decimals,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaymentRequirements":
        return cls(
            scheme=d["scheme"], network=d["network"], asset=d["asset"],
            amount=int(d["amount"]), pay_to=d["payTo"], resource=d["resource"],
            decimals=int(d["decimals"]), x402_version=int(d.get("x402Version", X402_VERSION)),
        )


@dataclass
class PaymentPayload:
    """서명된 결제 증빙 (payment-submitted 본문)."""
    network: str
    serialized_transaction: str  # base64 인코딩된 서명 트랜잭션
    scheme: str = "exact"
    x402_version: int = X402_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x402Version": self.x402_version,
            "scheme": self.scheme,
            "network": self.network,
            "payload": {"serializedTransaction": self.serialized_transaction},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaymentPayload":
        """무인증 HTTP 헤더에서 오는 값이라 형을 믿지 않는다 — 위 _as_str/_as_int 참고."""
        return cls(
            network=_as_str(d["network"], "network"),
            serialized_transaction=_as_str(
                d["payload"]["serializedTransaction"], "payload.serializedTransaction"),
            scheme=_as_str(d.get("scheme", "exact"), "scheme"),
            x402_version=_as_int(d.get("x402Version", X402_VERSION), "x402Version"),
        )


# ---- A2A 메시지 봉투 ----

@dataclass
class PaymentRequired:
    order_id: str
    symbol: str
    quantity: str            # Decimal 문자열
    price_usdc: str
    requirements: PaymentRequirements
    kind: str = "payment-required"


@dataclass
class PaymentSubmitted:
    order_id: str
    payment: PaymentPayload
    kind: str = "payment-submitted"


@dataclass
class PaymentCompleted:
    order_id: str
    tx_signature: str
    confirmed: bool
    delivered_asset: str     # 전달된 주식토큰 민트
    delivered_amount: int    # base units
    delivery_tx_signature: str = ""  # 주식토큰 전달 tx (라이브 정산 시)
    status: str = "settled"  # settled / failed / partial
    reason: str = ""         # 실패/보류 사유 (이중청구·만료·검증 실패 등)
    kind: str = "payment-completed"
