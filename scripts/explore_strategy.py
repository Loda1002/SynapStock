"""전략 고도화 탐색 — 눌림목 규칙 위에 얹는 '오버레이'들을 실측 비교한다.

검증 도구(validate_strategy.py)는 **실제 앱 전략을 그대로** 잰다. 이 탐색 도구는
아직 앱에 없는 **가설 오버레이**를 규칙 판단 위에 얹어 "구현할 가치가 있는가"를
같은 롤링 윈도우로 실측한다. 유망한 것만 실제 TradingAgent 에 반영하는 순서를 지킨다
(모델이 근거 없이 '좋다'고 주장하는 사각지대를 백테스트 숫자로 차단).

오버레이(규칙 판단 뒤에 후처리로 얹음 — 규칙의 매수 개시는 유지):
  - trend_filter : MA20 이 상승 중일 때만 매수 허용 (하락추세 눌림목 매수 차단)
  - trail_pct    : 고정 +profit% 익절을 트레일링으로 대체 — 익절선 도달 후 고점 대비 -trail% 에 매도(승자 태우기)
  - max_hold     : N봉 이상 보유하면 강제 청산 (미실현 손실에 갇히는 변동성 축소)
  - stop_loss    : 평단 대비 -N% 손절 (대조군 — 검증에서 이미 역효과 확인)

사용:
  python scripts/explore_strategy.py                 # 정의된 구성 전체 비교
  python scripts/explore_strategy.py --windows 8,20  # 세션 길이 지정
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402

from config import CFG  # noqa: E402
from market.price_feed import Bar, load_bars  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from agents.broker_agent import BrokerAgent  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError  # noqa: E402

CENT = Decimal("0.01")


@dataclass
class Overlay:
    name: str
    dip: Decimal = Decimal("2")
    profit: Decimal = Decimal("3")
    trend_filter: bool = False
    trail_pct: Optional[Decimal] = None
    max_hold: Optional[int] = None
    stop_loss_pct: Optional[Decimal] = None


def simulate(warmup: List[Bar], play: List[Bar], ov: Overlay, *,
             symbol: str, budget: Decimal, per_trade: Decimal, spend: Decimal,
             fee_bps: int) -> dict:
    kp = Keypair()
    strategy = Strategy(buy_dip_pct=ov.dip, take_profit_pct=ov.profit,
                        spend_per_trade_usdc=spend, decision_mode="strict")
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=budget, per_trade_max_usdc=per_trade,
        allowed_symbols=[symbol]).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    trading = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "explore",
                           brain=None, fee_bps=fee_bps)
    if warmup:
        trading.preload_bars(warmup)
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "explore", fee_bps=fee_bps)

    fee_rate = Decimal(fee_bps) / Decimal(10000)
    realized = Decimal(0)
    buys = sells = 0
    peak_equity = budget
    mdd = Decimal(0)
    first_price = play[0].close if play else Decimal(0)
    last_price = first_price
    prev_ma20: Optional[Decimal] = None
    pos_peak = Decimal(0)      # 트레일링용 보유 중 고점
    armed = False              # 트레일링: 익절선 도달 후 무장
    held = 0                   # 보유 봉 수 (time exit)

    for bar in play:
        price = bar.close
        last_price = price
        d = trading.decide(symbol, price, bar)
        pos = trading.position
        ma20 = trading._ma(20)
        action = d.action

        # --- 진입 오버레이: 추세 필터 (MA20 상승 중일 때만 매수) ---
        if action == "buy" and ov.trend_filter:
            rising = ma20 is not None and prev_ma20 is not None and ma20 > prev_ma20
            if not rising:
                action = "hold"

        forced_sell = False
        if pos.quantity > 0:
            pos_peak = max(pos_peak, price)
            held += 1
            # 손절
            if (ov.stop_loss_pct is not None and pos.avg_price_usdc > 0
                    and price <= pos.avg_price_usdc * (1 - ov.stop_loss_pct / 100)):
                forced_sell = True
            # 시간 청산
            if ov.max_hold is not None and held >= ov.max_hold:
                forced_sell = True
            # 트레일링 익절 — 고정 익절(action=="sell")을 대체
            if ov.trail_pct is not None:
                if pos.avg_price_usdc > 0 and price >= pos.avg_price_usdc * (1 + ov.profit / 100):
                    armed = True
                if armed and price <= pos_peak * (1 - ov.trail_pct / 100):
                    forced_sell = True
                if action == "sell":
                    action = "hold"   # 트레일링이 청산을 관리 — 고정 익절 억제

        if action == "buy" and not forced_sell:
            q = broker.quote(symbol, d.spend_usdc, price)
            try:
                auth.authorize(order_id=f"e_{buys}", symbol=symbol,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError:
                pass
            else:
                eff = (q.total_usdc / q.quantity).quantize(CENT) if q.quantity else price
                if pos.quantity == 0:
                    pos_peak = price
                    armed = False
                    held = 0
                pos.apply_buy(q.quantity, eff)
                buys += 1
        elif (action == "sell" or forced_sell) and pos.quantity > 0:
            qty = pos.quantity
            avg = pos.avg_price_usdc
            q = broker.sell_quote(symbol, qty, price)
            realized += (q.total_usdc - avg * qty).quantize(CENT)
            pos.apply_sell(qty)
            auth.credit_sale(q.total_usdc)
            sells += 1
            pos_peak = Decimal(0)
            armed = False
            held = 0

        equity = auth.remaining_usdc + (trading.position.quantity * price * (1 - fee_rate))
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            mdd = max(mdd, (peak_equity - equity) / peak_equity * 100)
        prev_ma20 = ma20

    pos = trading.position
    unrealized = ((pos.quantity * last_price * (1 - fee_rate))
                  - pos.avg_price_usdc * pos.quantity).quantize(CENT)
    total_pnl = (realized + unrealized).quantize(CENT)
    bh_qty = ((budget / (first_price * (1 + fee_rate))).quantize(Decimal("0.0001"),
              rounding=ROUND_DOWN) if first_price > 0 else Decimal(0))
    bh_final = (bh_qty * last_price * (1 - fee_rate)).quantize(CENT)
    bh_pnl = (bh_final - budget).quantize(CENT)
    strat_pct = (total_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    bh_pct = (bh_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    return {
        "total_pnl_usdc": total_pnl, "return_pct": strat_pct,
        "benchmark_pct": bh_pct, "excess_pct": (strat_pct - bh_pct).quantize(CENT),
        "mdd_pct": mdd.quantize(CENT), "buys": buys, "sells": sells,
        "ended_in_position": pos.quantity > 0,
    }


def summarize(rows: List[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"windows": 0}
    rets = [float(r["return_pct"]) for r in rows]
    exc = [float(r["excess_pct"]) for r in rows]
    profitable = sum(1 for r in rows if r["total_pnl_usdc"] > 0)
    beat = sum(1 for r in rows if r["excess_pct"] > 0)
    return {
        "windows": n,
        "profitable_pct": round(profitable / n * 100, 1),
        "beat_benchmark_pct": round(beat / n * 100, 1),
        "mean_return_pct": round(statistics.mean(rets), 2),
        "median_return_pct": round(statistics.median(rets), 2),
        "mean_excess_pct": round(statistics.mean(exc), 2),
        "worst_return_pct": round(min(rets), 2),
        "best_return_pct": round(max(rets), 2),
        "worst_mdd_pct": round(max(float(r["mdd_pct"]) for r in rows), 2),
        "avg_buys": round(statistics.mean(r["buys"] for r in rows), 2),
    }


def sweep(bars_by_symbol: dict, ov: Overlay, wins: List[int], warmup: int,
          *, budget, per_trade, spend, fee_bps, only_symbol: Optional[str] = None) -> List[dict]:
    rows: List[dict] = []
    for sym, bars in bars_by_symbol.items():
        if only_symbol and sym != only_symbol:
            continue
        for w in wins:
            for s in range(warmup, len(bars) - w + 1):
                warm = bars[max(0, s - warmup):s]
                play = bars[s:s + w]
                if len(play) < w:
                    break
                rows.append(simulate(warm, play, ov, symbol=sym, budget=budget,
                                     per_trade=per_trade, spend=spend, fee_bps=fee_bps))
    return rows


def sweep_portfolio(bars_by_symbol: dict, ov: Overlay, wins: List[int], warmup: int,
                    *, budget, per_trade, spend, fee_bps) -> List[dict]:
    syms = list(bars_by_symbol.keys())
    date_maps = {s: {b.date: b for b in bars} for s, bars in bars_by_symbol.items()}
    common = sorted(set.intersection(*(set(m) for m in date_maps.values())))
    aligned = {s: [date_maps[s][d] for d in common] for s in syms}
    n = len(syms)
    pb = (budget / n).quantize(CENT)
    pt = (per_trade / n).quantize(CENT)
    ps = (spend / n).quantize(CENT)
    rows: List[dict] = []
    for w in wins:
        for s in range(warmup, len(common) - w + 1):
            sub = []
            for sym in syms:
                warm = aligned[sym][max(0, s - warmup):s]
                play = aligned[sym][s:s + w]
                if len(play) < w:
                    break
                sub.append(simulate(warm, play, ov, symbol=sym, budget=pb,
                                    per_trade=pt, spend=ps, fee_bps=fee_bps))
            if len(sub) != n:
                break
            total = sum((r["total_pnl_usdc"] for r in sub), Decimal(0)).quantize(CENT)
            exc = (sum((r["excess_pct"] for r in sub), Decimal(0)) / n).quantize(CENT)
            rows.append({
                "total_pnl_usdc": total,
                "return_pct": (total / budget * 100).quantize(CENT),
                "excess_pct": exc,
                "mdd_pct": max((r["mdd_pct"] for r in sub), default=Decimal(0)),
                "buys": sum(r["buys"] for r in sub), "sells": sum(r["sells"] for r in sub),
                "ended_in_position": any(r["ended_in_position"] for r in sub),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="전략 고도화 오버레이 탐색")
    ap.add_argument("--symbols", default="AAPL,TSLA,NVDA")
    ap.add_argument("--windows", default="8,12,20,30")
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--budget", default="100")
    ap.add_argument("--per-trade", default="50")
    ap.add_argument("--spend", default="30")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    wins = [int(w) for w in args.windows.split(",") if w.strip()]
    p = dict(budget=Decimal(args.budget), per_trade=Decimal(args.per_trade),
             spend=Decimal(args.spend), fee_bps=CFG.broker_fee_bps)

    bars_by_symbol = {}
    for sym in symbols:
        csv = os.path.join(ROOT, "data", "market", f"{sym}_daily.csv")
        try:
            bars_by_symbol[sym] = load_bars(csv)
        except (FileNotFoundError, ValueError) as e:
            print(f"[건너뜀] {sym}: {e}")

    # 탐색 구성 — 원래 3안 + 고도화 후보 + 조합
    configs = [
        Overlay("① 현행 dip2/profit3"),
        Overlay("② dip3/profit5", dip=Decimal(3), profit=Decimal(5)),
        Overlay("추세필터(MA20↑)", trend_filter=True),
        Overlay("트레일링 익절 2%", trail_pct=Decimal(2)),
        Overlay("트레일링 익절 1.5%", trail_pct=Decimal("1.5")),
        Overlay("시간청산 10봉", max_hold=10),
        Overlay("추세필터+트레일2%", trend_filter=True, trail_pct=Decimal(2)),
        Overlay("추세필터+시간청산10", trend_filter=True, max_hold=10),
        Overlay("dip3p5+추세+트레일2", dip=Decimal(3), profit=Decimal(5),
                trend_filter=True, trail_pct=Decimal(2)),
        Overlay("★ dip3p5+시간청산10", dip=Decimal(3), profit=Decimal(5), max_hold=10),
        Overlay("★ dip3p5+시간청산8", dip=Decimal(3), profit=Decimal(5), max_hold=8),
        Overlay("★ dip2p3+시간청산8", max_hold=8),
        Overlay("대조: 손절5%", stop_loss_pct=Decimal(5)),
    ]

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "params": {"budget": args.budget, "per_trade": args.per_trade,
                         "spend": args.spend, "fee_bps": CFG.broker_fee_bps,
                         "windows": wins, "symbols": symbols},
              "single": {}, "aapl_only": {}, "portfolio": {}}

    def line(name, s):
        return (f"{name:<22}{s['profitable_pct']:>7}{s['beat_benchmark_pct']:>8}"
                f"{s['mean_return_pct']:>8}{s['mean_excess_pct']:>8}"
                f"{s['worst_return_pct']:>9}{s['worst_mdd_pct']:>9}{s['avg_buys']:>7}")

    hdr = f"{'구성':<22}{'흑자%':>7}{'벤치승%':>8}{'평균%':>8}{'초과%':>8}{'최악%':>9}{'최악MDD':>9}{'평균매수':>7}"

    print(f"전 심볼 합산 (세션 {wins}봉, 예산 {args.budget}) — 규칙 위 오버레이 실측")
    print(hdr)
    print("-" * len(hdr))
    for ov in configs:
        rows = sweep(bars_by_symbol, ov, wins, args.warmup, **p)
        s = summarize(rows)
        report["single"][ov.name] = s
        print(line(ov.name, s))

    print(f"\nAAPL 단독 (강한 추세장 = 현행 최약점) — 개선되나?")
    print(hdr)
    print("-" * len(hdr))
    for ov in configs:
        rows = sweep(bars_by_symbol, ov, wins, args.warmup, only_symbol="AAPL", **p)
        s = summarize(rows)
        report["aapl_only"][ov.name] = s
        if s.get("windows"):
            print(line(ov.name, s))

    if len(bars_by_symbol) >= 2:
        print(f"\n3종목 분산 포트폴리오 (예산 {args.budget} {len(bars_by_symbol)}분할)")
        print(hdr)
        print("-" * len(hdr))
        for ov in configs:
            rows = sweep_portfolio(bars_by_symbol, ov, wins, args.warmup, **p)
            s = summarize(rows)
            report["portfolio"][ov.name] = s
            print(line(ov.name, s))

    out_dir = os.path.join(ROOT, "artifacts", "backtests")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_explore.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n저장: {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
