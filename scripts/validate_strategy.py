"""전략 수익 신뢰도 검증 — "짧은 단일 종목 세션에서 수익이 확실히 나는가?"

기존 백테스트(scripts/backtest.py)는 한 구간을 한 번 돌려 한 숫자를 낸다.
이 도구는 같은 매매 로직(견적·AP2 한도·수수료·매도 대금 환입)을 실데이터 위에서
**여러 짧은 구간(롤링 윈도우)** 에 걸쳐 반복해, 수익의 '분포'와 '신뢰도'를 측정한다.

핵심 질문에 답한다:
  - 짧은 세션(예: 8~30봉)에서 몇 %가 흑자인가? (미실현 손실 포함 = 승률 착시 제거)
  - 몇 %가 '그냥 사서 들고 있기(매수후보유)' 벤치마크를 이겼는가?
  - 최악의 구간은 얼마나 나빴는가? (손절 부재의 실제 하방 리스크)

판단 두뇌는 규칙(rule) 고정 — 결정적이라 재현되고, Gemini 무료 티어 429 오염이 없다.
(엄격 모드 Gemini 는 규칙 위에서 '보류'만 하므로 수익 상한은 규칙과 같다.)

사용 예 (프로젝트 루트):
  python scripts/validate_strategy.py
  python scripts/validate_strategy.py --symbols AAPL,TSLA,NVDA --windows 8,12,20,30
  python scripts/validate_strategy.py --spend 20 --dip 2 --profit 3 --stop-loss 5
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
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


def _pct(numer: Decimal, denom: Decimal) -> Decimal:
    return (numer / denom * 100).quantize(CENT) if denom > 0 else Decimal(0)


def simulate_window(
    warmup: List[Bar],
    play: List[Bar],
    *,
    symbol: str,
    budget: Decimal,
    per_trade: Decimal,
    spend: Decimal,
    dip: Decimal,
    profit: Decimal,
    fee_bps: int,
    stop_loss_pct: Optional[Decimal] = None,
    max_hold: int = 0,
) -> dict:
    """한 구간(warmup+play)을 규칙 전략으로 시뮬레이션 → 성과 지표 dict.

    scripts/backtest.py 의 봉별 매매 루프를 그대로 옮겨, 검증 수치가 백테스트와
    같은 코드 경로(TradingAgent.decide / BrokerAgent.quote / PaymentAuthorizer)를 지나게 한다.
    stop_loss_pct 가 주어지면(선택) 평단 대비 -N% 도달 시 손절 매도를 규칙 위에 얹는다.
    """
    kp = Keypair()
    strategy = Strategy(
        buy_dip_pct=dip, take_profit_pct=profit,
        spend_per_trade_usdc=spend, decision_mode="strict",
        max_hold_bars=max_hold,   # 시간청산(안전레일) — 실제 decide() 경로로 검증
    )
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=budget, per_trade_max_usdc=per_trade,
        allowed_symbols=[symbol],
    ).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    trading = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "validate",
                           brain=None, fee_bps=fee_bps)
    if warmup:
        trading.preload_bars(warmup)
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "validate", fee_bps=fee_bps)

    fee_rate = Decimal(fee_bps) / Decimal(10000)
    realized = Decimal(0)
    cum_buy = Decimal(0)
    buys = sells = stops = 0
    peak = budget
    mdd = Decimal(0)
    first_price = play[0].close if play else Decimal(0)
    last_price = first_price

    for bar in play:
        price = bar.close
        last_price = price
        d = trading.decide(symbol, price, bar)
        pos = trading.position

        # 선택적 손절 — 규칙 신호와 무관하게 평단 대비 -stop_loss_pct 도달 시 전량 매도.
        # (현행 전략의 최대 약점 = 손절 부재로 하방이 무한정 열려 있는 점을 실측하기 위한 옵션)
        forced_stop = False
        if (stop_loss_pct is not None and pos.quantity > 0 and pos.avg_price_usdc > 0
                and price <= pos.avg_price_usdc * (1 - stop_loss_pct / 100)):
            forced_stop = True

        if d.action == "buy" and not forced_stop:
            q = broker.quote(symbol, d.spend_usdc, price)
            try:
                auth.authorize(order_id=f"v_{buys}", symbol=symbol,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError:
                pass
            else:
                eff = (q.total_usdc / q.quantity).quantize(CENT) if q.quantity else price
                pos.apply_buy(q.quantity, eff)
                cum_buy += q.total_usdc
                buys += 1
        elif (d.action == "sell" or forced_stop) and pos.quantity > 0:
            qty = pos.quantity
            avg = pos.avg_price_usdc
            q = broker.sell_quote(symbol, qty, price)
            realized += (q.total_usdc - avg * qty).quantize(CENT)
            pos.apply_sell(qty)
            auth.credit_sale(q.total_usdc)
            sells += 1
            if forced_stop:
                stops += 1

        equity = auth.remaining_usdc + (trading.position.quantity * price * (1 - fee_rate))
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)

    pos = trading.position
    unrealized = ((pos.quantity * last_price * (1 - fee_rate))
                  - pos.avg_price_usdc * pos.quantity).quantize(CENT)
    total_pnl = (realized + unrealized).quantize(CENT)

    # 벤치마크(매수후보유): 첫 봉에 예산 전액 매수 → 마지막 봉 전량 매도 (같은 수수료 모델)
    bh_qty = ((budget / (first_price * (1 + fee_rate))).quantize(Decimal("0.0001"),
              rounding=ROUND_DOWN) if first_price > 0 else Decimal(0))
    bh_final = (bh_qty * last_price * (1 - fee_rate)).quantize(CENT)
    bh_pnl = (bh_final - budget).quantize(CENT)

    strat_pct = _pct(total_pnl, budget)
    bh_pct = _pct(bh_pnl, budget)
    return {
        "from": play[0].date if play else "", "to": play[-1].date if play else "",
        "bars": len(play),
        "total_pnl_usdc": total_pnl,
        "realized_pnl_usdc": realized.quantize(CENT),
        "unrealized_pnl_usdc": unrealized,
        "return_pct": strat_pct,
        "benchmark_pct": bh_pct,
        "excess_pct": (strat_pct - bh_pct).quantize(CENT),
        "mdd_pct": mdd.quantize(CENT),
        "buys": buys, "sells": sells, "stops": stops,
        "ended_in_position": pos.quantity > 0,
    }


def sweep_symbol(bars: List[Bar], win: int, warmup: int, **params) -> List[dict]:
    """한 심볼의 봉 전체를 길이 win 의 겹치는 구간으로 슬라이드하며 시뮬레이션."""
    out: List[dict] = []
    # 시작 오프셋 s: 앞에 warmup 봉이 있어야 MA 성립 → s 는 warmup 이상
    for s in range(warmup, len(bars) - win + 1):
        w = bars[max(0, s - warmup):s]
        p = bars[s:s + win]
        if len(p) < win:
            break
        out.append(simulate_window(w, p, **params))
    return out


def sweep_portfolio(bars_by_symbol: dict, win: int, warmup: int,
                    budget: Decimal, **params) -> List[dict]:
    """날짜 정렬 포트폴리오 스윕 — 같은 구간에서 여러 종목을 예산 N분할로 동시 운용.

    분산 효과 측정: 각 종목은 예산/종목수 를 배정받아 독립 포지션·독립 AP2 한도로
    같은 날짜 구간을 굴리고, 구간 손익을 합산해 포트폴리오 수익률을 낸다.
    (멀티 종목 엔진이 하나의 세션에서 각 종목을 독립 가드·독립 포지션으로 굴리는 것과 동형.)
    모든 CSV 는 같은 날짜 구간이라 인덱스로 정렬한다(교집합 날짜만 사용)."""
    syms = list(bars_by_symbol.keys())
    if len(syms) < 2:
        return []
    # 공통 날짜 인덱스 — 날짜→봉 매핑 후 교집합 정렬
    date_maps = {s: {b.date: b for b in bars} for s, bars in bars_by_symbol.items()}
    common = sorted(set.intersection(*(set(m) for m in date_maps.values())))
    aligned = {s: [date_maps[s][d] for d in common] for s in syms}
    n_dates = len(common)
    n = len(syms)
    # 종목별로 예산·건별한도·1회매수를 1/N 로 스케일 → 단일종목과 동일 거래패턴의 1/N 축소판
    # (총 투입자본은 단일종목 실험과 동일하게 유지 = 공정한 분산 효과 비교)
    per_sym_budget = (budget / n).quantize(CENT)
    scaled = dict(params)
    scaled["per_trade"] = (params["per_trade"] / n).quantize(CENT)
    scaled["spend"] = (params["spend"] / n).quantize(CENT)
    out: List[dict] = []
    for s in range(warmup, n_dates - win + 1):
        sub = []
        for sym in syms:
            w = aligned[sym][max(0, s - warmup):s]
            p = aligned[sym][s:s + win]
            if len(p) < win:
                break
            sub.append(simulate_window(w, p, symbol=sym, budget=per_sym_budget, **scaled))
        if len(sub) != len(syms):
            break
        total_pnl = sum((r["total_pnl_usdc"] for r in sub), Decimal(0)).quantize(CENT)
        # 포트폴리오 초과수익 = 각 종목 예산가중 초과수익 합(동일 분할이라 단순 평균)
        excess = (sum((r["excess_pct"] for r in sub), Decimal(0)) / len(syms)).quantize(CENT)
        out.append({
            "from": common[s], "to": common[s + win - 1], "bars": win,
            "total_pnl_usdc": total_pnl,
            "return_pct": _pct(total_pnl, budget),
            "excess_pct": excess,
            "mdd_pct": max((r["mdd_pct"] for r in sub), default=Decimal(0)),
            "buys": sum(r["buys"] for r in sub), "sells": sum(r["sells"] for r in sub),
            "stops": sum(r["stops"] for r in sub),
            "ended_in_position": any(r["ended_in_position"] for r in sub),
        })
    return out


def summarize(rows: List[dict]) -> dict:
    """구간 결과 리스트 → 신뢰도 요약(흑자율·벤치 우위율·평균/중앙값·최악)."""
    n = len(rows)
    if not n:
        return {"windows": 0}
    rets = [float(r["return_pct"]) for r in rows]
    exc = [float(r["excess_pct"]) for r in rows]
    profitable = sum(1 for r in rows if r["total_pnl_usdc"] > 0)
    flat = sum(1 for r in rows if r["total_pnl_usdc"] == 0)
    beat = sum(1 for r in rows if r["excess_pct"] > 0)
    zero_trade = sum(1 for r in rows if r["buys"] == 0)
    return {
        "windows": n,
        "profitable_pct": round(profitable / n * 100, 1),
        "flat_pct": round(flat / n * 100, 1),           # 무거래 등 손익 0 구간
        "loss_pct": round((n - profitable - flat) / n * 100, 1),
        "beat_benchmark_pct": round(beat / n * 100, 1),
        "mean_return_pct": round(statistics.mean(rets), 2),
        "median_return_pct": round(statistics.median(rets), 2),
        "mean_excess_pct": round(statistics.mean(exc), 2),
        "worst_return_pct": round(min(rets), 2),
        "best_return_pct": round(max(rets), 2),
        "worst_mdd_pct": round(max(float(r["mdd_pct"]) for r in rows), 2),
        "zero_trade_pct": round(zero_trade / n * 100, 1),
        "avg_buys": round(statistics.mean(r["buys"] for r in rows), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="전략 수익 신뢰도 검증 (롤링 윈도우)")
    ap.add_argument("--symbols", default="AAPL,TSLA,NVDA")
    ap.add_argument("--windows", default="8,12,20,30", help="세션 길이(봉) 목록")
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--budget", default="100")
    ap.add_argument("--per-trade", default="50")
    ap.add_argument("--spend", default="30")
    ap.add_argument("--dip", default="2")
    ap.add_argument("--profit", default="3")
    ap.add_argument("--stop-loss", default="", help="평단 대비 -N%% 손절 (빈값=손절 없음, 현행)")
    ap.add_argument("--max-hold", type=int, default=0,
                    help="시간청산 — N봉 이상 보유 시 자동 청산(안전레일, 0=비활성)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    wins = [int(w) for w in args.windows.split(",") if w.strip()]
    stop_loss = Decimal(args.stop_loss) if args.stop_loss.strip() else None
    params = dict(
        budget=Decimal(args.budget), per_trade=Decimal(args.per_trade),
        spend=Decimal(args.spend), dip=Decimal(args.dip), profit=Decimal(args.profit),
        fee_bps=CFG.broker_fee_bps, stop_loss_pct=stop_loss, max_hold=args.max_hold,
    )

    sl_label = f"손절 -{stop_loss}%" if stop_loss is not None else "손절 없음"
    mh_label = f"시간청산 {args.max_hold}봉" if args.max_hold > 0 else "시간청산 없음"
    print(f"전략 검증 — 규칙 MA5 -{args.dip}% 매수 / 평단 +{args.profit}% 익절 · {sl_label} · {mh_label}")
    print(f"  예산 {args.budget} · 건별한도 {args.per_trade} · 1회매수 {args.spend} · "
          f"수수료 {CFG.broker_fee_bps}bps · 워밍업 {args.warmup}봉")
    print(f"  심볼 {symbols} · 세션 길이 {wins}봉\n")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {"budget": args.budget, "per_trade": args.per_trade,
                   "spend": args.spend, "dip": args.dip, "profit": args.profit,
                   "stop_loss_pct": args.stop_loss or None, "max_hold_bars": args.max_hold,
                   "fee_bps": CFG.broker_fee_bps, "warmup": args.warmup,
                   "brain": "rule", "symbols": symbols, "windows": wins},
        "by_symbol_window": {}, "by_window": {}, "overall": {},
    }
    all_rows: List[dict] = []
    per_window_rows: dict[int, List[dict]] = {w: [] for w in wins}

    bars_by_symbol = {}
    for sym in symbols:
        csv = os.path.join(ROOT, "data", "market", f"{sym}_daily.csv")
        try:
            bars_by_symbol[sym] = load_bars(csv)
        except (FileNotFoundError, ValueError) as e:
            print(f"[건너뜀] {sym}: {e}")

    header = f"{'심볼':<6}{'세션':>5}{'구간수':>6}{'흑자%':>7}{'벤치승%':>8}{'평균%':>8}{'중앙%':>8}{'초과%':>8}{'최악%':>9}{'최악MDD':>9}"
    print(header)
    print("-" * len(header))
    for sym, bars in bars_by_symbol.items():
        for w in wins:
            rows = sweep_symbol(bars, w, args.warmup, symbol=sym, **params)
            s = summarize(rows)
            report["by_symbol_window"][f"{sym}_{w}"] = s
            all_rows.extend(rows)
            per_window_rows[w].extend(rows)
            if s["windows"]:
                print(f"{sym:<6}{w:>5}{s['windows']:>6}{s['profitable_pct']:>7}"
                      f"{s['beat_benchmark_pct']:>8}{s['mean_return_pct']:>8}"
                      f"{s['median_return_pct']:>8}{s['mean_excess_pct']:>8}"
                      f"{s['worst_return_pct']:>9}{s['worst_mdd_pct']:>9}")

    print("\n=== 세션 길이별 종합(전 심볼 합산) ===")
    print(f"{'세션':>5}{'구간수':>7}{'흑자%':>8}{'벤치승%':>9}{'평균%':>8}{'초과%':>8}{'최악%':>9}")
    for w in wins:
        s = summarize(per_window_rows[w])
        report["by_window"][str(w)] = s
        if s["windows"]:
            print(f"{w:>5}{s['windows']:>7}{s['profitable_pct']:>8}{s['beat_benchmark_pct']:>9}"
                  f"{s['mean_return_pct']:>8}{s['mean_excess_pct']:>8}{s['worst_return_pct']:>9}")

    # 분산(멀티 종목) 효과 — 같은 예산을 N종목에 나눠 동시 운용한 포트폴리오의 신뢰도
    if len(bars_by_symbol) >= 2:
        pparams = {k: v for k, v in params.items() if k != "budget"}
        print(f"\n=== 분산 포트폴리오({'+'.join(bars_by_symbol)}, 예산 {args.budget} {len(bars_by_symbol)}분할) ===")
        print(f"{'세션':>5}{'구간수':>7}{'흑자%':>8}{'벤치승%':>9}{'평균%':>8}{'초과%':>8}{'최악%':>9}{'최악MDD':>9}")
        report["portfolio"] = {}
        for w in wins:
            prows = sweep_portfolio(bars_by_symbol, w, args.warmup, params["budget"], **pparams)
            s = summarize(prows)
            report["portfolio"][str(w)] = s
            if s["windows"]:
                print(f"{w:>5}{s['windows']:>7}{s['profitable_pct']:>8}{s['beat_benchmark_pct']:>9}"
                      f"{s['mean_return_pct']:>8}{s['mean_excess_pct']:>8}"
                      f"{s['worst_return_pct']:>9}{s['worst_mdd_pct']:>9}")

    overall = summarize(all_rows)
    report["overall"] = overall
    print("\n=== 전체 종합 ===")
    print(f"  총 구간 {overall['windows']}개 · 흑자 {overall['profitable_pct']}% · "
          f"벤치마크 우위 {overall['beat_benchmark_pct']}% · 손익0 {overall['flat_pct']}%")
    print(f"  평균 수익률 {overall['mean_return_pct']}% · 평균 초과수익 {overall['mean_excess_pct']}%p · "
          f"무거래 구간 {overall['zero_trade_pct']}%")
    print(f"  최악 구간 {overall['worst_return_pct']}% · 최악 MDD {overall['worst_mdd_pct']}% · "
          f"최고 구간 {overall['best_return_pct']}%")

    out_dir = os.path.join(ROOT, "artifacts", "backtests")
    os.makedirs(out_dir, exist_ok=True)
    tag = "validate" + ("_sl" if stop_loss is not None else "")
    path = os.path.join(out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  저장: {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
