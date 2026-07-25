"""추세추종 전략 탐색 — "상승세 보유·장기 하락세 매도·추세전환 재매수"가 매수후보유를 이기는가.

사용자 의도(2026-07-25): 짧은 익절이 아니라, 상승 추세를 타고 보유하다 **장기 하락세**가
확인되면 팔고, 추세가 다시 올라오면 산다. 이 '추세 판단 로직'이 핵심.
→ 현행 평균회귀(눌림목 매수)와 정반대. 여기서 여러 추세판단 방식을 실측해 매수후보유와 비교한다.

모델: 전량 진입/전량 청산(추세 신호에 따라 예산 전액 투입·전량 회수). 추세추종은 보통
승자를 오래 태우려 올인/올아웃한다. 수수료(30bps)는 매매마다 반영(추세추종은 매매가 적어 수수료도 적다).

핵심 비교축은 '흑자율'이 아니라 **매수후보유 대비 초과수익**(사용자 목표 = 그냥 보유를 이기기).

주의: 이 데이터(2026-02~07, AAPL·TSLA·NVDA 전부 순상승)에는 **큰 장기 하락장이 없다.**
추세추종의 진짜 강점(하락장 회피)은 하락 데이터가 있어야 드러난다 — 한계로 명시한다.

사용:
  python scripts/explore_trend.py
  python scripts/explore_trend.py --windows 30,50
"""
from __future__ import annotations
import argparse
import os
import statistics
import sys
from decimal import Decimal, ROUND_DOWN
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import CFG  # noqa: E402
from market.price_feed import Bar, load_bars  # noqa: E402

CENT = Decimal("0.01")


def ma(closes: List[Decimal], period: int) -> Optional[Decimal]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / Decimal(period)


def desired_long(variant: str, price: Decimal, ma5, ma10, ma20, prev_ma20, cur_long: bool) -> bool:
    """추세판단 방식별 '지금 롱(보유)이어야 하는가'."""
    if variant == "buyhold":
        return True
    if ma20 is None:
        return False
    if variant == "pxma20":            # 가격 > MA20 이면 상승추세로 간주
        return price >= ma20
    if variant == "pxma20_band":       # 히스테리시스: 진입은 >MA20, 이탈은 <MA20*0.97 (휩쏘 억제)
        if cur_long:
            return price >= ma20 * Decimal("0.97")
        return price >= ma20
    if variant == "cross_5_20":        # 골든/데드크로스 (단기 MA5 vs 중기 MA20)
        return ma5 is not None and ma5 >= ma20
    if variant == "cross_10_20":       # 골든/데드크로스 (MA10 vs MA20) — 더 느리고 덜 휩쏨
        return ma10 is not None and ma10 >= ma20
    if variant == "slope20":           # MA20 기울기 상승 = 상승추세
        return prev_ma20 is not None and ma20 > prev_ma20
    return False


def simulate(warmup: List[Bar], play: List[Bar], variant: str,
             budget: Decimal, fee_bps: int) -> dict:
    fee = Decimal(fee_bps) / Decimal(10000)
    closes = [b.close for b in warmup]
    cash = budget
    qty = Decimal(0)
    long = False
    trades = 0
    prev_ma20 = ma(closes, 20)
    first = play[0].close if play else Decimal(0)
    last = first
    peak = budget
    mdd = Decimal(0)
    for bar in play:
        price = bar.close
        last = price
        closes.append(price)
        ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
        want = desired_long(variant, price, ma5, ma10, ma20, prev_ma20, long)
        if want and not long:                       # 진입 — 예산 전액 매수
            qty = (cash / (price * (1 + fee))).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            cash = (cash - qty * price * (1 + fee)).quantize(CENT)
            long = True
            trades += 1
        elif not want and long:                     # 청산 — 전량 매도
            cash = (cash + qty * price * (1 - fee)).quantize(CENT)
            qty = Decimal(0)
            long = False
            trades += 1
        equity = cash + (qty * price * (1 - fee))
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)
        prev_ma20 = ma20
    final = (cash + qty * last * (1 - fee)).quantize(CENT)
    ret = ((final - budget) / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    return {"return_pct": ret, "trades": trades, "mdd_pct": mdd.quantize(CENT),
            "final_usdc": final}


def main() -> int:
    ap = argparse.ArgumentParser(description="추세추종 vs 매수후보유")
    ap.add_argument("--symbols", default="AAPL,TSLA,NVDA")
    ap.add_argument("--windows", default="30,50", help="롤링 윈도우 길이(전체는 자동 포함)")
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--budget", default="100")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    wins = [int(w) for w in args.windows.split(",") if w.strip()]
    budget = Decimal(args.budget)
    fee = CFG.broker_fee_bps

    variants = ["buyhold", "pxma20", "pxma20_band", "cross_5_20", "cross_10_20", "slope20"]
    vlabel = {"buyhold": "매수후보유(기준)", "pxma20": "가격>MA20", "pxma20_band": "가격>MA20+밴드",
              "cross_5_20": "골든크로스5/20", "cross_10_20": "골든크로스10/20", "slope20": "MA20 기울기"}

    bars_by = {}
    for s in symbols:
        try:
            bars_by[s] = load_bars(os.path.join(ROOT, "data", "market", f"{s}_daily.csv"))
        except (FileNotFoundError, ValueError) as e:
            print(f"[건너뜀] {s}: {e}")

    print(f"추세추종 vs 매수후보유 — 예산 {args.budget} · 수수료 {fee}bps · 워밍업 {args.warmup}봉\n")

    # 1) 전체 구간(각 심볼 = 워밍업 이후 전 구간 1회) — '한 번 사서 오래 보유' 관점의 대표 숫자
    print("=== 전체 구간 (심볼별 1세션, 워밍업 이후 끝까지) ===")
    print(f"{'심볼':<6}{'방식':<16}{'수익%':>8}{'vs보유%p':>9}{'매매수':>7}{'최악MDD':>9}")
    print("-" * 55)
    agg_excess = {v: [] for v in variants}
    for s, bars in bars_by.items():
        warm = bars[:args.warmup]
        play = bars[args.warmup:]
        bh = simulate(warm, play, "buyhold", budget, fee)
        for v in variants:
            r = simulate(warm, play, v, budget, fee)
            exc = (r["return_pct"] - bh["return_pct"]).quantize(CENT)
            agg_excess[v].append(float(exc))
            tag = "" if v == "buyhold" else f"{exc:+}"
            print(f"{s:<6}{vlabel[v]:<16}{r['return_pct']:>8}{tag:>9}{r['trades']:>7}{r['mdd_pct']:>9}")
        print("-" * 55)

    # 2) 롤링 윈도우 — 시작점 편향 제거 (전 심볼 합산, 방식별 매수후보유 대비 승률·평균초과)
    for w in wins:
        print(f"\n=== 롤링 {w}봉 (전 심볼 합산, 매수후보유 대비) ===")
        print(f"{'방식':<16}{'구간수':>6}{'보유이김%':>9}{'평균초과%p':>11}{'평균수익%':>10}{'평균매매':>9}")
        print("-" * 61)
        rows = {v: [] for v in variants}
        for s, bars in bars_by.items():
            for i in range(args.warmup, len(bars) - w + 1):
                warm = bars[max(0, i - args.warmup):i]
                play = bars[i:i + w]
                if len(play) < w:
                    break
                bh = simulate(warm, play, "buyhold", budget, fee)
                for v in variants:
                    r = simulate(warm, play, v, budget, fee)
                    rows[v].append((float(r["return_pct"]),
                                    float(r["return_pct"] - bh["return_pct"]), r["trades"]))
        for v in variants:
            data = rows[v]
            if not data:
                continue
            n = len(data)
            beat = sum(1 for _, e, _ in data if e > 0) / n * 100
            mean_exc = statistics.mean(e for _, e, _ in data)
            mean_ret = statistics.mean(r for r, _, _ in data)
            mean_tr = statistics.mean(t for _, _, t in data)
            print(f"{vlabel[v]:<16}{n:>6}{beat:>9.1f}{mean_exc:>11.2f}{mean_ret:>10.2f}{mean_tr:>9.2f}")

    print("\n[한계] 이 데이터(2026-02~07)는 3종목 전부 순상승 — 큰 장기 하락장이 없다.")
    print("       추세추종의 핵심 강점(하락장에서 빠져나와 손실 회피)은 하락 데이터가 있어야 드러난다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
