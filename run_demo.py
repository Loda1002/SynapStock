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
from market.price_feed import MockPriceFeed, ReplayPriceFeed
from payments import x402_solana as x
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from payments.guard import Guard, GuardError
from agents.broker_agent import BrokerAgent
from agents.trading_agent import TradingAgent, Strategy


def _load_or_new(path: str, required: bool = False, env_json: str = "") -> Keypair:
    """지갑 키 로드. 우선순위: 환경변수 JSON > 파일 > (드라이런 한정) 즉석 생성.

    env_json 은 Cloud Run 용 우회로다 — Secret Manager 시크릿 2개를 같은 디렉터리에
    파일로 마운트하는 구성이 플랫폼 제약에 걸릴 수 있어, 환경변수 주입 경로를 함께 둔다.

    required=True(라이브 세션)에서는 키가 없으면 **즉시 실패**한다. 예전에는 조용히 새
    키페어를 만들어 계속 진행했는데, 그러면 마운트가 어긋나도 서버는 정상처럼 뜨고
    잔고 0인 낯선 지갑으로 결제가 실패한다 — 시연 당일 가장 추적하기 비싼 실패다.
    """
    if env_json:
        return Keypair.from_bytes(bytes(json.loads(env_json)))
    if os.path.exists(path):
        return x.load_keypair(path)
    if required:
        raise FileNotFoundError(
            f"지갑 키 파일이 없습니다: {path} — WALLET_DIR({CFG.wallet_dir}) 설정과 "
            f"Secret Manager 마운트를 확인하세요 (또는 *_KEYPAIR_JSON 환경변수로 주입)")
    return x.new_keypair()


def _load_or_create_user_key(path: str, env_json: str = "") -> Keypair:
    """사용자(위임자) 키 로드 — 없으면 생성 후 저장한다.

    trading/broker 키와 달리 사용자 키는 온체인 자금이 필요 없다(오프라인에서
    open mandate 에 ed25519 서명만 한다). 따라서 없으면 조용히 새로 만들어 저장해도
    안전하며, 이렇게 해야 '사용자가 위임한 한도'의 서명자가 에이전트(trading) 키와
    물리적으로 분리된다(differentiation.md 결함 G 제거 — 자기 허가서 자기 서명 방지)."""
    if env_json:
        return Keypair.from_bytes(bytes(json.loads(env_json)))
    if os.path.exists(path):
        return x.load_keypair(path)
    kp = x.new_keypair()
    x.save_keypair(kp, path)
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


async def main(live: bool, ticks: int, use_gemini: bool = True,
               replay: str = "", date_from: str = "", date_to: str = "") -> None:
    print(f"\n=== AutoTrader Agent 데모  (모드: {'LIVE ' + CFG.network if live else 'DRY-RUN'}) ===")

    # --- 지갑 (user=위임자 / trading=구매 에이전트 / broker=판매) ---
    wd = CFG.wallet_dir
    user_kp = _load_or_create_user_key(os.path.join(wd, "user.json"),
                                       env_json=CFG.user_keypair_json)
    trading_kp = _load_or_new(os.path.join(wd, "trading.json"))
    broker_kp = _load_or_new(os.path.join(wd, "broker.json"))
    print(f"User(위임자)  지갑 : {user_kp.pubkey()}")
    print(f"Trading(구매) 지갑 : {trading_kp.pubkey()}")
    print(f"Broker(판매)  지갑 : {broker_kp.pubkey()}")

    # --- 판단 두뇌: Gemini (키 있으면) / 규칙 기반 ---
    brain = None
    brain_label = "규칙 기반 (GEMINI_API_KEY 미설정)"
    if not use_gemini:
        brain_label = "규칙 기반 (--no-gemini)"
    elif CFG.gemini_api_key:
        try:
            from agents.gemini_decider import GeminiDecider
            brain = GeminiDecider(CFG.gemini_api_key, CFG.gemini_model, CFG.gemini_mode)
            brain_label = f"Gemini ({CFG.gemini_model}, {brain.mode} 모드, 실패 시 규칙 폴백)"
        except Exception as e:
            brain_label = f"규칙 기반 (Gemini 초기화 실패: {type(e).__name__})"
    print(f"판단 모듈   : {brain_label}")

    usdc_mint = Pubkey.from_string(CFG.usdc_mint)
    stock_mint = Pubkey.from_string(CFG.stock_mint) if CFG.stock_mint else None
    symbol = CFG.stock_symbol

    # --- AP2 Open Payment Mandate (사용자가 한도 설정, 서명) ---
    hr("AP2 · 사용자가 자율결제 한도를 설정하고 서명")
    open_mandate = OpenPaymentMandate(
        user_pubkey=str(user_kp.pubkey()),           # 위임자(사용자) 키 — 에이전트 키와 분리
        allowed_asset=str(usdc_mint),
        budget_total_usdc=CFG.budget_usdc,
        per_trade_max_usdc=CFG.per_trade_max_usdc,
        allowed_symbols=[symbol],
    ).sign(user_kp)                                  # 사용자가 한도에 서명(위임 근거)
    print(f"예산 총액   : {CFG.budget_usdc} USDC")
    print(f"건별 한도   : {CFG.per_trade_max_usdc} USDC")
    print(f"허용 종목   : {symbol}")
    print(f"mandate 서명 검증: {open_mandate.verify()}")

    authorizer = PaymentAuthorizer(open_mandate, agent_kp=trading_kp)

    # --- 에이전트 구성 ---
    strategy = Strategy(
        buy_dip_pct=Decimal("2"), take_profit_pct=Decimal("3"),
        spend_per_trade_usdc=Decimal("30"),
    )
    trading = TradingAgent(trading_kp, authorizer, strategy, CFG.usdc_decimals, CFG.network,
                           brain=brain, fee_bps=CFG.broker_fee_bps)
    broker = BrokerAgent(
        broker_kp, usdc_mint, CFG.usdc_decimals, stock_mint, CFG.stock_decimals, CFG.network,
        fee_bps=CFG.broker_fee_bps,
    )
    # 402 Guard — 신뢰 수취인은 협의를 마친 브로커뿐. 구매 에이전트가 서명 직전 통과한다.
    trading.guard = Guard(open_mandate, [str(broker_kp.pubkey())], CFG.usdc_decimals)
    print(f"브로커 수수료: {CFG.broker_fee_bps} bps ({Decimal(CFG.broker_fee_bps) / 100}%) — 매수 가산·매도 차감")
    if replay:
        rpath = os.path.join("data", "market", f"{replay.upper()}_daily.csv")
        feed = ReplayPriceFeed(rpath, start=date_from, end=date_to, warmup=CFG.replay_warmup)
        trading.preload_bars(feed.warmup_bars)   # MA/TA 워밍업 주입 — 첫 틱부터 지표 성립
        print(f"시세 피드   : {feed.source_label} (실데이터 재생 — MA5/지표 규칙)")
    else:
        feed = MockPriceFeed()
        print("시세 피드   : 목 시세 (10스텝 데모 패턴 — 매수·매도 1사이클엔 최소 9틱 필요)")

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

    # --- 시세 루프 (mock=고정 틱 수 / replay=데이터 소진까지) ---
    def _ticks():
        if replay:
            while not feed.exhausted:
                p = feed.get_price(symbol)
                yield p, feed.last_bar          # 실 OHLC 봉을 판단에 전달 (TA·캔들)
        else:
            for _ in range(ticks):
                yield feed.get_price(symbol), None
    try:
        for t, (price, bar) in enumerate(_ticks()):
            decision = trading.decide(symbol, price, bar=bar)
            hr(f"틱 {t+1}  |  {symbol} = {price} USDC  →  판단: {decision.action.upper()}  [{decision.source}]")
            print(f"  이유: {decision.reason}")

            # --- 매도 사이클: 주식 전송 → 브로커 검증 → USDC 지급 ---
            if decision.action == "sell" and trading.position.quantity > 0:
                qty = trading.position.quantity
                quote = broker.sell_quote(symbol, qty, price)
                print(f"  [A2A] Trading→Broker: '{symbol} {qty} 주 되사줘'")
                print(f"        Broker 제안: {quote.quantity} {symbol} @ {quote.price_usdc}"
                      f" = {quote.subtotal_usdc} − 수수료 {quote.fee_usdc} = 수령 {quote.total_usdc} USDC")

                required = broker.make_stock_required(quote)
                print(f"  [x402 #1 payment-required(매도)] order={required.order_id} "
                      f"주식 {required.requirements.amount}(base) → {required.requirements.pay_to[:8]}…")

                blockhash = await x.get_latest_blockhash(client) if live else Hash.default()
                # 402 Guard(매도 청구서 검증) — 자산·수취인·수량을 독립 기준과 대조(매수 대칭).
                try:
                    submitted = trading.build_stock_transfer(
                        required, blockhash,
                        expected_stock_mint=stock_mint,
                        expected_quantity=qty,
                        stock_decimals=CFG.stock_decimals)
                except GuardError as e:
                    print(f"  [402 Guard 차단] {e} — 매도 서명 거부(유출 0)")
                    continue
                print(f"  [x402 #2 payment-submitted] 주식 전송 서명 제출 "
                      f"(len={len(submitted.payment.serialized_transaction)} b64)")

                completed = await broker.settle_sale(
                    submitted, required.requirements, quote.total_usdc, live=live, client=client,
                )
                status = "온체인 확정" if completed.confirmed else ("검증통과·미브로드캐스트" if not live else "실패")
                print(f"  [x402 #3 payment-completed] status={completed.status} ({status})")
                print(f"        주식 전송 tx: {completed.tx_signature}")
                if completed.delivery_tx_signature:
                    print(f"        USDC 지급 tx: {completed.delivery_tx_signature}")

                receipt = trading.on_sale_completed(completed, symbol, qty, price, quote.total_usdc)
                print(f"  [영수증] {receipt.side} {receipt.quantity} {receipt.symbol} "
                      f"/ {receipt.total_usdc} USDC / 확정={receipt.confirmed}"
                      + (f" / {receipt.note}" if receipt.note else ""))
                print(f"  [AP2] 매도 대금 환입 → 잔여 예산 {authorizer.remaining_usdc} USDC")

                trades.append({
                    "order_id": completed.order_id,
                    "side": "sell",
                    "decision_source": decision.source,
                    "decision_reason": decision.reason,
                    "symbol": symbol,
                    "quantity": str(qty),
                    "price_usdc": str(quote.price_usdc),
                    "subtotal_usdc": str(quote.subtotal_usdc),
                    "fee_usdc": str(quote.fee_usdc),
                    "total_usdc": str(quote.total_usdc),
                    "status": completed.status,
                    "confirmed": completed.confirmed,
                    "payment_tx": completed.tx_signature,
                    "delivery_tx": completed.delivery_tx_signature,
                    "explorer_payment": explorer_tx_url(completed.tx_signature) if completed.confirmed else "",
                    "explorer_delivery": explorer_tx_url(completed.delivery_tx_signature) if completed.delivery_tx_signature else "",
                })
                continue

            if decision.action != "buy":
                continue

            # (A2A #0) 견적 요청 → Broker 견적
            quote = broker.quote(symbol, decision.spend_usdc, price)
            print(f"  [A2A] Trading→Broker: '{symbol} 을 {decision.spend_usdc} USDC 어치 견적 줘'")
            print(f"        Broker 견적: {quote.quantity} {symbol} @ {quote.price_usdc}"
                  f" = {quote.subtotal_usdc} + 수수료 {quote.fee_usdc} = 총 {quote.total_usdc} USDC")

            # (A2A #1) payment-required
            required = broker.make_payment_required(quote)
            print(f"  [x402 #1 payment-required] order={required.order_id} "
                  f"amount={required.requirements.amount}(base) payTo={required.requirements.pay_to[:8]}…")

            # (A2A #2) 402 Guard 청구서 검증 + AP2 한도 승인 + 결제 서명 → payment-submitted
            try:
                blockhash = await x.get_latest_blockhash(client) if live else Hash.default()
                submitted = trading.build_payment(
                    required, blockhash, quote, max_spend_usdc=decision.spend_usdc)
            except GuardError as e:
                print(f"  [402 Guard 차단] {e} — 서명 거부(유출 0)")
                continue
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

            # 평단은 수수료 포함 실효 단가로 반영 (웹 엔진과 동일)
            eff_price = ((quote.total_usdc / quote.quantity).quantize(Decimal("0.01"))
                         if quote.quantity > 0 else price)
            receipt = trading.on_completed(
                completed, symbol, quote.quantity, eff_price, quote.total_usdc,
            )
            print(f"  [영수증] {receipt.side} {receipt.quantity} {receipt.symbol} "
                  f"/ {receipt.total_usdc} USDC / 확정={receipt.confirmed}"
                  + (f" / {receipt.note}" if receipt.note else ""))

            trades.append({
                "order_id": completed.order_id,
                "side": "buy",
                "decision_source": decision.source,
                "decision_reason": decision.reason,
                "symbol": symbol,
                "quantity": str(quote.quantity),
                "price_usdc": str(quote.price_usdc),
                "subtotal_usdc": str(quote.subtotal_usdc),
                "fee_usdc": str(quote.fee_usdc),
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
        buys = [t for t in confirmed_trades if t["side"] == "buy"]
        sells = [t for t in confirmed_trades if t["side"] == "sell"]
        net_spent = (sum((Decimal(t["total_usdc"]) for t in buys), Decimal(0))
                     - sum((Decimal(t["total_usdc"]) for t in sells), Decimal(0)))
        net_qty = (sum((Decimal(t["quantity"]) for t in buys), Decimal(0))
                   - sum((Decimal(t["quantity"]) for t in sells), Decimal(0)))
        usdc_out = Decimal(snap_before["trading"]["usdc"]) - Decimal(snap_after["trading"]["usdc"])
        stock_in = Decimal(snap_after["trading"]["stock"]) - Decimal(snap_before["trading"]["stock"])
        ok_usdc = usdc_out == net_spent
        ok_stock = stock_in == net_qty
        print(f"  구매자 USDC 순지출(온체인) {usdc_out} == 체결 순합계 {net_spent} "
              f"(매수 {len(buys)}건 − 매도 {len(sells)}건) : {'PASS' if ok_usdc else 'FAIL'}")
        print(f"  구매자 주식 순증감(온체인) {stock_in} == 체결 순수량 {net_qty} : {'PASS' if ok_stock else 'FAIL'}")

        os.makedirs(os.path.join("artifacts", "tx"), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        archive_path = os.path.join("artifacts", "tx", f"{ts}_{CFG.network}_live_buy.json")
        archive = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "network": CFG.network,
            "rpc_url": CFG.rpc_url,
            "wallets": {"user": str(user_kp.pubkey()), "trading": str(trading_kp.pubkey()), "broker": str(broker_kp.pubkey())},
            "mints": {"usdc": str(usdc_mint), "stock": str(stock_mint), "stock_symbol": symbol},
            "mandate": {
                "user_pubkey": open_mandate.user_pubkey,   # 위임자(서명자) — 에이전트와 분리 증빙
                "budget_total_usdc": str(CFG.budget_usdc),
                "per_trade_max_usdc": str(CFG.per_trade_max_usdc),
                "signature": open_mandate.signature,
            },
            "broker_fee": {
                "fee_bps": CFG.broker_fee_bps,
                "total_fees_usdc": str(sum(
                    (Decimal(t["fee_usdc"]) for t in trades if t["confirmed"]), Decimal(0))),
            },
            "balances_before": snap_before,
            "balances_after": snap_after,
            "trades": trades,
            "cross_check": {
                "usdc_net_out_onchain": str(usdc_out), "usdc_net_out_expected": str(net_spent), "usdc_ok": ok_usdc,
                "stock_net_in_onchain": str(stock_in), "stock_net_in_expected": str(net_qty), "stock_ok": ok_stock,
            },
        }
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
        print(f"\n[아카이브] {archive_path}")

    if not live:
        print("\n※ 드라이런: 실제 온체인 전송은 없었습니다. devnet 라이브 실행은 README 참고.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="클러스터에 실제 브로드캐스트")
    ap.add_argument("--ticks", type=int, default=4, help="시세 틱 수")
    ap.add_argument("--no-gemini", action="store_true", help="Gemini 없이 규칙 기반으로만 판단")
    ap.add_argument("--replay", default="", metavar="SYMBOL",
                    help="실데이터 재생 (예: AAPL) — data/market/{SYMBOL}_daily.csv, MA5/지표 규칙 매매")
    ap.add_argument("--from", dest="date_from", default="", help="재생 시작일 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default="", help="재생 종료일 YYYY-MM-DD")
    args = ap.parse_args()
    asyncio.run(main(args.live, args.ticks, use_gemini=not args.no_gemini,
                     replay=args.replay, date_from=args.date_from, date_to=args.date_to))
