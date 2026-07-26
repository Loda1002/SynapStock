"""Trading (구매) 에이전트 — 사용자 대리.

책임: 시세+규칙으로 매수/매도 판단 → AP2 한도 승인 → 결제 트랜잭션 서명(x402)
→ payment-completed 반영(포지션 갱신).

`decide()` 의 규칙 부분이 이후 Gemini(ADK) 로 교체될 지점이다.
현재는 사용자 정의 임계값 규칙(데모용, 투자 조언 아님).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash

from config import from_base_units
from market.price_feed import Bar
from market.indicators import ta_summary
from shared.models import Position, Receipt
from shared.a2a_messages import (
    PaymentRequired, PaymentSubmitted, PaymentPayload, PaymentCompleted,
)
from payments import x402_solana as x
from payments.ap2_mandate import PaymentAuthorizer, MandateError


@dataclass
class Decision:
    action: str          # "buy" / "sell" / "hold"
    reason: str
    spend_usdc: Decimal = Decimal(0)
    # 판단 출처 — "rule"(지표 규칙·추세 신호) / "gemini"(AI) / "rule-fallback"(AI 호출 실패)
    # / "rule-gate"(AI 가 규칙 밖 개시를 시도해 코드가 보류로 강등) / "dca"(적립 스케줄)
    source: str = "rule"


@dataclass
class Strategy:
    """매매 전략 — condition(조건형, 지표 규칙+Gemini 판단) / dca(적립형, 주기 정액 매수)
    / trend(추세추종, 상승세 전량 보유·하락세 전량 매도).

    조건형 규칙(2026-07-23 지표 기준 전환 — 절대가 178/185 는 목 시세 전용이었음):
      매수 = 현재가가 5일 이동평균(MA5) 대비 buy_dip_pct% 이상 낮을 때 (싸졌을 때)
      매도 = 현재가가 보유 평균단가 대비 take_profit_pct% 이상 높을 때 (익절)
    어떤 종목·가격대에도 동작하며, % 값은 사용자 설정값이다.

    B7: 적립형은 판단 없이 주기마다 정액 매수만 한다(매도 없음).
    주기 기준(dca_unit)은 사람이 고른다 — ticks(N틱마다) / minutes(N분마다) /
    daily(매일 지정 시각). AP2 mandate 검사는 어느 모드든 같은 결제 경로를 지난다.

    추세추종(mode="trend", 2026-07-25 하락장 실증 후 추가 — 사용자 진짜 의도):
      상승세(가격 ≥ MA20, 또는 골든크로스 MA5≥MA20)면 '전량 보유', 하락세로 꺾이면
      '전량 매도'(자본 보존)로 빠져나온다. 짧은 하락은 무시하고 추세가 살아 있는 한 태운다.
      평균회귀(눌림목 익절)와 정반대. 검증(scripts/explore_trend.py --suffix _bear)에서
      하락장이 있는 전체 사이클에서 매수후보유를 크게 이김(하락 전 탈출로 손실 회피 +
      회복 재진입). trend_signal 로 판단 방식을 고른다. 올인/올아웃이라 매도 대금은
      운용현금으로 복리 재투자되고(credit_sale allow_surplus), 시간청산은 미적용이다."""
    buy_dip_pct: Decimal = Decimal("2")       # 매수: MA5 대비 −N%
    take_profit_pct: Decimal = Decimal("3")   # 매도: 평단 대비 +N%
    spend_per_trade_usdc: Decimal = Decimal("30")
    # Gemini 재량 범위 — strict(규칙 그대로 판정) / trend(신호가 떠도 추세 근거로 보류 가능).
    # 어느 모드든 신규 매수·매도의 "개시"는 규칙 신호가 필요하고, AP2 한도 검사는 불변.
    decision_mode: str = "strict"
    # TA 보강(2026-07-23 매매 기준 개선): MA 배열·크로스·지지/저항·차트/캔들 패턴을
    # 코드로 계산해 판단(Gemini 프롬프트 + 규칙 폴백)에 주입. 백테스트로 개선이
    # 확인되기 전에는 기본 OFF 를 유지한다(message 가드레일). 실데이터 재생 전용.
    ta_mode: bool = False
    mode: str = "condition"                    # "condition" / "dca" / "trend"
    # 추세추종 판단 방식 — "pxma20"(가격 ≥ MA20) / "cross_5_20"(골든크로스 MA5≥MA20).
    # 검증 최선안(strategy_validation.md): 단순한 pxma20 이 기본, 골든크로스5/20 이 대안.
    trend_signal: str = "pxma20"
    dca_unit: str = "ticks"                    # "ticks" / "minutes" / "daily"
    dca_every_ticks: int = 5                   # ticks: N틱마다
    dca_every_minutes: int = 60                # minutes: N분마다
    dca_at_time: str = "09:00"                 # daily: 매일 HH:MM (서버 로컬 시각)
    dca_amount_usdc: Decimal = Decimal("10")   # 적립형: 회당 정액
    # 시간 기반 청산(안전레일) — 조건형에서 포지션을 max_hold_bars 봉 이상 보유하면
    # 규칙/Gemini 판단보다 우선해 전량 자동 청산한다(0=비활성). 미실현 손실에 무한정
    # 갇히는 것을 막아 꼬리 위험을 줄인다(검증 실측: AAPL 최악 -6.5%→-0.2%, MDD 7.3→1.3%).
    max_hold_bars: int = 0


class TradingAgent:
    def __init__(
        self,
        keypair: Keypair,
        authorizer: PaymentAuthorizer,
        strategy: Strategy,
        usdc_decimals: int,
        network: str,
        brain=None,       # GeminiDecider (없으면 규칙 기반)
        fee_bps: int = 0,  # A8 브로커 수수료 — Gemini 에 실효 가격 근거로 제공
    ):
        self.kp = keypair
        self.auth = authorizer
        self.strategy = strategy
        self.usdc_decimals = usdc_decimals
        self.network = network
        self.position = Position(symbol="")
        self.brain = brain
        self.fee_bps = fee_bps
        # 402 Guard — 서명 직전 청구서 검증 게이트(엔진/run_demo 가 주입). None 이면 미적용.
        self.guard = None
        self._history: list[Decimal] = []  # 직전 시세 (지표 계산·Gemini 판단 근거)
        self.HISTORY_MAX = 210             # MA200 계산 + 기울기 여유분 (TA 보강)
        self._bars: list[Bar] = []         # OHLC 봉 이력 — 캔들·패턴·지지/저항 탐지용
        self._last_action: Optional[dict] = None  # 직전 매수/매도 회고 (Gemini 프롬프트용)
        self._dca_tick = 0                 # B7 적립형(ticks): 다음 매수까지 틱 카운터
        self._dca_round = 0                # B7 적립형: 누적 회차
        self._dca_next_at: Optional[datetime] = None  # 적립형(minutes): 다음 집행 시각
        self._dca_last_date = ""           # 적립형(daily): 마지막 집행 날짜
        self._bars_held = 0                # 조건형 시간청산용 — 현재 포지션 연속 보유 봉 수
        self._now = datetime.now           # 테스트에서 가짜 시계로 교체 가능

    @property
    def pubkey(self) -> Pubkey:
        return self.kp.pubkey()

    def preload_history(self, prices: list[Decimal]) -> None:
        """재생 피드의 워밍업 봉 종가를 주입 — 첫 틱부터 MA5/MA20 이 계산되게 한다."""
        self._history = list(prices)[-self.HISTORY_MAX:]

    def preload_bars(self, bars: list[Bar]) -> None:
        """워밍업 봉을 OHLC 째로 주입 — 종가 이력과 TA 봉 이력을 함께 채운다."""
        self._bars = list(bars)[-self.HISTORY_MAX:]
        self.preload_history([b.close for b in self._bars])

    # ---------- 지표 (규칙·Gemini 판단 공용) ----------

    def _ma(self, period: int) -> Optional[Decimal]:
        """단순 이동평균 — _history(현재가 포함) 마지막 period 개. 부족하면 None."""
        if len(self._history) < period:
            return None
        window = self._history[-period:]
        return (sum(window) / Decimal(period)).quantize(Decimal("0.01"))

    def indicators(self) -> dict:
        """현재 틱 지표 묶음 — decide() 안(현재가 append 이후)에서 호출한다.

        buy_threshold = MA5 × (1 − buy_dip_pct%) / take_profit = 평단 × (1 + take_profit_pct%).
        MA 미성립(워밍업)이나 무보유면 해당 값은 None. 등락률·변동성·평단 손익률은
        Gemini 판단 입력 보강용(퍼센트, 소수 2자리)."""
        s = self.strategy
        ma5, ma20 = self._ma(5), self._ma(20)
        buy_th = ((ma5 * (1 - s.buy_dip_pct / 100)).quantize(Decimal("0.01"))
                  if ma5 is not None else None)
        pos = self.position
        tp = ((pos.avg_price_usdc * (1 + s.take_profit_pct / 100)).quantize(Decimal("0.01"))
              if pos.quantity > 0 and pos.avg_price_usdc > 0 else None)
        price = self._history[-1] if self._history else None

        def pct(now: Decimal, then: Decimal) -> Optional[Decimal]:
            return ((now / then - 1) * 100).quantize(Decimal("0.01")) if then else None

        # 최근 5봉 등락률 (현재가 vs 5봉 전 종가)
        change5 = (pct(price, self._history[-6])
                   if price is not None and len(self._history) >= 6 else None)
        # 변동성: 최근 10개 봉간 수익률의 표준편차(%) — 판단 참고용이라 float 계산으로 충분
        vol = None
        if len(self._history) >= 6:
            closes = [float(v) for v in self._history[-11:]]
            rets = [(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a]
            if len(rets) >= 3:
                mean = sum(rets) / len(rets)
                vol = Decimal(str(round((sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5, 2)))
        # 보유 평단 대비 손익률
        pos_pnl = (pct(price, pos.avg_price_usdc)
                   if price is not None and pos.quantity > 0 and pos.avg_price_usdc > 0 else None)
        ind = {"ma5": ma5, "ma20": ma20, "buy_threshold": buy_th, "take_profit": tp,
               "change5_pct": change5, "volatility_pct": vol, "position_pnl_pct": pos_pnl}
        if s.ta_mode and self._bars:
            # TA 보강 — MA 배열·크로스·지지/저항·패턴을 코드로 계산 (판단자 공용 입력)
            ind["ta"] = ta_summary(self._bars)
        return ind

    def _retrospective(self, price: Decimal) -> str:
        """직전 매수/매도 회고 한 줄 — '학습하는 것처럼' 직전 행동의 결과를 프롬프트에 준다."""
        a = self._last_action
        if not a:
            return "이번 세션 매수·매도 이력 없음"
        a["bars_ago"] += 1
        diff = ((price / a["price"] - 1) * 100).quantize(Decimal("0.01")) if a["price"] else 0
        side = "매수" if a["action"] == "buy" else "매도"
        return (f"{a['bars_ago']}봉 전 {side} @ {a['price']} USDC → 현재가는 그 대비 "
                f"{'+' if diff >= 0 else ''}{diff}%")

    # 1) 판단 — 적립형이면 스케줄 매수, 조건형이면 Gemini(있으면) → 실패 시 규칙 폴백
    def decide(self, symbol: str, price: Decimal, bar: Optional[Bar] = None) -> Decision:
        self.position.symbol = symbol
        self._history.append(price)
        if len(self._history) > self.HISTORY_MAX:
            self._history.pop(0)
        # TA 봉 이력 — 재생 피드는 실제 OHLC, 목 시세는 퇴화 봉(고=저, 캔들 패턴 미탐지)
        self._bars.append(bar if bar is not None
                          else Bar(date="", open=price, high=price, low=price, close=price))
        if len(self._bars) > self.HISTORY_MAX:
            self._bars.pop(0)

        if self.strategy.mode == "dca":
            return self._decide_dca()
        if self.strategy.mode == "trend":
            return self._decide_trend(price)

        ind = self.indicators()
        if ind["buy_threshold"] is None:
            # MA5 미성립 — 지표 워밍업 (재생 피드는 워밍업 주입으로 첫 틱부터 성립)
            return Decision("hold", f"지표 워밍업 — MA5 계산까지 {5 - len(self._history)}봉 더 필요")

        # 시간 기반 청산(안전레일) — 규칙/Gemini 판단보다 우선하는 백스톱.
        # 포지션을 max_hold_bars 봉 이상 보유하면 전량 자동 청산해, 미실현 손실에
        # 무한정 갇히는 것을 막는다(꼬리 위험 축소). max_hold_bars=0 이면 비활성.
        if self.position.quantity > 0:
            self._bars_held += 1
        else:
            self._bars_held = 0
        if (self.strategy.max_hold_bars > 0 and self.position.quantity > 0
                and self._bars_held >= self.strategy.max_hold_bars):
            self._last_action = {"action": "sell", "price": price, "bars_ago": 0}
            return Decision(
                "sell", f"시간청산(안전레일) — {self._bars_held}봉 보유 후 자동 청산", source="rule")

        retro = self._retrospective(price)
        if self.brain is not None:
            try:
                raw = self.brain.decide(
                    symbol, price, self._history[-9:-1], self.strategy,
                    self.auth.remaining_usdc, self.position,
                    fee_bps=self.fee_bps, indicators=ind, retrospective=retro,
                )
            except Exception as e:
                d = self._decide_by_rule(symbol, price, ind)
                d.source = "rule-fallback"
                # 실제 원인 표면화 (예: 429 쿼터 초과). 두뇌가 이미 한 줄로 요약해 올리므로
                # 여기서 잘려도 사유가 남는다 — 예전엔 100자에서 잘려 quotaId 가 통째로
                # 사라졌고, 분당 초과인지 일일 소진인지 화면에서 구분할 수 없었다.
                detail = str(e).replace("\n", " ")[:200]
                d.reason += f" — Gemini 호출 실패({type(e).__name__}: {detail}) → 규칙 폴백"
            else:
                # 한도 클램프(_sanitize) → 규칙 게이트 순서.
                # 게이트는 "규칙 신호 없는 개시"를 코드로 막는다(프롬프트 의존 제거).
                d = self._rule_gate(self._sanitize(raw), price, ind)
        else:
            d = self._decide_by_rule(symbol, price, ind)
        if d.action in ("buy", "sell"):
            self._last_action = {"action": d.action, "price": price, "bars_ago": 0}
        return d

    # B7 적립형 — 가격 판단 없이 주기(틱/분/매일 시각)마다 정액 매수 (매도 없음)
    def _dca_due(self) -> tuple[bool, str]:
        """이번 틱이 적립 시점인지 — (실행 여부, 대기 사유)."""
        s = self.strategy
        if s.dca_unit == "minutes":
            every = max(1, s.dca_every_minutes)
            now = self._now()
            if self._dca_next_at is None:
                self._dca_next_at = now          # 세션 시작 직후 1회차를 바로 집행
            if now < self._dca_next_at:
                left = int((self._dca_next_at - now).total_seconds())
                return False, f"적립 대기 — 다음 정액 매수까지 {left // 60}분 {left % 60}초"
            self._dca_next_at = now + timedelta(minutes=every)
            return True, ""
        if s.dca_unit == "daily":
            now = self._now()
            try:
                hh, mm = (int(v) for v in s.dca_at_time.split(":"))
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                return False, f"적립 보류 — 시각 형식 오류({s.dca_at_time}), HH:MM 이어야 합니다"
            today = now.strftime("%Y-%m-%d")
            if self._dca_last_date == today:
                return False, f"적립 대기 — 오늘({today}) {s.dca_at_time} 정액 매수 완료, 내일 재개"
            if now < target:
                return False, f"적립 대기 — 매일 {s.dca_at_time} 정액 매수 (오늘 아직 미도래)"
            self._dca_last_date = today
            return True, ""
        every = max(1, s.dca_every_ticks)        # 기본: 틱 기준
        self._dca_tick += 1
        if self._dca_tick < every:
            return False, f"적립 대기 — 다음 정액 매수까지 {every - self._dca_tick}틱"
        self._dca_tick = 0
        return True, ""

    def _decide_dca(self) -> Decision:
        amount = self.strategy.dca_amount_usdc
        due, wait_reason = self._dca_due()
        if not due:
            return Decision("hold", wait_reason, source="dca")
        if self.auth.remaining_usdc <= 0:
            return Decision("hold", "적립 보류 — 예산 소진", source="dca")
        if amount > self.auth.remaining_usdc:
            return Decision(
                "hold",
                f"적립 보류 — 잔여 예산 {self.auth.remaining_usdc} < 정액 {amount} USDC",
                source="dca")
        self._dca_round += 1
        return Decision(
            "buy",
            f"적립식 {self._dca_round}회차 — {self.dca_schedule_label()} {amount} USDC 정액 매수",
            amount, source="dca")

    def dca_schedule_label(self) -> str:
        """사람이 읽는 적립 주기 문구 (타임라인·로그·UI 공용)."""
        s = self.strategy
        if s.dca_unit == "minutes":
            return f"{s.dca_every_minutes}분마다"
        if s.dca_unit == "daily":
            return f"매일 {s.dca_at_time}"
        return f"{s.dca_every_ticks}틱마다"

    # 추세추종 (mode="trend") — 상승세 전량 보유 · 하락세 전량 매도(자본 보존) · 재상승 재매수
    def _trend_ma(self, period: int) -> Optional[Decimal]:
        """추세 판단용 이동평균 — 검증 도구(scripts/explore_trend.py)와 동일하게 라운딩 없이
        계산한다. 조건형의 _ma 는 0.01 로 반올림하지만, 추세 신호는 가격이 MA 경계에 걸칠 때
        판정이 갈리므로 탐색 도구와 같은 정밀도를 써야 진입/청산 시점이 정확히 재현된다."""
        if len(self._history) < period:
            return None
        window = self._history[-period:]
        return sum(window) / Decimal(period)

    def _decide_trend(self, price: Decimal) -> Decision:
        """올인/올아웃 추세추종. 상승세면 예산 전액 진입해 태우고, 하락세로 꺾이면 전량 청산.

        판단은 결정론적 규칙 신호(Gemini 미사용)라 검증(explore_trend)이 그대로 재현된다.
        - pxma20         : 가격 ≥ MA20 = 상승세(보유), 미만 = 하락세(청산)
        - cross_5_20     : MA5 ≥ MA20(골든크로스) = 상승세, 데드크로스 = 청산
        - cross_1_5      : 가격 ≥ MA5(1/5 골든크로스) = 상승세 — 아주 짧은 변동에 빠르게 반응
        - cross_5_20_1_5 : 5/20(큰 추세)'과' 1/5(가격≥MA5) 둘 다 상승일 때만 보유. 하나라도
                           꺾이면 청산 — 5/20 은 방향, 1/5 는 빠른 손절(작은 손실도 조기 차단).
        시간청산(max_hold_bars)은 적용하지 않는다 — 추세가 살아 있는 한 오래 태우는 게 핵심."""
        def q(v):
            return v.quantize(Decimal("0.01"))
        sig = self.strategy.trend_signal
        ma5, ma20 = self._trend_ma(5), self._trend_ma(20)
        if sig == "cross_1_5":
            if ma5 is None:
                return Decision(
                    "hold", f"추세 워밍업 — MA5 계산까지 {5 - len(self._history)}봉 더 필요",
                    source="rule")
            want_long = price >= ma5
            basis = f"가격 {price} vs MA5 {q(ma5)}"
            up, down = "가격≥MA5(1/5 골든)", "가격<MA5(1/5 데드)"
        elif sig in ("cross_5_20", "cross_5_20_1_5"):
            if ma20 is None:
                return Decision(
                    "hold", f"추세 워밍업 — MA20 계산까지 {20 - len(self._history)}봉 더 필요",
                    source="rule")
            big_up = ma5 >= ma20
            if sig == "cross_5_20_1_5":
                want_long = big_up and price >= ma5
                basis = f"5/20: MA5 {q(ma5)} vs MA20 {q(ma20)} · 1/5: 가격 {price} vs MA5 {q(ma5)}"
                up = "5/20↑ & 1/5↑(둘 다 상승)"
                down = "데드크로스(MA5<MA20)" if not big_up else "1/5 이탈(가격<MA5)"
            else:
                want_long = big_up
                basis = f"MA5 {q(ma5)} vs MA20 {q(ma20)}"
                up, down = "골든크로스(MA5≥MA20)", "데드크로스(MA5<MA20)"
        else:  # pxma20 (기본)
            if ma20 is None:
                return Decision(
                    "hold", f"추세 워밍업 — MA20 계산까지 {20 - len(self._history)}봉 더 필요",
                    source="rule")
            want_long = price >= ma20
            basis = f"가격 {price} vs MA20 {q(ma20)}"
            up, down = "가격≥MA20", "가격<MA20"
        holding = self.position.quantity > 0

        if want_long and not holding:
            if self.auth.remaining_usdc <= 0:
                return Decision("hold", f"상승세({up})지만 운용현금 소진 — 진입 보류", source="rule")
            spend = self.auth.remaining_usdc   # 올인 — 가진 현금 전액 진입
            self._last_action = {"action": "buy", "price": price, "bars_ago": 0}
            return Decision(
                "buy", f"상승세 진입(전량 매수) — {up} · {basis}", spend, source="rule")
        if not want_long and holding:
            self._last_action = {"action": "sell", "price": price, "bars_ago": 0}
            return Decision(
                "sell", f"하락세 이탈(전량 매도, 자본 보존) — {down} · {basis}", source="rule")
        state = "상승세 보유 중" if holding else "하락세 관망(현금 보유)"
        return Decision("hold", f"추세 유지 — {basis} · {state}", source="rule")

    def _decide_by_rule(self, symbol: str, price: Decimal, ind: dict) -> Decision:
        """지표 규칙 — 익절(매도)을 먼저 검사한다: 급반등 구간에서 매수·매도 조건이
        동시에 성립하면 이익 확정이 우선이다.

        ta_mode 면 TA 피처가 거부권/보류권으로 겹쳐진다(개시 게이트는 기존 규칙 유지):
        - 사지마: 대기 패턴 탐지 / 중기선(MA10) 하락 / TA 매도 신호 우세 → 매수 보류
        - 팔지마: 장기선 상승 + TA 매수 신호 우세 → 익절 보류(추세 살아있음)
        Gemini 실패 시에도 같은 TA 기준으로 폴백 판단한다(무료 티어 쿼터 초과 대비)."""
        s = self.strategy
        tp, buy_th = ind["take_profit"], ind["buy_threshold"]
        ta = ind.get("ta") or {}
        buy_sc, sell_sc = ta.get("buy_score", 0), ta.get("sell_score", 0)
        if tp is not None and price >= tp:
            if ta and ta.get("hold_sell_hint") and buy_sc > sell_sc:
                return Decision(
                    "hold",
                    f"익절 조건 충족({price} ≥ {tp})이지만 장기선(MA{ta['slopes']['long_period']}) "
                    f"상승 + TA 매수 우세(매수합 {buy_sc} vs 매도합 {sell_sc}) — 성급한 매도 보류(팔지마)")
            return Decision(
                "sell",
                f"가격 {price} ≥ 익절기준 {tp} (평단 {self.position.avg_price_usdc} +{s.take_profit_pct}%)")
        if price <= buy_th and self.auth.remaining_usdc > 0:
            if ta:
                if ta.get("wait"):
                    waits = ", ".join(p["name"] for p in ta["patterns"] if p["signal"] == "wait")
                    return Decision(
                        "hold", f"매수 조건 충족이지만 대기 패턴({waits}) — 방향 확정까지 보류")
                if ta.get("veto_buy"):
                    return Decision(
                        "hold", "매수 조건 충족이지만 중기선(MA10) 하락 중 — 매수 보류(사지마)")
                if sell_sc >= buy_sc + 60:
                    return Decision(
                        "hold",
                        f"매수 조건 충족이지만 TA 매도 신호 우세(매도합 {sell_sc} vs 매수합 {buy_sc}) — 보류")
            spend = min(s.spend_per_trade_usdc, self.auth.remaining_usdc)
            reason = f"가격 {price} ≤ 매수기준 {buy_th} (MA5 {ind['ma5']} −{s.buy_dip_pct}%)"
            if buy_sc > 0:
                reason += f" · TA 동조(매수합 {buy_sc})"
            return Decision("buy", reason, spend)
        hold = f"조건 미충족 — 가격 {price} · 매수기준 {buy_th}(MA5−{s.buy_dip_pct}%)"
        hold += f" · 익절기준 {tp}" if tp is not None else " · 보유 없음(매도 조건 없음)"
        return Decision("hold", hold)

    def _rule_gate(self, d: Decision, price: Decimal, ind: dict) -> Decision:
        """규칙 게이트 — "규칙 신호 없는 개시 금지"를 프롬프트가 아니라 코드로 강제한다.

        프롬프트(gemini_decider.MODE_RULES)가 이미 "규칙 조건이 충족되지 않았는데 새로
        매수·매도를 시작하는 것은 금지"라고 지시하지만, 그건 모델의 순응에 기대는 규약일
        뿐이다. 모델이 어기면 규칙 밖 매매가 그대로 집행된다. 이 게이트는 같은 제약을
        코드에서 기계적으로 강제한다:

          - 매수 통과 조건: 가격 ≤ 매수기준(MA5 −buy_dip_pct%)
          - 매도 통과 조건: 가격 ≥ 익절기준(평단 +take_profit_pct%)
          - 위반 시 hold 로 강등하고 출처를 "rule-gate" 로 바꿔 계측에 남긴다.

        보류(hold)는 어느 모드에서도 항상 통과시킨다 — AI 재량은 '멈추는 방향'으로만
        열려 있고(추세 모드의 보류 재량), 여는 방향은 규칙이 잠근다. 결과적으로 지출은
        3중으로 통제된다: 개시=규칙 게이트 · 금액=AP2 mandate · 청구서=402 Guard.
        규칙 판단(rule/rule-fallback)과 시간청산은 정의상 규칙 안이므로 대상이 아니다."""
        if d.action == "hold" or d.source != "gemini":
            return d
        buy_th, tp = ind.get("buy_threshold"), ind.get("take_profit")
        if d.action == "buy" and not (buy_th is not None and price <= buy_th):
            return Decision(
                "hold",
                f"규칙 게이트 — 매수 신호 미충족(가격 {price} > 매수기준 "
                f"{buy_th if buy_th is not None else '산출 전'})이라 AI 매수 판단을 보류로 강등 "
                f"· AI 판단: {d.reason}",
                source="rule-gate")
        if d.action == "sell" and not (tp is not None and price >= tp):
            return Decision(
                "hold",
                f"규칙 게이트 — 익절 신호 미충족(가격 {price} < 익절기준 "
                f"{tp if tp is not None else '없음(보유 없음)'})이라 AI 매도 판단을 보류로 강등 "
                f"· AI 판단: {d.reason}",
                source="rule-gate")
        return d

    def _sanitize(self, d: Decision) -> Decision:
        """Gemini 응답을 한도 안으로 강제 (AP2 mandate 가 최종 관문이지만 이중 방어)."""
        if d.action == "buy":
            if self.auth.remaining_usdc <= 0:
                return Decision("hold", f"{d.reason} (예산 소진 → 보류)", source=d.source)
            spend = d.spend_usdc if d.spend_usdc > 0 else self.strategy.spend_per_trade_usdc
            d.spend_usdc = min(spend, self.strategy.spend_per_trade_usdc, self.auth.remaining_usdc)
        if d.action == "sell" and self.position.quantity <= 0:
            return Decision("hold", f"{d.reason} (보유 수량 없음 → 보류)", source=d.source)
        return d

    # 2) payment-required → 한도 승인 + 결제 서명 → payment-submitted
    def build_payment(
        self,
        required: PaymentRequired,
        blockhash: Hash,
        quote=None,
        max_spend_usdc: Optional[Decimal] = None,
    ) -> PaymentSubmitted:
        reqs = required.requirements
        amount_usdc = from_base_units(reqs.amount, reqs.decimals)

        # 402 Guard — AP2 한도 검사 '앞'에서 청구서를 검증한다(금액·수취인·자산·주문번호).
        # max_spend_usdc(의도 지출)를 함께 넘겨 브로커 부풀리기(BUG-03)도 차단한다.
        # 위반이면 GuardError 로 결제 서명 자체가 일어나지 않는다(온체인 유출 0).
        if self.guard is not None and quote is not None:
            self.guard.assert_demand(required, quote, expected_order_id=required.order_id,
                                     max_spend_usdc=max_spend_usdc)

        # AP2 한도 검사 (초과·미허용 자산 시 MandateError → 결제 자체가 일어나지 않음).
        # asset 을 넘겨 allowed_asset 를 실제로 검증하게 한다(결함 C).
        self.auth.authorize(
            order_id=required.order_id, symbol=required.symbol,
            amount_usdc=amount_usdc, pay_to=reqs.pay_to, asset=reqs.asset,
        )

        # 주문번호를 온체인 Memo 에 박는다 (대사 키 + tx 유일성 → 리플레이 방어)
        memo = f"{x.MEMO_PREFIX}:{required.order_id}:{(self.auth.open.signature or '')[:8]}"
        tx = x.build_transfer_transaction(
            payer=self.kp,
            mint=Pubkey.from_string(reqs.asset),
            dest_owner=Pubkey.from_string(reqs.pay_to),
            amount=reqs.amount,
            decimals=reqs.decimals,
            blockhash=blockhash,
            memo=memo,
        )
        payload = PaymentPayload(
            network=self.network,
            serialized_transaction=x.encode_payload(tx),
        )
        return PaymentSubmitted(order_id=required.order_id, payment=payload)

    # 2') 매도: 주식 전송 트랜잭션 서명 (AP2 는 '지출' 한도이므로 매도엔 미적용)
    def build_stock_transfer(
        self,
        required: PaymentRequired,
        blockhash: Hash,
        expected_stock_mint: Optional[Pubkey] = None,
        expected_quantity: Optional[Decimal] = None,
        stock_decimals: Optional[int] = None,
    ) -> PaymentSubmitted:
        reqs = required.requirements

        # 402 Guard — 매도 레그도 서명 직전 청구서를 검증한다(매수 build_payment 의 assert_demand 대칭).
        # 자산(합의된 주식 민트)·수취인(신뢰 브로커)·수량을 엔진의 독립 기준과 대조 — 악성 브로커가
        # asset 을 USDC 로 바꿔 유휴 자금을 빼가는 counterparty 공격을 서명 전에 차단한다(유출 0).
        # expected_stock_mint 가 주어질 때만 검사한다(엔진/run_demo 는 전달, 저수준 테스트는 생략).
        if self.guard is not None and expected_stock_mint is not None:
            self.guard.assert_stock_transfer(
                required, expected_stock_mint=expected_stock_mint,
                expected_quantity=expected_quantity, stock_decimals=stock_decimals,
                expected_order_id=required.order_id)

        memo = f"{x.MEMO_PREFIX}:{required.order_id}:{(self.auth.open.signature or '')[:8]}"
        tx = x.build_transfer_transaction(
            payer=self.kp,
            mint=Pubkey.from_string(reqs.asset),      # 주식 민트
            dest_owner=Pubkey.from_string(reqs.pay_to),
            amount=reqs.amount,
            decimals=reqs.decimals,
            blockhash=blockhash,
            memo=memo,
        )
        payload = PaymentPayload(
            network=self.network,
            serialized_transaction=x.encode_payload(tx),
        )
        return PaymentSubmitted(order_id=required.order_id, payment=payload)

    # 3) 정산 완료 반영
    def on_completed(self, completed: PaymentCompleted, quote_symbol: str,
                     quantity: Decimal, price: Decimal, total_usdc: Decimal) -> Receipt:
        if completed.status == "settled":
            self.position.apply_buy(quantity, price)
        return Receipt(
            order_id=completed.order_id, symbol=quote_symbol, side="buy",
            quantity=quantity, total_usdc=total_usdc,
            tx_signature=completed.tx_signature, confirmed=completed.confirmed,
            note="" if completed.confirmed else "dry-run: 미브로드캐스트(로컬 서명만)",
        )

    # 3') 매도 완료 반영 — 포지션 차감 + 매도 대금을 예산에 환입
    def on_sale_completed(self, completed: PaymentCompleted, quote_symbol: str,
                          quantity: Decimal, price: Decimal, total_usdc: Decimal) -> Receipt:
        if completed.status == "settled":
            self.position.apply_sell(quantity)
            # 추세추종(올인/올아웃)은 매도 대금 전액을 운용현금으로 환입해 복리 재투자한다.
            # 조건형/적립형은 기존대로 예산(순투입 한도)까지만 환입한다.
            self.auth.credit_sale(total_usdc, allow_surplus=(self.strategy.mode == "trend"))
        return Receipt(
            order_id=completed.order_id, symbol=quote_symbol, side="sell",
            quantity=quantity, total_usdc=total_usdc,
            tx_signature=completed.tx_signature, confirmed=completed.confirmed,
            note="" if completed.confirmed else "dry-run: 미브로드캐스트(로컬 서명만)",
        )
