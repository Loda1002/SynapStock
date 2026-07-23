"""TA 지표·패턴 계산 (결정적) — 매매 판단의 "근거 피처"를 코드가 만든다.

설계 방침(2026-07-23 매매 기준 개선): 산수는 코드가 하고, 해석은 판단자
(Gemini / 규칙 폴백)가 한다. 입력은 OHLC 봉(market.price_feed.Bar) 리스트.

- 이동평균 1/5/10/20/50/100/200일 + 골든/데드크로스 + 단·중·장기 기울기
- 지지/저항: 피벗(좌우 2봉 극점) 가격대 중 2회 이상 반응(터치)한 것만 유효
- 차트 패턴: 쌍봉(M)/쌍바닥(W)·삼중 천장/바닥·H&S/역H&S·삼각형(상승/하락/대칭)·
  쐐기(상승/하락)·박스권·확산 삼각형·상승 채널
  ※ 상승/하락 깃발·다이아몬드·역V자는 v1 미탐지 — TODO(탐지 난도 대비 가치 낮음,
    프롬프트의 패턴→신호 매핑에는 존재하므로 추후 탐지만 추가하면 됨)
- 캔들 패턴: 장악형·도지·적삼병/흑삼병·샛별/석별·유성/망치(스파이크)·장대양/음봉

★정정(표준 TA·이미지5 우선): 상승 쐐기형은 "매도(약세)" 신호다(원본 이미지3의
매수 100% 배정은 오류). 비게 된 매수 100% 칸은 사용자 확인(2026-07-23)에 따라
최강 상승 반전인 역헤드앤숄더·삼중바닥으로 채웠다. 신뢰도 수치는 사람에게 강도의
"느낌"을 주기 위한 값이라 극단 조건이며, 추후 백테스트로 조정 가능(사용자 메모).

※ 룩백 해석: 사용자 답 "N=2"는 봉 수로는 패턴이 성립할 수 없어 "유효 터치 2회 +
  피벗 강도(좌우 2봉)"로 해석했고, 탐지 창은 40봉으로 두었다(전부 상수로 조정 가능).
※ 이 패턴들은 통계적으로 보장된 신호가 아니라 휴리스틱이다 — 반드시 백테스트로 검증.
※ 이 계산은 사용자가 정의한 규칙의 실행이며 투자 조언이 아니다(데모용).
"""
from __future__ import annotations
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

CENT = Decimal("0.01")

# ---- 탐지 파라미터 (message 열린 결정 반영: 유효 지지/저항 = 2회 이상 터치) ----
PIVOT_STRENGTH = 2        # 피벗: 좌우 2봉보다 높/낮아야 극점으로 인정
TOUCH_MIN = 2             # 지지/저항 유효 터치 수 (사용자 답: 2)
PATTERN_LOOKBACK = 40     # 패턴·지지/저항 탐지 창(봉)
RECENT_PIVOT = 15         # 패턴의 마지막 구성 피벗이 이 안에 있어야 "살아있는" 패턴
LEVEL_TOL = 0.006         # 같은 가격대로 묶는 허용 오차 (0.6%)
EQUAL_TOL = 0.012         # 쌍봉/쌍바닥 극점 동일 판정 (1.2%)
HS_MARGIN = 0.015         # H&S: 머리가 어깨보다 커야 하는 최소 비율 (1.5%)
SLOPE_FLAT_PCT = 0.05     # 추세선 기울기 횡보 판정 (봉당 %, 피벗 회귀선)
PARALLEL_TOL_PCT = 0.03   # 두 추세선을 평행으로 보는 기울기 차 (봉당 %)
MA_SLOPE_FLAT_PCT = 0.15  # MA 기울기 횡보 판정 (3봉 변화율 %)
MA_PERIODS = (1, 5, 10, 20, 50, 100, 200)   # 단위: 일 (1일선 = 종가 그 자체)
CROSS_PAIRS = ((1, 5), (5, 10), (5, 20), (10, 20), (20, 50), (50, 200))
CROSS_RECENT = 3          # "최근" 크로스로 보고하는 봉 수


# ---------- 이동평균 ----------

def sma_last(closes: Sequence[Decimal], period: int) -> Optional[Decimal]:
    """마지막 봉 기준 단순이동평균. 봉 부족이면 None."""
    if period <= 0 or len(closes) < period:
        return None
    return (sum(closes[-period:]) / Decimal(period)).quantize(CENT)


def _sma_at(vals: Sequence[float], period: int, idx: int) -> Optional[float]:
    """vals[idx] 를 마지막으로 하는 period 평균 (내부 float 계산용)."""
    if idx + 1 < period or idx >= len(vals):
        return None
    window = vals[idx + 1 - period: idx + 1]
    return sum(window) / period


def ma_snapshot(closes: Sequence[Decimal]) -> Dict[int, Optional[Decimal]]:
    """MA_PERIODS 전부의 현재값 — 봉 부족이면 None (데이터 늘면 자동 활성)."""
    return {p: sma_last(closes, p) for p in MA_PERIODS}


def detect_crosses(closes: Sequence[Decimal]) -> List[Dict]:
    """골든/데드크로스 — CROSS_PAIRS 중 최근 CROSS_RECENT 봉 안에서 발생한 것.

    (1,5) 쌍은 "종가가 MA5 를 상/하향 돌파"를 뜻한다. 쌍마다 가장 최근 1건만 보고."""
    vals = [float(c) for c in closes]
    n = len(vals)
    out: List[Dict] = []
    for s, l in CROSS_PAIRS:
        if n < l + 1:
            continue
        found = None
        for back in range(CROSS_RECENT):           # back=0 이 현재 봉
            i = n - 1 - back
            if i < 1:
                break
            a_s, a_l = _sma_at(vals, s, i), _sma_at(vals, l, i)
            b_s, b_l = _sma_at(vals, s, i - 1), _sma_at(vals, l, i - 1)
            if None in (a_s, a_l, b_s, b_l):
                break
            prev_diff, diff = b_s - b_l, a_s - a_l
            if prev_diff <= 0 < diff:
                found = {"pair": f"{s}/{l}", "kind": "golden", "bars_ago": back}
            elif prev_diff >= 0 > diff:
                found = {"pair": f"{s}/{l}", "kind": "dead", "bars_ago": back}
            if found:
                out.append(found)
                break
    return out


def ma_slope(closes: Sequence[Decimal], period: int, span: int = 3) -> Optional[str]:
    """MA 기울기 — span봉 전 대비 변화율로 상승/하락/횡보 분류. 봉 부족이면 None."""
    vals = [float(c) for c in closes]
    n = len(vals)
    now, then = _sma_at(vals, period, n - 1), _sma_at(vals, period, n - 1 - span)
    if now is None or then is None or then == 0:
        return None
    pct = (now / then - 1) * 100
    if pct > MA_SLOPE_FLAT_PCT:
        return "상승"
    if pct < -MA_SLOPE_FLAT_PCT:
        return "하락"
    return "횡보"


# ---------- 피벗 · 지지/저항 ----------

def find_pivots(bars: Sequence, strength: int = PIVOT_STRENGTH,
                ) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """(피벗 고점, 피벗 저점) — (인덱스, 가격). 좌우 strength 봉보다 높/낮은 극점."""
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    ph: List[Tuple[int, float]] = []
    pl: List[Tuple[int, float]] = []
    for i in range(strength, len(bars) - strength):
        left, right = range(i - strength, i), range(i + 1, i + 1 + strength)
        if all(highs[i] > highs[j] for j in left) and all(highs[i] > highs[j] for j in right):
            ph.append((i, highs[i]))
        if all(lows[i] < lows[j] for j in left) and all(lows[i] < lows[j] for j in right):
            pl.append((i, lows[i]))
    return ph, pl


def _cluster_levels(prices: List[float]) -> List[Tuple[float, int]]:
    """피벗 가격들을 LEVEL_TOL 안에서 묶어 (가격대 평균, 터치 수) 목록으로."""
    out: List[Tuple[float, int]] = []
    for p in sorted(prices):
        if out and abs(p - out[-1][0]) / out[-1][0] <= LEVEL_TOL:
            avg, n = out[-1]
            out[-1] = ((avg * n + p) / (n + 1), n + 1)
        else:
            out.append((p, 1))
    return out


def support_resistance(bars: Sequence, price: Decimal) -> Dict:
    """지지/저항 — 2회 이상 반응한 가격대만 유효(사용자 답). 현재가 기준 가장 가까운
    아래 지지/위 저항과, 직전 봉의 반등·거부·돌파·이탈 이벤트를 보고한다."""
    window = list(bars)[-PATTERN_LOOKBACK:]
    ph, pl = find_pivots(window)
    levels = [(lv, n) for lv, n in _cluster_levels([p for _, p in ph + pl]) if n >= TOUCH_MIN]
    p = float(price)
    support = max(((lv, n) for lv, n in levels if lv < p), default=None, key=lambda t: t[0])
    resistance = min(((lv, n) for lv, n in levels if lv > p), default=None, key=lambda t: t[0])

    events: List[Dict] = []
    if len(window) >= 2:
        cur, prev = window[-1], window[-2]
        c, pc = float(cur.close), float(prev.close)
        lo, hi = float(cur.low), float(cur.high)
        bullish, bearish = c > float(cur.open), c < float(cur.open)
        for lv, _n in levels:
            tol = lv * LEVEL_TOL
            if pc <= lv < c:      # 저항 돌파 → 그 선은 지지로 전환
                events.append({"name": "저항 돌파", "signal": "buy", "confidence": 75})
            elif pc >= lv > c:    # 지지 이탈 → 그 선은 저항으로 전환
                events.append({"name": "지지 이탈", "signal": "sell", "confidence": 75})
            elif lo <= lv + tol and c > lv and bullish:
                events.append({"name": "지지 반등", "signal": "buy", "confidence": 70})
            elif hi >= lv - tol and c < lv and bearish:
                events.append({"name": "저항 거부", "signal": "sell", "confidence": 70})
    # 같은 이벤트 중복 제거, 최대 2건
    seen, uniq = set(), []
    for e in events:
        if e["name"] not in seen:
            seen.add(e["name"])
            uniq.append(e)
    return {
        "support": Decimal(str(round(support[0], 2))) if support else None,
        "support_touches": support[1] if support else 0,
        "resistance": Decimal(str(round(resistance[0], 2))) if resistance else None,
        "resistance_touches": resistance[1] if resistance else 0,
        "events": uniq[:2],
    }


# ---------- 차트 패턴 ----------

def _fit_slope(points: List[Tuple[int, float]]) -> float:
    """피벗 (인덱스, 가격) 최소제곱 회귀 기울기 (가격/봉)."""
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    var = sum((x - mx) ** 2 for x, _ in points)
    if var == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in points) / var


def _classify_trendlines(ph: List[Tuple[int, float]], pl: List[Tuple[int, float]],
                         ref_price: float) -> Optional[Dict]:
    """피벗 고점선·저점선의 기울기 조합으로 삼각형/쐐기/박스/확산/채널 분류.

    ★정정 반영: 상승 쐐기(수렴하며 상승)=매도, 하락 쐐기(수렴하며 하락)=매수."""
    if len(ph) < 2 or len(pl) < 2 or ref_price <= 0:
        return None
    sh = _fit_slope(ph[-3:]) / ref_price * 100   # 봉당 %
    sl = _fit_slope(pl[-3:]) / ref_price * 100

    def cls(s: float) -> str:
        if s > SLOPE_FLAT_PCT:
            return "up"
        if s < -SLOPE_FLAT_PCT:
            return "down"
        return "flat"

    ch, cl_ = cls(sh), cls(sl)
    gap = sh - sl                                # 양수=벌어짐(확산), 음수=좁아짐(수렴)
    parallel = abs(gap) <= PARALLEL_TOL_PCT
    if ch == "up" and cl_ == "up":
        if parallel:
            return {"name": "상승 채널", "signal": "wait", "confidence": 50}
        if gap < 0:
            return {"name": "상승 쐐기형", "signal": "sell", "confidence": 90}
        return {"name": "확산 삼각형", "signal": "wait", "confidence": 50}
    if ch == "down" and cl_ == "down":
        if not parallel and gap < 0:
            return {"name": "하락 쐐기형", "signal": "buy", "confidence": 80}
        return None                              # 하락 채널은 매핑 목록에 없음 — 미보고
    if ch == "flat" and cl_ == "up":
        return {"name": "상승 삼각형", "signal": "buy", "confidence": 70}
    if ch == "down" and cl_ == "flat":
        return {"name": "하락 삼각형", "signal": "sell", "confidence": 70}
    if ch == "down" and cl_ == "up":
        return {"name": "대칭 삼각수렴", "signal": "wait", "confidence": 50}
    if ch == "up" and cl_ == "down":
        return {"name": "확산 삼각형", "signal": "wait", "confidence": 50}
    if ch == "flat" and cl_ == "flat":
        return {"name": "박스권 횡보", "signal": "wait", "confidence": 50}
    return None


def _near(a: float, b: float, tol: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= tol


def detect_patterns(bars: Sequence, price: Decimal) -> List[Dict]:
    """차트 패턴 탐지 — 신뢰도 내림차순 최대 3건.

    신뢰도(확률 스펙트럼, 인간용 강도 느낌值): M형 100 · 삼중천장/H&S/상승쐐기 90 ·
    W바닥 65 · 삼중바닥/역H&S 100(매수 100% 칸, 사용자 확인) · 하락쐐기 80 ·
    상승/하락 삼각형 70 · 대기 패턴 50."""
    window = list(bars)[-PATTERN_LOOKBACK:]
    n = len(window)
    if n < PIVOT_STRENGTH * 2 + 3:
        return []
    ph, pl = find_pivots(window)
    p = float(price)
    out: List[Dict] = []

    def recent(pivots: List[Tuple[int, float]]) -> bool:
        return bool(pivots) and pivots[-1][0] >= n - RECENT_PIVOT

    # --- 천장형 (마지막 피벗 고점 2~3개) ---
    top = None
    if len(ph) >= 3 and recent(ph):
        (i1, a), (i2, b), (i3, c) = ph[-3:]
        if b > a * (1 + HS_MARGIN) and b > c * (1 + HS_MARGIN) and _near(a, c, EQUAL_TOL * 1.5):
            top = {"name": "헤드앤숄더(천장)", "signal": "sell", "confidence": 90}
        elif _near(a, b, EQUAL_TOL) and _near(b, c, EQUAL_TOL):
            top = {"name": "삼중 천장형", "signal": "sell", "confidence": 90}
    if top is None and len(ph) >= 2 and recent(ph):
        (i1, a), (i2, b) = ph[-2:]
        valley_between = any(i1 < i < i2 for i, _ in pl)
        if _near(a, b, EQUAL_TOL) and valley_between and p < max(a, b) * (1 + EQUAL_TOL):
            top = {"name": "M형(이중천장)", "signal": "sell", "confidence": 100}
    if top and p < max(v for _, v in ph[-3:]) * (1 + EQUAL_TOL):
        out.append(top)

    # --- 바닥형 (마지막 피벗 저점 2~3개) ---
    bottom = None
    if len(pl) >= 3 and recent(pl):
        (i1, a), (i2, b), (i3, c) = pl[-3:]
        if b < a * (1 - HS_MARGIN) and b < c * (1 - HS_MARGIN) and _near(a, c, EQUAL_TOL * 1.5):
            bottom = {"name": "역헤드앤숄더", "signal": "buy", "confidence": 100}
        elif _near(a, b, EQUAL_TOL) and _near(b, c, EQUAL_TOL):
            bottom = {"name": "삼중 바닥형", "signal": "buy", "confidence": 100}
    if bottom is None and len(pl) >= 2 and recent(pl):
        (i1, a), (i2, b) = pl[-2:]
        peak_between = any(i1 < i < i2 for i, _ in ph)
        if _near(a, b, EQUAL_TOL) and peak_between and p > min(a, b) * (1 - EQUAL_TOL):
            bottom = {"name": "W바닥(이중바닥)", "signal": "buy", "confidence": 65}
    if bottom and p > min(v for _, v in pl[-3:]) * (1 - EQUAL_TOL):
        out.append(bottom)

    # --- 추세선 조합 (삼각형·쐐기·박스·확산·채널) ---
    if recent(ph) or recent(pl):
        shape = _classify_trendlines(ph, pl, p)
        if shape:
            out.append(shape)

    out.sort(key=lambda d: -d["confidence"])
    return out[:3]


# ---------- 캔들 패턴 ----------

def _body(b) -> float:
    return float(b.close) - float(b.open)


def _range(b) -> float:
    return float(b.high) - float(b.low)


def detect_candles(bars: Sequence) -> List[Dict]:
    """캔들 패턴 — 마지막 1~3봉. 목 시세의 퇴화 봉(고가=저가)은 건너뛴다.

    도지는 방향 전환/불확실 → 단독으로는 보류(신호 wait, 점수 미가산)."""
    bs = list(bars)
    if not bs or _range(bs[-1]) <= 0:
        return []
    cur = bs[-1]
    body, rng = _body(cur), _range(cur)
    upper = float(cur.high) - max(float(cur.open), float(cur.close))
    lower = min(float(cur.open), float(cur.close)) - float(cur.low)
    bodies = [abs(_body(b)) for b in bs[-11:-1] if _range(b) > 0]
    avg_body = (sum(bodies) / len(bodies)) if bodies else 0.0
    out: List[Dict] = []

    if abs(body) <= rng * 0.1:
        out.append({"name": "도지", "signal": "wait", "confidence": 50})
    else:
        lows5 = [float(b.low) for b in bs[-5:]]
        highs5 = [float(b.high) for b in bs[-5:]]
        if lower >= abs(body) * 2 and upper <= abs(body) and float(cur.low) <= min(lows5):
            out.append({"name": "망치(스파이크 저점)", "signal": "buy", "confidence": 65})
        if upper >= abs(body) * 2 and lower <= abs(body) and float(cur.high) >= max(highs5):
            out.append({"name": "유성", "signal": "sell", "confidence": 65})
        if avg_body > 0 and abs(body) >= avg_body * 2:
            if body > 0:
                out.append({"name": "장대양봉", "signal": "buy", "confidence": 60})
            else:
                out.append({"name": "장대음선", "signal": "sell", "confidence": 60})

    if len(bs) >= 2 and _range(bs[-2]) > 0:
        prev = bs[-2]
        pb = _body(prev)
        if pb < 0 < body and float(cur.open) <= float(prev.close) and float(cur.close) >= float(prev.open):
            out.append({"name": "상승장악(안아올리기)", "signal": "buy", "confidence": 70})
        if pb > 0 > body and float(cur.open) >= float(prev.close) and float(cur.close) <= float(prev.open):
            out.append({"name": "하락장악", "signal": "sell", "confidence": 70})

    if len(bs) >= 3 and all(_range(b) > 0 for b in bs[-3:]):
        b1, b2, b3 = bs[-3:]
        v1, v2, v3 = _body(b1), _body(b2), _body(b3)
        closes_up = float(b1.close) < float(b2.close) < float(b3.close)
        closes_dn = float(b1.close) > float(b2.close) > float(b3.close)
        solid = all(abs(_body(b)) >= _range(b) * 0.5 for b in (b1, b2, b3))
        if v1 > 0 and v2 > 0 and v3 > 0 and closes_up and solid:
            out.append({"name": "적삼병", "signal": "buy", "confidence": 75})
        if v1 < 0 and v2 < 0 and v3 < 0 and closes_dn and solid:
            out.append({"name": "흑삼병", "signal": "sell", "confidence": 75})
        # 샛별/석별: 큰 봉 → 작은 몸통 → 반대 방향 큰 봉이 첫 봉 몸통 중간을 되돌림
        if v1 < 0 and abs(v1) >= avg_body and abs(v2) <= abs(v1) * 0.4 and v3 > 0 \
                and float(b3.close) > (float(b1.open) + float(b1.close)) / 2:
            out.append({"name": "새벽의 샛별", "signal": "buy", "confidence": 75})
        if v1 > 0 and abs(v1) >= avg_body and abs(v2) <= abs(v1) * 0.4 and v3 < 0 \
                and float(b3.close) < (float(b1.open) + float(b1.close)) / 2:
            out.append({"name": "석별(이브닝스타)", "signal": "sell", "confidence": 75})

    out.sort(key=lambda d: -d["confidence"])
    return out[:3]


# ---------- 종합 요약 (판단자 공용 입력) ----------

def ta_summary(bars: Sequence) -> Dict:
    """봉 이력 → TA 피처 묶음. 규칙 폴백·Gemini 프롬프트가 같은 값을 쓴다.

    score: 방향별 신뢰도 합(크로스 75 + 지지/저항 이벤트 + 패턴 + 캔들).
    wait: 대기 차트 패턴(삼각수렴·박스·확산·상승채널) 탐지 여부 → 신규 매수 보류.
    veto_buy: 중기선(MA10) 하락 중 = "사지마" / hold_sell_hint: 장기선 상승 = "팔지마"."""
    closes = [b.close for b in bars]
    if not closes:
        return {}
    price = closes[-1]
    mas = ma_snapshot(closes)
    crosses = detect_crosses(closes)
    long_p = 50 if mas.get(50) is not None else 20
    slopes = {
        "short": ma_slope(closes, 5),
        "mid": ma_slope(closes, 10),
        "long_period": long_p,
        "long": ma_slope(closes, long_p),
    }
    sr = support_resistance(bars, price)
    patterns = detect_patterns(bars, price)
    candles = detect_candles(bars)

    # 크로스는 쌍이 여러 개 겹칠 수 있어 방향당 1회(75)만 반영
    buy_score = 75 if any(c["kind"] == "golden" for c in crosses) else 0
    sell_score = 75 if any(c["kind"] == "dead" for c in crosses) else 0
    for sig in sr["events"] + patterns + candles:
        if sig["signal"] == "buy":
            buy_score += sig["confidence"]
        elif sig["signal"] == "sell":
            sell_score += sig["confidence"]
    wait = any(s["signal"] == "wait" for s in patterns)
    return {
        "mas": mas,
        "slopes": slopes,
        "crosses": crosses,
        "sr": sr,
        "patterns": patterns,
        "candles": candles,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "wait": wait,
        "veto_buy": slopes["mid"] == "하락",       # 사지마: 중기선 하락 중
        "hold_sell_hint": slopes["long"] == "상승",  # 팔지마: 장기선 상승 중
    }


def format_ta_block(ta: Dict) -> str:
    """ta_summary → 프롬프트/로그용 압축 한국어 블록 (모델이 산수하지 않게 값만 전달)."""
    if not ta:
        return "TA 산출 전 (봉 부족)"
    mas = ta["mas"]
    ma_line = " · ".join(f"{p}:{mas[p]}" if mas[p] is not None else f"{p}:—"
                         for p in MA_PERIODS)
    sl = ta["slopes"]
    slope_line = (f"단기(5) {sl['short'] or '—'} · 중기(10) {sl['mid'] or '—'} · "
                  f"장기({sl['long_period']}) {sl['long'] or '—'}")
    if ta["crosses"]:
        cross_line = " · ".join(
            f"{c['pair']} {'골든' if c['kind'] == 'golden' else '데드'}크로스"
            f"({c['bars_ago']}봉 전)" for c in ta["crosses"])
    else:
        cross_line = "최근 3봉 내 없음"
    sr = ta["sr"]
    sr_parts = []
    sr_parts.append(f"지지 {sr['support']}({sr['support_touches']}회 터치)"
                    if sr["support"] is not None else "지지 없음")
    sr_parts.append(f"저항 {sr['resistance']}({sr['resistance_touches']}회 터치)"
                    if sr["resistance"] is not None else "저항 없음")
    if sr["events"]:
        sr_parts.append("이벤트 " + ", ".join(e["name"] for e in sr["events"]))
    sig_ko = {"buy": "매수", "sell": "매도", "wait": "대기"}

    def fmt(sigs: List[Dict]) -> str:
        return " · ".join(f"{s['name']}→{sig_ko[s['signal']]}(신뢰{s['confidence']})"
                          for s in sigs) or "탐지 없음"
    lines = [
        f"- MA(일): {ma_line}",
        f"- MA 기울기: {slope_line}",
        f"- 최근 크로스: {cross_line}",
        f"- 지지/저항(2회 이상 터치만 유효): {' · '.join(sr_parts)}",
        f"- 차트 패턴: {fmt(ta['patterns'])}",
        f"- 캔들 패턴: {fmt(ta['candles'])}",
        f"- 신호 종합: 매수합 {ta['buy_score']} vs 매도합 {ta['sell_score']}"
        f" · 대기패턴 {'있음' if ta['wait'] else '없음'}"
        f" · 중기선 하락(사지마) {'해당' if ta['veto_buy'] else '아님'}"
        f" · 장기선 상승(팔지마 참고) {'해당' if ta['hold_sell_hint'] else '아님'}",
    ]
    return "\n".join(lines)
