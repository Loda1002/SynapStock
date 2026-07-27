"""청구서 의미 대조(payments/invoice_semantics.py) 단위 테스트.

실행: python -m scripts.test_invoice_semantics   (네트워크·Gemini 키 불필요)

검증 축 4개:
  1. 값은 다 맞는데 **물건만 다른** 청구서를 잡는다 (하드 검사 6종이 전부 통과한 뒤에).
  2. LLM 은 **차단만** 가능하다 — 하드 검사가 막은 것을 되살리는 경로가 없다.
  3. 검사 불가일 때 **매수는 차단, 매도는 진행** (노출을 늘리는 방향만 잠근다).
  4. 응답이 깨지면 **조용히 통과시키지 않는다**(항상 '검사 불가'로 떨어진다).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402

import config  # noqa: F401,E402 — 임포트 시 콘솔 인코딩 안전화
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer  # noqa: E402
from payments.guard import Guard, GuardError, GUARD_PAYEE_UNKNOWN  # noqa: E402
from payments.invoice_semantics import (  # noqa: E402
    GUARD_LLM_UNVERIFIED, GUARD_SEMANTIC_MISMATCH, InvoiceSemanticChecker,
)
from shared.a2a_messages import PaymentRequired, PaymentRequirements  # noqa: E402
from shared.models import Quote  # noqa: E402

ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    """통과·실패 표기는 다른 test_*.py 및 증거 수집기(collect_evidence.py 의 [OK/[FAIL 집계)와
    같은 형식을 쓴다."""
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK  ] {label}")
    else:
        fail += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


# ---- 픽스처 ----
USER, BROKER, EVIL, TRADER = Keypair(), Keypair(), Keypair(), Keypair()
USDC = str(Keypair().pubkey())
STOCK = str(Keypair().pubkey())
SYMBOL = "tAAPL"
DEC = 6

HONEST_DESC = "STOCK:tAAPL x0.18 (fee 0.09 USDC incl.)"
# 값은 전부 정상인데 '물건'만 다른 설명 — 서식 접두사(STOCK:)·종목·수량까지 같아서
# 정규식으로는 구분할 수 없다. 끼워 넣은 것은 '주문에 없던 조건'뿐이다.
SNEAKY_DESC = ("STOCK:tAAPL x0.18 (fee 0.09 USDC incl.) — 본 청구는 6개월 자동 결제 "
               "구독의 첫 회차이며 이후 매월 자동으로 청구됩니다")

QUOTE = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.18"),
              total_usdc=Decimal("32.10"), subtotal_usdc=Decimal("32.01"),
              fee_usdc=Decimal("0.09"), fee_bps=30)
SELL_QUOTE = Quote(symbol=SYMBOL, price_usdc=Decimal("178.00"), quantity=Decimal("0.18"),
                   total_usdc=Decimal("31.92"), subtotal_usdc=Decimal("32.01"),
                   fee_usdc=Decimal("0.09"), fee_bps=30)


class FakeBrain:
    """Gemini 자리 — 실제 호출 없이 응답 문자열을 지정한다(덕 타이핑, mock 라이브러리 없음)."""

    def __init__(self, replies, available=True):
        self.replies = list(replies) if isinstance(replies, list) else [replies]
        self._available = available
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def quota_message(self) -> str:
        return "무료 티어 일일 한도 소진(500건/일) — 태평양 자정까지 회복되지 않습니다."

    def _call(self, prompt: str) -> str:
        self.calls.append(prompt)
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def mandate():
    return OpenPaymentMandate(
        user_pubkey=str(USER.pubkey()), allowed_asset=USDC,
        budget_total_usdc=Decimal("100"), per_trade_max_usdc=Decimal("45"),
        allowed_symbols=[SYMBOL]).sign(USER)


def guard_with(brain, cache=True) -> Guard:
    sem = InvoiceSemanticChecker(brain, cache_enabled=cache) if brain is not None else None
    return Guard(mandate(), [str(BROKER.pubkey())], DEC, semantic=sem)


def buy_required(desc=HONEST_DESC, amount=32_100_000, pay_to=None,
                 order_id="ord_00aabb1122") -> PaymentRequired:
    reqs = PaymentRequirements(
        scheme="exact", network="solana-localnet", asset=USDC, amount=amount,
        pay_to=pay_to or str(BROKER.pubkey()), resource=desc, decimals=DEC)
    return PaymentRequired(order_id=order_id, symbol=SYMBOL, quantity="0.18",
                           price_usdc="178.00", requirements=reqs)


def sell_required(desc="USDC-BUYBACK:tAAPL x0.18 @ 178.00") -> PaymentRequired:
    reqs = PaymentRequirements(
        scheme="exact", network="solana-localnet", asset=STOCK, amount=180_000,
        pay_to=str(BROKER.pubkey()), resource=desc, decimals=DEC)
    return PaymentRequired(order_id="ord_00aabb1122", symbol=SYMBOL, quantity="0.18",
                           price_usdc="178.00", requirements=reqs)


MATCH = '{"match": true, "reason": "주문한 tAAPL 0.18주 매수와 동일한 청구입니다."}'
MISMATCH = ('{"match": false, "reason": "주문에 없던 6개월 자동 결제 구독 조건이 '
            '끼워져 있습니다."}')


def sem_kw(leg="buy", q=QUOTE):
    return dict(leg=leg, symbol=q.symbol, quantity=q.quantity,
                price_usdc=q.price_usdc, total_usdc=q.total_usdc)


# ------------------------------------------------- 1. 물건만 다른 청구서

def test_catches_different_product() -> None:
    print("\n[1] 값은 다 맞고 물건만 다른 청구서")
    g = guard_with(FakeBrain(MATCH))
    r = g.check_semantics(buy_required(), **sem_kw())
    check("정직한 청구서는 통과한다", r.ok, r.code)
    check("통과에도 판정 근거가 남는다", bool(g.last_semantic and g.last_semantic.reason))

    g2 = guard_with(FakeBrain(MISMATCH))
    hard = g2.check_demand(buy_required(SNEAKY_DESC), QUOTE, max_spend_usdc=Decimal("33"),
                           expected_symbol=SYMBOL)
    check("[전제] 하드 검사 6종은 이 청구서를 전부 통과시킨다", hard.ok, hard.code)

    r2 = g2.check_semantics(buy_required(SNEAKY_DESC), **sem_kw())
    check("의미 대조가 차단한다(GUARD_SEMANTIC_MISMATCH)",
          (not r2.ok) and r2.code == GUARD_SEMANTIC_MISMATCH, r2.code)
    check("차단 사유에 모델의 근거가 실린다", "구독" in r2.detail, r2.detail)
    check("차단 위치가 소스 라인으로 기록된다", r2.where.startswith("guard.py:L"), r2.where)


def test_layer_order() -> None:
    print("\n[2] 계층 순서 — LLM 은 하드 검사를 되살릴 수 없다")
    brain = FakeBrain(MATCH)
    g = guard_with(brain)
    ta = TradingAgent(TRADER, PaymentAuthorizer(mandate(), agent_kp=TRADER),
                      Strategy(), DEC, "solana-localnet")
    ta.guard = g

    # 수취인 위조 — 하드 검사에서 걸린다. 그 뒤의 의미 대조는 아예 실행되지 않아야 한다.
    from solders.hash import Hash
    try:
        ta.build_payment(buy_required(pay_to=str(EVIL.pubkey())), Hash.default(), QUOTE,
                         max_spend_usdc=Decimal("33"), expected_symbol=SYMBOL)
        check("하드 검사 위반이 차단된다", False, "서명이 생성됨")
    except GuardError as e:
        check("하드 검사 위반이 먼저 차단된다(GUARD_PAYEE_UNKNOWN)",
              e.result.code == GUARD_PAYEE_UNKNOWN, e.result.code)
    check("하드 검사에서 막히면 LLM 을 부르지 않는다(호출 0)", len(brain.calls) == 0,
          f"{len(brain.calls)}회 호출")
    check("하드 검사에서 막히면 의미 통계도 안 늘어난다", brain and g.semantic.stats.checked == 0,
          str(g.semantic.stats.as_dict()))

    # 하드 검사를 통과한 정상 청구서 — 이때만 LLM 이 돈다.
    ta.build_payment(buy_required(), Hash.default(), QUOTE,
                     max_spend_usdc=Decimal("33"), expected_symbol=SYMBOL)
    check("하드 검사 통과 후에만 LLM 이 호출된다(호출 1)", len(brain.calls) == 1,
          f"{len(brain.calls)}회 호출")
    check("프롬프트에 청구서 설명이 실린다", HONEST_DESC in brain.calls[0])
    check("프롬프트에 우리 주문 의도가 실린다",
          "tAAPL" in brain.calls[0] and "32.10" in brain.calls[0])

    # 그리고 결정적으로 — LLM 이 match 라고 해도 하드 검사 위반은 통과하지 못한다.
    g3 = guard_with(FakeBrain(MATCH))
    r = g3.check_demand(buy_required(pay_to=str(EVIL.pubkey())), QUOTE,
                        expected_symbol=SYMBOL)
    check("LLM 이 match 여도 하드 검사 위반은 그대로 차단(통과 권한 없음)",
          (not r.ok) and r.code == GUARD_PAYEE_UNKNOWN, r.code)


# ------------------------------------------------- 3. 검사 불가의 비대칭

def test_unverified_asymmetry() -> None:
    print("\n[3] 검사 불가 — 매수는 차단, 매도는 진행")
    for label, brain in (("쿼터 쿨다운", FakeBrain(MATCH, available=False)),
                         ("호출 예외", FakeBrain([RuntimeError("429 RESOURCE_EXHAUSTED")])),
                         ("두뇌 없음", None)):
        if brain is None:
            continue  # semantic=None 은 계층 자체가 없는 경우 — 아래 test_no_layer 에서 다룬다
        gb = guard_with(brain)
        rb = gb.check_semantics(buy_required(), **sem_kw("buy"))
        check(f"매수: {label} → 이 건 차단(GUARD_LLM_UNVERIFIED)",
              (not rb.ok) and rb.code == GUARD_LLM_UNVERIFIED, rb.code)
        check(f"매수: {label} → 사유가 남는다", bool(rb.detail), rb.detail)

        gs = guard_with(brain)
        rs = gs.check_semantics(sell_required(), **sem_kw("sell", SELL_QUOTE))
        check(f"매도: {label} → 하드 검사만으로 진행(차단하지 않음)", rs.ok, rs.code)
        check(f"매도: {label} → 진행했다는 사실이 판정에 남는다",
              gs.last_semantic is not None and gs.last_semantic.verdict == "unverified",
              str(gs.last_semantic))

    print("  · 근거: 못 사는 것은 기회비용이고 못 파는 것은 실손실 — 노출을 늘리는 방향만 잠근다")


def test_system_keeps_running() -> None:
    print("\n[4] 검사기가 죽어도 시스템은 계속 돈다 (축④ — 결제를 완료하는가)")
    brain = FakeBrain([RuntimeError("429 RESOURCE_EXHAUSTED")])
    g = guard_with(brain)
    # 매수는 막히지만 예외가 GuardError(정상 차단 경로)이지 크래시가 아니다.
    r = g.check_semantics(buy_required(), **sem_kw())
    check("검사 실패가 예외 전파가 아니라 판정으로 돌아온다", r.code == GUARD_LLM_UNVERIFIED, r.code)
    # 같은 세션에서 곧바로 매도는 정상 진행된다 = 파이프라인이 살아 있다.
    r2 = g.check_semantics(sell_required(), **sem_kw("sell", SELL_QUOTE))
    check("같은 세션의 매도는 그대로 정산 경로로 간다", r2.ok, r2.code)
    st = g.semantic.stats
    check("차단·통과가 각각 계측된다",
          st.unverified_blocked == 1 and st.unverified_skipped == 1, str(st.as_dict()))


# ------------------------------------------------- 5. 응답 파손

def test_broken_responses() -> None:
    print("\n[5] 응답이 깨져도 조용히 통과시키지 않는다")
    cases = [
        ("빈 응답", ""),
        ("JSON 아님", "네, 정상적인 청구서로 보입니다."),
        ("match 필드 없음", '{"verdict": "ok", "reason": "괜찮음"}'),
        ("잘린 JSON", '{"match": tr'),
    ]
    for label, reply in cases:
        g = guard_with(FakeBrain([reply]))
        r = g.check_semantics(buy_required(), **sem_kw())
        check(f"{label} → 검사 불가로 처리(임의 통과 없음)",
              (not r.ok) and r.code == GUARD_LLM_UNVERIFIED, r.code)

    # 반대로, 형식이 조금 지저분해도 판정 자체는 읽어낸다.
    for label, reply in [
        ("코드펜스", '```json\n{"match": false, "reason": "다른 상품"}\n```'),
        ("문자열 불리언", '{"match": "false", "reason": "다른 상품"}'),
        ("앞뒤 설명 첨부", '판정: {"match": false, "reason": "다른 상품"} 입니다'),
    ]:
        g = guard_with(FakeBrain([reply]))
        r = g.check_semantics(buy_required(SNEAKY_DESC), **sem_kw())
        check(f"{label} → 차단 판정을 정상 인식", r.code == GUARD_SEMANTIC_MISMATCH, r.code)


# ------------------------------------------------- 6. 호출 예산(캐시)

def test_cache_budget() -> None:
    print("\n[6] 호출 예산 — 같은 서식은 재호출하지 않고, 문구가 바뀌면 반드시 재검사")
    brain = FakeBrain(MATCH)
    g = guard_with(brain)
    for i in range(5):
        # 수량·수수료 숫자만 매번 다른 같은 서식 (실제 재생 세션의 모습)
        desc = f"STOCK:tAAPL x0.1{i} (fee 0.0{i} USDC incl.)"
        r = g.check_semantics(buy_required(desc), **sem_kw())
        check(f"동일 서식 {i + 1}회차 통과", r.ok, r.code)
    check("같은 서식 5회는 LLM 1회만 호출한다", len(brain.calls) == 1, f"{len(brain.calls)}회")
    check("캐시 적중이 계측된다", g.semantic.stats.cache_hits == 4,
          str(g.semantic.stats.as_dict()))

    # 공격자가 문구를 바꾸면 서식이 달라져 새로 검사받는다.
    brain.replies = [MATCH, MISMATCH]
    r = g.check_semantics(buy_required(SNEAKY_DESC), **sem_kw())
    check("문구가 바뀌면 새로 호출한다", len(brain.calls) == 2, f"{len(brain.calls)}회")
    check("새 문구는 차단된다", r.code == GUARD_SEMANTIC_MISMATCH, r.code)

    # 차단 판정도 캐시된다 — 같은 공격을 반복해도 통과하지 않는다.
    r2 = g.check_semantics(buy_required(SNEAKY_DESC), **sem_kw())
    check("같은 공격 재시도는 캐시로도 차단된다", r2.code == GUARD_SEMANTIC_MISMATCH, r2.code)
    check("재시도에 추가 호출 없음", len(brain.calls) == 2, f"{len(brain.calls)}회")

    g2 = guard_with(FakeBrain(MATCH), cache=False)
    for _ in range(3):
        g2.check_semantics(buy_required(), **sem_kw())
    check("캐시를 끄면 매번 호출한다", g2.semantic.stats.llm_calls == 3,
          str(g2.semantic.stats.as_dict()))


def test_no_layer() -> None:
    print("\n[7] 계층 미적용 — 두뇌 없는 세션은 하드 검사만으로 그대로 돈다")
    g = Guard(mandate(), [str(BROKER.pubkey())], DEC)          # semantic 미주입
    r = g.check_semantics(buy_required(SNEAKY_DESC), **sem_kw())
    check("의미 대조기가 없으면 통과(제품을 멈추지 않는다)", r.ok, r.code)
    check("판정 기록도 남지 않는다", g.last_semantic is None)
    check("하드 검사는 그대로 작동한다",
          not g.check_demand(buy_required(pay_to=str(EVIL.pubkey())), QUOTE).ok)


def test_stale_verdict_not_leaked() -> None:
    """하드 검사에서 차단된 건에 '직전 주문'의 판정이 새어 나가면 안 된다 (bug-dept BUG-04).

    Guard 는 세션 1개를 전 종목이 공유한다. 하드 검사에서 막히면 check_semantics 는
    애초에 호출되지 않으므로 last_semantic 에 직전 주문의 값이 남고, 엔진의 GuardError
    핸들러가 그걸 **차단된 주문의 order_id 로** 이벤트에 실어 보냈다 — 같은 주문에
    '차단'과 '의미 대조 통과' 두 줄이 동시에 남았다.
    """
    print("\n[9] 스테일 판정 누출 — 차단된 주문에 직전 판정이 붙지 않는다")
    g = guard_with(FakeBrain(MATCH))

    # 1건차: 정상 청구서가 의미 대조를 통과한다.
    ok1 = g.check_semantics(buy_required(order_id="ord_00aabb1101"), **sem_kw())
    check("1건차 통과", ok1.ok, ok1.code)
    check("판정에 그 주문번호가 봉인된다",
          g.last_semantic.order_id == "ord_00aabb1101", str(g.last_semantic.order_id))

    # 2건차: 수취인 위조 — 하드 검사에서 막히므로 의미 대조는 돌지 않는다.
    blocked_req = buy_required(pay_to=str(EVIL.pubkey()), order_id="ord_00aabb1102")
    r = g.check_demand(blocked_req, QUOTE, expected_symbol=SYMBOL)
    check("2건차는 하드 검사에서 차단", (not r.ok) and r.code == GUARD_PAYEE_UNKNOWN, r.code)

    stale = g.last_semantic
    check("차단 후에도 last_semantic 은 1건차 값이다(구조상 불가피)",
          stale is not None and stale.order_id == "ord_00aabb1101")
    check("★ 그 판정의 주문번호가 차단된 주문과 다르다 — 호출측이 대조로 걸러낼 수 있다",
          stale.order_id != "ord_00aabb1102", str(stale.order_id))
    check("의미 통계는 오염되지 않는다(차단 건은 세지 않음)",
          g.semantic.stats.checked == 1, str(g.semantic.stats.as_dict()))

    # 매도 미검증 통과 경로도 주문번호를 담아야 한다.
    g2 = guard_with(FakeBrain(MATCH, available=False))
    g2.check_semantics(sell_required(), **sem_kw("sell", SELL_QUOTE))
    check("매도 미검증 통과 판정에도 주문번호가 담긴다",
          g2.last_semantic.order_id == "ord_00aabb1122", str(g2.last_semantic.order_id))

    check("as_event 에 order_id 가 실린다", "order_id" in g.last_semantic.as_event())


def test_decimals_unit() -> None:
    """청구서의 decimals 를 아무도 안 보면 AP2 예산 차감이 브로커 값에 좌우된다 (BUG-02)."""
    print("\n[10] 단위(decimals) 검증 — 금액 정수의 '단위'를 상대가 정하지 못하게")
    g = guard_with(None)

    r_ok = g.check_demand(buy_required(), QUOTE, expected_symbol=SYMBOL)
    check("정상 단위(6)는 통과", r_ok.ok, r_ok.code)

    # amount 는 정직하게 두고 decimals 만 9 로 — 금액 검사는 그대로 통과한다.
    forged = buy_required()
    forged.requirements.decimals = 9
    r = g.check_demand(forged, QUOTE, expected_symbol=SYMBOL)
    check("단위 위조 차단(GUARD_ASSET_MISMATCH)",
          (not r.ok) and r.code == "GUARD_ASSET_MISMATCH", r.code)
    check("차단 사유가 단위임을 밝힌다", "decimals" in r.detail, r.detail)

    # 실제 피해 재현 — 가드가 없으면 AP2 차감액이 1/1000 이 된다.
    from config import from_base_units
    honest = from_base_units(32_100_000, 6)
    cheated = from_base_units(32_100_000, 9)
    check("[재현] 단위만 바꾸면 AP2 차감액이 1/1000 로 줄어든다",
          honest == Decimal("32.10") and cheated == Decimal("0.03210"),
          f"{honest} vs {cheated}")

    # 매도 레그 대칭
    sell_forged = sell_required()
    sell_forged.requirements.decimals = 9
    r_sell = g.check_stock_transfer(sell_forged, expected_stock_mint=STOCK,
                                    expected_quantity=Decimal("0.18"), stock_decimals=DEC)
    check("매도 레그도 단위 위조를 차단",
          (not r_sell.ok) and r_sell.code == "GUARD_ASSET_MISMATCH", r_sell.code)
    r_sell_ok = g.check_stock_transfer(sell_required(), expected_stock_mint=STOCK,
                                       expected_quantity=Decimal("0.18"), stock_decimals=DEC)
    check("매도 정상 단위는 통과(오탐 없음)", r_sell_ok.ok, r_sell_ok.code)


def test_stats_shape() -> None:
    print("\n[8] 계측 — _ai_stats 로 올라가는 집계")
    g = guard_with(FakeBrain([MATCH, MISMATCH]))
    g.check_semantics(buy_required("A설명"), **sem_kw())
    g.check_semantics(buy_required("B설명"), **sem_kw())
    d = g.semantic.stats.as_dict()
    check("checked 2건", d["checked"] == 2, str(d))
    check("passed 1 / blocked 1", d["passed"] == 1 and d["blocked"] == 1, str(d))
    check("llm_calls 2건", d["llm_calls"] == 2, str(d))
    check("키 집합이 고정돼 있다(프론트 계약)",
          set(d) == {"checked", "llm_calls", "cache_hits", "passed", "blocked",
                     "unverified_blocked", "unverified_skipped"}, str(sorted(d)))


def main() -> int:
    print("=" * 70)
    print(" 청구서 의미 대조 — 값은 맞고 물건만 다른 청구서 (payments/invoice_semantics.py)")
    print("=" * 70)
    test_catches_different_product()
    test_layer_order()
    test_unverified_asymmetry()
    test_system_keeps_running()
    test_broken_responses()
    test_cache_budget()
    test_no_layer()
    test_stale_verdict_not_leaked()
    test_decimals_unit()
    test_stats_shape()
    print("\n" + "-" * 70)
    print(f" 결과: 통과 {ok} · 실패 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
