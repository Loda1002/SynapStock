"""402 Guard — 구매 에이전트 지출 승인 게이트 (결함 A·B·C·H·I 를 닫는 레이어).

x402 exact 스킴은 '파는 쪽(resource server)'을 보호하도록 설계돼 있지만, '사는 쪽
(사람 없이 결제하는 구매 에이전트)'을 보호하는 counterparty verification 이 비어 있다.
이 모듈이 그 비어 있던 절반이다 — 구매 에이전트가 브로커의 청구서(payment-required)에
서명하기 직전, 반드시 통과해야 하는 마지막 게이트.

  check_demand()   : 청구서를 사용자 mandate · 합의 견적 · 신뢰 수취인 목록과 대조한다.
                     하나라도 어긋나면 '서명 거부'(유출 0). 차단 코드 6종.
                       - 금액(base units 정수, 오차 0)
                       - 수취인(allowlist — 결함 B: AP2 가 미검사하던 counterparty)
                       - 자산(mandate allowed_asset — 결함 C: 죽어 있던 필드)
                       - 주문번호(온체인 Memo 대사 키가 될 값)
                       - 종목 / 건별 한도 (AP2 이전 방어선)
  check_delivery() : 정산 후 온체인 잔액을 재조회(재시도 2회)해 청구서대로 자산이 실제
                     도착했는지 확인한다. 미확인은 '차단'이 아니라 pending_delivery 보류
                     + 세션 정지 신호다(결함 I: 배송 실패해도 settled 되던 문제).

모든 판정은 GuardResult 로 반환하고, 방어가 발동한 소스 라인(guard.py:L{n})을 런타임에
생성해 담는다 — 코드를 리팩터링해도 로그의 위치가 거짓말하지 않는다.

check_demand 는 오프라인(네트워크 없이) 동작한다. check_delivery 는 잔액을 읽어오는
balance_reader 를 주입받으므로, 라이브(실 RPC)와 오프라인 테스트(가짜 원장) 모두에서
같은 코드로 동작한다.
"""
from __future__ import annotations
import asyncio
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable, Iterable, Optional

from config import to_base_units

# ---- check_demand 차단 코드 (7종) ----
GUARD_AMOUNT_MISMATCH = "GUARD_AMOUNT_MISMATCH"        # 청구 금액 != 합의 견적 (base units, 오차 0)
GUARD_INTENT_EXCEEDED = "GUARD_INTENT_EXCEEDED"        # 청구 금액이 사용자 의도 지출(decision.spend)을 초과
GUARD_PAYEE_UNKNOWN = "GUARD_PAYEE_UNKNOWN"            # 수취인이 신뢰 목록(allowlist)에 없음
GUARD_ASSET_MISMATCH = "GUARD_ASSET_MISMATCH"         # 결제 자산이 mandate 허용 자산이 아님
GUARD_SYMBOL_NOT_ALLOWED = "GUARD_SYMBOL_NOT_ALLOWED"  # 종목이 mandate 허용 종목이 아님
GUARD_LIMIT_EXCEEDED = "GUARD_LIMIT_EXCEEDED"         # 청구 금액이 건별 한도 초과
GUARD_ORDER_INVALID = "GUARD_ORDER_INVALID"           # 주문번호 누락/형식 오류 (대사 키 부재)

DEMAND_CODES = (
    GUARD_AMOUNT_MISMATCH, GUARD_INTENT_EXCEEDED, GUARD_PAYEE_UNKNOWN, GUARD_ASSET_MISMATCH,
    GUARD_SYMBOL_NOT_ALLOWED, GUARD_LIMIT_EXCEEDED, GUARD_ORDER_INVALID,
)

# 정직한 견적의 센트 반올림 오탐 방지용 허용치(2센트). 브로커 quote 는 subtotal 과 fee 를
# 각각 센트로 반올림(각 최대 +0.005)하므로, 정직한 총액이 의도 지출을 넘는 최악치는
# 0.01(기본) + 0.005(subtotal) + 0.005(fee) = 0.02 이다(수수료율 100%까지 유효). 브로커가
# 준 값에 의존하지 않는 고정 상수로 둔다. 공격은 달러 단위라 2센트로도 차단력 무영향.
_INTENT_SLIPPAGE_USDC = Decimal("0.02")

# ---- check_delivery 판정 코드 ----
GUARD_DELIVERY_UNCONFIRMED = "GUARD_DELIVERY_UNCONFIRMED"  # 온체인 재조회에서 자산 미도착 → 보류
GUARD_ORDER_MISMATCH = "GUARD_ORDER_MISMATCH"             # 정산 결과 주문번호가 서명한 주문과 불일치

# 브로커가 생성하는 주문번호 형식 (broker_agent.make_payment_required 와 일치)
_ORDER_RE = re.compile(r"^ord_[0-9a-f]{10}$")


@dataclass
class GuardResult:
    """게이트 1건의 판정 결과. where 는 방어가 발동한 소스 라인(런타임 생성)."""
    ok: bool
    code: str             # "OK" / "GUARD_..." / "PENDING_DELIVERY"
    detail: str
    where: str = ""
    expected: str = ""
    actual: str = ""

    def as_event(self) -> dict:
        return {
            "ok": self.ok, "code": self.code, "detail": self.detail,
            "where": self.where, "expected": self.expected, "actual": self.actual,
        }


class GuardError(Exception):
    """check_demand 차단 — 결제 서명 자체가 일어나지 않는다 (온체인 유출 0).

    엔진/에이전트는 MandateError 와 같은 층위에서 이 예외를 잡아 GUARD_BLOCKED 로 표면화한다.
    """

    def __init__(self, result: GuardResult):
        super().__init__(f"[{result.code}] {result.detail} ({result.where})")
        self.result = result


class Guard:
    """구매 에이전트의 지출 승인 게이트.

    payee_allowlist: 신뢰하는 수취인(브로커) pubkey 문자열의 집합. A2A 로 협의를 마친
    상대만 담는다 — 악성 브로커가 수취인을 자기 다른 지갑으로 바꾸면 여기서 걸린다.
    """

    def __init__(self, mandate, payee_allowlist: Iterable[str], usdc_decimals: int):
        self.mandate = mandate
        self.payees = {str(p) for p in payee_allowlist}
        self.usdc_decimals = usdc_decimals

    # ---- 서명 직전: 청구서 4항목 대조 ----

    def check_demand(self, required, quote, expected_order_id: Optional[str] = None,
                     max_spend_usdc: Optional[Decimal] = None) -> GuardResult:
        """브로커의 payment-required(청구서)를 합의 견적·mandate·신뢰 목록과 대조한다.

        통과하면 ok=True 를 돌려주고, 어긋나면 첫 위반에서 즉시 차단 결과를 돌려준다.
        엔진은 ok=False 면 서명을 진행하지 않는다(GuardError 로 승격).

        max_spend_usdc: 사용자 에이전트가 결정한 이번 거래의 의도 지출(decision.spend_usdc).
                        브로커 quote 와 독립적인 상한이다 — quote 는 브로커가 만들고
                        required 도 그 quote 에서 파생되므로 둘의 정합만으로는 '브로커가
                        의도보다 많이 청구'하는 공격(BUG-03)을 못 잡는다. 이 값이 주어지면
                        청구 금액이 의도 지출(+1센트)을 넘을 때 GUARD_INTENT_EXCEEDED 로 차단한다.
        """
        reqs = required.requirements
        order_id = required.order_id

        # 1) 주문번호 — 온체인 Memo 대사 키가 될 값. 형식·존재·(있으면) 기대치 대조.
        if not order_id or not _ORDER_RE.match(str(order_id)):
            return self._block(GUARD_ORDER_INVALID, f"주문번호 형식 오류: {order_id!r}",
                               "ord_<10 hex>", str(order_id))
        if expected_order_id is not None and order_id != expected_order_id:
            return self._block(GUARD_ORDER_INVALID, "청구서 주문번호가 처리 중인 주문과 다릅니다",
                               str(expected_order_id), str(order_id))

        # 2) 자산 — mandate 가 허용한 결제 자산(USDC)인가 (결함 C: 죽어 있던 allowed_asset 을 살린다)
        if str(reqs.asset) != str(self.mandate.allowed_asset):
            return self._block(GUARD_ASSET_MISMATCH, "결제 자산이 mandate 허용 자산이 아닙니다",
                               str(self.mandate.allowed_asset), str(reqs.asset))

        # 3) 종목 — mandate 허용 종목인가
        if required.symbol not in self.mandate.allowed_symbols:
            return self._block(GUARD_SYMBOL_NOT_ALLOWED, f"허용되지 않은 종목: {required.symbol}",
                               ",".join(self.mandate.allowed_symbols), str(required.symbol))

        # 4) 수취인 — 신뢰 목록(counterparty)인가 (결함 B: AP2 가 pay_to 를 받고도 미검사하던 구멍)
        if str(reqs.pay_to) not in self.payees:
            return self._block(GUARD_PAYEE_UNKNOWN,
                               "수취인이 신뢰 목록에 없습니다 (counterparty 미검증 — 자금 유출 위험)",
                               "|".join(sorted(self.payees)), str(reqs.pay_to))

        # 5) 금액 — 합의 견적과 base units 정수 정합 (오차 0). exact 스킴은 초과도 부족도 안 된다.
        expected_amount = to_base_units(quote.total_usdc, self.usdc_decimals)
        if int(reqs.amount) != expected_amount:
            return self._block(GUARD_AMOUNT_MISMATCH, "청구 금액이 합의 견적과 다릅니다",
                               f"{expected_amount} base units", f"{int(reqs.amount)} base units")

        # 5b) 의도 지출 상한 — 브로커와 독립적으로, 청구 금액이 사용자 의도 지출을 넘는가.
        #     (BUG-03: quote↔required 정합만으로는 '한도 안쪽 부풀리기'를 못 잡는다. 악성/버그
        #      브로커가 의도 30 에 대해 건별 한도(45) 안쪽 44.94 를 자기정합으로 청구하는 공격.)
        if max_spend_usdc is not None:
            intent_ceiling = to_base_units(max_spend_usdc + _INTENT_SLIPPAGE_USDC, self.usdc_decimals)
            if int(reqs.amount) > intent_ceiling:
                return self._block(GUARD_INTENT_EXCEEDED,
                                   "청구 금액이 사용자 의도 지출을 초과합니다 (브로커 부풀리기)",
                                   f"<= {to_base_units(max_spend_usdc, self.usdc_decimals)} base units",
                                   f"{int(reqs.amount)} base units")

        # 6) 건별 한도 — 청구 금액이 사용자 건별 한도를 넘는가 (AP2 이전 1차 방어선)
        limit = to_base_units(self.mandate.per_trade_max_usdc, self.usdc_decimals)
        if int(reqs.amount) > limit:
            return self._block(GUARD_LIMIT_EXCEEDED, "청구 금액이 건별 한도를 초과합니다",
                               f"{limit} base units", f"{int(reqs.amount)} base units")

        where = f"guard.py:L{sys._getframe(0).f_lineno}"
        return GuardResult(True, "OK", "청구서 4항목 대조 통과 (금액·수취인·자산·주문번호)",
                           where, str(expected_amount), str(int(reqs.amount)))

    def assert_demand(self, required, quote, expected_order_id: Optional[str] = None,
                      max_spend_usdc: Optional[Decimal] = None) -> GuardResult:
        """check_demand 후 위반이면 GuardError 를 던진다 (결제 경로 결선용)."""
        res = self.check_demand(required, quote, expected_order_id, max_spend_usdc)
        if not res.ok:
            raise GuardError(res)
        return res

    # ---- 정산 후: 온체인 재조회로 실제 도착 확인 ----

    async def check_delivery(
        self,
        completed,
        *,
        signed_order_id: str,
        balance_reader: Callable[[], Awaitable[int]],
        before_units: int,
        expected_increase_units: int,
        retries: int = 2,
        retry_delay_sec: float = 0.0,
    ) -> GuardResult:
        """정산 완료 후, 구매자 자산 잔액을 온체인에서 재조회해 청구서대로 도착했는지 검증한다.

        balance_reader(): 현재 구매자 자산 잔액(base units) 을 돌려주는 async 콜러블.
                          라이브는 실 RPC, 오프라인 테스트는 가짜 원장을 주입한다.
        before_units:     정산 직전 잔액(base units).
        expected_increase_units: 청구서가 약속한 증가분(base units).

        증가분이 확인되면 ok=True. 재시도(기본 2회)에도 미확인이면
        GUARD_DELIVERY_UNCONFIRMED — 차단이 아니라 pending_delivery 보류 신호다
        (포지션 미반영·한도 원복·세션 정지는 호출측이 수행).
        """
        # 정산 결과 주문번호가 우리가 서명한 그 주문인가 (대사 키 일치)
        if signed_order_id and str(completed.order_id) != str(signed_order_id):
            return self._block_delivery(GUARD_ORDER_MISMATCH,
                                        "정산 결과 주문번호가 서명한 주문과 다릅니다",
                                        str(signed_order_id), str(completed.order_id))

        last = before_units
        for attempt in range(retries + 1):
            try:
                cur = await balance_reader()
            except Exception:
                cur = last  # 재조회 실패는 '미확인'으로 취급하고 재시도
            if cur - before_units >= expected_increase_units:
                where = f"guard.py:L{sys._getframe(0).f_lineno}"
                return GuardResult(
                    True, "OK",
                    f"온체인 재조회 확인 — 자산 +{cur - before_units} base units 도착",
                    where, str(expected_increase_units), str(cur - before_units))
            last = cur
            if attempt < retries and retry_delay_sec > 0:
                await asyncio.sleep(retry_delay_sec)

        return self._block_delivery(
            GUARD_DELIVERY_UNCONFIRMED,
            "정산 후 온체인 재조회에서 자산 미도착 — pending_delivery 보류(세션 정지)",
            f"+{expected_increase_units} base units", f"+{last - before_units} base units")

    # ---- 내부: 차단 결과 생성 (호출 지점 라인을 런타임 캡처) ----

    def _block(self, code: str, detail: str, expected: str = "", actual: str = "") -> GuardResult:
        where = f"guard.py:L{sys._getframe(1).f_lineno}"  # frame(1) = check_demand 의 위반 라인
        return GuardResult(False, code, detail, where, str(expected), str(actual))

    def _block_delivery(self, code: str, detail: str, expected: str = "", actual: str = "") -> GuardResult:
        where = f"guard.py:L{sys._getframe(1).f_lineno}"
        return GuardResult(False, code, detail, where, str(expected), str(actual))
