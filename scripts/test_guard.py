"""402 Guard 단위 테스트 — check_demand 6종 차단 + 정상 오탐 0 + check_delivery.

실행:  .venv/Scripts/python.exe -m scripts.test_guard   (네트워크 불필요)

핵심 검증: 결정적 공격 시나리오(한도 안쪽 금액 위조 / 수취인 스왑)가 서명 직전에
차단되고, 정상 청구서 14건은 오탐 0으로 통과한다.
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from solders.keypair import Keypair
from solders.pubkey import Pubkey

import config  # noqa: F401 — 임포트 시 콘솔 인코딩 안전화
from agents.broker_agent import BrokerAgent
from payments.ap2_mandate import OpenPaymentMandate
from payments.guard import (
    Guard,
    GUARD_AMOUNT_MISMATCH, GUARD_INTENT_EXCEEDED, GUARD_PAYEE_UNKNOWN, GUARD_ASSET_MISMATCH,
    GUARD_SYMBOL_NOT_ALLOWED, GUARD_LIMIT_EXCEEDED, GUARD_ORDER_INVALID,
    GUARD_DELIVERY_UNCONFIRMED, GUARD_ORDER_MISMATCH,
)
from shared.a2a_messages import PaymentRequired, PaymentRequirements, PaymentCompleted
from shared.models import Quote

_fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _fail
    tag = "[OK  ]" if cond else "[FAIL]"
    if not cond:
        _fail += 1
    print(f"{tag} {name}" + (f" — {extra}" if extra else ""))


# ---- 공통 픽스처 ----
USER = Keypair()
BROKER = Keypair()
EVIL = Keypair()
USDC = str(Keypair().pubkey())
OTHER_MINT = str(Keypair().pubkey())
SYMBOL = "tAAPL"
DECIMALS = 6

MANDATE = OpenPaymentMandate(
    user_pubkey=str(USER.pubkey()), allowed_asset=USDC,
    budget_total_usdc=Decimal("100"), per_trade_max_usdc=Decimal("45"),
    allowed_symbols=[SYMBOL],
).sign(USER)

GUARD = Guard(MANDATE, [str(BROKER.pubkey())], DECIMALS)


def make_required(order_id="ord_00aabb1122", amount_base=32_100_000,
                  pay_to=None, asset=USDC, symbol=SYMBOL) -> PaymentRequired:
    reqs = PaymentRequirements(
        scheme="exact", network="solana-localnet", asset=asset, amount=amount_base,
        pay_to=pay_to if pay_to is not None else str(BROKER.pubkey()),
        resource=f"STOCK:{symbol} x0.18", decimals=DECIMALS,
    )
    return PaymentRequired(order_id=order_id, symbol=symbol, quantity="0.18",
                           price_usdc="178.00", requirements=reqs)


# 합의 견적: 총액 32.10 USDC (= 32,100,000 base units)
QUOTE = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.18"),
              total_usdc=Decimal("32.10"), subtotal_usdc=Decimal("32.01"),
              fee_usdc=Decimal("0.09"), fee_bps=30)


def test_demand() -> None:
    print("\n== check_demand — 정상 통과(오탐 0) ==")
    res = GUARD.check_demand(make_required(), QUOTE)
    check("정상 청구서 통과", res.ok, res.detail)
    check("통과 결과에 방어 위치 기록", res.where.startswith("guard.py:L"), res.where)

    print("\n== check_demand — 결정적 공격 2종 ==")
    # 공격 1: 한도(45) 안쪽 금액으로 위조 — 44.94 청구, 견적은 32.10
    atk = make_required(amount_base=44_940_000)
    r1 = GUARD.check_demand(atk, QUOTE)
    check("금액 위조 차단(GUARD_AMOUNT_MISMATCH)", (not r1.ok) and r1.code == GUARD_AMOUNT_MISMATCH,
          f"{r1.code} {r1.expected}!={r1.actual} @ {r1.where}")

    # 공격 2: 수취인만 악성 지갑으로 스왑 (금액은 정상 32.10)
    atk2 = make_required(pay_to=str(EVIL.pubkey()))
    r2 = GUARD.check_demand(atk2, QUOTE)
    check("수취인 스왑 차단(GUARD_PAYEE_UNKNOWN)", (not r2.ok) and r2.code == GUARD_PAYEE_UNKNOWN,
          f"{r2.code} @ {r2.where}")

    print("\n== check_demand — 나머지 차단 코드 ==")
    # 자산 불일치
    r3 = GUARD.check_demand(make_required(asset=OTHER_MINT), QUOTE)
    check("자산 불일치 차단(GUARD_ASSET_MISMATCH, 결함 C)",
          (not r3.ok) and r3.code == GUARD_ASSET_MISMATCH, r3.where)
    # 미허용 종목
    r4 = GUARD.check_demand(make_required(symbol="tTSLA"), QUOTE)
    check("미허용 종목 차단(GUARD_SYMBOL_NOT_ALLOWED)",
          (not r4.ok) and r4.code == GUARD_SYMBOL_NOT_ALLOWED, r4.where)
    # 건별 한도 초과 — 견적/청구는 정합(50.00)이나 한도(45) 초과
    over_quote = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.28"),
                       total_usdc=Decimal("50.00"), subtotal_usdc=Decimal("49.85"),
                       fee_usdc=Decimal("0.15"), fee_bps=30)
    r5 = GUARD.check_demand(make_required(amount_base=50_000_000), over_quote)
    check("건별 한도 초과 차단(GUARD_LIMIT_EXCEEDED)",
          (not r5.ok) and r5.code == GUARD_LIMIT_EXCEEDED, r5.where)
    # 주문번호 형식 오류
    r6 = GUARD.check_demand(make_required(order_id="not-an-order"), QUOTE)
    check("주문번호 형식 오류 차단(GUARD_ORDER_INVALID)",
          (not r6.ok) and r6.code == GUARD_ORDER_INVALID, r6.where)
    # 주문번호 기대치 불일치
    r7 = GUARD.check_demand(make_required(order_id="ord_deadbeef00"), QUOTE,
                            expected_order_id="ord_00aabb1122")
    check("주문번호 기대 불일치 차단(GUARD_ORDER_INVALID)",
          (not r7.ok) and r7.code == GUARD_ORDER_INVALID, r7.where)


def test_intent_ceiling() -> None:
    print("\n== check_demand — 의도 지출 상한(GUARD_INTENT_EXCEEDED, BUG-03) ==")
    # 정직한 견적: 의도 30, 총액 29.99(수수료 포함, 의도 이하) → 통과
    honest = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.1680"),
                   total_usdc=Decimal("29.99"), subtotal_usdc=Decimal("29.90"),
                   fee_usdc=Decimal("0.09"), fee_bps=30)
    r_ok = GUARD.check_demand(make_required(amount_base=29_990_000), honest,
                              max_spend_usdc=Decimal("30"))
    check("정직한 견적(총액 29.99 <= 의도 30) 통과", r_ok.ok, r_ok.code)

    # 악성/버그 브로커: 청구서·견적이 자기정합(둘 다 44.94, 한도 45 안쪽)이지만 의도는 30 → 차단
    forged = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.25"),
                   total_usdc=Decimal("44.94"), subtotal_usdc=Decimal("44.94"),
                   fee_usdc=Decimal("0"), fee_bps=0)
    r_bad = GUARD.check_demand(make_required(amount_base=44_940_000), forged,
                               max_spend_usdc=Decimal("30"))
    check("의도 초과 자기정합 청구 차단(GUARD_INTENT_EXCEEDED)",
          (not r_bad.ok) and r_bad.code == GUARD_INTENT_EXCEEDED,
          f"{r_bad.code} {r_bad.expected}<{r_bad.actual} @ {r_bad.where}")

    # 하위호환: max_spend 미지정이면 의도검사 스킵 → 자기정합 44.94 는 통과(과거 동작 유지)
    r_skip = GUARD.check_demand(make_required(amount_base=44_940_000), forged)
    check("max_spend 미지정 시 의도검사 스킵(하위호환)", r_skip.ok, r_skip.code)

    # 고수수료(100bps) 정직 견적: 실 BrokerAgent 의 subtotal·fee 이중 센트반올림으로 total 이
    # 의도를 미세 초과할 수 있으나 2센트 허용치가 흡수해야 한다(검증 워크플로우가 잡은 회귀 —
    # 1센트 고정 허용치면 이 정직 견적이 GUARD_INTENT_EXCEEDED 로 오탐 차단됐다).
    hbk = BrokerAgent(BROKER, Pubkey.from_string(USDC), DECIMALS, None, DECIMALS, "n", fee_bps=100)
    hspend = Decimal("3.529994")
    hq = hbk.quote("tAAPL", hspend, Decimal("27.52"))
    hreq = hbk.make_payment_required(hq)
    r_hi = GUARD.check_demand(hreq, hq, expected_order_id=hreq.order_id, max_spend_usdc=hspend)
    check("고수수료 정직 견적 오탐 없음(2센트 허용)", r_hi.ok, f"{r_hi.code} total={hq.total_usdc}")


def test_no_false_positive() -> None:
    print("\n== check_demand — 정상 거래 14건 오탐 0 ==")
    fp = 0
    for i in range(14):
        # 금액/수량은 달라도 견적과 청구가 정합하면 통과해야 한다
        base = 10_000_000 + i * 1_500_000       # 10.0 ~ 29.5 USDC
        total = Decimal(base) / Decimal(10 ** DECIMALS)
        q = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"),
                  quantity=(total / Decimal("178.00")).quantize(Decimal("0.0001")),
                  total_usdc=total, subtotal_usdc=total, fee_usdc=Decimal("0"), fee_bps=0)
        oid = f"ord_{i:010x}"
        res = GUARD.check_demand(make_required(order_id=oid, amount_base=base), q,
                                 expected_order_id=oid)
        if not res.ok:
            fp += 1
            print(f"   오탐! i={i} {res.code} {res.detail}")
    check("정상 14건 오탐 0", fp == 0, f"오탐 {fp}건")


async def _delivery_cases() -> None:
    print("\n== check_delivery — 온체인 재조회 ==")
    oid = "ord_00aabb1122"
    before, inc = 5_000_000, 180_000  # 0.18 주 (stock decimals 6)
    completed = PaymentCompleted(order_id=oid, tx_signature="sig1", confirmed=True,
                                 delivered_asset=OTHER_MINT, delivered_amount=inc,
                                 status="settled")

    async def arrived():
        return before + inc

    async def nothing():
        return before

    r_ok = await GUARD.check_delivery(completed, signed_order_id=oid, balance_reader=arrived,
                                      before_units=before, expected_increase_units=inc)
    check("정상 배송 확인(온체인 +도착)", r_ok.ok, r_ok.detail)

    r_un = await GUARD.check_delivery(completed, signed_order_id=oid, balance_reader=nothing,
                                      before_units=before, expected_increase_units=inc)
    check("미배송 보류(GUARD_DELIVERY_UNCONFIRMED)",
          (not r_un.ok) and r_un.code == GUARD_DELIVERY_UNCONFIRMED, r_un.where)

    mism = PaymentCompleted(order_id="ord_ffffffffff", tx_signature="sig2", confirmed=True,
                            delivered_asset=OTHER_MINT, delivered_amount=inc, status="settled")
    r_mm = await GUARD.check_delivery(mism, signed_order_id=oid, balance_reader=arrived,
                                      before_units=before, expected_increase_units=inc)
    check("정산 주문번호 불일치 차단(GUARD_ORDER_MISMATCH)",
          (not r_mm.ok) and r_mm.code == GUARD_ORDER_MISMATCH, r_mm.where)


def main() -> int:
    test_demand()
    test_intent_ceiling()
    test_no_false_positive()
    asyncio.run(_delivery_cases())
    print("\n결과: " + ("모든 테스트 통과" if _fail == 0 else f"{_fail}건 실패"))
    return _fail


if __name__ == "__main__":
    raise SystemExit(main())
