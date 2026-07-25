"""데이터 품질 수집기 판정 로직 단위테스트 — 합성 불량 입력으로 각 탐지기를 검증.

collect_dataquality.py 의 순수 함수(parse_rows·check_ohlc·find_gaps 등)에 의도적으로
불량한 입력(high<low·30% 점프·주말 봉·깨진 헤더·중복·워밍업 부족)을 먹여 '정말 잡는지'
확인한다. 실데이터가 깨끗해 수집기가 전부 0을 내도, 탐지기가 침묵하는 게 아니라
'문제가 없어서 0'임을 이 테스트가 보증한다(ai-regression-testing: 파싱·검증 로직은 AI 가
반복 실수하는 곳 — 자동 테스트로만 회귀를 막는다).

네트워크·파일 I/O 없음 — 전부 인메모리 합성 행.

실행: python -m scripts.test_dataquality
"""
from decimal import Decimal

from scripts.collect_dataquality import (
    parse_rows, check_schema, check_ohlc, find_duplicates, check_ordering,
    find_gaps, find_weekend_bars, find_anomalies, zero_volume_bars, adequacy,
)

D = lambda v: Decimal(str(v))  # noqa: E731

_fails = 0


def ok(cond: bool, label: str):
    global _fails
    if cond:
        print(f"[OK] {label}")
    else:
        print(f"[FAIL] {label}")
        _fails += 1


def R(line, date, o, h, low, c, v=1000):
    """합성 행 dict — 수집기 순수 함수 입력 형태."""
    return {"line": line, "date": date, "open": D(o), "high": D(h),
            "low": D(low), "close": D(c), "volume": v}


# ─────────────────────────────────────────── parse_rows

def test_parse_rows():
    good = ("date,open,high,low,close,volume\n"
            "2026-01-02,10,11,9,10.5,1000\n"
            "2026-01-05,10.5,12,10,11,2000\n")
    header, rows, errs = parse_rows(good)
    ok(header == ["date", "open", "high", "low", "close", "volume"], "parse: 헤더 파싱")
    ok(len(rows) == 2 and not errs, "parse: 정상 2행·오류0")
    ok(rows[0]["open"] == D("10") and rows[0]["volume"] == 1000, "parse: 숫자 변환")

    # 필드 수 부족 + 숫자 아님 + 빈 줄
    bad = ("date,open,high,low,close,volume\n"
           "2026-01-02,10,11,9\n"              # 필드 4개
           "2026-01-05,x,12,10,11,2000\n"      # open 이 숫자 아님
           "\n"                                  # 빈 줄(건너뜀)
           "2026-01-06,10,12,10,11,3000\n")
    _, rows2, errs2 = parse_rows(bad)
    ok(len(rows2) == 1, "parse: 유효 행만 1개(불량 2·빈줄 제외)")
    ok(len(errs2) == 2, "parse: 오류 좌표 2개(필드수·숫자변환)")
    ok(any(e["line"] == 2 for e in errs2), "parse: 오류 줄번호 기록")


# ─────────────────────────────────────────── check_schema

def test_schema():
    rows = [R(2, "2026-01-02", 10, 11, 9, 10)]
    good = check_schema(["date", "open", "high", "low", "close", "volume"], rows)
    ok(good["header_ok"], "schema: 정상 헤더 통과")
    bad = check_schema(["date", "o", "h", "l", "c", "v"], rows)
    ok(not bad["header_ok"], "schema: 틀린 헤더 검출")

    bad_date = [R(2, "2026/01/02", 10, 11, 9, 10), R(3, "20260103", 10, 11, 9, 10)]
    s = check_schema(EXPECTED, bad_date)
    ok(s["bad_date_count"] == 2, "schema: 날짜 형식 위반 2건")


EXPECTED = ["date", "open", "high", "low", "close", "volume"]


# ─────────────────────────────────────────── check_ohlc

def test_ohlc():
    clean = [R(2, "2026-01-02", 10, 11, 9, 10.5)]
    ok(check_ohlc(clean) == [], "ohlc: 정상 봉 위반 없음")

    hl = check_ohlc([R(2, "2026-01-02", 10, 9, 11, 10)])  # high<low
    ok(len(hl) == 1 and any("high" in p for p in hl[0]["problems"]), "ohlc: high<low 검출")

    ho = check_ohlc([R(2, "2026-01-02", 10, 10.4, 9, 10.5)])  # close>high
    ok(len(ho) == 1, "ohlc: high<close 검출")

    neg = check_ohlc([R(2, "2026-01-02", 0, 11, -1, 10)])  # 가격<=0
    ok(len(neg) == 1 and any("<=0" in p for p in neg[0]["problems"]), "ohlc: 가격<=0 검출")

    nv = check_ohlc([R(2, "2026-01-02", 10, 11, 9, 10, v=-5)])  # volume<0
    ok(len(nv) == 1 and any("volume" in p for p in nv[0]["problems"]), "ohlc: volume<0 검출")


# ─────────────────────────────────────────── duplicates·ordering

def test_dupes_ordering():
    dup = find_duplicates([R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-02", 10, 11, 9, 10)])
    ok(len(dup) == 1 and dup[0]["date"] == "2026-01-02", "dupes: 중복 날짜 검출")
    uniq = find_duplicates([R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-03", 10, 11, 9, 10)])
    ok(uniq == [], "dupes: 고유 날짜 통과")

    asc = check_ordering([R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-05", 10, 11, 9, 10)])
    ok(asc["ascending_in_file"], "ordering: 오름차순 통과")
    desc = check_ordering([R(2, "2026-01-05", 10, 11, 9, 10), R(3, "2026-01-02", 10, 11, 9, 10)])
    ok(not desc["ascending_in_file"], "ordering: 뒤섞임 검출")


# ─────────────────────────────────────────── gaps

def test_gaps():
    # 금(01-02)→월(01-05) = 3일 = 정상 주말(관용 4일 이내)
    weekend = [R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-05", 10, 11, 9, 10)]
    ok(find_gaps(weekend) == [], "gaps: 3일 주말 갭 무시")

    # 01-02 → 01-20 = 18일 갭
    big = [R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-20", 10, 11, 9, 10)]
    g = find_gaps(big)
    ok(len(g) == 1 and g[0]["gap_days"] == 18, "gaps: 18일 결측 검출")


# ─────────────────────────────────────────── weekend bars

def test_weekend():
    # 2026-01-03 = 토요일, 2026-01-02 = 금요일
    w = find_weekend_bars([R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-03", 10, 11, 9, 10)])
    ok(len(w) == 1 and w[0]["date"] == "2026-01-03", "weekend: 토요일 봉 검출")


# ─────────────────────────────────────────── anomalies

def test_anomalies():
    # 종가 10 → 13 = +30% (임계 20% 초과)
    jump = [R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-05", 10, 14, 10, 13)]
    a = find_anomalies(jump)
    ok(len(a) == 1 and a[0]["date"] == "2026-01-05", "anomaly: +30% 점프 검출")

    # 종가 10 → 10.5 = +5% (임계 이내)
    calm = [R(2, "2026-01-02", 10, 11, 9, 10), R(3, "2026-01-05", 10, 11, 10, 10.5)]
    ok(find_anomalies(calm) == [], "anomaly: 5% 정상변동 통과")

    # prev_close 0 은 건너뜀(0분모 방지)
    zero = [R(2, "2026-01-02", 0.01, 0.01, 0.01, 0), R(3, "2026-01-05", 10, 11, 10, 10)]
    ok(find_anomalies(zero) == [], "anomaly: prev_close=0 안전 건너뜀")


# ─────────────────────────────────────────── zero volume·adequacy

def test_zerovol_adequacy():
    zv = zero_volume_bars([R(2, "2026-01-02", 10, 11, 9, 10, v=0), R(3, "2026-01-05", 10, 11, 9, 10, v=100)])
    ok(len(zv) == 1 and zv[0]["date"] == "2026-01-02", "zerovol: 거래량0 봉 검출")

    a100 = adequacy(100)
    ok(a100["enough_for_ma20"] and not a100["enough_for_ma200"], "adequacy: 100봉 MA20 O·MA200 X")
    ok(a100["bars_after_warmup"] == 80, "adequacy: 워밍업 후 잔여 80봉")
    a10 = adequacy(10)
    ok(not a10["enough_for_ma20"], "adequacy: 10봉 MA20 부족")


def main() -> int:
    for t in (test_parse_rows, test_schema, test_ohlc, test_dupes_ordering,
              test_gaps, test_weekend, test_anomalies, test_zerovol_adequacy):
        t()
    print(f"\n{'전부 통과' if _fails == 0 else str(_fails) + '건 실패'} — 데이터 품질 탐지기 단위테스트")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
