"""시세 데이터 1회 수집 — Alpha Vantage 일봉 → data/market/{SYMBOL}_daily.csv

사용법 (프로젝트 루트에서):
  python scripts/fetch_market_data.py                        # 기본: AAPL TSLA NVDA, 2024-01-01 이후
  python scripts/fetch_market_data.py --symbols AAPL --since 2023-01-01

- 무료 키 발급: https://www.alphavantage.co/support/#api-key (이메일만, 즉시)
  → .env 에 `ALPHAVANTAGE_API_KEY=발급키` 추가
- 무료 한도: 25콜/일 · 5콜/분 → 종목당 1콜이면 충분, 종목 사이 15초 대기
- 저장 형식: date,open,high,low,close,volume (과거→최근 오름차순, UTF-8)
- 재실행하면 기존 파일을 덮어쓴다(최신 구간까지 갱신).

이 데이터는 ReplayPriceFeed(재생 피드)의 입력이다 — 심사 재현성을 위해
data/market/*.csv 는 저장소에 커밋한다(출처: Alpha Vantage, README 표기).
"""
from __future__ import annotations
import argparse
import csv
import io
import os
import sys
import time
import urllib.parse
import urllib.request

# 프로젝트 루트에서 실행해도, scripts/ 에서 실행해도 동작하게 경로 보정
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import CFG  # noqa: E402  (.env 로드 포함)

API_URL = "https://www.alphavantage.co/query"
OUT_DIR = os.path.join(ROOT, "data", "market")


def fetch_daily_csv(symbol: str, api_key: str) -> list[dict]:
    """Alpha Vantage TIME_SERIES_DAILY(무료) 전체 이력 → 행 dict 리스트(오름차순)."""
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "full",
        "datatype": "csv",
        "apikey": api_key,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "AutoTraderAgent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()

    # 오류는 datatype=csv 여도 JSON 으로 온다 (한도 초과 Information / 잘못된 키 Error Message)
    if body.startswith("{"):
        raise RuntimeError(f"{symbol}: API 오류 응답 — {body[:300]}")

    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows or "timestamp" not in rows[0]:
        raise RuntimeError(f"{symbol}: 예상 밖 응답 형식 — 첫 줄: {body.splitlines()[0][:120]}")
    rows.sort(key=lambda r: r["timestamp"])  # API 는 최신→과거 순이라 뒤집는다
    return rows


def save_csv(symbol: str, rows: list[dict], since: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{symbol}_daily.csv")
    kept = [r for r in rows if r["timestamp"] >= since]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in kept:
            w.writerow([r["timestamp"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
    print(f"  저장: {os.path.relpath(path, ROOT)} — {len(kept)}봉 "
          f"({kept[0]['timestamp']} ~ {kept[-1]['timestamp']}, "
          f"종가 {kept[0]['close']} → {kept[-1]['close']})")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Alpha Vantage 일봉 수집 → data/market/*.csv")
    ap.add_argument("--symbols", default="AAPL,TSLA,NVDA", help="쉼표 구분 (기본 AAPL,TSLA,NVDA)")
    ap.add_argument("--since", default="2024-01-01", help="이 날짜 이후만 저장 (기본 2024-01-01)")
    args = ap.parse_args()

    api_key = CFG.alphavantage_api_key
    if not api_key:
        print("ALPHAVANTAGE_API_KEY 가 없습니다.\n"
              "  1) https://www.alphavantage.co/support/#api-key 에서 무료 발급(이메일만)\n"
              "  2) .env 에 `ALPHAVANTAGE_API_KEY=발급키` 한 줄 추가 후 재실행")
        return 1

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"수집 시작: {', '.join(symbols)} (일봉, {args.since} 이후, 무료 한도 25콜/일)")
    for i, sym in enumerate(symbols):
        if i > 0:
            time.sleep(15)  # 무료 한도 5콜/분 보호
        try:
            rows = fetch_daily_csv(sym, api_key)
        except Exception as e:
            print(f"  실패: {e}")
            return 1
        save_csv(sym, rows, args.since)
    print("완료 — ReplayPriceFeed 가 이 CSV 를 재생합니다 (.env: PRICE_FEED=replay)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
