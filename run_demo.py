"""엔드투엔드 데모: 두 에이전트가 협의 → devnet USDC 로 주식토큰 매수.

사용법:
  python run_demo.py            # 드라이런 (네트워크 불필요, 트랜잭션 생성·서명·검증까지)
  python run_demo.py --live     # devnet 브로드캐스트 (사전에 scripts/setup_devnet.py 실행 필요)
  python run_demo.py --ticks 5  # 시세 틱 수

드라이런은 실제 서명 트랜잭션을 만들고 x402 검증까지 수행하지만 브로드캐스트만 생략한다.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from datetime import datetime
from decimal import Decimal

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash

from config import CFG, from_base_units
from market.price_feed import MockPriceFeed
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy


def _load_or_new(path: str) -> Keypair:
    if os.path.exists(path):
        return x.load_keypair(path)
    kp = x.new_keypair()
    return kp


def hr(title: str) -> None:
    print("\n" + "─" * 62)
    print(f"  {title}")
    print("─" * 62)


def explorer_tx_url(sig: str) -> str:
    if "devnet" in CFG.network:
        return f"https://explorer.solana.com/tx/{sig}?cluster=devnet"
    # localnet: 검증기 실행 중일 때 브라우저에서 조회 가능
    return (f"https://explorer.solana.com/tx/{sig}"
            f"?cluster=custom&customUrl=http%3A%2F%2F127.0.0.1%3A8899")


async def snapshot_balances(client, trading_pk, broker_pk, usdc_mint, stock_mint) -> dict:
    """양 지갑의 SOL·USDC·주식토큰 온체인 잔액 (검증 루틴 2단계: 교차 확인용)."""
    snap: dict = {}
    for name, pk in (("trading", trading_pk), ("broker", broker_pk)):
        snap[name] = {
            "sol": await x.get_sol_balance(client, pk),
            "usdc": await x.get_token_balance_ui(client, pk, usdc_mint),
            "stock": await x.get_token_balance_ui(client, pk, stock_mint),
        }
    return snap


def print_snapshot(snap: dict, symbol: str) -> None:
    for name, b in snap.items():
        print(f"  {name:7s}: {b['sol']:.4f} SOL / {b['usdc']} USDC / {b['stock']} {symbol}")


async def main(live: bool, ticks: int) -> None:
    print(f"\n=== AutoTrader Agent 데모  (모드: {'LIVE ' + CFG.network if live else 'DRY-RUN'}) ===")

    # --- 지갑 ---
    wd = CFG.wallet_dir
    trading_kp = _load_or_new(os.path.join(wd, "trading.json"))
    broker_kp = _load_or_new(os.path.join(wd, "broker.json"))
    print(f"Trading(구매) 지갑 : {trading_kp.pubkey()}")
    print(f"Broker(판매)  지갑 : {broker_kp.pubkey()}")

    usdc_mint = Pubkey.from_string(CFG.usdc_mint)
    stock_mint = Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint else None
    symbol = CFG.stock_symbol

    # --- AP2 Open Payment Mandate (사용자가 한도 설정, 서명) ---
    hr("AP2 · 사용자가 자율결제 한도를 설정하고 서명")
    open_mandate = OpenPaymentMandate(
        user_pubkey=str(trading_kp.pubkey()),        # 데모: 사용자=구매 에이전트 소유자
        allowed_asset=str(usdc_mint),
        budget_total_usdc=CFG.budget_usdc,
        per_trade_max_usdc=CFG.per_trade_max_usdc,
        allowed_symbols=[symbol],
    ).sign(trading_kp)
    print(f"예산 총액   : {CFG.budget_usdc} USDC")
    print(f"건별 한도   : {CFG.per_trade_max_usdc} USDC")
    print(f"허용 종목   : {symbol}")
    print(f"mandate 서명 검증: {open_mandate.verify()}")

    authorizer = PaymentAuthorizer(open_mandate, agent_kp=trading_kp)

    # --- 에이전트 구성 ---
    strategy = Strategy(
        buy_below=Decimal("178"), sell_above=Decimal("185"),
        spend_per_trade_usdc=Decimal("30"),
    )
    trading = TradingAgent(trading_kp, authorizer, strategy, CFG.usdc_decimals, CFG.network)
    broker = BrokerAgent(
        broker_kp, usdc_mint, CFG.usdc_decimals, stock_mint, CFG.stock_decimals, CFG.network,
    )
    feed = MockPriceFeed()

    # --- 라이브면 클라이언트 준비 + 실행 전 잔액 스냅샷 ---
    client = None
    snap_before = None
    snap_after = None
    trades: list = []
    if live:
        if stock_mint is None:
            print("\n[중단] STOCK_MINT 미설정 — 먼저 scripts/setup_devnet.py 를 실행하세요.")
            return
        client = await x.get_client(CFG.rpc_url)
        snap_before = await snapshot_balances(
            client, trading_kp.pubkey(), broker_kp.pubkey(), usdc_mint, stock_mint)
        hr("온체인 잔액 (실행 전)")
        print_snapshot(snap_before, symbol)

    # --- 시세 루프 ---
    try:
        for t in range(ticks):
            price = feed.get_price(symbol)
            decision = trading.decide(symbol, price)
            hr(f"틱 {t+1}  |  {symbol} = {price} USDC  →  판단: {decision.action.upper()}")
            print(f"  이유: {decision.reason}")

            if decision.action != "buy":
                continue

            # (A2A #0) 견적 요청 → Broker 견적
            quote = broker.quote(symbol, decision.spend_usdc, price)
            print(f"  [A2A] Trading→Broker: '{symbol} 을 {decision.spend_usdc} USDC 어치 견적 줘'")
            print(f"        Broker 견적: {quote.quantity} {symbol} @ {quote.price_usdc} = {quote.total_usdc} USDC")

            # (A2A #1) payment-required
            required = broker.make_payment_required(quote)
            print(f"  [x402 #1 payment-required] order={required.order_id} "
                  f"amount={required.requirements.amount}(base) payTo={required.requirements.pay_to[:8]}…")

            # (A2A #2) 한도 승인 + 결제 서명 → payment-submitted
            try:
                blockhash = await x.get_latest_blockhash(client) if live else Hash.default()
                submitted = trading.build_payment(required, blockhash)
            except MandateError as e:
                print(f"  [AP2 거부] {e} — 결제 중단")
                continue
            print(f"  [AP2 승인] 잔여 예산 {authorizer.remaining_usdc} USDC")
            print(f"  [x402 #2 payment-submitted] 서명 트랜잭션 제출 "
                  f"(len={len(submitted.payment.serialized_transaction)} b64)")

            # (A2A #3) 검증 + 정산 → payment-completed
            completed = await broker.settle(
                submitted, required.requirements, quote.quantity, live=live, client=client,
            )
            status = "온체인 확정" if completed.confirmed else ("검증통과·미브로드캐스트" if not live else "실패")
            print(f"  [x402 #3 payment-completed] status={completed.status} ({status})")
            print(f"        결제 tx: {completed.tx_signature}")
            if completed.delivery_tx_signature:
                print(f"        주식 전달 tx: {completed.delivery_tx_signature}")

            receipt = trading.on_completed(
                completed, symbol, quote.quantity, price, quote.total_usdc,
            )
            print(f"  [영수증] {receipt.side} {receipt.quantity} {receipt.symbol} "
                  f"/ {receipt.total_usdc} USDC / 확정={receipt.confirmed}"
                  + (f" / {receipt.note}" if receipt.note else ""))

            trades.append({
                "order_id": completed.order_id,
                "side": "buy",
                "symbol": symbol,
                "quantity": str(quote.quantity),
                "price_usdc": str(quote.price_usdc),
                "total_usdc": str(quote.total_usdc),
                "status": completed.status,
                "confirmed": completed.confirmed,
                "payment_tx": completed.tx_signature,
                "delivery_tx": completed.delivery_tx_signature,
                "explorer_payment": explorer_tx_url(completed.tx_signature) if completed.confirmed else "",
                "explorer_delivery": explorer_tx_url(completed.delivery_tx_signature) if completed.delivery_tx_signature else "",
            })

        # 실행 후 잔액 스냅샷 (클라이언트 닫히기 전에)
        if live and client is not None:
            snap_after = await snapshot_balances(
                client, trading_kp.pubkey(), broker_kp.pubkey(), usdc_mint, stock_mint)
    finally:
        if client is not None:
            await client.close()

    # --- 요약 ---
    hr("결과 요약")
    print(f"보유 포지션 : {trading.position.quantity} {symbol} "
          f"(평단 {trading.position.avg_price_usdc} USDC)")
    print(f"사용 예산   : {authorizer.spent_usdc} / {CFG.budget_usdc} USDC")

    # --- 라이브: 잔액 교차 검증 + 증빙 아카이브 (검증 루틴 2·4단계) ---
    if live and snap_before is not None and snap_after is not None:
        hr("온체인 잔액 (실행 후) · 교차 검증")
        print_snapshot(snap_after, symbol)
        confirmed_trades = [t for t in trades if t["confirmed"]]
        spent = sum((Decimal(t["total_usdc"]) for t in confirmed_trades), Decimal(0))
        qty = sum((Decimal(t["quantity"]) for t in confirmed_trades), Decimal(0))
        usdc_out = Decimal(snap_before["trading"]["usdc"]) - Decimal(snap_after["trading"]["usdc"])
        stock_in = Decimal(snap_after["trading"]["stock"]) - Decimal(snap_before["trading"]["stock"])
        ok_usdc = usdc_out == spent
        ok_stock = stock_in == qty
        print(f"  구매자 USDC 지출(온체인) {usdc_out} == 체결 합계 {spent} : {'PASS' if ok_usdc else 'FAIL'}")
        print(f"  구매자 주식 수령(온체인) {stock_in} == 체결 수량 {qty} : {'PASS' if ok_stock else 'FAIL'}")

        os.makedirs(os.path.join("artifacts", "tx"), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        archive_path = os.path.join("artifacts", "tx", f"{ts}_{CFG.network}_live_buy.json")
        archive = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "network": CFG.network,
            "rpc_url": CFG.rpc_url,
            "wallets": {"trading": str(trading_kp.pubkey()), "broker": str(broker_kp.pubkey())},
            "mints": {"usdc": str(usdc_mint), "stock": str(stock_mint), "stock_symbol": symbol},
            "mandate": {
                "budget_total_usdc": str(CFG.budget_usdc),
                "per_trade_max_usdc": str(CFG.per_trade_max_usdc),
                "signature": open_mandate.signature,
            },
            "balances_before": snap_before,
            "balances_after": snap_after,
            "trades": trades,
            "cross_check": {
                "usdc_spent_onchain": str(usdc_out), "usdc_spent_expected": str(spent), "usdc_ok": ok_usdc,
                "stock_received_onchain": str(stock_in), "stock_expected": str(qty), "stock_ok": ok_stock,
            },
        }
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
        print(f"\n[아카이브] {archive_path}")

    if not live:
        print("\n※ 드라이런: 실제 온체인 전송은 없었습니다. devnet 라이브 실행은 README 참고.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="devnet 에 실제 브로드캐스트")
    ap.add_argument("--ticks", type=int, default=4, help="시세 틱 수")
    args = ap.parse_args()
    asyncio.run(main(args.live, args.ticks))
