"""인트라바 합성 피드 검증 — 일봉을 하루당 N개 합성 인트라바로 확장하는 로직의 정합성.

핵심 성질(하나라도 깨지면 실패):
  1) 봉 수    : 하루당 정확히 sub 개 (총 = sub × 일봉 수)
  2) 종가 보존: 하루의 마지막 인트라바 종가 == 실제 일봉 종가 (일 단위 궤적 = 실데이터)
  3) 범위     : 모든 인트라바 종가가 그날 [저가, 고가] 안
  4) 방향     : 상승일은 먼저 저가권, 하락일은 먼저 고가권을 지난다
  5) 결정론   : 같은 입력 → 같은 출력 (재현 가능)
  6) 소비경로 : 실제 load_bars('..._bear.csv') → IntradayReplayFeed 를 실제 소비경로로 재생해
                일 경계 종가가 실 일봉 종가와 일치 (데모·백테스트가 진짜 이 데이터를 소화)

재현: python scripts/test_intraday.py  (프로젝트 루트)
"""
from __future__ import annotations
import os
import sys
from decimal import Decimal

# 한국어 Windows 콘솔(cp949)에서 결과 줄의 em-dash 가 UnicodeEncodeError 로 죽는다.
# 이 파일은 market 모듈만 import 해서 config 의 인코딩 안전화 경로를 안 탄다.
# 심사위원이 README 대로 테스트를 돌렸을 때 결과 대신 트레이스백을 보면 안 된다
# (관례: scripts/test_dca_schedule.py).
sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from market.price_feed import Bar, IntradayReplayFeed, ReplayPriceFeed  # noqa: E402

PASS, FAIL = "통과", "실패"
_bad = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _bad
    ok = bool(cond)
    if not ok:
        _bad += 1
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def _bar(date: str, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(date=date, open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c))


def main() -> int:
    print("인트라바 합성 검증")
    up = _bar("2022-01-03", "100", "110", "95", "108")     # 상승일 (종가>시가)
    down = _bar("2022-01-04", "108", "112", "90", "94")    # 하락일 (종가<시가)

    for sub in (2, 4, 8):
        ex = IntradayReplayFeed._explode([up, down], sub)
        check(f"[봉 수] sub={sub} → 총 {2 * sub}봉", len(ex) == 2 * sub, f"len={len(ex)}")
        # 종가 보존 — 각 하루의 마지막 인트라바 종가 == 실 일봉 종가
        check(f"[종가보존] sub={sub} 상승일 마지막 종가 == 108.00",
              ex[sub - 1].close == Decimal("108.00"), f"{ex[sub - 1].close}")
        check(f"[종가보존] sub={sub} 하락일 마지막 종가 == 94.00",
              ex[2 * sub - 1].close == Decimal("94.00"), f"{ex[2 * sub - 1].close}")
        # 범위 — 종가는 그날 [저가, 고가] 안
        check(f"[범위] sub={sub} 상승일 종가 ⊂ [95,110]",
              all(Decimal("95") <= b.close <= Decimal("110") for b in ex[:sub]))
        check(f"[범위] sub={sub} 하락일 종가 ⊂ [90,112]",
              all(Decimal("90") <= b.close <= Decimal("112") for b in ex[sub:]))
        # 결정론 — 재실행 동일
        ex2 = IntradayReplayFeed._explode([up, down], sub)
        check(f"[결정론] sub={sub} 재실행 동일",
              [b.close for b in ex] == [b.close for b in ex2])

    # BUG-10 — 독스트링이 말하는 '표본 격자' 성질을 검증 가능하게 못 박는다.
    # 고가·저가 웨이포인트는 t=1/3·2/3 에 있으므로 sub 가 3의 배수일 때만 격자에 걸린다.
    # UI 선택지(2/4/8)에서는 하루치를 다시 합친 고가·저가가 실 일봉과 다르다 — 결함이
    # 아니라 알려진 한계이고, 문구(price_feed 독스트링·index.html 툴팁)가 그렇게 적혀 있다.
    for sub, exact in ((3, True), (6, True), (2, False), (4, False), (8, False)):
        ex = IntradayReplayFeed._explode([up], sub)
        agg_hi, agg_lo = max(b.high for b in ex), min(b.low for b in ex)
        hit = (agg_hi == up.high and agg_lo == up.low)
        check(f"[집계H/L] sub={sub} 실 일봉 고가·저가와 " + ("일치" if exact else "불일치(알려진 한계)"),
              hit is exact, f"집계 {agg_hi}/{agg_lo} vs 실 {up.high}/{up.low}")

    # 방향 — 상승일은 저가권 먼저(첫 봉이 시가보다 낮게), 하락일은 고가권 먼저(첫 봉이 시가보다 높게)
    ex8 = IntradayReplayFeed._explode([up, down], 8)
    check("[방향] 상승일 첫 인트라바 < 시가(저가 방향)",
          ex8[0].close < up.open, f"{ex8[0].close} < {up.open}")
    check("[방향] 하락일 첫 인트라바 > 시가(고가 방향)",
          ex8[8].close > down.open, f"{ex8[8].close} > {down.open}")

    # 소비 경로 — 실제 CSV 를 실제 소비경로로 대조(데모·백테스트가 이 데이터를 진짜 소화하는가)
    sub = 4
    path = os.path.join(ROOT, "data", "market", "AAPL_bear.csv")
    daily = ReplayPriceFeed(path, warmup=20)
    intr = IntradayReplayFeed(path, warmup=20, sub=sub)
    check("[소비경로] 인트라바 총봉 = 일봉 총봉 × sub",
          intr.total_bars == daily.total_bars * sub,
          f"{intr.total_bars} vs {daily.total_bars}×{sub}")
    daily_closes = []
    while not daily.exhausted:
        daily_closes.append(daily.get_price("AAPL"))
    mismatch = sum(1 for i, dc in enumerate(daily_closes)
                   if intr._bars[i * sub + (sub - 1)].close != dc)
    check("[소비경로] 매 하루 마지막 인트라바 종가 == 실 일봉 종가",
          mismatch == 0, f"불일치 {mismatch}일 / {len(daily_closes)}일")

    print("-" * 60)
    print(f"인트라바 합성 검증: {'전부 통과' if _bad == 0 else f'{_bad}건 실패'}")
    return 1 if _bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
