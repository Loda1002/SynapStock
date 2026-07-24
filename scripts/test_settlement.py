"""G4 정산 계층 테스트 — exact 정합 · Memo 대사 · 이중청구 dedup · 청구서 만료.

실행:  .venv/Scripts/python.exe -m scripts.test_settlement   (네트워크 불필요)
"""
from __future__ import annotations
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from solders.keypair import Keypair
from solders.hash import Hash

import config  # noqa: F401
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer
from payments.guard import Guard
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy

_fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _fail
    print(("[OK  ] " if cond else "[FAIL] ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        _fail += 1


USER = Keypair()
TRADING = Keypair()
BROKER = Keypair()
USDC = Keypair().pubkey()
STOCK = Keypair().pubkey()


# ---- settle_sale 의 라이브 경로(주식 확정 → USDC 지급)만 오프라인으로 태우는 최소 스텁 ----
class _V:
    def __init__(self, value):
        self.value = value


class _Conf:
    def __init__(self, err):
        self.err = err


class _BH:
    blockhash = Hash.default()


class FakeClient:
    """submit_and_confirm / get_latest_blockhash 만 흉내낸다.
    confirm_results: confirm_transaction 호출 순서대로의 성공 여부(True=err None)."""

    def __init__(self, confirm_results):
        self._confirms = list(confirm_results)
        self._i = 0
        self._sig = 0

    async def send_raw_transaction(self, raw, opts=None):
        self._sig += 1
        return _V(f"fakesig{self._sig}")

    async def confirm_transaction(self, sig, commitment=None):
        ok = self._confirms[self._i] if self._i < len(self._confirms) else True
        self._i += 1
        return _V([_Conf(None if ok else "InstructionError")])

    async def get_latest_blockhash(self):
        return _V(_BH())


def _agents():
    mandate = OpenPaymentMandate(
        user_pubkey=str(USER.pubkey()), allowed_asset=str(USDC),
        budget_total_usdc=Decimal("1000"), per_trade_max_usdc=Decimal("50"),
        allowed_symbols=["tAAPL"],
    ).sign(USER)
    auth = PaymentAuthorizer(mandate, agent_kp=TRADING)
    ta = TradingAgent(TRADING, auth, Strategy(), 6, "solana-localnet")
    ta.guard = Guard(mandate, [str(BROKER.pubkey())], 6)
    bk = BrokerAgent(BROKER, USDC, 6, STOCK, 6, "solana-localnet", fee_bps=30)
    return ta, bk


def test_exact():
    print("\n== verify_payment — exact 정합 (< → !=) ==")
    tx = x.build_transfer_transaction(
        payer=TRADING, mint=USDC, dest_owner=BROKER.pubkey(),
        amount=32_100_000, decimals=6, blockhash=Hash.default(), memo="AT1:ord_00aabb1122:deadbeef")
    ok, _, _ = x.verify_payment(tx, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                expected_amount=32_100_000)
    check("정확히 일치하면 통과", ok)
    ov, rov, _ = x.verify_payment(tx, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                  expected_amount=20_000_000)
    check("초과지불 거부(과거엔 < 라 통과하던 결함 D)", not ov, rov)
    un, run, _ = x.verify_payment(tx, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                  expected_amount=44_940_000)
    check("부족지불 거부", not un, run)


def test_memo():
    print("\n== verify_payment — Memo 주문번호 대사 (결함 E) ==")
    tx = x.build_transfer_transaction(
        payer=TRADING, mint=USDC, dest_owner=BROKER.pubkey(),
        amount=32_100_000, decimals=6, blockhash=Hash.default(), memo="AT1:ord_abc0011223:deadbeef")
    ok, _, _ = x.verify_payment(tx, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                expected_amount=32_100_000, expected_order_id="ord_abc0011223")
    check("일치하는 주문번호 Memo 통과", ok)
    wr, rwr, _ = x.verify_payment(tx, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                  expected_amount=32_100_000, expected_order_id="ord_zzzzzzzzzz")
    check("다른 주문번호면 거부", not wr, rwr)
    tx2 = x.build_transfer_transaction(
        payer=TRADING, mint=USDC, dest_owner=BROKER.pubkey(),
        amount=32_100_000, decimals=6, blockhash=Hash.default(), memo=None)
    nm, rnm, _ = x.verify_payment(tx2, expected_mint=USDC, expected_dest_owner=BROKER.pubkey(),
                                  expected_amount=32_100_000, expected_order_id="ord_abc0011223")
    check("Memo 없으면 거부", not nm, rnm)


def test_memo_present_in_build_payment():
    print("\n== build_payment — 매수 tx 에 Memo 가 실제로 박히는가 ==")
    ta, bk = _agents()
    quote = bk.quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required = bk.make_payment_required(quote)
    submitted = ta.build_payment(required, Hash.default(), quote)
    tx = x.decode_payload(submitted.payment.serialized_transaction)
    keys = list(tx.message.account_keys)
    memos = [bytes(ix.data).decode("utf-8", "replace")
             for ix in tx.message.instructions
             if keys[ix.program_id_index] == x.MEMO_PROGRAM_ID]
    check("Memo instruction 1개 존재", len(memos) == 1, str(memos))
    check("Memo 가 AT1:{order_id} 로 시작",
          bool(memos) and memos[0].startswith(f"AT1:{required.order_id}:"),
          memos[0] if memos else "")


async def test_dedup_and_expiry():
    print("\n== settle — 이중청구 dedup · 청구서 만료 ==")
    ta, bk = _agents()
    # 정상 정산 1회 → settled, 같은 결제 재정산 → 이중청구 차단
    quote = bk.quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required = bk.make_payment_required(quote)
    submitted = ta.build_payment(required, Hash.default(), quote)
    c1 = await bk.settle(submitted, required.requirements, quote.quantity, live=False)
    check("정상 매수 정산 settled", c1.status == "settled", c1.reason)
    c2 = await bk.settle(submitted, required.requirements, quote.quantity, live=False)
    check("동일 결제 재정산 차단(이중청구)", c2.status == "failed" and "이중청구" in c2.reason, c2.reason)

    # 만료된 청구서 정산 거부
    quote2 = bk.quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required2 = bk.make_payment_required(quote2)
    submitted2 = ta.build_payment(required2, Hash.default(), quote2)
    bk._order_created[required2.order_id] = time.time() - (bk.order_ttl_sec + 5)
    c3 = await bk.settle(submitted2, required2.requirements, quote2.quantity, live=False)
    check("만료 청구서 정산 거부", c3.status == "failed" and "만료" in c3.reason, c3.reason)


async def test_sell_payout_failure():
    print("\n== settle_sale — 매도 대금(USDC) 지급 실패 시 partial (BUG-02) ==")
    ta, bk = _agents()
    price, qty = Decimal("178.00"), Decimal("0.18")
    sq = bk.sell_quote("tAAPL", qty, price)

    # 주식 수령 confirmed + USDC 지급 실패 → partial (과거엔 settled 로 오정산)
    required = bk.make_stock_required(sq)
    submitted = ta.build_stock_transfer(required, Hash.default())
    c_fail = await bk.settle_sale(submitted, required.requirements, sq.total_usdc,
                                  live=True, client=FakeClient([True, False]))
    check("주식수령·지급실패 → partial (과거 settled 오정산)",
          c_fail.status == "partial", f"status={c_fail.status}")
    check("지급 실패면 delivery_tx 비어있음", c_fail.delivery_tx_signature == "",
          c_fail.delivery_tx_signature)

    # 주식 confirmed + 지급 confirmed → settled
    required2 = bk.make_stock_required(sq)
    submitted2 = ta.build_stock_transfer(required2, Hash.default())
    c_ok = await bk.settle_sale(submitted2, required2.requirements, sq.total_usdc,
                                live=True, client=FakeClient([True, True]))
    check("주식수령·지급확정 → settled", c_ok.status == "settled", f"status={c_ok.status}")

    # 주식 미확정 → failed (지급 시도 자체 없음, 손실 없음)
    required3 = bk.make_stock_required(sq)
    submitted3 = ta.build_stock_transfer(required3, Hash.default())
    c_no = await bk.settle_sale(submitted3, required3.requirements, sq.total_usdc,
                                live=True, client=FakeClient([False]))
    check("주식 미확정 → failed", c_no.status == "failed", f"status={c_no.status}")

    # 다운스트림: partial 매도는 포지션·예산을 건드리지 않는다
    ta.position.apply_buy(qty, Decimal("170"))
    ta.auth.spent_usdc = Decimal("50")
    ta.on_sale_completed(c_fail, "tAAPL", qty, price, sq.total_usdc)
    check("partial 매도는 포지션 미차감", ta.position.quantity == qty, str(ta.position.quantity))
    check("partial 매도는 예산 미환입", ta.auth.spent_usdc == Decimal("50"),
          str(ta.auth.spent_usdc))


def main() -> int:
    test_exact()
    test_memo()
    test_memo_present_in_build_payment()
    asyncio.run(test_dedup_and_expiry())
    asyncio.run(test_sell_payout_failure())
    print("\n결과: " + ("모든 테스트 통과" if _fail == 0 else f"{_fail}건 실패"))
    return _fail


if __name__ == "__main__":
    raise SystemExit(main())
