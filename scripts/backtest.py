"""백테스트 러너 — 같은 실데이터 구간에서 규칙 vs Gemini(엄격/추세) 성과 비교.

드라이 시뮬레이션(온체인 미전송): 견적·AP2 한도 검사는 실전과 동일 코드
(BrokerAgent.quote / PaymentAuthorizer)를 그대로 지나고, x402 서명·전송만 생략한다.
소개서에 넣을 "규칙 vs AI(엄격) vs AI(추세)" 비교 숫자를 만드는 도구.

사용 예 (프로젝트 루트):
  python scripts/backtest.py --brain rule
  python scripts/backtest.py --brain gemini --mode strict --from 2025-02-03 --to 2025-04-30
  python scripts/backtest.py --brain gemini --mode trend --max-bars 60

무료 티어 보호: Gemini 호출 간 --pace 초(기본 4s) 간격 + 429 쿨다운 대기,
재생 봉 수는 --max-bars(기본 60)로 제한. rule-fallback 발생 수를 함께 기록한다
(0이 아니면 그 구간은 규칙 판단이 섞였다는 뜻 — 비교 순수성 주의).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402

from config import CFG  # noqa: E402
from market.price_feed import ReplayPriceFeed  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from agents.broker_agent import BrokerAgent  # noqa: E402
from payments.ap2_mandate import (  # noqa: E402
    OpenPaymentMandate, PaymentAuthorizer, MandateError,
)

CENT = Decimal("0.01")


def build_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="리플레이 백테스트 (드라이, 온체인 미전송)")
    ap.add_argument("--symbol", default="AAPL", help="data/market/{SYMBOL}_daily.csv (기본 AAPL)")
    ap.add_argument("--file", default="", help="CSV 경로 직접 지정 (symbol 보다 우선)")
    ap.add_argument("--from", dest="date_from", default="", help="재생 시작일 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default="", help="재생 종료일 YYYY-MM-DD")
    ap.add_argument("--brain", choices=["rule", "gemini"], default="rule")
    ap.add_argument("--mode", choices=["strict", "trend"], default="strict",
                    help="gemini 재량 모드 (rule 이면 무시)")
    ap.add_argument("--max-bars", type=int, default=60, help="최대 재생 봉 수 (기본 60)")
    ap.add_argument("--warmup", type=int, default=20, help="지표 워밍업 봉 수 (기본 20)")
    ap.add_argument("--budget", default="100", help="AP2 총예산 USDC")
    ap.add_argument("--per-trade", default="50", help="AP2 건별 한도 USDC")
    ap.add_argument("--spend", default="30", help="1회 매수 금액 USDC")
    ap.add_argument("--dip", default="2", help="매수: MA5 대비 -%%")
    ap.add_argument("--profit", default="3", help="매도: 평단 대비 +%%")
    ap.add_argument("--pace", type=float, default=4.0, help="gemini 호출 간 최소 간격(초)")
    ap.add_argument("--quiet", action="store_true", help="봉별 로그 생략(요약만)")
    return ap.parse_args()


def main() -> int:
    args = build_args()
    csv_path = args.file or os.path.join(ROOT, "data", "market", f"{args.symbol.upper()}_daily.csv")
    feed = ReplayPriceFeed(csv_path, start=args.date_from, end=args.date_to, warmup=args.warmup)

    symbol = CFG.stock_symbol
    strategy = Strategy(
        buy_dip_pct=Decimal(args.dip), take_profit_pct=Decimal(args.profit),
        spend_per_trade_usdc=Decimal(args.spend), decision_mode=args.mode,
    )
    kp = Keypair()
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=Decimal(args.budget), per_trade_max_usdc=Decimal(args.per_trade),
        allowed_symbols=[symbol],
    ).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)

    brain = None
    brain_desc = "rule"
    if args.brain == "gemini":
        if not CFG.gemini_api_key:
            print("GEMINI_API_KEY 미설정 — gemini 백테스트 불가 (.env 확인)")
            return 1
        from agents.gemini_decider import GeminiDecider
        brain = GeminiDecider(CFG.gemini_api_key, CFG.gemini_model, CFG.gemini_mode)
        brain_desc = f"gemini/{args.mode} ({CFG.gemini_model})"

    trading = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "backtest",
                           brain=brain, fee_bps=CFG.broker_fee_bps)
    if feed.warmup_bars:
        trading.preload_history([b.close for b in feed.warmup_bars])
    from solders.pubkey import Pubkey
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "backtest", fee_bps=CFG.broker_fee_bps)

    fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
    budget = Decimal(args.budget)
    realized = Decimal(0)
    fees = Decimal(0)
    cum_buy = Decimal(0)
    rejects = 0
    fallbacks = 0
    wins = 0
    trades: list[dict] = []
    decisions: list[dict] = []
    by_action: dict[str, int] = {}
    by_source: dict[str, int] = {}
    peak = budget
    mdd = Decimal(0)
    last_price = Decimal(0)
    last_call = 0.0
    played = 0

    print(f"백테스트 시작 — {feed.source_label} / 두뇌 {brain_desc} / "
          f"규칙: MA5 −{args.dip}% 매수 · 평단 +{args.profit}% 익절 / "
          f"예산 {budget} USDC (최대 {args.max_bars}봉)")

    while not feed.exhausted and played < args.max_bars:
        price = feed.get_price(symbol)
        bar = feed.last_bar
        last_price = price
        played += 1

        if brain is not None:
            # 무료 티어 보호 — 호출 간격 유지 + 429 쿨다운이 걸려 있으면 기다렸다 재개
            wait = max(0.0, args.pace - (time.time() - last_call))
            cd = max(0.0, getattr(brain, "_cooldown_until", 0.0) - time.time())
            if cd > 0:
                print(f"  … 무료 티어 쿨다운 {cd:.0f}s 대기 (봉 {bar.date})")
            time.sleep(wait + cd)
            last_call = time.time()

        d = trading.decide(symbol, price)
        by_action[d.action] = by_action.get(d.action, 0) + 1
        by_source[d.source] = by_source.get(d.source, 0) + 1
        if d.source == "rule-fallback":
            fallbacks += 1
        decisions.append({"date": bar.date, "price": str(price),
                          "action": d.action, "source": d.source, "reason": d.reason})

        if d.action == "buy":
            q = broker.quote(symbol, d.spend_usdc, price)
            try:
                auth.authorize(order_id=f"bt_{played}", symbol=symbol,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError as e:
                rejects += 1
                decisions[-1]["ap2_rejected"] = str(e)
            else:
                eff = (q.total_usdc / q.quantity).quantize(CENT) if q.quantity else price
                trading.position.apply_buy(q.quantity, eff)
                cum_buy += q.total_usdc
                fees += q.fee_usdc
                trades.append({"date": bar.date, "side": "buy", "qty": str(q.quantity),
                               "price": str(price), "total": str(q.total_usdc)})
        elif d.action == "sell" and trading.position.quantity > 0:
            qty = trading.position.quantity
            avg = trading.position.avg_price_usdc
            q = broker.sell_quote(symbol, qty, price)
            pnl = (q.total_usdc - avg * qty).quantize(CENT)
            realized += pnl
            fees += q.fee_usdc
            trading.position.apply_sell(qty)
            auth.credit_sale(q.total_usdc)
            if pnl > 0:
                wins += 1
            trades.append({"date": bar.date, "side": "sell", "qty": str(qty),
                           "price": str(price), "total": str(q.total_usdc),
                           "realized": str(pnl)})

        # 자산가치 곡선 (가용 예산 + 보유 평가액, 수수료 차감) → 최대낙폭(MDD)
        equity = auth.remaining_usdc + (trading.position.quantity * price * (1 - fee_rate))
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)
        if not args.quiet:
            mark = {"buy": "🟢", "sell": "🔴"}.get(d.action, "·")
            print(f"  {bar.date} {price:>8} {mark} {d.action:<4} [{d.source}] {d.reason[:72]}")

    pos = trading.position
    unrealized = ((pos.quantity * last_price * (1 - fee_rate))
                  - pos.avg_price_usdc * pos.quantity).quantize(CENT)
    total_pnl = (realized + unrealized).quantize(CENT)
    sells = [t for t in trades if t["side"] == "sell"]
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "source": feed.source_label, "file": csv_path,
            "from": args.date_from, "to": args.date_to, "bars_played": played,
            "brain": args.brain, "mode": args.mode if args.brain == "gemini" else "-",
            "rules": {"buy_dip_pct": args.dip, "take_profit_pct": args.profit,
                      "spend_per_trade": args.spend},
            "budget_usdc": args.budget, "per_trade_max_usdc": args.per_trade,
            "fee_bps": CFG.broker_fee_bps,
        },
        "metrics": {
            "realized_pnl_usdc": str(realized.quantize(CENT)),
            "unrealized_pnl_usdc": str(unrealized),
            "total_pnl_usdc": str(total_pnl),
            "return_on_budget_pct": str((total_pnl / budget * 100).quantize(CENT)),
            "buy_count": len(trades) - len(sells), "sell_count": len(sells),
            "win_rate_pct": str(Decimal(wins) / len(sells) * 100 if sells else Decimal(0))[:6],
            "max_drawdown_pct": str(mdd.quantize(CENT)),
            "cum_buy_usdc": str(cum_buy), "fees_usdc": str(fees),
            "ap2_rejects": rejects, "gemini_fallbacks": fallbacks,
            "decisions_by_action": by_action, "decisions_by_source": by_source,
            "position_left_qty": str(pos.quantity),
        },
        "trades": trades,
        "decisions": decisions,
    }

    m = result["metrics"]
    print(f"\n===== 결과 ({brain_desc}) =====")
    print(f"  총손익      : {m['total_pnl_usdc']} USDC "
          f"(실현 {m['realized_pnl_usdc']} + 평가 {m['unrealized_pnl_usdc']})")
    print(f"  예산 수익률 : {m['return_on_budget_pct']}%  ·  최대낙폭(MDD) {m['max_drawdown_pct']}%")
    print(f"  매매        : 매수 {m['buy_count']} / 매도 {m['sell_count']} "
          f"(승률 {m['win_rate_pct']}%) · 수수료 {m['fees_usdc']} USDC")
    print(f"  AP2 거부    : {m['ap2_rejects']}건 · Gemini 폴백 {m['gemini_fallbacks']}건")
    print(f"  판단 분포   : {m['decisions_by_action']} / 출처 {m['decisions_by_source']}")

    out_dir = os.path.join(ROOT, "artifacts", "backtests")
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{args.symbol.upper()}_{args.brain}" + (f"-{args.mode}" if args.brain == "gemini" else "")
    path = os.path.join(out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"  저장        : {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
