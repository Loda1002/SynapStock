"""TA 지표·패턴 탐지 단위테스트 — 결정적 과거 시세 배열로 각 탐지기를 검증.

message(2026-07-23) 검증 방법 항목: "패턴 탐지 함수는 결정적 과거 시세 배열로
단위테스트(각 패턴이 의도대로 탐지되는지)". 실데이터 검증은 backtest.py 몫.

실행: python -m scripts.test_indicators
"""
from decimal import Decimal

from market.price_feed import Bar
from market import indicators as ta

D = lambda v: Decimal(str(v))  # noqa: E731


def flat_bars(closes) -> list:
    """시=고=저=종 봉 (MA·크로스·피벗·패턴 테스트용 — 캔들 패턴은 퇴화라 미탐지)."""
    return [Bar(date=f"D{i:03d}", open=D(c), high=D(c), low=D(c), close=D(c))
            for i, c in enumerate(closes)]


def bar(o, h, l, c, i=0) -> Bar:
    return Bar(date=f"E{i:03d}", open=D(o), high=D(h), low=D(l), close=D(c))


def check(name: str, got, want) -> int:
    ok = got == want
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}\n       기대 {want}\n       실제 {got}")
    return 0 if ok else 1


def names(sigs) -> list:
    return [s["name"] for s in sigs]


def main() -> int:
    bad = 0

    # ① 이동평균 값·미성립 None
    closes = [D(i) for i in range(1, 31)]                     # 1..30
    mas = ta.ma_snapshot(closes)
    bad += check("MA1=마지막 종가", mas[1], D("30.00"))
    bad += check("MA5(26..30 평균)", mas[5], D("28.00"))
    bad += check("MA20(11..30 평균)", mas[20], D("20.50"))
    bad += check("MA50 봉 부족 → None", mas[50], None)

    # ② 골든/데드크로스 — 하락 후 급반등이면 단기선이 장기선을 상향 돌파
    up = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 95, 100, 105, 110]
    crosses = ta.detect_crosses([D(v) for v in up])
    bad += check("급반등 → 골든크로스 존재", any(c["kind"] == "golden" for c in crosses), True)
    bad += check("급반등 → 데드크로스 없음", any(c["kind"] == "dead" for c in crosses), False)
    down = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 105, 100, 95, 90]
    crosses = ta.detect_crosses([D(v) for v in down])
    bad += check("급락 → 데드크로스 존재", any(c["kind"] == "dead" for c in crosses), True)

    # ③ MA 기울기 분류
    rise = [D(100 + i) for i in range(30)]
    bad += check("상승 배열 기울기", ta.ma_slope(rise, 5), "상승")
    fall = [D(130 - i) for i in range(30)]
    bad += check("하락 배열 기울기", ta.ma_slope(fall, 5), "하락")
    flat = [D(100) for _ in range(30)]
    bad += check("횡보 배열 기울기", ta.ma_slope(flat, 5), "횡보")

    # ④ 피벗 — 좌우 2봉보다 높은/낮은 극점만
    seq = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
    ph, pl = ta.find_pivots(flat_bars(seq))
    bad += check("피벗 고점 (idx5=10)", ph, [(5, 10.0)])
    seq = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
    ph, pl = ta.find_pivots(flat_bars(seq))
    bad += check("피벗 저점 (idx5=1)", pl, [(5, 1.0)])

    # ⑤ 지지/저항 — 2회 터치 가격대만 유효 + 지지 반등 이벤트
    osc = [105, 103, 100, 103, 105, 108, 110, 108, 105, 103,
           100, 103, 105, 108, 110, 108, 105, 104]
    bars = flat_bars(osc) + [bar(100.8, 103.2, 100.3, 103, 99)]
    sr = ta.support_resistance(bars, D(103))
    bad += check("지지 100 (2회 터치)", (sr["support"], sr["support_touches"]), (D("100"), 2))
    bad += check("저항 110 (2회 터치)", (sr["resistance"], sr["resistance_touches"]), (D("110"), 2))
    bad += check("지지 반등 이벤트", [e["name"] for e in sr["events"]], ["지지 반등"])

    # ⑥ 이중천장(M형) — 매도 100
    m_top = [100, 102, 104, 106, 108, 110, 108, 106, 104, 106,
             108, 110.2, 108, 106, 104, 103, 102]
    pats = ta.detect_patterns(flat_bars(m_top), D(102))
    bad += check("M형(이중천장) 탐지", names(pats), ["M형(이중천장)"])
    bad += check("M형 신호=매도/신뢰 100", (pats[0]["signal"], pats[0]["confidence"]), ("sell", 100))

    # ⑦ W바닥(이중바닥) — 매수 65
    w_bot = [110, 108, 106, 104, 102, 100, 102, 104, 106, 104,
             102, 99.8, 102, 104, 106, 107, 108]
    pats = ta.detect_patterns(flat_bars(w_bot), D(108))
    bad += check("W바닥(이중바닥) 탐지", names(pats), ["W바닥(이중바닥)"])
    bad += check("W바닥 신호=매수/신뢰 65", (pats[0]["signal"], pats[0]["confidence"]), ("buy", 65))

    # ⑧ 추세선 조합 — ★정정: 상승 쐐기=매도, 하락 쐐기=매수
    shape = ta._classify_trendlines([(0, 105), (5, 105.5), (10, 106)],
                                    [(0, 100), (5, 101.5), (10, 103)], 106)
    bad += check("상승 쐐기형=매도(정정)", (shape["name"], shape["signal"], shape["confidence"]),
                 ("상승 쐐기형", "sell", 90))
    shape = ta._classify_trendlines([(0, 110), (5, 108), (10, 106.5)],
                                    [(0, 100), (5, 99.3), (10, 98.8)], 106)
    bad += check("하락 쐐기형=매수", (shape["name"], shape["signal"]), ("하락 쐐기형", "buy"))
    shape = ta._classify_trendlines([(0, 105), (5, 105.02), (10, 104.98)],
                                    [(0, 100), (5, 101.5), (10, 103)], 105)
    bad += check("상승 삼각형=매수", (shape["name"], shape["signal"]), ("상승 삼각형", "buy"))
    shape = ta._classify_trendlines([(0, 105), (5, 105.02), (10, 104.98)],
                                    [(0, 100), (5, 100.02), (10, 99.98)], 102)
    bad += check("박스권=대기", (shape["name"], shape["signal"]), ("박스권 횡보", "wait"))
    shape = ta._classify_trendlines([(0, 110), (5, 108), (10, 106)],
                                    [(0, 100), (5, 101.5), (10, 103)], 105)
    bad += check("대칭 삼각수렴=대기", (shape["name"], shape["signal"]), ("대칭 삼각수렴", "wait"))
    shape = ta._classify_trendlines([(0, 106), (5, 108), (10, 110)],
                                    [(0, 100), (5, 98.5), (10, 97)], 105)
    bad += check("확산 삼각형=대기", (shape["name"], shape["signal"]), ("확산 삼각형", "wait"))

    # ⑨ 캔들 패턴
    warm = [bar(100, 101, 99, 100.5, i) for i in range(8)]     # 평균 몸통용 사전 봉
    engulf = warm + [bar(105, 105.5, 102.8, 103, 90), bar(102.9, 106, 102.5, 105.5, 91)]
    bad += check("상승장악 탐지", "상승장악(안아올리기)" in names(ta.detect_candles(engulf)), True)
    doji = warm + [bar(100, 101, 99, 100.05, 92)]
    bad += check("도지=대기", [(s["name"], s["signal"]) for s in ta.detect_candles(doji)],
                 [("도지", "wait")])
    soldiers = warm + [bar(100, 102.2, 99.9, 102, 93), bar(102, 104.2, 101.9, 104, 94),
                       bar(104, 106.2, 103.9, 106, 95)]
    bad += check("적삼병 탐지", "적삼병" in names(ta.detect_candles(soldiers)), True)
    star = warm + [bar(106, 108, 105.8, 108, 96), bar(108, 110, 107.8, 110, 97),
                   bar(110, 115, 109.9, 111, 98)]
    bad += check("유성 탐지", "유성" in names(ta.detect_candles(star)), True)
    hammer = warm + [bar(102, 102.2, 100, 100.2, 96), bar(100.2, 100.4, 98.5, 98.7, 97),
                     bar(100.2, 101.3, 96.5, 101.2, 98)]
    bad += check("망치(스파이크 저점) 탐지", "망치(스파이크 저점)" in names(ta.detect_candles(hammer)), True)
    crows = warm + [bar(106, 106.2, 103.9, 104, 93), bar(104, 104.2, 101.9, 102, 94),
                    bar(102, 102.2, 99.9, 100, 95)]
    bad += check("흑삼병 탐지", "흑삼병" in names(ta.detect_candles(crows)), True)
    bad += check("퇴화 봉(고=저) → 캔들 미탐지", ta.detect_candles(flat_bars([1, 2, 3])), [])

    # ⑩ 종합 요약 — 상승 추세면 장기선 상승(팔지마), 중기선 하락 아님
    bars = flat_bars([100 + i * 0.5 for i in range(60)])
    s = ta.ta_summary(bars)
    bad += check("상승 추세: 팔지마 힌트", (s["hold_sell_hint"], s["veto_buy"]), (True, False))
    bad += check("MA50 활성(60봉)", s["mas"][50] is not None, True)
    bad += check("MA100 미성립(60봉)", s["mas"][100], None)
    block = ta.format_ta_block(s)
    bad += check("프롬프트 블록 생성", "MA(일)" in block and "신호 종합" in block, True)
    bad += check("빈 이력 → 안내 문구", ta.format_ta_block({}), "TA 산출 전 (봉 부족)")

    print("\n결과:", "전부 통과" if bad == 0 else f"{bad}건 실패")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
