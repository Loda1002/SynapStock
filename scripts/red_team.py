"""402 Guard 레드팀 — "안전하다"고 말하는 대신 "공격해보라"로 증명한다.

계층이 서로 다른 공격 3종을, 실제 구매 에이전트(build_payment)·402 Guard·브로커
정산 코드를 그대로 태워 실행한다. 자작 목업이 아니라 저장소의 진짜 결제 경로다.

  ① 청구 위조   (계층: check_demand 서명 게이트) — 악성 브로커가 한도 안쪽 금액으로
                 청구서를 만들되 수취인을 자기 지갑으로 바꾸거나(counterparty),
                 합의 견적과 다른 금액을 청구한다(amount).
  ② 이중청구     (계층: Memo 바인딩 + 서명 dedup) — 같은 결제를 재정산해 이중으로 처리.
  ③ 정산 미이행  (계층: check_delivery 온체인 재조회) — 결제는 settled 인데 자산 미전달.

--report 는 공격/차단 매트릭스 + 온체인 재조회 결과 + '같은 실행 안에서' 정상 거래
N건 오탐 0 을 함께 산출한다(과거 아티팩트를 끌어오지 않는다).

실행:  .venv/Scripts/python.exe -m scripts.red_team --report   (네트워크 불필요)
       옵션 --attacker <pubkey> 로 심사위원이 직접 악성 수취인 주소를 넣을 수 있다.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash

import config  # noqa: F401 — 콘솔 인코딩 안전화
from config import to_base_units
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer
from payments.guard import (
    Guard, GuardError,
    GUARD_PAYEE_UNKNOWN, GUARD_INTENT_EXCEEDED, GUARD_DELIVERY_UNCONFIRMED,
    GUARD_ASSET_MISMATCH,
)
from payments import x402_solana as x
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy
from shared.a2a_messages import PaymentRequirements, PaymentRequired
from shared.models import Quote

DEC = 6
PER_TRADE = Decimal("45")
# 사용자 에이전트가 결정한 이번 거래 의도 지출 (decision.spend_usdc)
INTENDED_SPEND = Decimal("30")
# 합의 견적: 32.10 USDC (구매 에이전트가 받아들인 정상 청구액)
AGREED_TOTAL = Decimal("32.10")
AGREED_BASE = to_base_units(AGREED_TOTAL, DEC)          # 32,100,000
# 위조 청구: 한도(45) 안쪽이지만 의도 지출(30)보다 큰 44.94 USDC
FORGED_TOTAL = Decimal("44.94")
FORGED_BASE = to_base_units(FORGED_TOTAL, DEC)          # 44,940,000


def _env(attacker_pubkey: str = ""):
    """공격 1건마다 새 환경(사용자·에이전트·정상 브로커·악성 지갑)."""
    user, trading, broker = Keypair(), Keypair(), Keypair()
    usdc, stock = Keypair().pubkey(), Keypair().pubkey()
    evil = Pubkey.from_string(attacker_pubkey) if attacker_pubkey else Keypair().pubkey()
    mandate = OpenPaymentMandate(
        user_pubkey=str(user.pubkey()), allowed_asset=str(usdc),
        budget_total_usdc=Decimal("1000"), per_trade_max_usdc=PER_TRADE,
        allowed_symbols=["tAAPL"],
    ).sign(user)
    auth = PaymentAuthorizer(mandate, agent_kp=trading)
    ta = TradingAgent(trading, auth, Strategy(spend_per_trade_usdc=Decimal("30")),
                      DEC, "solana-localnet")
    ta.guard = Guard(mandate, [str(broker.pubkey())], DEC)
    bk = BrokerAgent(broker, usdc, DEC, stock, DEC, "solana-localnet", fee_bps=0)
    return {"user": user, "trading": trading, "broker": broker, "evil": evil,
            "usdc": usdc, "stock": stock, "auth": auth, "ta": ta, "bk": bk}


def _required(env, order_id, amount_base, pay_to=None, asset=None, symbol="tAAPL"):
    reqs = PaymentRequirements(
        scheme="exact", network="solana-localnet",
        asset=asset or str(env["usdc"]), amount=amount_base,
        pay_to=pay_to or str(env["broker"].pubkey()),
        resource=f"STOCK:{symbol} x0.18", decimals=DEC)
    return PaymentRequired(order_id=order_id, symbol=symbol, quantity="0.18",
                           price_usdc="178.00", requirements=reqs)


def _agreed_quote(total=AGREED_TOTAL):
    return Quote(symbol="tAAPL", price_usdc=Decimal("178.00"), quantity=Decimal("0.18"),
                 total_usdc=total, subtotal_usdc=total, fee_usdc=Decimal("0"), fee_bps=0)


# ---------- 공격 ① 청구 위조 (check_demand) ----------

def attack_payee_swap(attacker: str):
    """한도(45) 안쪽 44.94 를 청구하며 수취인을 악성 지갑으로 스왑."""
    env = _env(attacker)
    required = _required(env, "ord_a11ce50001", FORGED_BASE, pay_to=str(env["evil"]))
    quote = _agreed_quote()

    # [가드 없음 = 업계 기본값] 구매 에이전트가 그대로 서명 → 악성 지갑에 44.94 결제
    env["ta"].guard = None
    submitted = env["ta"].build_payment(required, Hash.default(), quote)
    tx = x.decode_payload(submitted.payment.serialized_transaction)
    leaked_ok, _, leaked_amt = x.verify_payment(
        tx, expected_mint=env["usdc"], expected_dest_owner=env["evil"],
        expected_amount=FORGED_BASE, expected_order_id="ord_a11ce50001")
    without = leaked_ok  # 서명된 tx 가 실제로 악성 지갑에 44.94 를 보내는가

    # [402 Guard] 서명 직전 차단
    env2 = _env(attacker)
    required2 = _required(env2, "ord_a11ce50001", FORGED_BASE, pay_to=str(env2["evil"]))
    code, where = _guarded(env2["ta"], required2, _agreed_quote())
    return {
        "name": "청구 위조 - 수취인 스왑", "layer": "check_demand (서명 게이트)",
        "without": f"악성 지갑에 {FORGED_TOTAL} USDC 서명·전송" if without else "재현 실패",
        "code": code, "where": where, "blocked": code == GUARD_PAYEE_UNKNOWN,
        "leak": FORGED_TOTAL if without else Decimal(0),
    }


def attack_amount_forge(attacker: str):
    """수취인 정상, 청구서(required)와 견적(quote)이 자기정합(둘 다 44.94)이지만 사용자
    의도 지출(30)을 초과 청구. quote↔required 정합만 보는 방어는 못 잡고(실 데이터 흐름에서는
    required 가 quote 에서 파생돼 늘 정합), 브로커와 독립적인 의도 상한이 잡는다(BUG-03)."""
    env = _env(attacker)
    required = _required(env, "ord_a11ce50002", FORGED_BASE)  # pay_to = 정상 브로커
    forged_quote = _agreed_quote(FORGED_TOTAL)                # quote 도 44.94 (자기정합)

    env["ta"].guard = None
    submitted = env["ta"].build_payment(required, Hash.default(), forged_quote,
                                        max_spend_usdc=INTENDED_SPEND)
    tx = x.decode_payload(submitted.payment.serialized_transaction)
    over_ok, _, _ = x.verify_payment(
        tx, expected_mint=env["usdc"], expected_dest_owner=env["broker"].pubkey(),
        expected_amount=FORGED_BASE, expected_order_id="ord_a11ce50002")
    overpay = (FORGED_TOTAL - INTENDED_SPEND) if over_ok else Decimal(0)

    env2 = _env(attacker)
    required2 = _required(env2, "ord_a11ce50002", FORGED_BASE)
    code, where = _guarded(env2["ta"], required2, _agreed_quote(FORGED_TOTAL),
                           max_spend_usdc=INTENDED_SPEND)
    return {
        "name": "청구 위조 - 금액 부풀리기(의도 초과)", "layer": "check_demand (의도 상한)",
        "without": f"의도 {INTENDED_SPEND} 인데 {FORGED_TOTAL} 서명(초과 {overpay})" if over_ok else "재현 실패",
        "code": code, "where": where, "blocked": code == GUARD_INTENT_EXCEEDED,
        "leak": overpay,
    }


def _guarded(ta, required, quote, max_spend_usdc=None):
    """가드가 켜진 상태로 build_payment 시도 → (차단코드, 위치). 통과하면 ('통과','')."""
    try:
        ta.build_payment(required, Hash.default(), quote, max_spend_usdc=max_spend_usdc)
        return "통과(유출!)", ""
    except GuardError as e:
        return e.result.code, e.result.where


# ---------- 공격 ①' 매도 청구 위조 (check_stock_transfer — 매수 위조의 매도 대칭) ----------
# 합의된 매도 수량 0.18 주 / 예산 밖 유휴 USDC 400 (mandate < 지갑잔액인 self-custody 정상 상태).
AGREED_QTY = Decimal("0.18")
AGREED_QTY_BASE = to_base_units(AGREED_QTY, DEC)       # 180,000 (stock base units)
IDLE_USDC = Decimal("400")
IDLE_USDC_BASE = to_base_units(IDLE_USDC, DEC)         # 400,000,000


def _stock_required(env, order_id, amount_base, asset=None, pay_to=None, symbol="tAAPL"):
    reqs = PaymentRequirements(
        scheme="exact", network="solana-localnet",
        asset=asset or str(env["stock"]), amount=amount_base,
        pay_to=pay_to or str(env["broker"].pubkey()),
        resource=f"USDC-BUYBACK:{symbol} x{AGREED_QTY}", decimals=DEC)
    return PaymentRequired(order_id=order_id, symbol=symbol, quantity=str(AGREED_QTY),
                           price_usdc="178.00", requirements=reqs)


def _guarded_sell(env, required):
    """가드가 켜진 상태로 build_stock_transfer 시도 → (차단코드, 위치). 통과하면 ('통과','')."""
    try:
        env["ta"].build_stock_transfer(
            required, Hash.default(),
            expected_stock_mint=env["stock"], expected_quantity=AGREED_QTY, stock_decimals=DEC)
        return "통과(유출!)", ""
    except GuardError as e:
        return e.result.code, e.result.where


def attack_stock_asset_swap(attacker: str):
    """매도 레그 — 수취인은 정상 브로커(신뢰)지만 지불 자산을 주식→USDC 로, 수량을 유휴 USDC
    전액으로 바꿔 구매자의 예산 밖 유휴 자금을 빼간다. 자산 검증이 비면 그대로 서명된다."""
    env = _env(attacker)
    required = _stock_required(env, "ord_5e11a50001", IDLE_USDC_BASE, asset=str(env["usdc"]))

    # [가드 없음 = 업계 기본값] 구매 에이전트가 그대로 서명 → 브로커에게 유휴 USDC 400 전송
    env["ta"].guard = None
    submitted = env["ta"].build_stock_transfer(required, Hash.default())
    tx = x.decode_payload(submitted.payment.serialized_transaction)
    leaked_ok, _, _ = x.verify_payment(
        tx, expected_mint=env["usdc"], expected_dest_owner=env["broker"].pubkey(),
        expected_amount=IDLE_USDC_BASE, expected_order_id="ord_5e11a50001")
    without = leaked_ok  # 서명된 tx 가 실제로 유휴 USDC 를 내보내는가

    # [402 Guard] 서명 직전 차단
    env2 = _env(attacker)
    required2 = _stock_required(env2, "ord_5e11a50001", IDLE_USDC_BASE, asset=str(env2["usdc"]))
    code, where = _guarded_sell(env2, required2)
    return {
        "name": "매도 자산 스왑 - 주식→USDC 유출", "layer": "check_stock_transfer (서명 게이트)",
        "without": f"예산 밖 유휴 {IDLE_USDC} USDC 를 서명·전송" if without else "재현 실패",
        "code": code, "where": where, "blocked": code == GUARD_ASSET_MISMATCH,
        "leak": IDLE_USDC if without else Decimal(0),
    }


def attack_stock_payee_swap(attacker: str):
    """매도 레그 — 자산·수량은 정상(주식 0.18)이지만 수취인을 악성 지갑으로 스왑해 주식을 탈취."""
    env = _env(attacker)
    required = _stock_required(env, "ord_5e11a50002", AGREED_QTY_BASE, pay_to=str(env["evil"]))

    env["ta"].guard = None
    submitted = env["ta"].build_stock_transfer(required, Hash.default())
    tx = x.decode_payload(submitted.payment.serialized_transaction)
    leaked_ok, _, _ = x.verify_payment(
        tx, expected_mint=env["stock"], expected_dest_owner=env["evil"],
        expected_amount=AGREED_QTY_BASE, expected_order_id="ord_5e11a50002")
    without = leaked_ok

    env2 = _env(attacker)
    required2 = _stock_required(env2, "ord_5e11a50002", AGREED_QTY_BASE, pay_to=str(env2["evil"]))
    code, where = _guarded_sell(env2, required2)
    return {
        "name": "매도 수취인 스왑 - 주식 탈취", "layer": "check_stock_transfer (서명 게이트)",
        "without": f"주식 {AGREED_QTY} 주를 악성 지갑에 서명·전송" if without else "재현 실패",
        "code": code, "where": where, "blocked": code == GUARD_PAYEE_UNKNOWN,
        "leak": Decimal(0),   # 유출은 '주식'(USDC 환산 아님) — 표에는 자산 탈취로 표기, USDC 합계엔 미포함
    }


# ---------- 공격 ② 이중청구 (Memo + 서명 dedup) ----------

async def attack_double_bill(attacker: str):
    env = _env(attacker)
    quote = env["bk"].quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required = env["bk"].make_payment_required(quote)
    submitted = env["ta"].build_payment(required, Hash.default(), quote)

    first = await env["bk"].settle(submitted, required.requirements, quote.quantity, live=False)

    # [가드 없음] 서명 dedup 이 없다면 같은 결제가 다시 처리된다(이중 정산)
    env["bk"].used_signatures.clear()
    replay_no_guard = await env["bk"].settle(submitted, required.requirements, quote.quantity, live=False)
    without_double = replay_no_guard.status == "settled"

    # [402 Guard = 서명 dedup 켜짐] 같은 결제 재정산 차단
    env2 = _env(attacker)
    quote2 = env2["bk"].quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required2 = env2["bk"].make_payment_required(quote2)
    submitted2 = env2["ta"].build_payment(required2, Hash.default(), quote2)
    await env2["bk"].settle(submitted2, required2.requirements, quote2.quantity, live=False)
    replay = await env2["bk"].settle(submitted2, required2.requirements, quote2.quantity, live=False)
    blocked = replay.status == "failed" and "이중청구" in replay.reason
    return {
        "name": "이중청구 - 결제 리플레이", "layer": "Memo 바인딩 + 서명 dedup",
        "without": f"같은 결제 재정산됨({quote.total_usdc} USDC 중복)" if without_double else "재현 실패",
        "code": ("이중청구 차단" if blocked else replay.status), "where": "broker_agent._guard_settlement",
        "blocked": blocked, "leak": quote.total_usdc if without_double else Decimal(0),
    }


# ---------- 공격 ③ 정산 미이행 (check_delivery 온체인 재조회) ----------

async def attack_non_delivery(attacker: str):
    env = _env(attacker)
    quote = env["bk"].quote("tAAPL", Decimal("30"), Decimal("178.00"))
    required = env["bk"].make_payment_required(quote)
    submitted = env["ta"].build_payment(required, Hash.default(), quote)
    completed = await env["bk"].settle(submitted, required.requirements, quote.quantity, live=False)
    # 결제는 settled 로 왔다. 하지만 브로커가 주식을 전달하지 않았다 →
    # 온체인 재조회에서 잔액이 그대로다(가짜 원장 오라클).
    before = 0
    expected_inc = to_base_units(quote.quantity, DEC)

    async def no_arrival():
        return before  # 미도착

    async def arrived():
        return before + expected_inc

    # [402 Guard] check_delivery 가 미도착을 잡아 pending 보류
    result = await env["ta"].guard.check_delivery(
        completed, signed_order_id=required.order_id, balance_reader=no_arrival,
        before_units=before, expected_increase_units=expected_inc)
    blocked = (not result.ok) and result.code == GUARD_DELIVERY_UNCONFIRMED
    # 대조군: 실제 도착했다면 통과(정상 정산에서는 오탐 없음)
    ok_result = await env["ta"].guard.check_delivery(
        completed, signed_order_id=required.order_id, balance_reader=arrived,
        before_units=before, expected_increase_units=expected_inc)
    return {
        "name": "정산 미이행 - 자산 미전달", "layer": "check_delivery (온체인 재조회)",
        "without": f"미전달인데 settled 로 오인 → {quote.total_usdc} USDC 대가 상실",
        "code": result.code, "where": result.where,
        "blocked": blocked, "leak": quote.total_usdc,
        "onchain_recheck_ok": ok_result.ok,  # 정상 도착 시 통과(온체인 재조회 PASS)
    }


# ---------- 정상 거래(오탐 0) — 같은 실행 안에서 함께 태운다 ----------

async def normal_trades(n: int = 14):
    env = _env()
    false_pos = 0
    settled = 0
    recheck_pass = 0
    for i in range(n):
        spend = Decimal("20") + Decimal(i)          # 20~33 USDC (한도 45 안쪽)
        quote = env["bk"].quote("tAAPL", spend, Decimal("178.00"))
        required = env["bk"].make_payment_required(quote)
        try:
            # 정직한 견적 + 의도 상한(spend) 을 함께 넘겨도 통과해야 정상(의도검사 오탐 0)
            submitted = env["ta"].build_payment(required, Hash.default(), quote,
                                                max_spend_usdc=spend)
        except GuardError:
            false_pos += 1
            continue
        completed = await env["bk"].settle(submitted=submitted, requirements=required.requirements,
                                           quantity=quote.quantity, live=False)
        if completed.status == "settled":
            settled += 1
        # 온체인 재조회(정상 도착) — 오탐이면 정상 배송을 미도착으로 잡는 것
        inc = to_base_units(quote.quantity, DEC)

        async def arrived(_inc=inc):
            return _inc

        d = await env["ta"].guard.check_delivery(
            completed, signed_order_id=required.order_id, balance_reader=arrived,
            before_units=0, expected_increase_units=inc)
        if d.ok:
            recheck_pass += 1
        else:
            false_pos += 1
    return {"n": n, "false_pos": false_pos, "settled": settled, "recheck_pass": recheck_pass}


def _fmt(v: Decimal) -> str:
    return f"{v:.2f}"


async def main(attacker: str) -> int:
    attacks = [
        attack_payee_swap(attacker),
        attack_amount_forge(attacker),
        await attack_double_bill(attacker),
        await attack_non_delivery(attacker),
        attack_stock_asset_swap(attacker),
        attack_stock_payee_swap(attacker),
    ]
    normal = await normal_trades(14)

    # 공격 계층 — ①청구 위조(매수)는 수취인/금액 두 변형, ①'는 그 매도 대칭(자산/수취인)
    # (표시 라벨, 방어 계층 성격)
    #   prevent — 서명·정산 '이전'에 막는다. 위반이면 트랜잭션 자체가 만들어지지 않으므로
    #             이 계층의 유출이 0 인 것은 측정 결과가 아니라 구조적 사실이다.
    #   detect  — 정산이 '끝난 뒤' 탐지한다. 돈은 이미 나갔고 저장소에 회수 경로가 없다
    #             (환불·에스크로·분쟁 구현 0건). 탐지·세션 정지가 할 수 있는 전부다.
    #   counterparty — 막은 주체가 **판매자(브로커) 자기 코드**다(broker_agent._guard_settlement
    #                  + used_signatures). 402 Guard 에는 리플레이 방어가 없다(guard.py 에
    #                  dedup 상태 0건). 악성 브로커라면 애초에 이 방어를 안 넣는다 —
    #                  구매자 측 방어 실적으로 세면 우리 방어력을 부풀리게 되므로 따로 센다.
    #                  게다가 이 공격은 build_payment 로 서명이 이미 만들어진 뒤라
    #                  '서명 전 차단·트랜잭션 미생성' 이라는 설명이 이 행에는 맞지 않는다.
    labels = [("① 매수 청구 위조 (a) 수취인 스왑", "prevent"),
              ("① 매수 청구 위조 (b) 금액 부풀리기", "prevent"),
              ("② 이중청구", "counterparty"),
              ("③ 정산 미이행", "detect"),
              ("①' 매도 청구 위조 (a) 자산 스왑", "prevent"),
              ("①' 매도 청구 위조 (b) 수취인 스왑", "prevent")]
    line = "─" * 78
    print("\n" + line)
    print("  402 Guard 레드팀 — 공격/차단 매트릭스 (실제 결제 경로를 그대로 태움)")
    print(line)
    total_leak = Decimal(0)
    all_blocked = True
    for (lab, stage), a in zip(labels, attacks):
        if stage == "detect":
            mark = "탐지(사후 — 자금은 이미 나감)" if a["blocked"] else "!! 미탐지 !!"
        elif stage == "counterparty":
            mark = "차단(판매자측 방어 — 구매자 Guard 아님)" if a["blocked"] else "!! 통과(유출) !!"
        else:
            mark = "차단(서명 전)" if a["blocked"] else "!! 통과(유출) !!"
        print(f"\n  {lab}   [계층: {a['layer']}]")
        print(f"    가드 없음(업계 기본값) : {a['without']}")
        print(f"    402 Guard             : {a['code']} — {mark}"
              + (f"  @ {a['where']}" if a.get("where") else ""))
        total_leak += a["leak"]
        all_blocked = all_blocked and a["blocked"]

    # 계층별로 나눠 센다. 예전에는 guarded_leak 이 하드코딩된 Decimal(0) 이었고, 게다가
    # '정산 미이행'(사후 탐지)의 피해액까지 total_leak 에 합산한 뒤 바로 아랫줄에
    # '402 Guard 적용 후 유출 0.00' 을 찍었다 — 회수하지 못한 금액을 되찾은 것처럼 읽혔다.
    def _of(stage):
        return [a for (_, s), a in zip(labels, attacks) if s == stage]

    prevented, counterparty, detected = _of("prevent"), _of("counterparty"), _of("detect")
    # 서명 전 계층에서 '차단 실패'가 있으면 그게 진짜 유출이다(하드코딩이 아니라 실제 계산).
    prevent_leak = sum((a["leak"] for a in prevented if not a["blocked"]), Decimal(0))
    # 사후 탐지 계층은 막은 것이 아니다 — 탐지했어도 그 금액은 회수하지 못한다.
    unrecoverable = sum((a["leak"] for a in detected), Decimal(0))

    print("\n" + line)
    print(f"  가드 없을 때 피해 합계        : {_fmt(total_leak)} USDC"
          f" (공격 {len(attacks)}종 회피 피해의 합 — 시나리오 가정치 IDLE_USDC 포함)")
    print(f"  구매자 서명 전 차단 {len(prevented)}건     : 유출 {_fmt(prevent_leak)} USDC"
          f"  ← 이 계층은 트랜잭션 미생성이라 구조적으로 0")
    print(f"  판매자측 방어 {len(counterparty)}건          : 402 Guard 가 아니라 브로커 자기 코드가 막았다"
          f" (구매자측 리플레이 방어는 미구현)")
    print(f"  정산 후 탐지 {len(detected)}건           : {_fmt(unrecoverable)} USDC 는 이미 나간 뒤 —"
          f" 탐지·세션 정지만 가능(회수 경로 없음)")
    print(f"     └ 이번 실행은 live=False 시뮬레이션이라 실제 온체인 이동은 없다")
    print(f"  온체인 재조회(check_delivery) : 미도착 탐지 {'PASS' if attacks[3]['blocked'] else 'FAIL'}"
          f" · 정상 도착 통과 {'PASS' if attacks[3].get('onchain_recheck_ok') else 'FAIL'}")
    print(f"  정상 거래 {normal['n']}건(같은 실행)    : 오탐 {normal['false_pos']}건 · "
          f"정산 {normal['settled']}건 · 온체인 재조회 통과 {normal['recheck_pass']}건")
    print(line)

    # 첫 화면 KPI — 계층을 합치지 않는다. 합치면 사후 탐지·판매자측 방어가 구매자
    # 서명 전 차단인 것처럼 읽히고, 그 순간 '유출 0.00' 이 그 건들까지 덮는 것처럼 보인다.
    # 공격과 정상 거래도 분모를 나눈다(정상 14건은 통과해야 정상이지 '못 막은 시도'가 아니다).
    p_blocked = sum(1 for a in prevented if a["blocked"])
    c_blocked = sum(1 for a in counterparty if a["blocked"])
    d_blocked = sum(1 for a in detected if a["blocked"])
    print(f"\n  [KPI]  공격 {len(attacks)} (구매자 서명 전 차단 {p_blocked} · 판매자측 {c_blocked}"
          f" · 사후 탐지 {d_blocked}) · 서명 전 유출 {_fmt(prevent_leak)} USDC"
          f" · 정상 {normal['n']}건 오탐 {normal['false_pos']}")

    # 기계 판독용 한 줄 — 사람이 읽는 문구를 바꿔도 수집기가 깨지지 않게 분리한다.
    # (2026-07-27: KPI 문구를 바꿨더니 collect_evidence/collect_bugscan 의 정규식이
    #  조용히 빈 객체를 돌려줘 심사 부서가 근거 없이 돌 뻔했다.)
    print("  [KPI-JSON] " + json.dumps({
        "attacks": len(attacks), "attempts": len(attacks) + normal["n"],
        "prevent_blocked": p_blocked, "counterparty_blocked": c_blocked,
        "detected": d_blocked,
        "blocked": p_blocked + c_blocked + d_blocked,   # 하위호환(옛 '차단' 축)
        "leak_usdc": _fmt(prevent_leak),                 # 하위호환(옛 '유출' 축)
        "prevent_leak_usdc": _fmt(prevent_leak),
        "unrecoverable_usdc": _fmt(unrecoverable),
        "without_guard_usdc": _fmt(total_leak),
        "normal": normal["n"], "false_positives": normal["false_pos"],
    }, ensure_ascii=False, sort_keys=True))

    ok = all_blocked and normal["false_pos"] == 0 and attacks[3].get("onchain_recheck_ok")
    print("\n  " + (f"구매자 서명 전 공격 {len(prevented)}종 전부 차단(유출 {_fmt(prevent_leak)} USDC) · "
                    f"판매자측 {len(counterparty)}종 · 사후 미이행 {len(detected)}종 탐지 · 정상 거래 오탐 0."
                    if ok else "경고: 일부 공격이 차단되지 않았거나 오탐이 발생했습니다!"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="공격/차단 매트릭스 출력(기본 동작)")
    ap.add_argument("--attacker", default="", help="악성 수취인 pubkey (심사위원 직접 입력)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.attacker)))
