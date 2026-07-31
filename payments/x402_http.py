"""x402 HTTP 전송 레이어 (G5) — 인프로세스 A2A 메시지를 '진짜' HTTP 402 왕복으로 감싼다.

지금까지 x402 3단계(payment-required → submitted → completed)는 같은 프로세스 안의
파이썬 객체로만 오갔다. 프로토콜 의미는 동일하지만, x402 의 'HTTP' 정합 — 즉
**서버가 `402 Payment Required` 상태 코드와 `accepts[]` 를 실제로 돌려주고, 클라이언트가
`X-PAYMENT` 헤더를 붙여 같은 요청을 재시도한다** — 는 증명할 수 없었다.

이 모듈은 그 전송 규약 하나만 담당한다(결제 로직은 기존 BrokerAgent·Guard·AP2 그대로):

    ① POST /broker/orders                      → 402 + {"x402Version":1,"accepts":[…]}
    ② POST /broker/orders  (X-PAYMENT: base64) → 200 + X-PAYMENT-RESPONSE: base64

서버(web/broker_service.py)와 클라이언트(Http402BrokerClient)가 **같은 함수**로 바디를
만들고 읽으므로 양쪽 와이어 포맷이 갈라질 수 없다.

402 Guard 와의 관계: 전송이 HTTP 가 되면 상대(브로커)가 진짜 원격이 된다. 구매 에이전트는
브로커가 보낸 accepts[] 를 그대로 믿지 않고, **자기가 계산한 견적**과 대조한 뒤에야
서명한다(payments/guard.py check_demand). 즉 이 레이어는 신뢰를 늘리지 않고 거리를 늘린다.
"""
from __future__ import annotations
import base64
import json
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from shared.a2a_messages import (
    X402_VERSION, PaymentCompleted, PaymentPayload, PaymentRequired,
    PaymentRequirements, PaymentSubmitted,
)

# x402 표준 헤더 이름 (대소문자 무관하게 읽되, 보낼 때는 이 표기를 쓴다)
PAYMENT_HEADER = "X-PAYMENT"
PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"

# 청구서 유효시간(초) — BrokerAgent.order_ttl_sec 과 같은 값을 accepts[] 에 실어 알린다
DEFAULT_MAX_TIMEOUT_SEC = 120


class X402ProtocolError(Exception):
    """상대가 x402 규약을 벗어난 응답을 보냈다 (상태 코드·필드 누락·형식 오류)."""


# ---------------------------------------------------------------- 서버 → 클라이언트

def accepts_entry(required: PaymentRequired, resource_url: str,
                  max_timeout_sec: int = DEFAULT_MAX_TIMEOUT_SEC) -> Dict[str, Any]:
    """PaymentRequired → x402 accepts[] 항목 하나 (공식 필드명으로 직렬화).

    스펙 필드(scheme·network·maxAmountRequired·resource·payTo·asset)는 그대로 쓰고,
    우리 도메인 값(주문번호·종목·수량·소수자릿수·Memo 규약)은 `extra` 에 넣는다 —
    스펙이 확장 슬롯으로 정의한 자리라 표준을 깨지 않는다."""
    r = required.requirements
    return {
        "scheme": r.scheme,                    # "exact"
        "network": r.network,                  # "solana-devnet" / "solana-localnet"
        "maxAmountRequired": str(r.amount),    # base units (문자열 — 큰 수 정밀 보존)
        "resource": resource_url,
        "description": r.resource,             # 사람이 읽는 구매 내역
        "mimeType": "application/json",
        "payTo": r.pay_to,
        "maxTimeoutSeconds": max_timeout_sec,
        "asset": r.asset,                      # USDC 민트
        "extra": {
            "decimals": r.decimals,
            "orderId": required.order_id,
            "symbol": required.symbol,
            "quantity": required.quantity,
            "priceUsdc": required.price_usdc,
            # 온체인 Memo 대사 규약 — 구매자는 AT1:{orderId}:{mandate_sig8} 을 tx 에 박는다
            "memoFormat": "AT1:{orderId}:{mandateSig8}",
        },
    }


def payment_required_body(required: PaymentRequired, resource_url: str,
                          error: str = "payment required",
                          max_timeout_sec: int = DEFAULT_MAX_TIMEOUT_SEC) -> Dict[str, Any]:
    """402 응답 본문 — x402 표준 형태 {x402Version, error, accepts[]}."""
    return {
        "x402Version": X402_VERSION,
        "error": error,
        "accepts": [accepts_entry(required, resource_url, max_timeout_sec)],
    }


def settlement_payload(completed: PaymentCompleted) -> Dict[str, Any]:
    """정산 결과 → X-PAYMENT-RESPONSE 에 실을 dict."""
    return {
        "x402Version": X402_VERSION,
        "success": completed.status == "settled",
        "orderId": completed.order_id,
        "status": completed.status,
        "transaction": completed.tx_signature,
        "confirmed": completed.confirmed,
        "deliveredAsset": completed.delivered_asset,
        "deliveredAmount": completed.delivered_amount,
        "deliveryTransaction": completed.delivery_tx_signature,
        "reason": completed.reason,
    }


def encode_settlement_header(completed: PaymentCompleted) -> str:
    return _b64_encode(settlement_payload(completed))


def parse_settlement(payload: Dict[str, Any]) -> PaymentCompleted:
    """X-PAYMENT-RESPONSE(또는 200 본문) → PaymentCompleted."""
    try:
        return PaymentCompleted(
            order_id=str(payload["orderId"]),
            tx_signature=str(payload.get("transaction", "")),
            confirmed=bool(payload.get("confirmed", False)),
            delivered_asset=str(payload.get("deliveredAsset", "")),
            delivered_amount=int(payload.get("deliveredAmount", 0)),
            delivery_tx_signature=str(payload.get("deliveryTransaction", "")),
            status=str(payload.get("status", "failed")),
            reason=str(payload.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise X402ProtocolError(f"정산 응답 형식 오류: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 클라이언트 → 서버

def encode_payment_header(order_id: str, submitted: PaymentSubmitted) -> str:
    """서명된 결제 → X-PAYMENT 헤더 값 (base64 JSON).

    본문이 아니라 헤더로 보내는 것이 x402 규약이다 — 같은 요청을 '결제를 붙여' 재시도하는
    모양이라, 자원 요청과 결제가 하나의 HTTP 왕복으로 묶인다."""
    body = submitted.payment.to_dict()
    body["orderId"] = order_id          # 어느 청구서에 대한 결제인지 (서버의 대사 키)
    return _b64_encode(body)


def decode_payment_header(raw: str) -> Tuple[str, PaymentSubmitted]:
    """X-PAYMENT 헤더 → (order_id, PaymentSubmitted). 형식 오류는 X402ProtocolError."""
    try:
        data = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception as e:
        raise X402ProtocolError(f"X-PAYMENT 헤더를 해석할 수 없습니다: {type(e).__name__}: {e}")
    if not isinstance(data, dict):
        raise X402ProtocolError("X-PAYMENT 본문이 JSON 객체가 아닙니다")
    order_id = str(data.get("orderId", ""))
    if not order_id:
        raise X402ProtocolError("X-PAYMENT 에 orderId 가 없습니다 (대사 키 부재)")
    try:
        payload = PaymentPayload.from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        # ValueError 가 빠져 있으면 x402Version="abc" 같은 값이 int() 에서 터진 채 그대로
        # 올라가 **무인증 공개 엔드포인트가 500(비JSON)** 을 준다. 형식 오류는 서버 잘못이
        # 아니라 요청 잘못이므로 400 이어야 한다(broker_service 가 그렇게 바꾼다).
        raise X402ProtocolError(f"X-PAYMENT payload 형식 오류: {type(e).__name__}: {e}")
    return order_id, PaymentSubmitted(order_id=order_id, payment=payload)


def parse_payment_required(body: Dict[str, Any]) -> PaymentRequired:
    """402 응답 본문 → PaymentRequired (accepts[0] 사용).

    ⚠ 여기서 만들어진 값은 '브로커의 주장'일 뿐이다. 서명 전에 402 Guard 가
    구매자 자신의 견적과 대조한다(payments/guard.py check_demand)."""
    if not isinstance(body, dict):
        raise X402ProtocolError("402 응답 본문이 JSON 객체가 아닙니다")
    accepts = body.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        raise X402ProtocolError("402 응답에 accepts[] 가 없습니다")
    a = accepts[0]
    extra = a.get("extra") or {}
    try:
        reqs = PaymentRequirements(
            scheme=str(a["scheme"]),
            network=str(a["network"]),
            asset=str(a["asset"]),
            amount=int(a["maxAmountRequired"]),
            pay_to=str(a["payTo"]),
            resource=str(a.get("description") or a.get("resource", "")),
            decimals=int(extra.get("decimals", 6)),
            x402_version=int(body.get("x402Version", X402_VERSION)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise X402ProtocolError(f"accepts[0] 형식 오류: {type(e).__name__}: {e}")
    order_id = str(extra.get("orderId", ""))
    if not order_id:
        raise X402ProtocolError("accepts[0].extra.orderId 가 없습니다 (온체인 대사 키 부재)")
    return PaymentRequired(
        order_id=order_id,
        symbol=str(extra.get("symbol", "")),
        quantity=str(extra.get("quantity", "0")),
        price_usdc=str(extra.get("priceUsdc", "0")),
        requirements=reqs,
    )


class Http402BrokerClient:
    """브로커 HTTP 402 서비스 클라이언트 — 구매 에이전트 쪽 전송 어댑터.

    엔진은 이 객체를 통해 브로커와 대화하지만, 받아온 청구서는 여전히 402 Guard 를
    거쳐야 서명된다. 즉 '전송이 바뀌어도 방어는 그대로'다.
    """

    def __init__(self, base_url: str, timeout_sec: float = 20.0, transport=None):
        """transport: httpx 전송 계층 교체용(테스트·데모에서 ASGITransport 로 서버를
        띄우지 않고 같은 HTTP 의미론을 검증한다). 운영에서는 None = 실제 네트워크."""
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self._transport = transport
        self._client = None   # httpx.AsyncClient (지연 생성)

    async def _http(self):
        if self._client is None:
            import httpx  # 지연 임포트 — HTTP 레그를 안 쓰는 실행 경로에 의존성을 주지 않는다
            self._client = httpx.AsyncClient(timeout=self.timeout_sec, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def discovery(self) -> Dict[str, Any]:
        """GET /.well-known/x402 — 이 서버가 어떤 결제를 요구하는지 공개."""
        cli = await self._http()
        r = await cli.get(f"{self.base_url}/.well-known/x402")
        r.raise_for_status()
        return r.json()

    async def request_order(self, symbol: str, spend_usdc: Decimal,
                            price_usdc: Decimal, mode: str = "dry") -> PaymentRequired:
        """① 결제 없이 주문 요청 → 서버는 402 Payment Required 로 청구서를 돌려준다.

        200 이 오면 규약 위반이다(결제 없이 자원을 내주는 서버) — 오류로 처리한다."""
        cli = await self._http()
        r = await cli.post(f"{self.base_url}/broker/orders", json={
            "symbol": symbol, "spend_usdc": str(spend_usdc),
            "price_usdc": str(price_usdc), "mode": mode,
        })
        if r.status_code != 402:
            raise X402ProtocolError(
                f"402 Payment Required 를 기대했으나 {r.status_code} 응답: {r.text[:200]}")
        return parse_payment_required(r.json())

    async def submit_payment(self, required: PaymentRequired, submitted: PaymentSubmitted,
                             spend_usdc: Decimal, mode: str = "dry") -> PaymentCompleted:
        """② 같은 요청에 X-PAYMENT 헤더를 붙여 재시도 → 200 + 정산 결과.

        서버가 정산에 실패하면 402 를 다시 돌려준다(결제가 성립하지 않았으므로).
        그 경우에도 본문의 settlement 를 읽어 실패 사유를 그대로 전달한다."""
        cli = await self._http()
        r = await cli.post(
            f"{self.base_url}/broker/orders",
            json={"symbol": required.symbol, "spend_usdc": str(spend_usdc),
                  "price_usdc": required.price_usdc, "mode": mode},
            headers={PAYMENT_HEADER: encode_payment_header(required.order_id, submitted)},
        )
        header = r.headers.get(PAYMENT_RESPONSE_HEADER)
        if header:
            return parse_settlement(_b64_decode(header))
        try:
            body = r.json()
        except Exception:
            raise X402ProtocolError(f"정산 응답을 해석할 수 없습니다 ({r.status_code}): {r.text[:200]}")
        settlement = body.get("settlement") if isinstance(body, dict) else None
        if isinstance(settlement, dict):
            return parse_settlement(settlement)
        raise X402ProtocolError(
            f"정산 응답에 X-PAYMENT-RESPONSE·settlement 가 없습니다 ({r.status_code}): {str(body)[:200]}")


# ---------------------------------------------------------------- 내부 유틸

def _b64_encode(obj: Dict[str, Any]) -> str:
    return base64.b64encode(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _b64_decode(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception as e:
        raise X402ProtocolError(f"base64 JSON 해석 실패: {type(e).__name__}: {e}")


def optional_client(base_url: str) -> Optional[Http402BrokerClient]:
    """설정값이 비어 있으면 None — 호출측이 '인프로세스 A2A' 기본 경로를 그대로 쓴다."""
    url = (base_url or "").strip()
    return Http402BrokerClient(url) if url else None
