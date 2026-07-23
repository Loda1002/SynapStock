"""시세 피드 (읽기전용).

- MockPriceFeed   : 결정적 목 시세 (8스텝 반복) — 규칙 트리거 데모·오프라인 폴백용
- ReplayPriceFeed : 실데이터 CSV(일봉)를 순서대로 재생 — 기본 피드
  · 입력: scripts/fetch_market_data.py 가 저장한 data/market/{SYMBOL}_daily.csv
  · 1틱 = 1봉 (시가·고가·저가·종가 그대로 캔들이 됨)
  · 재생 시작 전 warmup 봉(기본 20개)을 따로 제공 — MA5/MA20 지표가 첫 틱부터 계산됨
  · 데이터가 끝나면 exhausted=True — 엔진이 세션을 자동 종료한다

주의: 이 값은 매매 판단 입력일 뿐, 투자 조언이 아니다. 데모용 규칙에 사용.
"""
from __future__ import annotations
import csv
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

_CENT = Decimal("0.01")


class PriceFeed:
    def get_price(self, symbol: str) -> Decimal:  # pragma: no cover - 인터페이스
        raise NotImplementedError


class MockPriceFeed(PriceFeed):
    """기준가 주변을 오르내리는 결정적 시세(시드 기반). 데모에서 규칙 트리거 확인용."""

    def __init__(self, base: Dict[str, Decimal] | None = None):
        self.base = base or {"tAAPL": Decimal("180"), "tTSLA": Decimal("250")}
        self._tick = 0
        # 데모용 가격 경로: 하락(매수 구간) → 반등·급등(매도 구간) → 안정
        # 틱 7 = 기준가×1.04 (tAAPL 187.20) 가 매도기준(185)을 넘도록 설계
        self._path: List[Decimal] = [
            Decimal("1.00"), Decimal("0.985"), Decimal("0.97"),
            Decimal("0.96"), Decimal("0.975"), Decimal("1.01"),
            Decimal("1.04"), Decimal("0.995"),
        ]

    def get_price(self, symbol: str) -> Decimal:
        base = self.base.get(symbol, Decimal("100"))
        mult = self._path[self._tick % len(self._path)]
        self._tick += 1
        return (base * mult).quantize(_CENT)

    def peek(self, symbol: str, tick: int) -> Decimal:
        base = self.base.get(symbol, Decimal("100"))
        return (base * self._path[tick % len(self._path)]).quantize(_CENT)


# ---------- 실데이터 재생 ----------

@dataclass(frozen=True)
class Bar:
    """일봉 하나 — CSV 한 행. 가격은 USDC 센트(소수 2자리)로 정규화."""
    date: str            # YYYY-MM-DD
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0


def load_bars(csv_path: str) -> List[Bar]:
    """fetch_market_data.py 형식(date,open,high,low,close,volume)의 CSV → Bar 리스트(오름차순)."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"시세 CSV 가 없습니다: {csv_path} — 먼저 `python scripts/fetch_market_data.py` 실행")
    bars: List[Bar] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                bars.append(Bar(
                    date=row["date"],
                    open=Decimal(row["open"]).quantize(_CENT),
                    high=Decimal(row["high"]).quantize(_CENT),
                    low=Decimal(row["low"]).quantize(_CENT),
                    close=Decimal(row["close"]).quantize(_CENT),
                    volume=int(float(row.get("volume") or 0)),
                ))
            except (KeyError, ArithmeticError, ValueError) as e:
                raise ValueError(f"CSV 형식 오류({csv_path}): {row} — {e}")
    if not bars:
        raise ValueError(f"빈 시세 CSV: {csv_path}")
    bars.sort(key=lambda b: b.date)
    return bars


class ReplayPriceFeed(PriceFeed):
    """실데이터 CSV 를 1틱=1봉으로 순서 재생. 결정적 — 같은 구간은 항상 같게 흐른다.

    start/end(YYYY-MM-DD, 포함)로 재생 구간을 고정할 수 있어 데모·백테스트 재현에 쓴다.
    warmup_bars 는 재생 시작 직전 N봉 — 이동평균(MA20) 워밍업과 차트 사전 이력용.
    """

    def __init__(self, csv_path: str, start: str = "", end: str = "", warmup: int = 20):
        bars = load_bars(csv_path)
        if start:
            i = next((k for k, b in enumerate(bars) if b.date >= start), len(bars))
        else:
            i = min(max(warmup, 0), max(len(bars) - 1, 0))  # 시작일 미지정 → 워밍업 이후부터
        j = len(bars)
        if end:
            j = next((k for k in range(len(bars), 0, -1) if bars[k - 1].date <= end), 0)
        self._bars: List[Bar] = bars[i:j]
        if not self._bars:
            raise ValueError(
                f"재생 구간이 비어 있습니다 (start={start or '-'} end={end or '-'}, "
                f"데이터 {bars[0].date}~{bars[-1].date})")
        self.warmup_bars: List[Bar] = bars[max(0, i - max(warmup, 0)):i]
        self._idx = 0
        self.last_bar: Optional[Bar] = None
        base = os.path.basename(csv_path)
        self.symbol_name = base.split("_")[0] if "_" in base else base
        self.source_label = (f"{self.symbol_name} 일봉 재생 "
                             f"{self._bars[0].date}~{self._bars[-1].date} ({len(self._bars)}봉)")

    @property
    def exhausted(self) -> bool:
        return self._idx >= len(self._bars)

    @property
    def total_bars(self) -> int:
        return len(self._bars)

    @property
    def played_bars(self) -> int:
        return self._idx

    def get_price(self, symbol: str) -> Decimal:
        if self.exhausted:
            raise RuntimeError("재생 데이터 소진 — exhausted 를 먼저 확인하세요")
        bar = self._bars[self._idx]
        self._idx += 1
        self.last_bar = bar
        return bar.close


# --- 라이브 시세로 확장할 때 참고 스텁 ---
# class AlpacaPriceFeed(PriceFeed):
#     """Alpaca market data API (페이퍼/무료) 로 실시간 시세 — 실패 시 Replay 폴백."""
#     def get_price(self, symbol): ...
#
# class PythPriceFeed(PriceFeed):
#     """Solana 온체인 오라클 Pyth 로 시세 조회."""
#     def get_price(self, symbol): ...
