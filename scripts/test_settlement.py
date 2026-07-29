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
from shared.a2a_messages import PaymentCompleted

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


async def test_buy_delivery_failure():
    """매수 레그: USDC 는 온체인 확정됐는데 주식 전달이 실패하면 partial (BUG-01).

    매도 레그(settle_sale)에는 이미 있던 3분기가 매수 레그에만 빠져 있었다 — 구매자가
    돈을 보냈는데 주식이 한 주도 안 온 거래가 settled 로 보고돼, 아카이브에 '성공'으로
    남고 앱이 유령 포지션으로 계속 매매했다."""
    print("\n== settle — 매수 주식 전달 실패 시 partial (BUG-01, 매도 대칭) ==")
    ta, bk = _agents()
    quote = bk.quote("tAAPL", Decimal("30"), Decimal("178.00"))

    # USDC 확정 + 주식 전달 실패 → partial
    required = bk.make_payment_required(quote)
    submitted = ta.build_payment(required, Hash.default(), quote)
    c_fail = await bk.settle(submitted, required.requirements, quote.quantity,
                             live=True, client=FakeClient([True, False]))
    check("USDC확정·주식전달실패 → partial (과거 settled 오정산)",
          c_fail.status == "partial", f"status={c_fail.status}")
    check("미배송이면 delivery_tx 비어있음", c_fail.delivery_tx_signature == "",
          c_fail.delivery_tx_signature)
    check("보내지도 않은 수량을 delivered_amount 에 싣지 않는다",
          c_fail.delivered_amount == 0, str(c_fail.delivered_amount))
    check("사유가 미배송임을 밝힌다", "미배송" in c_fail.reason, c_fail.reason)

    # 다운스트림: partial 매수는 포지션을 반영하지 않는다(유령 포지션 방지)
    before = ta.position.quantity
    ta.on_completed(c_fail, "tAAPL", quote.quantity, quote.price_usdc, quote.total_usdc)
    check("partial 매수는 포지션 미반영", ta.position.quantity == before,
          f"{before} → {ta.position.quantity}")

    # 둘 다 확정 → settled
    required2 = bk.make_payment_required(quote)
    submitted2 = ta.build_payment(required2, Hash.default(), quote)
    c_ok = await bk.settle(submitted2, required2.requirements, quote.quantity,
                           live=True, client=FakeClient([True, True]))
    check("USDC확정·주식전달확정 → settled", c_ok.status == "settled", f"status={c_ok.status}")
    check("정상 배송이면 수량이 실린다", c_ok.delivered_amount > 0, str(c_ok.delivered_amount))

    # USDC 미확정 → failed (전달 시도 자체 없음)
    required3 = bk.make_payment_required(quote)
    submitted3 = ta.build_payment(required3, Hash.default(), quote)
    c_no = await bk.settle(submitted3, required3.requirements, quote.quantity,
                           live=True, client=FakeClient([False]))
    check("USDC 미확정 → failed", c_no.status == "failed", f"status={c_no.status}")

    # 드라이런은 종전대로 settled (오프라인 데모 경로 무변경)
    required4 = bk.make_payment_required(quote)
    submitted4 = ta.build_payment(required4, Hash.default(), quote)
    c_dry = await bk.settle(submitted4, required4.requirements, quote.quantity, live=False)
    check("드라이런은 그대로 settled", c_dry.status == "settled", f"status={c_dry.status}")
    check("드라이런은 수량이 실린다", c_dry.delivered_amount > 0, str(c_dry.delivered_amount))


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

    # 다운스트림: partial 매도(BUG-05) — '주식이 나갔는가'와 '대금이 들어왔는가'는 다른 사건이다.
    # 매도의 partial 은 confirmed and not paid, 즉 주식 전송 tx 는 온체인에서 확정됐고
    # USDC 지급만 실패한 상태다. 주식은 실제로 지갑을 떠났으므로 포지션은 차감해야 한다
    # (예전에는 남겨 둬서 총자산 카드가 부풀었다 — 유출 KPI 는 정확한데 총자산만 낙관).
    # 반대로 대금은 못 받았으므로 예산 환입은 여전히 없어야 한다.
    ta.position.apply_buy(qty, Decimal("170"))
    ta.auth.spent_usdc = Decimal("50")
    r_partial = ta.on_sale_completed(c_fail, "tAAPL", qty, price, sq.total_usdc)
    check("partial 매도는 포지션 차감 (주식은 전송 확정 = 지갑을 떠났다)",
          ta.position.quantity == Decimal("0"), str(ta.position.quantity))
    check("partial 매도는 예산 미환입", ta.auth.spent_usdc == Decimal("50"),
          str(ta.auth.spent_usdc))
    check("partial 매도 영수증이 정상 매도와 구분된다",
          "대금 미확인" in r_partial.note, r_partial.note or "<빈 note>")

    # 대조군: 정상(settled) 매도는 포지션 차감 + 예산 환입, note 는 비어 있다
    ta.position.apply_buy(qty, Decimal("170"))
    ta.auth.spent_usdc = Decimal("50")
    r_ok = ta.on_sale_completed(c_ok, "tAAPL", qty, price, sq.total_usdc)
    check("[대조군] settled 매도는 포지션 차감", ta.position.quantity == Decimal("0"),
          str(ta.position.quantity))
    check("[대조군] settled 매도는 예산 환입", ta.auth.spent_usdc < Decimal("50"),
          str(ta.auth.spent_usdc))
    check("[대조군] settled 매도 영수증 note 는 비어 있다", r_ok.note == "", r_ok.note)


class _Amt:
    def __init__(self, amount):
        self.amount = amount


class _RaiseClient:
    def __init__(self, exc):
        self._exc = exc

    async def get_token_account_balance(self, ata):
        raise self._exc


class _OkClient:
    async def get_token_account_balance(self, ata):
        return _V(_Amt("5000000"))


def test_baseline_read_classification():
    print("\n== get_token_balance_base — 기준선 오염 방지 (BUG-01) ==")
    owner, mint = Keypair().pubkey(), Keypair().pubkey()

    # ATA 미존재(진짜 잔액 0) → 0
    nf = asyncio.run(x.get_token_balance_base(
        _RaiseClient(Exception("Invalid param: could not find account")), owner, mint))
    check("계정 미존재 → 0", nf == 0, str(nf))

    # 불명 오류(연결 실패 등) → 전파 (0 으로 삼키면 기준선 오염 → 미배송 오탐 통과)
    raised = False
    try:
        asyncio.run(x.get_token_balance_base(
            _RaiseClient(Exception("connection reset - unknown transport failure")), owner, mint))
    except Exception:
        raised = True
    check("불명 오류 → 전파(0 삼킴 금지)", raised)

    # 정상 조회 → 잔액 정수
    okv = asyncio.run(x.get_token_balance_base(_OkClient(), owner, mint))
    check("정상 조회 → 잔액 정수", okv == 5_000_000, str(okv))

    # 분류기: 429/일시적 오류는 '계정 미존재'가 아니다(전파 대상) / 미존재 신호만 True
    check("429 는 계정미존재 아님(전파)",
          not x._is_account_not_found(Exception("HTTP 429 Too Many Requests")))
    check("계정미존재 신호는 True", x._is_account_not_found(Exception("could not find account")))


def test_release_surplus():
    """추세추종의 복리 자본(음수 spent)이 release 로 증발하지 않는지 — bug-dept BUG-03.

    순서는 제품 그대로다: 매수 100 정산 → 150 에 매도 정산(on_sale_completed 가
    credit_sale(allow_surplus=True) 를 불러 spent 를 −50 으로 내린다 = 실현이익 재투자)
    → 재진입 매수 150 authorize → 그 결제가 온체인에서 실패해 release.
    예전에는 release 의 0 클램프가 음수 spent 를 끌어올려, 벌어 놓은 50 USDC 가 세션이
    끝날 때까지 한도에서 조용히 사라졌다(아무 로그도 남지 않는다)."""
    print("\n== release — 매도 잉여(복리 자본) 원복 (BUG-03) ==")
    mandate = OpenPaymentMandate(
        user_pubkey=str(USER.pubkey()), allowed_asset=str(USDC),
        budget_total_usdc=Decimal("100"), per_trade_max_usdc=Decimal("1000"),
        allowed_symbols=["tAAPL"],
    ).sign(USER)
    auth = PaymentAuthorizer(mandate, agent_kp=TRADING)
    ta = TradingAgent(TRADING, auth, Strategy(mode="trend"), 6, "solana-localnet")

    auth.authorize("ord_b1", "tAAPL", Decimal("100"), str(BROKER.pubkey()))
    auth.settle("ord_b1")
    ta.position.apply_buy(Decimal("1"), Decimal("100"))
    sold = PaymentCompleted(
        order_id="ord_s1", tx_signature="sig_sell", confirmed=True,
        delivered_asset=str(USDC), delivered_amount=150_000_000, status="settled")
    ta.on_sale_completed(sold, "tAAPL", Decimal("1"), Decimal("150"), Decimal("150"))
    check("매도 잉여 → spent 가 음수(복리 자본)",
          auth.spent_usdc == Decimal("-50"), str(auth.spent_usdc))

    auth.authorize("ord_b2", "tAAPL", Decimal("150"), str(BROKER.pubkey()))
    check("재진입 올인 매수 후 spent=100", auth.spent_usdc == Decimal("100"), str(auth.spent_usdc))
    auth.release("ord_b2")
    check("release 가 잉여까지 되돌린다 (과거엔 0 으로 잘려 50 증발)",
          auth.spent_usdc == Decimal("-50"), str(auth.spent_usdc))
    check("잔여 예산도 복구", auth.remaining_usdc == Decimal("150"), str(auth.remaining_usdc))
    again = auth.release("ord_b2")
    check("두 번 release 해도 한 번만 되돌린다(멱등)",
          again == Decimal(0) and auth.spent_usdc == Decimal("-50"), str(auth.spent_usdc))

    # 음성 대조 — 비잉여(조건형/적립형)는 credit_sale 쪽 클램프가 그대로 살아 있어야 한다.
    # 이쪽 0 클램프는 '순투입 한도' 정의라 의미가 있고, 이번 수정 대상이 아니다.
    auth2 = PaymentAuthorizer(mandate, agent_kp=TRADING)
    auth2.authorize("ord_c1", "tAAPL", Decimal("100"), str(BROKER.pubkey()))
    auth2.settle("ord_c1")
    auth2.credit_sale(Decimal("150"))
    check("조건형은 매도 잉여로 한도가 늘지 않는다(순투입 한도)",
          auth2.spent_usdc == Decimal(0), str(auth2.spent_usdc))


def main() -> int:
    test_exact()
    test_memo()
    test_memo_present_in_build_payment()
    asyncio.run(test_dedup_and_expiry())
    asyncio.run(test_buy_delivery_failure())
    asyncio.run(test_sell_payout_failure())
    test_baseline_read_classification()
    test_release_surplus()
    print("\n결과: " + ("모든 테스트 통과" if _fail == 0 else f"{_fail}건 실패"))
    return _fail


if __name__ == "__main__":
    raise SystemExit(main())
