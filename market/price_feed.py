"""시세 피드 (읽기전용).

- MockPriceFeed   : 결정적 목 시세 (10스텝 반복) — 규칙 트리거 데모·오프라인 폴백용
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
        # 데모용 가격 경로(지표 규칙 dip3/profit5 기준 재설계, 2026-07-25).
        # 목 피드는 워밍업 주입이 없어 첫 4틱은 MA5 미성립(보류)이므로, 앞을 평탄하게 두어
        # 워밍업을 마친 뒤(인덱스 5~6) MA5 대비 -3% 이상 깊은 눌림목이 오게 하고
        # (매수 트리거), 이어 인덱스 8~9 에서 평단 +5% 를 넘는 반등이 오게 한다(익절 트리거).
        # 더 느슨한 dip2/profit3(run_demo)도 자동으로 트리거된다.
        self._path: List[Decimal] = [
            Decimal("1.00"), Decimal("1.00"), Decimal("1.00"), Decimal("0.99"),
            Decimal("0.98"), Decimal("0.95"), Decimal("0.93"), Decimal("0.97"),
            Decimal("1.02"), Decimal("1.04"),
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


def _seg_interp(wp: List[Decimal], t: Decimal) -> Decimal:
    """4개 웨이포인트(3구간 균등)를 지나는 조각선형 보간. t in (0,1], t=1 → 마지막 값."""
    x = t * 3
    seg = int(x)
    if seg > 2:
        seg = 2                     # t=1 이면 x=3 → 마지막 구간의 끝(=wp[3])
    local = x - seg
    a, c = wp[seg], wp[seg + 1]
    return a + (c - a) * local


class IntradayReplayFeed(ReplayPriceFeed):
    """일봉 CSV 를 하루당 sub 개의 '합성 인트라바'로 확장해 재생한다.

    실제 일봉(시가·고가·저가·종가)을 지나는 결정론적 경로를 만든다:
      상승일(종가≥시가): 시가 → 저가 → 고가 → 종가 (눌림목 후 반등)
      하락일(종가<시가) : 시가 → 고가 → 저가 → 종가
    하루의 마지막 인트라바 종가는 실제 일봉 종가와 정확히 같아, '일 단위 궤적'은
    실데이터 그대로다(하위 경로만 합성). 이동평균(MA5/MA20)이 인트라바 단위로 계산돼
    아주 짧은 시간의 변동에도 신호가 반응한다 — 대신 봉 수가 늘어 세션이 길어지고
    잦은 매매(휩쏘)가 늘 수 있다.

    주의: 인트라바는 '합성(가상)'이며 데모 재현용이다. 실제 분/시간봉이 아니다
    (일봉 자체는 실데이터). start/end/warmup 슬라이싱은 일 단위로 먼저 하고 확장한다.
    """

    def __init__(self, csv_path: str, start: str = "", end: str = "",
                 warmup: int = 20, sub: int = 8):
        super().__init__(csv_path, start=start, end=end, warmup=warmup)
        self.sub = max(1, int(sub))
        if self.sub > 1:
            self._bars = self._explode(self._bars, self.sub)
            self.warmup_bars = self._explode(self.warmup_bars, self.sub)
            days = self.total_bars // self.sub
            self.source_label = (
                f"{self.symbol_name} 인트라바 재생(합성 {self.sub}봉/일) "
                f"{self._bars[0].date}~{self._bars[-1].date} "
                f"({self.total_bars}봉≈{days}일)")

    @staticmethod
    def _explode(bars: List[Bar], sub: int) -> List[Bar]:
        """일봉 리스트를 하루당 sub 개의 인트라바로 확장(각 종가는 O/H/L/C 경로 위의 점)."""
        out: List[Bar] = []
        for b in bars:
            up = b.close >= b.open
            wp = [b.open, b.low, b.high, b.close] if up else [b.open, b.high, b.low, b.close]
            prev = b.open
            for k in range(sub):
                t = Decimal(k + 1) / Decimal(sub)      # (0,1], 마지막은 정확히 종가
                price = _seg_interp(wp, t)
                hi, lo = max(prev, price), min(prev, price)
                out.append(Bar(
                    date=b.date, open=prev.quantize(_CENT), high=hi.quantize(_CENT),
                    low=lo.quantize(_CENT), close=price.quantize(_CENT)))
                prev = price
        return out


# --- 라이브 시세로 확장할 때 참고 스텁 ---
# class AlpacaPriceFeed(PriceFeed):
#     """Alpaca market data API (페이퍼/무료) 로 실시간 시세 — 실패 시 Replay 폴백."""
#     def get_price(self, symbol): ...
#
# class PythPriceFeed(PriceFeed):
#     """Solana 온체인 오라클 Pyth 로 시세 조회."""
#     def get_price(self, symbol): ...
