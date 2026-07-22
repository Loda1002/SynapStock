"""시세 피드 (읽기전용).

MVP 는 결정적 목(mock) 피드로 시작한다. 실제 시세로 교체하려면 get_price 만
바꾸면 된다(예: Alpaca/무료 quotes API, 또는 Solana 네이티브 오라클 Pyth).

주의: 이 값은 매매 판단 입력일 뿐, 투자 조언이 아니다. 데모용 규칙에 사용.
"""
from __future__ import annotations
from decimal import Decimal
from typing import Dict, List


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
        return (base * mult).quantize(Decimal("0.01"))

    def peek(self, symbol: str, tick: int) -> Decimal:
        base = self.base.get(symbol, Decimal("100"))
        return (base * self._path[tick % len(self._path)]).quantize(Decimal("0.01"))


# --- 실제 시세로 교체할 때 참고 스텁 ---
# class AlpacaPriceFeed(PriceFeed):
#     """Alpaca market data API (페이퍼/무료) 로 실시간 시세."""
#     def get_price(self, symbol): ...
#
# class PythPriceFeed(PriceFeed):
#     """Solana 온체인 오라클 Pyth 로 시세 조회."""
#     def get_price(self, symbol): ...
