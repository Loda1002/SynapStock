"""Broker (판매) 에이전트.

책임: 토큰화 주식 시세 견적 → payment-required 발행 → 제출된 결제 검증
→ (라이브면) 온체인 정산 및 주식토큰 전달 → payment-completed.

지금은 규칙 기반. 이후 Gemini(ADK) 로 동적 가격/재고 판단을 붙일 수 있다.
"""
from __future__ import annotations
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import to_base_units, from_base_units
from shared.models import Quote
from shared.a2a_messages import (
    PaymentRequired, PaymentRequirements, PaymentSubmitted, PaymentCompleted,
)
from payments import x402_solana as x


class BrokerAgent:
    def __init__(
        self,
        keypair: Keypair,
        usdc_mint: Pubkey,
        usdc_decimals: int,
        stock_mint: Optional[Pubkey],
        stock_decimals: int,
        network: str,
        fee_bps: int = 0,   # A8 브로커 수수료 (30 = 0.3%) — 브로커 수익모델
    ):
        self.kp = keypair
        self.usdc_mint = usdc_mint
        self.usdc_decimals = usdc_decimals
        self.stock_mint = stock_mint
        self.stock_decimals = stock_decimals
        self.network = network
        self.fee_bps = fee_bps
        # 이중청구/리플레이 방어 — 이미 처리한 결제 서명은 다시 정산하지 않는다.
        self.used_signatures: set[str] = set()
        # 청구서 유효시간(expires_at) — 발행 후 이 시간이 지나면 정산을 거부한다.
        self.order_ttl_sec: int = 120
        self._order_created: dict[str, float] = {}

    @property
    def pubkey(self) -> Pubkey:
        return self.kp.pubkey()

    def _order_expired(self, order_id: str) -> bool:
        """발행한 청구서인데 유효시간(expires_at)을 넘겼는지. 미발행 주문은 만료 판정 안 함."""
        created = self._order_created.get(order_id)
        return created is not None and (time.time() - created) > self.order_ttl_sec

    def _guard_settlement(self, order_id: str, sig: str) -> Optional[str]:
        """정산 전 공통 방어 — 이중청구(서명 재사용)·만료면 사유 문자열, 통과면 None."""
        if sig in self.used_signatures:
            return "이중청구 — 이미 처리된 결제 서명(리플레이)"
        if self._order_expired(order_id):
            return "청구서 만료 — 유효시간(expires_at) 초과"
        return None

    @property
    def _fee_rate(self) -> Decimal:
        return Decimal(self.fee_bps) / Decimal(10000)

    # 1) 견적 (매수: 예산 → 수량) — 수수료 포함 총액이 spend_usdc 를 넘지 않게 수량을 내림
    def quote(self, symbol: str, spend_usdc: Decimal, price_usdc: Decimal) -> Quote:
        quantity = (spend_usdc / (price_usdc * (1 + self._fee_rate))).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN)
        subtotal = (quantity * price_usdc).quantize(Decimal("0.01"))
        fee = (subtotal * self._fee_rate).quantize(Decimal("0.01"))
        return Quote(symbol=symbol, price_usdc=price_usdc, quantity=quantity,
                     total_usdc=subtotal + fee,   # 구매자 지불 총액 (AP2 검사 기준)
                     subtotal_usdc=subtotal, fee_usdc=fee, fee_bps=self.fee_bps)

    # 1') 매도 견적 (수량 → 대금) — 브로커가 되사주되 수수료를 대금에서 차감
    def sell_quote(self, symbol: str, quantity: Decimal, price_usdc: Decimal) -> Quote:
        subtotal = (quantity * price_usdc).quantize(Decimal("0.01"))
        fee = (subtotal * self._fee_rate).quantize(Decimal("0.01"))
        return Quote(symbol=symbol, price_usdc=price_usdc, quantity=quantity,
                     total_usdc=subtotal - fee,   # 판매자 수령액
                     subtotal_usdc=subtotal, fee_usdc=fee, fee_bps=self.fee_bps)

    # 2) payment-required 발행
    def make_payment_required(self, quote: Quote) -> PaymentRequired:
        order_id = f"ord_{uuid.uuid4().hex[:10]}"
        self._order_created[order_id] = time.time()   # expires_at 기준 시각
        amount = to_base_units(quote.total_usdc, self.usdc_decimals)
        reqs = PaymentRequirements(
            scheme="exact",
            network=self.network,
            asset=str(self.usdc_mint),
            amount=amount,
            pay_to=str(self.pubkey),
            resource=f"STOCK:{quote.symbol} x{quote.quantity} (fee {quote.fee_usdc} USDC incl.)",
            decimals=self.usdc_decimals,
        )
        return PaymentRequired(
            order_id=order_id, symbol=quote.symbol,
            quantity=str(quote.quantity), price_usdc=str(quote.price_usdc),
            requirements=reqs,
        )

    # 2') 매도용 payment-required — "주식을 이만큼 보내면 USDC 로 되사준다"
    def make_stock_required(self, quote: Quote) -> PaymentRequired:
        if self.stock_mint is None:
            raise ValueError("stock_mint 미설정 — 매도 견적 불가")
        order_id = f"ord_{uuid.uuid4().hex[:10]}"
        self._order_created[order_id] = time.time()   # expires_at 기준 시각
        amount = to_base_units(quote.quantity, self.stock_decimals)
        reqs = PaymentRequirements(
            scheme="exact",
            network=self.network,
            asset=str(self.stock_mint),          # 매도는 '주식'이 지불 자산
            amount=amount,
            pay_to=str(self.pubkey),
            resource=(f"USDC-BUYBACK:{quote.symbol} x{quote.quantity} @ {quote.price_usdc}"
                      f" (fee {quote.fee_usdc} USDC deducted)"),
            decimals=self.stock_decimals,
        )
        return PaymentRequired(
            order_id=order_id, symbol=quote.symbol,
            quantity=str(quote.quantity), price_usdc=str(quote.price_usdc),
            requirements=reqs,
        )

    # 3') 매도 정산 — 주식 수령 검증 → USDC 지급
    async def settle_sale(
        self,
        submitted: PaymentSubmitted,
        requirements: PaymentRequirements,
        total_usdc: Decimal,
        live: bool,
        client=None,
    ) -> PaymentCompleted:
        tx = x.decode_payload(submitted.payment.serialized_transaction)
        sig = x.signature_str(tx)

        blocked = self._guard_settlement(submitted.order_id, sig)
        if blocked is not None:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.usdc_mint), delivered_amount=0,
                status="failed", reason=blocked,
            )

        ok, reason, amount = x.verify_payment(
            tx,
            expected_mint=Pubkey.from_string(requirements.asset),  # 주식 민트
            expected_dest_owner=self.pubkey,
            expected_amount=requirements.amount,
            expected_order_id=submitted.order_id,   # Memo 대사 키
        )
        if not ok:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.usdc_mint), delivered_amount=0,
                status="failed", reason=reason,
            )
        self.used_signatures.add(sig)   # 검증 통과한 결제 서명 기록 (리플레이 방어)

        seller_pubkey = tx.message.account_keys[0]
        stock_sig = sig
        payout_sig = ""
        confirmed = False
        paid = True   # 드라이런은 지급 성공으로 간주. 라이브에서만 실제 확정 여부로 갱신.
        payout_amount = to_base_units(total_usdc, self.usdc_decimals)

        if live and client is not None:
            stock_sig, confirmed = await x.submit_and_confirm(client, tx)
            if confirmed:
                # USDC 지급 (broker → seller)
                bh = await x.get_latest_blockhash(client)
                payout_tx = x.build_transfer_transaction(
                    payer=self.kp, mint=self.usdc_mint, dest_owner=seller_pubkey,
                    amount=payout_amount, decimals=self.usdc_decimals, blockhash=bh,
                )
                payout_sig, paid = await x.submit_and_confirm(client, payout_tx)
                if not paid:
                    payout_sig = ""
            else:
                paid = False   # 주식 미확정이면 지급 시도 자체를 안 함

        # 라이브: 주식 수령이 확정되고 USDC 지급까지 확정돼야 settled.
        # 주식은 넘어왔는데 지급이 실패하면 partial(대금 미도착) — 매수측 결함 I 의 매도 대칭(BUG-02).
        if not live:
            status = "settled"
        elif confirmed and paid:
            status = "settled"
        elif confirmed and not paid:
            status = "partial"
        else:
            status = "failed"

        return PaymentCompleted(
            order_id=submitted.order_id,
            tx_signature=stock_sig,               # 판매자가 보낸 주식 전송 tx
            confirmed=confirmed,
            delivered_asset=str(self.usdc_mint),  # 브로커가 지급한 자산
            # 지급하지도 않은 대금을 실어 보내지 않는다 — 미지급이면 0.
            # 매수 레그(:289)의 매도 대칭이다. 이 값은 증빙 아카이브(artifacts/tx/)에
            # 그대로 남으므로, 전액을 적으면 status=partial 과 서로를 반박하는 파일이
            # 공개 저장소에 쌓인다(축④는 이력 기반 심사다).
            delivered_amount=(payout_amount if status == "settled" else 0),
            delivery_tx_signature=payout_sig,     # USDC 지급 tx
            status=status,
            reason=("" if status != "partial"
                    else "주식은 수령했으나 USDC 지급 tx 가 확정되지 않았습니다 (대금 미지급)"),
        )

    # 3) 결제 검증 + 정산 + 주식 전달
    async def settle(
        self,
        submitted: PaymentSubmitted,
        requirements: PaymentRequirements,
        quantity: Decimal,
        live: bool,
        client=None,
    ) -> PaymentCompleted:
        tx = x.decode_payload(submitted.payment.serialized_transaction)
        sig = x.signature_str(tx)

        blocked = self._guard_settlement(submitted.order_id, sig)
        if blocked is not None:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.stock_mint or ""), delivered_amount=0,
                status="failed", reason=blocked,
            )

        ok, reason, amount = x.verify_payment(
            tx,
            expected_mint=self.usdc_mint,
            expected_dest_owner=self.pubkey,
            expected_amount=requirements.amount,
            expected_order_id=submitted.order_id,   # Memo 대사 키
        )
        if not ok:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.stock_mint or ""), delivered_amount=0,
                status="failed", reason=reason,
            )
        self.used_signatures.add(sig)   # 검증 통과한 결제 서명 기록 (리플레이 방어)

        # 결제 트랜잭션의 fee payer == 구매자
        buyer_pubkey = tx.message.account_keys[0]
        usdc_sig = sig
        confirmed = False
        delivery_sig = ""
        # 드라이런은 배송 성공으로 간주. 라이브에서만 실제 확정 여부로 갱신한다
        # (매도 레그 settle_sale 의 `paid` 와 같은 자리·같은 규칙 — BUG-01 대칭 수정).
        delivered = True

        if live and client is not None:
            usdc_sig, confirmed = await x.submit_and_confirm(client, tx)
            if confirmed and self.stock_mint is not None:
                # 주식토큰 전달 (broker → buyer)
                bh = await x.get_latest_blockhash(client)
                stock_qty = to_base_units(quantity, self.stock_decimals)
                deliver_tx = x.build_transfer_transaction(
                    payer=self.kp, mint=self.stock_mint, dest_owner=buyer_pubkey,
                    amount=stock_qty, decimals=self.stock_decimals, blockhash=bh,
                )
                delivery_sig, delivered = await x.submit_and_confirm(client, deliver_tx)
                if not delivered:
                    delivery_sig = ""
            else:
                delivered = False   # USDC 미확정이면 전달 시도 자체를 안 했다

        # 라이브: USDC 수령이 확정되고 주식 전달까지 확정돼야 settled.
        # 돈은 받았는데 주식을 못 보냈으면 partial(미배송) — 예전에는 confirmed 만 보고
        # settled 로 보고해, 구매자가 USDC 를 보냈는데 주식이 한 주도 안 온 거래가
        # 아카이브에 '성공'으로 남고 앱이 유령 포지션으로 계속 매매했다.
        # 매도 레그(settle_sale:190-197)에는 이미 있던 3분기를 매수 레그에 맞춘 것이다.
        if not live:
            status = "settled"
        elif confirmed and delivered:
            status = "settled"
        elif confirmed and not delivered:
            status = "partial"
        else:
            status = "failed"

        return PaymentCompleted(
            order_id=submitted.order_id,
            tx_signature=usdc_sig,
            confirmed=confirmed,
            delivered_asset=str(self.stock_mint or ""),
            # 보내지도 않은 수량을 실어 보내지 않는다 — 미배송이면 0
            delivered_amount=(to_base_units(quantity, self.stock_decimals)
                              if status == "settled" else 0),
            delivery_tx_signature=delivery_sig,
            status=status,
            reason=("" if status != "partial"
                    else "대금은 수령됐으나 주식 전달 tx 가 확정되지 않았습니다 (미배송)"),
        )
