"""하락장 검증용 일봉 수신 — 2022 약세장 + 2023 회복 (추세추종 강점 실증용).

무료 Alpha Vantage(fetch_market_data.py)는 최근 100봉(compact)만 줘서 과거 약세장을
받을 수 없다. 이 스크립트는 yfinance(공개 Yahoo 시세, 무료·키 불필요, 분할·배당 조정)로
2022-01~2023-12 일봉을 받아 우리 형식 CSV(data/market/{SYMBOL}_bear.csv)로 저장한다.

개발/검증 전용 도구다(런타임 미사용). 실행 전 `pip install yfinance` 필요.
용도: `python scripts/explore_trend.py --suffix _bear --windows 60,120` 로 추세추종 vs 매수후보유 실측.

사용:
  python scripts/fetch_bear_data.py                         # AAPL,TSLA,NVDA 2022~2023
  python scripts/fetch_bear_data.py --symbols AAPL --from 2020-01-01 --to 2020-06-30
"""
from __future__ import annotations
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser(description="하락장 일봉 수신(yfinance)")
    ap.add_argument("--symbols", default="AAPL,TSLA,NVDA")
    ap.add_argument("--from", dest="start", default="2022-01-01")
    ap.add_argument("--to", dest="end", default="2023-12-31")
    ap.add_argument("--suffix", default="_bear")
    args = ap.parse_args()
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance 미설치 — `pip install yfinance` 후 재실행")
        return 1

    out = os.path.join(ROOT, "data", "market")
    os.makedirs(out, exist_ok=True)
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        df = yf.download(sym, start=args.start, end=args.end, auto_adjust=True, progress=False)
        if df is None or df.empty:
            print(f"{sym}: 수신 실패(빈 데이터)")
            continue
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
        path = os.path.join(out, f"{sym}{args.suffix}.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("date,open,high,low,close,volume\n")
            for idx, row in df.iterrows():
                v = int(row["Volume"]) if row["Volume"] == row["Volume"] else 0
                f.write(f"{idx.strftime('%Y-%m-%d')},{round(float(row['Open']),2)},"
                        f"{round(float(row['High']),2)},{round(float(row['Low']),2)},"
                        f"{round(float(row['Close']),2)},{v}\n")
        print(f"{sym}: {len(df)}봉 {df.index[0].date()}~{df.index[-1].date()} -> "
              f"{os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
