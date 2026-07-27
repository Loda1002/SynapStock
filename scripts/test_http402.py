"""브로커 HTTP 402 레그(G5) 검증 — x402 의 'HTTP' 정합이 실물로 성립하는가.

심사 리포트 축③ 갭: "HTTP 402 를 실제 HTTP status-code 서비스로 노출 안 함
(코드매치 0)". 이 테스트는 그 갭이 닫혔음을 **HTTP 의미론 수준에서** 확인한다:

  1) GET  /.well-known/x402         → 디스커버리(무엇을 어떤 조건으로 파는가)
  2) POST /broker/orders            → **402 Payment Required** + accepts[]
  3) POST /broker/orders + X-PAYMENT→ **200** + X-PAYMENT-RESPONSE (정산 결과)
  4) 같은 X-PAYMENT 재사용           → 402 (1회용 청구서 — 리플레이 차단)
  5) 전송이 HTTP 여도 402 Guard 는 그대로 — 원격 브로커가 수취인·금액을 위조하면 차단

서버를 실제 포트에 띄우지 않고 httpx ASGITransport 로 같은 FastAPI 앱을 호출한다
(상태 코드·헤더·본문은 실제 네트워크와 동일하게 흐른다 — 결정론적이고 빠르다).

재현: python scripts/test_http402.py  (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import httpx  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.keypair import Keypair  # noqa: E402

from config import CFG, to_base_units  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer  # noqa: E402
from payments.guard import Guard, GuardError  # noqa: E402
from payments.x402_http import (  # noqa: E402
    PAYMENT_HEADER, PAYMENT_RESPONSE_HEADER, Http402BrokerClient, X402ProtocolError,
    encode_payment_header, parse_payment_required,
)
from web import broker_service  # noqa: E402

SYMBOL = "AAPL"
PRICE = Decimal("200")
SPEND = Decimal("10")

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _reset_service() -> None:
    """모듈 전역(미결 청구서)을 비운다 — 케이스 간 간섭 제거."""
    broker_service._pending.clear()


def _asgi_client() -> Http402BrokerClient:
    transport = httpx.ASGITransport(app=broker_service.app)
    return Http402BrokerClient("http://broker.test", transport=transport)


def _raw_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=broker_service.app),
                             base_url="http://broker.test")


def _buyer(payee_allowlist=None):
    """구매 에이전트 일습 — 사용자 키(mandate 서명자) ≠ 에이전트 키, 402 Guard 결선."""
    user_kp, agent_kp = Keypair(), Keypair()
    broker_pk = str(broker_service.get_broker().pubkey)
    mandate = OpenPaymentMandate(
        user_pubkey=str(user_kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=Decimal("100"), per_trade_max_usdc=Decimal("50"),
        allowed_symbols=[SYMBOL]).sign(user_kp)
    auth = PaymentAuthorizer(mandate, agent_kp=agent_kp)
    guard = Guard(mandate, payee_allowlist or [broker_pk], CFG.usdc_decimals)
    strat = Strategy(spend_per_trade_usdc=SPEND)
    agent = TradingAgent(agent_kp, auth, strat, CFG.usdc_decimals, CFG.network)
    agent.guard = guard
    return agent, guard


# ---------- 1) 디스커버리 ----------
async def test_discovery() -> None:
    print("\n[1] GET /.well-known/x402 — 디스커버리")
    _reset_service()
    cli = _asgi_client()
    try:
        doc = await cli.discovery()
    finally:
        await cli.aclose()
    res = (doc.get("resources") or [{}])[0]
    check("x402Version 1", doc.get("x402Version") == 1, str(doc.get("x402Version")))
    check("자원 경로가 /broker/orders", res.get("resource") == "/broker/orders", str(res.get("resource")))
    check("scheme=exact", res.get("scheme") == "exact", str(res.get("scheme")))
    check("payTo 가 브로커 지갑", res.get("payTo") == str(broker_service.get_broker().pubkey))
    check("결제 자산이 USDC 민트", res.get("asset") == CFG.usdc_mint)
    check("미구현 항목을 먼저 공개(정직성)", bool(doc.get("notImplemented")), str(doc.get("notImplemented")))


# ---------- 2) 결제 없는 요청 → 402 ----------
async def test_402_challenge() -> None:
    print("\n[2] POST /broker/orders (결제 없음) — 402 Payment Required")
    _reset_service()
    async with _raw_client() as raw:
        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)})
    check("HTTP 상태 코드가 402", r.status_code == 402, str(r.status_code))
    body = r.json()
    check("x402Version 1", body.get("x402Version") == 1, str(body.get("x402Version")))
    check("error 필드 존재", bool(body.get("error")), str(body.get("error")))
    accepts = body.get("accepts") or []
    check("accepts[] 1건", len(accepts) == 1, str(len(accepts)))
    a = accepts[0] if accepts else {}
    for field in ("scheme", "network", "maxAmountRequired", "resource", "payTo", "asset"):
        check(f"accepts[0].{field} 존재", field in a, str(a.get(field)))
    check("캐시 금지 헤더", r.headers.get("cache-control") == "no-store",
          str(r.headers.get("cache-control")))
    check("extra.orderId 로 온체인 대사 키 제공",
          bool((a.get("extra") or {}).get("orderId")), str((a.get("extra") or {}).get("orderId")))
    # 청구 금액이 우리 쪽 견적과 정수 정합인가 (같은 수수료 모델)
    quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)
    check("maxAmountRequired == 우리 견적 총액(base units)",
          int(a.get("maxAmountRequired", -1)) == to_base_units(quote.total_usdc, CFG.usdc_decimals),
          f"{a.get('maxAmountRequired')} vs {to_base_units(quote.total_usdc, CFG.usdc_decimals)}")


# ---------- 3) 전체 왕복: 402 → 서명 → 200 ----------
async def test_full_cycle() -> None:
    print("\n[3] 전체 왕복 — 402 → X-PAYMENT 재시도 → 200 + X-PAYMENT-RESPONSE")
    _reset_service()
    agent, guard = _buyer()
    cli = _asgi_client()
    try:
        required = await cli.request_order(SYMBOL, SPEND, PRICE)
        check("클라이언트가 청구서를 파싱", required.order_id.startswith("ord_"), required.order_id)

        # 구매자는 브로커 말을 믿지 않는다 — 자기 견적으로 대조한 뒤에야 서명(402 Guard)
        my_quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)
        submitted = agent.build_payment(required, Hash.default(), my_quote, max_spend_usdc=SPEND)
        check("Guard·AP2 통과 후 서명 생성", bool(submitted.payment.serialized_transaction))
        check("AP2 예산이 청구액만큼 예약됨",
              agent.auth.remaining_usdc < Decimal("100"), str(agent.auth.remaining_usdc))

        completed = await cli.submit_payment(required, submitted, SPEND)
        check("정산 status=settled", completed.status == "settled", completed.status)
        check("주문번호가 왕복 내내 동일", completed.order_id == required.order_id)
        check("전달 자산이 주식 민트(또는 미설정 환경에선 빈값)",
              completed.delivered_asset == str(broker_service.get_broker().stock_mint or ""))

        # 같은 결제를 다시 제출 → 1회용 청구서라 402
        replay = await cli._http()
        r2 = await replay.post(
            f"{cli.base_url}/broker/orders",
            json={"symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)},
            headers={PAYMENT_HEADER: encode_payment_header(required.order_id, submitted)})
        check("같은 결제 재제출은 402 로 거부", r2.status_code == 402, str(r2.status_code))
        check("사유가 unknown_order", r2.json().get("error") == "unknown_order", str(r2.json()))
    finally:
        await cli.aclose()


# ---------- 4) 200 응답에 정산 헤더가 실린다 ----------
async def test_settlement_header() -> None:
    print("\n[4] 200 응답의 X-PAYMENT-RESPONSE 헤더")
    _reset_service()
    agent, _ = _buyer()
    async with _raw_client() as raw:
        r1 = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)})
        required = parse_payment_required(r1.json())
        my_quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)
        submitted = agent.build_payment(required, Hash.default(), my_quote, max_spend_usdc=SPEND)
        r2 = await raw.post(
            "/broker/orders",
            json={"symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)},
            headers={PAYMENT_HEADER: encode_payment_header(required.order_id, submitted)})
    check("HTTP 200", r2.status_code == 200, str(r2.status_code))
    check("X-PAYMENT-RESPONSE 헤더 존재", PAYMENT_RESPONSE_HEADER.lower() in
          {k.lower() for k in r2.headers.keys()})
    body = r2.json()
    check("본문에 settlement 포함", isinstance(body.get("settlement"), dict))
    check("settlement.success=True", (body.get("settlement") or {}).get("success") is True,
          str(body.get("settlement")))


# ---------- 5) 전송이 HTTP 여도 402 Guard 는 그대로 작동 ----------
async def test_guard_still_applies() -> None:
    print("\n[5] 원격 브로커가 청구서를 위조해도 Guard 가 서명 전에 차단")
    _reset_service()
    evil_payee = str(Keypair().pubkey())
    cli = _asgi_client()
    try:
        required = await cli.request_order(SYMBOL, SPEND, PRICE)
    finally:
        await cli.aclose()
    my_quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)

    # (a) 수취인 위조 — 한도 안쪽 금액이지만 돈이 다른 지갑으로 간다
    agent, _ = _buyer()
    required.requirements.pay_to = evil_payee
    try:
        agent.build_payment(required, Hash.default(), my_quote, max_spend_usdc=SPEND)
        check("수취인 위조 차단", False, "서명이 생성됨 — 차단 실패")
    except GuardError as e:
        check("수취인 위조 차단(GUARD_PAYEE_UNKNOWN)", e.result.code == "GUARD_PAYEE_UNKNOWN",
              e.result.code)
        check("차단 시 AP2 예산 미차감(유출 0)", agent.auth.remaining_usdc == Decimal("100"),
              str(agent.auth.remaining_usdc))

    # (b) 금액 부풀리기 — 우리 견적과 base units 가 다르면 차단
    _reset_service()
    cli = _asgi_client()
    try:
        required2 = await cli.request_order(SYMBOL, SPEND, PRICE)
    finally:
        await cli.aclose()
    agent2, _ = _buyer()
    required2.requirements.amount = int(required2.requirements.amount) + 1_000_000
    try:
        agent2.build_payment(required2, Hash.default(), my_quote, max_spend_usdc=SPEND)
        check("금액 위조 차단", False, "서명이 생성됨 — 차단 실패")
    except GuardError as e:
        check("금액 위조 차단(GUARD_AMOUNT_MISMATCH)", e.result.code == "GUARD_AMOUNT_MISMATCH",
              e.result.code)


# ---------- 6) 잘못된 입력·규약 위반 처리 ----------
async def test_bad_inputs() -> None:
    print("\n[6] 잘못된 요청·헤더 처리")
    _reset_service()
    async with _raw_client() as raw:
        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE), "oops": 1})
        check("선언 안 된 필드는 422 거부", r.status_code == 422, str(r.status_code))

        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": "abc", "price_usdc": str(PRICE)})
        check("숫자 아닌 금액은 400", r.status_code == 400, str(r.status_code))

        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": "0", "price_usdc": str(PRICE)})
        check("0 이하 금액은 400", r.status_code == 400, str(r.status_code))

        r = await raw.post("/broker/orders",
                           json={"symbol": SYMBOL, "spend_usdc": str(SPEND),
                                 "price_usdc": str(PRICE)},
                           headers={PAYMENT_HEADER: "not-base64-json"})
        check("깨진 X-PAYMENT 헤더는 400", r.status_code == 400, str(r.status_code))

        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE),
            "mode": "live"})
        # 라이브는 기본 잠김 — 청구서 발행 자체는 되지만 정산 단계에서 403
        check("라이브 모드 청구서는 발행됨(402)", r.status_code == 402, str(r.status_code))
        required = parse_payment_required(r.json())
        agent, _ = _buyer()
        my_quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)
        submitted = agent.build_payment(required, Hash.default(), my_quote, max_spend_usdc=SPEND)
        r = await raw.post("/broker/orders",
                           json={"symbol": SYMBOL, "spend_usdc": str(SPEND),
                                 "price_usdc": str(PRICE), "mode": "live"},
                           headers={PAYMENT_HEADER: encode_payment_header(
                               required.order_id, submitted)})
        check("온체인 정산은 기본 잠김(403)", r.status_code == 403, str(r.status_code))
        check("잠김 사유가 live_disabled", r.json().get("error") == "live_disabled", str(r.json()))


# ---------- 7) 규약 위반 응답을 클라이언트가 잡아낸다 ----------
async def test_client_protocol_errors() -> None:
    print("\n[7] 클라이언트의 규약 검증")
    try:
        parse_payment_required({"x402Version": 1})
        check("accepts[] 없는 응답 거부", False, "예외 없음")
    except X402ProtocolError:
        check("accepts[] 없는 응답 거부", True)
    try:
        parse_payment_required({"accepts": [{"scheme": "exact", "network": "n", "asset": "a",
                                             "maxAmountRequired": "1", "payTo": "p"}]})
        check("orderId 없는 accepts 거부(대사 키 부재)", False, "예외 없음")
    except X402ProtocolError:
        check("orderId 없는 accepts 거부(대사 키 부재)", True)


# ---------- 8) 메인 대시보드 앱에도 같은 402 가 마운트돼 있다 ----------
async def test_mounted_on_main_app() -> None:
    print("\n[8] 메인 앱 마운트 — 배포 URL 에서도 같은 402")
    from web.server import app as main_app
    _reset_service()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_app),
                                 base_url="http://app.test") as raw:
        r = await raw.post("/broker/orders", json={
            "symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)})
        check("메인 앱에서도 402", r.status_code == 402, str(r.status_code))
        d = await raw.get("/.well-known/x402")
        check("메인 앱에서도 디스커버리 200", d.status_code == 200, str(d.status_code))


# ---------- 9) 엔진의 매수 레그가 HTTP 402 전송으로도 동작한다 ----------
async def test_engine_over_http() -> None:
    print("\n[9] 엔진 매수 사이클 — 인프로세스 A2A ↔ HTTP 402 전송 교체")
    from agents.trading_agent import Decision
    from web.engine import TradingEngine
    from web.events import EventBus
    from web.store import BaseStore

    _reset_service()
    engine = TradingEngine(EventBus(), BaseStore())
    await engine.start("dry", {"type": "condition", "brain": "rule"},
                       {"type": "replay", "dataset": "daily", "symbol": SYMBOL},
                       autostart=False)
    check("기본(미설정)에서는 인프로세스 경로", engine._broker_http is None)

    # 브로커 서비스를 엔진의 원격 상대로 붙인다(ASGI 전송 — 포트 없이 같은 HTTP 의미론)
    engine._broker_http = Http402BrokerClient(
        "http://broker.test", transport=httpx.ASGITransport(app=broker_service.app))
    # 엔진의 Guard 신뢰 목록은 세션 브로커 키인데, HTTP 상대는 secrets/broker.json 지갑이다.
    # 실제 운영에서도 같은 키 파일을 읽어 동일하지만, 드라이 세션은 임시 키일 수 있으므로
    # 원격 상대를 신뢰 목록에 추가한다(= A2A 협의를 마친 상대라는 뜻).
    engine._guard.payees.add(str(broker_service.get_broker().pubkey))

    # 단일 종목 세션의 에이전트 키는 CFG.stock_symbol(예: tAAPL) — 엔진이 알려주는 값을 쓴다
    sym = engine._focus
    agent = engine.agents[sym]
    price = Decimal(agent._history[-1])
    before = agent.auth.remaining_usdc
    await engine._buy_cycle(sym, agent, price, Decision("buy", "HTTP 402 경로", SPEND))

    check("HTTP 전송으로 체결 1건", len(engine.trades) == 1, str(len(engine.trades)))
    if engine.trades:
        t = engine.trades[0]
        check("체결 status=settled", t.get("status") == "settled", str(t.get("status")))
        check("주문번호가 브로커 발행 형식", str(t.get("order_id", "")).startswith("ord_"),
              str(t.get("order_id")))
    check("AP2 예산이 실제로 차감됨", agent.auth.remaining_usdc < before,
          f"{before} → {agent.auth.remaining_usdc}")
    check("가드 차단 0건(정상 흐름 오탐 없음)", engine.guard_block_count == 0,
          str(engine.guard_block_count))
    check("포지션 반영", agent.position.quantity > 0, str(agent.position.quantity))
    await engine._finalize()
    check("세션 종료 시 HTTP 클라이언트 정리", engine._broker_http is None)


async def main() -> int:
    print("=== 브로커 HTTP 402 레그 (G5) ===")
    await test_discovery()
    await test_402_challenge()
    await test_full_cycle()
    await test_settlement_header()
    await test_guard_still_applies()
    await test_bad_inputs()
    await test_client_protocol_errors()
    await test_mounted_on_main_app()
    await test_engine_over_http()
    failed = [r for r in _results if not r[1]]
    print(f"\n===== 결과: 통과 {len(_results) - len(failed)} · 실패 {len(failed)} =====")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
