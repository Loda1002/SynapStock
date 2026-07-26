"""브로커 HTTP 402 서비스 (G5) — 판매자 측을 진짜 HTTP 자원 서버로 노출한다.

    curl -i -X POST http://127.0.0.1:8402/broker/orders \
         -H "Content-Type: application/json" \
         -d '{"symbol":"AAPL","spend_usdc":"10","price_usdc":"200"}'
    → HTTP/1.1 402 Payment Required
       {"x402Version":1,"error":"payment required","accepts":[{…"payTo":…,"asset":…}]}

**실행 방법 두 가지 — 같은 코드, 같은 브로커 지갑**

  1) 별도 프로세스(시연·심사용):  python -m web.broker_service --port 8402
     브로커가 구매 에이전트와 **다른 프로세스·다른 포트·다른 키페어**임을 보여준다.
     "악성 브로커도 당신들이 짰잖나" 에 대한 구조적 답이다.
  2) 메인 앱에 마운트(배포용):    web/server.py 가 이 모듈의 router 를 include 한다.
     Cloud Run 은 컨테이너당 포트를 하나(`$PORT`)만 외부에 노출하므로, 배포 URL 에서도
     `curl -i https://<url>/broker/orders` 로 같은 402 를 확인할 수 있어야 한다.

**범위(정직하게 명시)**: HTTP 402 실왕복은 **매수 레그(자산 구매)** 다. 매도(환매)는
브로커가 구매자에게 돈을 보내는 방향이라 402 challenge 모델과 구조가 맞지 않아
A2A 인프로세스로 남는다 — README 스펙 대응표에 그대로 적는다.

**결제 로직은 하나도 새로 만들지 않았다**: 견적·청구서·검증·정산은 기존 BrokerAgent 를
그대로 호출하고, 이 파일은 전송(HTTP 상태 코드·헤더)만 담당한다. 구매자 쪽 방어
(402 Guard·AP2 mandate)도 그대로다 — 오히려 상대가 진짜 원격이 되어 의미가 커진다.
"""
from __future__ import annotations
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from solders.pubkey import Pubkey

from config import CFG
from agents.broker_agent import BrokerAgent
from payments import x402_solana as x
from payments.x402_http import (
    PAYMENT_HEADER, PAYMENT_RESPONSE_HEADER, X402ProtocolError,
    decode_payment_header, encode_settlement_header, payment_required_body, settlement_payload,
)
from run_demo import _load_or_new
from shared.models import Quote

# 라이브(온체인 전송) 정산을 이 서비스가 수행해도 되는가. 기본 차단 —
# 실수로 실제 자금이 움직이지 않게, 웹 라이브(ALLOW_LIVE_FROM_WEB)와 같은 원칙을 따른다.
ALLOW_LIVE = os.environ.get("BROKER_SERVICE_ALLOW_LIVE", "0").lower() in ("1", "true", "yes")

router = APIRouter(tags=["x402"])

# 발행했으나 아직 결제되지 않은 청구서 — order_id → (PaymentRequired, Quote, mode)
_pending: Dict[str, Tuple[Any, Quote, str]] = {}
_MAX_PENDING = 200

_broker: Optional[BrokerAgent] = None
_rpc_client = None


def get_broker() -> BrokerAgent:
    """브로커 에이전트 (지연 생성). 엔진과 **같은 키 파일**(secrets/broker.json)을 읽어,
    별도 프로세스로 띄워도 온체인 수취 지갑이 동일하다."""
    global _broker
    if _broker is None:
        kp = _load_or_new(os.path.join(CFG.wallet_dir, "broker.json"), required=False,
                          env_json=CFG.broker_keypair_json)
        _broker = BrokerAgent(
            kp,
            Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
            Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint else None,
            CFG.stock_decimals, CFG.network, fee_bps=CFG.broker_fee_bps,
        )
    return _broker


class OrderBody(BaseModel):
    """주문 요청 — 선언되지 않은 필드는 422 로 거부한다(web/server.py StrictBody 와 같은 원칙)."""
    model_config = {"extra": "forbid"}

    symbol: str
    spend_usdc: str          # Decimal 정밀 보존을 위해 문자열
    price_usdc: str          # 합의된 시세 (구매자·판매자가 같은 시세 피드를 본다는 전제)
    mode: str = "dry"        # dry(서명만) / live(온체인 브로드캐스트)


def _resource_url(request: Request, order_id: str) -> str:
    return str(request.url).split("?")[0] + f"/{order_id}"


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "x402Version": 1, "error": code, "detail": detail})


@router.get("/.well-known/x402")
async def discovery() -> Dict[str, Any]:
    """x402 디스커버리 — 이 서버가 무엇을 어떤 조건으로 파는지 공개한다."""
    b = get_broker()
    return {
        "x402Version": 1,
        "resources": [{
            "resource": "/broker/orders",
            "methods": ["POST"],
            "scheme": "exact",
            "network": CFG.network,
            "asset": CFG.usdc_mint,
            "assetDecimals": CFG.usdc_decimals,
            "payTo": str(b.pubkey),
            "feeBps": CFG.broker_fee_bps,
            "maxTimeoutSeconds": b.order_ttl_sec,
            "description": "토큰화 주식 매수 — exact 결제 확인 후 주식 토큰을 전달한다",
        }],
        # 미구현을 먼저 밝힌다 (docs/differentiation.md §6 정직성 기준선)
        "notImplemented": [
            "매도(환매) 레그는 A2A 인프로세스 — 402 challenge 모델과 방향이 반대",
            "facilitator 미사용 — 판매자가 직접 검증·정산한다",
        ],
    }


@router.post("/broker/orders")
async def create_order(body: OrderBody, request: Request):
    """x402 자원 엔드포인트.

    - `X-PAYMENT` 헤더가 없으면 → **402 Payment Required** + accepts[] (청구서)
    - `X-PAYMENT` 헤더가 있으면 → 검증·정산 후 **200** + `X-PAYMENT-RESPONSE`
      (정산 실패는 결제가 성립하지 않은 것이므로 다시 402)
    """
    try:
        spend = Decimal(body.spend_usdc)
        price = Decimal(body.price_usdc)
    except InvalidOperation:
        return _error(400, "bad_request", "spend_usdc·price_usdc 가 숫자 형식이 아닙니다.")
    if spend <= 0 or price <= 0:
        return _error(400, "bad_request", "spend_usdc·price_usdc 는 0보다 커야 합니다.")
    if body.mode not in ("dry", "live"):
        return _error(400, "bad_request", "mode 는 dry 또는 live 여야 합니다.")

    broker = get_broker()
    raw = request.headers.get(PAYMENT_HEADER) or request.headers.get(PAYMENT_HEADER.lower())

    # ---- ① 결제 없는 첫 요청 → 402 Payment Required ----
    if not raw:
        quote = broker.quote(body.symbol, spend, price)
        if quote.quantity <= 0:
            return _error(400, "bad_request",
                          "지불액이 1단위 미만이라 수량이 0입니다 — spend_usdc 를 올리세요.")
        required = broker.make_payment_required(quote)
        if len(_pending) >= _MAX_PENDING:      # 메모리 무한 증가 방지 (가장 오래된 것부터)
            for k in list(_pending)[:len(_pending) - _MAX_PENDING + 1]:
                _pending.pop(k, None)
        _pending[required.order_id] = (required, quote, body.mode)
        return JSONResponse(
            status_code=402,
            content=payment_required_body(
                required, _resource_url(request, required.order_id),
                max_timeout_sec=broker.order_ttl_sec),
            headers={"Cache-Control": "no-store"},
        )

    # ---- ② X-PAYMENT 를 붙인 재요청 → 검증·정산 ----
    try:
        order_id, submitted = decode_payment_header(raw)
    except X402ProtocolError as e:
        return _error(400, "invalid_payment_header", str(e))

    pending = _pending.get(order_id)
    if pending is None:
        # 발행한 적 없거나 이미 정산된 주문 — 재사용(리플레이) 시도도 여기서 걸린다
        return _error(402, "unknown_order",
                      f"발행하지 않았거나 이미 처리된 주문입니다: {order_id}")
    required, quote, issued_mode = pending

    live = (body.mode == "live") or (issued_mode == "live")
    if live and not ALLOW_LIVE:
        return _error(403, "live_disabled",
                      "이 브로커 서비스는 온체인 정산이 잠겨 있습니다 "
                      "(BROKER_SERVICE_ALLOW_LIVE=1 로 명시적으로 열어야 합니다).")

    client = await _get_rpc_client() if live else None
    completed = await broker.settle(
        submitted, required.requirements, quote.quantity, live=live, client=client)

    _pending.pop(order_id, None)   # 성공·실패 모두 1회용 (이중 정산 차단 — 서명 dedup 과 이중 방어)

    payload = settlement_payload(completed)
    if completed.status != "settled":
        # 결제가 성립하지 않았다 → 자원을 내주지 않고 402 를 유지한다
        return JSONResponse(status_code=402, content={
            "x402Version": 1, "error": "payment_invalid",
            "detail": completed.reason or "결제 검증·정산 실패",
            "settlement": payload,
        }, headers={PAYMENT_RESPONSE_HEADER: encode_settlement_header(completed)})

    return JSONResponse(status_code=200, content={
        "x402Version": 1,
        "orderId": completed.order_id,
        "symbol": required.symbol,
        "quantity": required.quantity,
        "resource": required.requirements.resource,
        "settlement": payload,
    }, headers={PAYMENT_RESPONSE_HEADER: encode_settlement_header(completed)})


async def _get_rpc_client():
    global _rpc_client
    if _rpc_client is None:
        _rpc_client = await x.get_client(CFG.rpc_url)
    return _rpc_client


# ---- 단독 실행용 앱 (별도 프로세스·별도 포트) ----

app = FastAPI(title="402 Guard — Broker x402 Service", version="0.1.0")
app.include_router(router)


def main() -> None:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="브로커 HTTP 402 서비스 (x402 자원 서버)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BROKER_PORT", "8402")))
    ap.add_argument("--host", default=os.environ.get("BROKER_HOST", "127.0.0.1"))
    args = ap.parse_args()
    b = get_broker()
    print(f"[브로커 402 서비스] http://{args.host}:{args.port}  수취 지갑 {b.pubkey}")
    print(f"  네트워크 {CFG.network} · USDC {CFG.usdc_mint} · 수수료 {CFG.broker_fee_bps}bps")
    print(f"  온체인 정산 {'허용(BROKER_SERVICE_ALLOW_LIVE=1)' if ALLOW_LIVE else '잠김(드라이 전용)'}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
