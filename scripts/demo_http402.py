"""HTTP 402 실왕복 데모 + 증빙 아카이빙 (G5).

`scripts/test_http402.py` 가 ASGI 로 의미론을 검증한다면, 이 스크립트는 **실제 TCP 소켓**
위에서 같은 흐름을 돌려 심사·데모용 증빙을 남긴다. 브로커를 진짜 uvicorn 서버로 띄우고
(별도 스레드·별도 포트), 구매 에이전트가 네트워크 너머의 상대와 결제한다.

  ① POST /broker/orders                 → HTTP/1.1 402 Payment Required + accepts[]
  ② 402 Guard 로 청구서 검증 → AP2 한도 승인 → 서명
  ③ POST /broker/orders + X-PAYMENT     → HTTP/1.1 200 OK + X-PAYMENT-RESPONSE
  ④ 같은 결제 재제출                     → 402 (1회용 청구서 — 리플레이 차단)
  ⑤ 악성 브로커 시나리오(수취인 위조)     → 서명 전 차단, 유출 0.00 USDC

증빙: artifacts/x402_http/<ts>_http402_cycle.json (요청·상태코드·헤더·본문 전문)

실행:
  python scripts/demo_http402.py              # 드라이(온체인 미전송)
  python scripts/demo_http402.py --port 8500  # 포트 지정

라이브(온체인) 왕복은 브로커 서비스를 따로 띄우고 엔진에 BROKER_HTTP_URL 을 주는 경로다:
  1) BROKER_SERVICE_ALLOW_LIVE=1 python -m web.broker_service --port 8402
  2) BROKER_HTTP_URL=http://127.0.0.1:8402 python -m web.server
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import socket
import sys
import threading
from datetime import datetime
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import httpx  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.keypair import Keypair  # noqa: E402

from config import CFG  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer  # noqa: E402
from payments.guard import Guard, GuardError  # noqa: E402
from payments.x402_http import (  # noqa: E402
    PAYMENT_HEADER, PAYMENT_RESPONSE_HEADER, encode_payment_header, parse_payment_required,
)
from web import broker_service  # noqa: E402

SYMBOL = "AAPL"
PRICE = Decimal("200")
SPEND = Decimal("10")
BUDGET = Decimal("100")


def hr(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def free_port(preferred: int) -> int:
    """선호 포트가 점유돼 있으면 빈 포트를 자동 할당한다(데모가 포트 충돌로 죽지 않게)."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_broker(port: int):
    """브로커를 진짜 HTTP 서버로 띄운다(별도 스레드에서 자체 이벤트 루프)."""
    import uvicorn
    config = uvicorn.Config(broker_service.app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):                       # 최대 10초 대기
        if server.started:
            return server
        await asyncio.sleep(0.1)
    raise RuntimeError(f"브로커 서비스가 포트 {port} 에서 뜨지 않았습니다.")


def build_buyer(broker_pubkey: str):
    """구매 에이전트 — 사용자 키(mandate 서명자) ≠ 에이전트 키, 402 Guard 결선."""
    user_kp, agent_kp = Keypair(), Keypair()
    mandate = OpenPaymentMandate(
        user_pubkey=str(user_kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=BUDGET, per_trade_max_usdc=Decimal("50"),
        allowed_symbols=[SYMBOL]).sign(user_kp)
    auth = PaymentAuthorizer(mandate, agent_kp=agent_kp)
    agent = TradingAgent(agent_kp, auth, Strategy(spend_per_trade_usdc=SPEND),
                         CFG.usdc_decimals, CFG.network)
    agent.guard = Guard(mandate, [broker_pubkey], CFG.usdc_decimals)
    return agent


def show(resp: httpx.Response, label: str) -> dict:
    """curl -i 처럼 상태줄·헤더·본문을 그대로 보여주고, 증빙용 dict 로 돌려준다."""
    print(f"\n  ← {label}")
    print(f"    HTTP/{resp.http_version.split('/')[-1]} {resp.status_code} {resp.reason_phrase}")
    for k in ("content-type", "cache-control", PAYMENT_RESPONSE_HEADER.lower()):
        if k in resp.headers:
            v = resp.headers[k]
            print(f"    {k}: {v[:96]}{'…' if len(v) > 96 else ''}")
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text[:400]}
    print("    " + json.dumps(body, ensure_ascii=False)[:400])
    return {
        "status_code": resp.status_code, "reason": resp.reason_phrase,
        "headers": {k: v for k, v in resp.headers.items()
                    if k.lower() in ("content-type", "cache-control",
                                     PAYMENT_RESPONSE_HEADER.lower())},
        "body": body,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="HTTP 402 실왕복 데모 (드라이)")
    ap.add_argument("--port", type=int, default=8402)
    args = ap.parse_args()

    port = free_port(args.port)
    base = f"http://127.0.0.1:{port}"
    server = await start_broker(port)
    broker_pk = str(broker_service.get_broker().pubkey)
    transcript: list = []

    print(f"402 Guard — 브로커 HTTP 402 실왕복 데모")
    print(f"  브로커 서버   : {base}  (실제 TCP 소켓, 별도 스레드)")
    print(f"  브로커 지갑   : {broker_pk}")
    print(f"  네트워크/자산 : {CFG.network} · USDC {CFG.usdc_mint}")
    print(f"  주문          : {SYMBOL} {SPEND} USDC 어치 @ {PRICE} (수수료 {CFG.broker_fee_bps}bps)")

    order_body = {"symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=20.0) as http:
            # ---------- ① 디스커버리 ----------
            hr("① GET /.well-known/x402 — 이 서버는 무엇을 어떤 조건으로 파는가")
            r = await http.get("/.well-known/x402")
            transcript.append({"step": "discovery", "request": {"method": "GET",
                               "path": "/.well-known/x402"}, "response": show(r, "디스커버리")})

            # ---------- ② 결제 없는 요청 → 402 ----------
            hr("② POST /broker/orders (결제 없음) — 진짜 402 Payment Required")
            print(f"  → POST {base}/broker/orders  {json.dumps(order_body, ensure_ascii=False)}")
            r = await http.post("/broker/orders", json=order_body)
            rec = show(r, "402 청구서")
            transcript.append({"step": "payment-required",
                               "request": {"method": "POST", "path": "/broker/orders",
                                           "body": order_body}, "response": rec})
            if r.status_code != 402:
                print("\n[실패] 402 가 아닙니다 — 데모 중단")
                return 1
            required = parse_payment_required(r.json())

            # ---------- ③ 402 Guard 검증 → 서명 ----------
            hr("③ 402 Guard — 원격 브로커의 청구서를 '내 견적'과 대조한 뒤에야 서명한다")
            agent = build_buyer(broker_pk)
            my_quote = broker_service.get_broker().quote(SYMBOL, SPEND, PRICE)
            print(f"  내 견적(독립 계산) : {my_quote.quantity} 주 · 총액 {my_quote.total_usdc} USDC")
            print(f"  브로커 청구        : {required.requirements.amount} base units "
                  f"→ 수취인 {required.requirements.pay_to}")
            res = agent.guard.check_demand(required, my_quote,
                                           expected_order_id=required.order_id,
                                           max_spend_usdc=SPEND)
            print(f"  Guard 판정         : {res.code} — {res.detail} ({res.where})")
            submitted = agent.build_payment(required, Hash.default(), my_quote,
                                            max_spend_usdc=SPEND)
            print(f"  AP2 승인 후 잔여   : {agent.auth.remaining_usdc} USDC")
            transcript.append({"step": "guard-check", "result": res.as_event(),
                               "my_quote": {"quantity": str(my_quote.quantity),
                                            "total_usdc": str(my_quote.total_usdc)}})

            # ---------- ④ X-PAYMENT 재시도 → 200 ----------
            hr("④ POST /broker/orders + X-PAYMENT — 같은 요청을 '결제를 붙여' 재시도")
            header = encode_payment_header(required.order_id, submitted)
            print(f"  → {PAYMENT_HEADER}: {header[:72]}… ({len(header)} bytes, base64 JSON)")
            r = await http.post("/broker/orders", json=order_body,
                                headers={PAYMENT_HEADER: header})
            rec = show(r, "정산 결과")
            transcript.append({"step": "payment-submitted",
                               "request": {"method": "POST", "path": "/broker/orders",
                                           "headers": {PAYMENT_HEADER: header}},
                               "response": rec})
            settled = r.status_code == 200

            # ---------- ⑤ 리플레이 ----------
            hr("⑤ 같은 결제를 다시 제출 — 1회용 청구서라 402 로 거부")
            r = await http.post("/broker/orders", json=order_body,
                                headers={PAYMENT_HEADER: header})
            rec = show(r, "리플레이 시도")
            transcript.append({"step": "replay-blocked", "response": rec})
            replay_blocked = r.status_code == 402

            # ---------- ⑥ 악성 브로커: 수취인 위조 ----------
            hr("⑥ 악성 브로커 — 한도 안쪽 금액으로 청구하되 수취인만 자기 지갑으로 바꾼다")
            r = await http.post("/broker/orders", json=order_body)
            evil = parse_payment_required(r.json())
            evil_wallet = str(Keypair().pubkey())
            evil.requirements.pay_to = evil_wallet     # 네트워크 응답을 가로채 위조한 상황
            print(f"  위조된 수취인 : {evil_wallet}")
            print(f"  청구 금액     : {evil.requirements.amount} base units (한도 이내 — AP2만으로는 통과)")
            agent2 = build_buyer(broker_pk)
            blocked = None
            try:
                agent2.build_payment(evil, Hash.default(), my_quote, max_spend_usdc=SPEND)
                print("  [실패] 서명이 생성됐다 — 차단 실패")
            except GuardError as e:
                blocked = e.result
                print(f"  Guard 판정    : {blocked.code} — {blocked.detail} ({blocked.where})")
                print(f"  예상 수취인   : {blocked.expected}")
                print(f"  실제 청구     : {blocked.actual}")
            leak = BUDGET - agent2.auth.remaining_usdc
            print(f"  유출 금액     : {leak:.2f} USDC  (예산 {BUDGET} → 잔여 {agent2.auth.remaining_usdc})")
            transcript.append({"step": "attack-payee-forged",
                               "evil_payee": evil_wallet,
                               "guard": blocked.as_event() if blocked else None,
                               "leak_usdc": str(leak)})
    finally:
        server.should_exit = True

    # ---------- 증빙 아카이빙 ----------
    out_dir = os.path.join(ROOT, "artifacts", "x402_http")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"{ts}_http402_cycle.json")
    archive = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "scripts/demo_http402.py",
        "transport": "http-402 (real TCP socket, uvicorn)",
        "mode": "dry",                       # 서명·검증은 실제, 온체인 브로드캐스트만 생략
        "network": CFG.network,
        "broker_url": base,
        "broker_pubkey": broker_pk,
        "usdc_mint": CFG.usdc_mint,
        "fee_bps": CFG.broker_fee_bps,
        "order": {"symbol": SYMBOL, "spend_usdc": str(SPEND), "price_usdc": str(PRICE)},
        "summary": {
            "challenge_402": True,
            "settled_200": settled,
            "replay_blocked_402": replay_blocked,
            "attack_blocked": bool(blocked),
            "leak_usdc": str(leak),
        },
        "transcript": transcript,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2, default=str)

    hr("결과")
    s = archive["summary"]
    print(f"  402 청구서 발행    : {'예' if s['challenge_402'] else '아니오'}")
    print(f"  결제 후 200 정산   : {'예' if s['settled_200'] else '아니오'}")
    print(f"  리플레이 차단      : {'예' if s['replay_blocked_402'] else '아니오'}")
    print(f"  수취인 위조 차단   : {'예' if s['attack_blocked'] else '아니오'}")
    print(f"  유출 금액          : {s['leak_usdc']} USDC")
    print(f"  증빙               : {os.path.relpath(path, ROOT)}")
    ok = s["challenge_402"] and s["settled_200"] and s["replay_blocked_402"] and s["attack_blocked"]
    return 0 if ok and Decimal(s["leak_usdc"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
