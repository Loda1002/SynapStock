"""Broker (판매) 에이전트.

책임: 토큰화 주식 시세 견적 → payment-required 발행 → 제출된 결제 검증
→ (라이브면) 온체인 정산 및 주식토큰 전달 → payment-completed.

지금은 규칙 기반. 이후 Gemini(ADK) 로 동적 가격/재고 판단을 붙일 수 있다.
"""
from __future__ import annotations
import uuid
from decimal import Decimal
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
    ):
        self.kp = keypair
        self.usdc_mint = usdc_mint
        self.usdc_decimals = usdc_decimals
        self.stock_mint = stock_mint
        self.stock_decimals = stock_decimals
        self.network = network

    @property
    def pubkey(self) -> Pubkey:
        return self.kp.pubkey()

    # 1) 견적 (매수: 예산 → 수량)
    def quote(self, symbol: str, spend_usdc: Decimal, price_usdc: Decimal) -> Quote:
        quantity = (spend_usdc / price_usdc).quantize(Decimal("0.0001"))
        total = (quantity * price_usdc).quantize(Decimal("0.01"))
        return Quote(symbol=symbol, price_usdc=price_usdc, quantity=quantity, total_usdc=total)

    # 1') 매도 견적 (수량 → 대금) — 브로커가 주식을 되사줌
    def sell_quote(self, symbol: str, quantity: Decimal, price_usdc: Decimal) -> Quote:
        total = (quantity * price_usdc).quantize(Decimal("0.01"))
        return Quote(symbol=symbol, price_usdc=price_usdc, quantity=quantity, total_usdc=total)

    # 2) payment-required 발행
    def make_payment_required(self, quote: Quote) -> PaymentRequired:
        order_id = f"ord_{uuid.uuid4().hex[:10]}"
        amount = to_base_units(quote.total_usdc, self.usdc_decimals)
        reqs = PaymentRequirements(
            scheme="exact",
            network=self.network,
            asset=str(self.usdc_mint),
            amount=amount,
            pay_to=str(self.pubkey),
            resource=f"STOCK:{quote.symbol} x{quote.quantity}",
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
        amount = to_base_units(quote.quantity, self.stock_decimals)
        reqs = PaymentRequirements(
            scheme="exact",
            network=self.network,
            asset=str(self.stock_mint),          # 매도는 '주식'이 지불 자산
            amount=amount,
            pay_to=str(self.pubkey),
            resource=f"USDC-BUYBACK:{quote.symbol} x{quote.quantity} @ {quote.price_usdc}",
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

        ok, reason, amount = x.verify_payment(
            tx,
            expected_mint=Pubkey.from_string(requirements.asset),  # 주식 민트
            expected_dest_owner=self.pubkey,
            min_amount=requirements.amount,
        )
        if not ok:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.usdc_mint), delivered_amount=0,
                status="failed",
            )

        seller_pubkey = tx.message.account_keys[0]
        stock_sig = x.signature_str(tx)
        payout_sig = ""
        confirmed = False
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

        return PaymentCompleted(
            order_id=submitted.order_id,
            tx_signature=stock_sig,               # 판매자가 보낸 주식 전송 tx
            confirmed=confirmed,
            delivered_asset=str(self.usdc_mint),  # 브로커가 지급한 자산
            delivered_amount=payout_amount,
            delivery_tx_signature=payout_sig,     # USDC 지급 tx
            status="settled" if (not live or confirmed) else "failed",
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

        ok, reason, amount = x.verify_payment(
            tx,
            expected_mint=self.usdc_mint,
            expected_dest_owner=self.pubkey,
            min_amount=requirements.amount,
        )
        if not ok:
            return PaymentCompleted(
                order_id=submitted.order_id, tx_signature="", confirmed=False,
                delivered_asset=str(self.stock_mint or ""), delivered_amount=0,
                status="failed",
            )

        # 결제 트랜잭션의 fee payer == 구매자
        buyer_pubkey = tx.message.account_keys[0]
        usdc_sig = x.signature_str(tx)
        confirmed = False
        delivery_sig = ""

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

        return PaymentCompleted(
            order_id=submitted.order_id,
            tx_signature=usdc_sig,
            confirmed=confirmed,
            delivered_asset=str(self.stock_mint or ""),
            delivered_amount=to_base_units(quantity, self.stock_decimals),
            delivery_tx_signature=delivery_sig,
            # 라이브인데 온체인 확정 실패면 settled 로 치지 않는다 (포지션 미반영)
            status="settled" if (not live or confirmed) else "failed",
        )
