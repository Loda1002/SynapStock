"""데이터 수집 부서 증거수집기 — 시세 CSV 의 '품질 표면'을 JSON 스냅샷으로 덤프.

LLM 없음. 추정 없음. 오직 파일 내용·실제 소비 코드 경로(load_bars)만 본다.
데이터 부서(워크플로우 data-dept)가 이 JSON 을 근거로 시세 CSV 를 점검한다.
인스펙터 에이전트가 기억이 아니라 이 실물 좌표(날짜·행번호)에 붙게 만드는 게 목적이다.

collect_evidence.py · collect_bugscan.py 와 같은 설계 원칙:
  - 각 섹션은 독립. 하나가 실패해도 나머지는 수집하고, 실패는
    {"status":"error","reason":...} 로 남긴다(조용히 넘어가지 않는다).
  - 숫자는 전부 재현 가능한 출처에서 온다: 파일 바이트·실제 load_bars() 결과.

이 수집기는 '판단'하지 않는다 — 마커(OHLC 위반·갭·이상치)는 결함 확정이 아니라
'여기를 봐라'는 좌표다. 진짜 결함(vs 공휴일 갭·실적 실변동)인지는 워크플로우가 행을 열어 판정한다.

핵심 설계(ai-regression-testing 스킬): 독립 재파싱만으로 '괜찮아 보인다'는 사각지대를
피하려고, **실제 소비 경로인 market.price_feed.load_bars() 로 로드**해 데모·백테스트가
정말 이 CSV 를 소화할 수 있는지 대조한다. 판정 로직은 순수 함수로 분리해 test_dataquality.py
가 직접 호출·검증한다.

읽기 전용 — CSV·앱 코드·네트워크를 일절 건드리지 않는다(자동 수집 없음).

실행:
  .venv/Scripts/python.exe -m scripts.collect_dataquality
  → docs/reports/_dataquality_YYYYMMDD_HHMMSS.json 생성, 경로를 stdout 마지막 줄에 출력.
"""
from __future__ import annotations

import config  # noqa: F401 — cp949 콘솔 인코딩 안전화(모든 진입점 필수)

import csv
import glob
import io
import json
import os
import re
import sys
import traceback
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# 점검 대상 = 시세 입력 CSV 만(사용자 확정 2026-07-25). artifacts 정합은 judge-dept 몫.
DATA_GLOB = os.path.join(REPO, "data", "market", "*.csv")

EXPECTED_HEADER = ["date", "open", "high", "low", "close", "volume"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 판정 상수(임계값) — 마커는 '점검 좌표'이지 결함 확정이 아니다.
GAP_DAYS = 4          # 달력 일수 차 > 4 = 결측 의심(정상 주말+공휴일 관용)
ANOMALY_PCT = Decimal("20")  # 전일 종가 대비 |변동| > 20% = 분할/오류 의심(실적 실변동일 수도)
DEFAULT_WARMUP = 20   # MA20 워밍업 봉 수(config.replay_warmup 기본값과 일치)
SAMPLE_CAP = 12       # 위반/이상치 표본 상한(인스펙터에게 줄 좌표 수)


# ═══════════════════════════════════════════════ 순수 판정 함수 (test 가 직접 호출)
#
# 아래 함수들은 파일·네트워크를 모른다. 파싱된 행(dict)만 받아 판정한다 —
# test_dataquality.py 가 합성 입력으로 각 탐지기를 검증할 수 있게 하기 위함.
# 행 dict 형태: {"line": int(파일 줄번호), "date": str, "open"/"high"/"low"/"close": Decimal, "volume": int}

def parse_rows(text: str):
    """CSV 텍스트 → (header, rows, parse_errors). 순수 — 파일 I/O 없음.

    rows 는 성공적으로 파싱된 행만. parse_errors 는 필드수/숫자변환 실패 좌표(줄번호 포함).
    load_bars 와 달리 '첫 오류에서 중단'하지 않고 전부 훑어 모든 문제 좌표를 모은다.
    """
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], [], [{"line": 1, "reason": "빈 파일"}]
    header = reader[0]
    rows = []
    errors = []
    for i, raw in enumerate(reader[1:], start=2):  # 파일 줄번호(헤더=1줄)
        if not any(cell.strip() for cell in raw):
            continue  # 완전 빈 줄은 건너뜀(오류 아님)
        if len(raw) != len(EXPECTED_HEADER):
            errors.append({"line": i, "reason": f"필드수 {len(raw)}!={len(EXPECTED_HEADER)}", "raw": raw[:8]})
            continue
        d, o, h, low, c, v = raw
        try:
            row = {
                "line": i,
                "date": d.strip(),
                "open": Decimal(o), "high": Decimal(h),
                "low": Decimal(low), "close": Decimal(c),
                "volume": int(float(v)) if str(v).strip() else 0,
            }
        except (InvalidOperation, ValueError) as e:
            errors.append({"line": i, "reason": f"숫자 변환 실패: {e}", "raw": raw})
            continue
        rows.append(row)
    return header, rows, errors


def check_schema(header, rows):
    """헤더 일치 + 날짜 형식(YYYY-MM-DD) 위반 좌표."""
    header_norm = [h.strip() for h in (header or [])]
    bad_dates = [
        {"line": r["line"], "date": r["date"]}
        for r in rows if not DATE_RE.match(r["date"])
    ]
    return {
        "header_ok": header_norm == EXPECTED_HEADER,
        "expected_header": EXPECTED_HEADER,
        "actual_header": header_norm,
        "bad_date_count": len(bad_dates),
        "bad_date_rows": bad_dates[:SAMPLE_CAP],
    }


def check_ohlc(rows):
    """봉별 OHLC 정합 위반. high≥low, high≥max(open,close), low≤min(open,close), 전부>0, volume≥0."""
    viol = []
    for r in rows:
        problems = []
        o, h, low, c = r["open"], r["high"], r["low"], r["close"]
        if h < low:
            problems.append(f"high({h})<low({low})")
        if h < o or h < c:
            problems.append(f"high({h})<open/close({o}/{c})")
        if low > o or low > c:
            problems.append(f"low({low})>open/close({o}/{c})")
        if any(x <= 0 for x in (o, h, low, c)):
            problems.append("가격<=0")
        if r["volume"] < 0:
            problems.append(f"volume<0({r['volume']})")
        if problems:
            viol.append({"line": r["line"], "date": r["date"], "problems": problems})
    return viol


def find_duplicates(rows):
    """중복 날짜 목록(같은 날짜가 2회 이상)."""
    seen = {}
    for r in rows:
        seen.setdefault(r["date"], []).append(r["line"])
    return [{"date": d, "lines": lns} for d, lns in seen.items() if len(lns) > 1]


def check_ordering(rows):
    """파일에 적힌 순서(정렬 전)가 날짜 오름차순인가. load_bars 는 정렬하지만 원본 흐트러짐은 냄새."""
    dates = [r["date"] for r in rows]
    return {"ascending_in_file": dates == sorted(dates)}


def _to_date(s: str):
    return datetime.strptime(s, "%Y-%m-%d").date()


def find_gaps(rows, gap_days: int = GAP_DAYS):
    """날짜 오름차순으로 인접 봉의 달력 일수 차가 gap_days 초과면 결측 의심.

    3일 주말(금→월)은 정상이라 기본 4일 관용. 파싱 가능한 날짜만 대상.
    """
    valid = sorted((r for r in rows if DATE_RE.match(r["date"])), key=lambda r: r["date"])
    gaps = []
    for prev, cur in zip(valid, valid[1:]):
        try:
            delta = (_to_date(cur["date"]) - _to_date(prev["date"])).days
        except ValueError:
            continue
        if delta > gap_days:
            gaps.append({"from": prev["date"], "to": cur["date"], "gap_days": delta})
    return gaps


def find_weekend_bars(rows):
    """주말(토=5·일=6) 날짜의 봉 — 주식 일봉엔 없어야 정상."""
    out = []
    for r in rows:
        if not DATE_RE.match(r["date"]):
            continue
        try:
            wd = _to_date(r["date"]).weekday()
        except ValueError:
            continue
        if wd >= 5:
            out.append({"line": r["line"], "date": r["date"], "weekday": wd})
    return out


def find_anomalies(rows, pct: Decimal = ANOMALY_PCT):
    """전일 종가 대비 |변동률| > pct(%) 인 봉 — 분할/오류 의심(단, 실적 실변동일 수도).

    날짜 오름차순 인접 봉만 비교. prev_close==0 은 건너뜀(0분모 방지).
    """
    valid = sorted((r for r in rows if DATE_RE.match(r["date"])), key=lambda r: r["date"])
    out = []
    for prev, cur in zip(valid, valid[1:]):
        pc = prev["close"]
        if pc == 0:
            continue
        change = (cur["close"] - pc) / pc * Decimal("100")
        if abs(change) > pct:
            out.append({
                "date": cur["date"], "prev_close": str(pc), "close": str(cur["close"]),
                "change_pct": str(change.quantize(Decimal("0.01"))),
            })
    return out


def zero_volume_bars(rows):
    """거래량 0 인 봉 — 휴장/데이터 결측 의심."""
    return [{"line": r["line"], "date": r["date"]} for r in rows if r["volume"] == 0]


def adequacy(bar_count: int, warmup: int = DEFAULT_WARMUP):
    """지표 워밍업·재생 여력 충분성. MA20/MA200 을 계산할 봉이 있는가."""
    return {
        "bar_count": bar_count,
        "warmup": warmup,
        "enough_for_ma20": bar_count >= 20,
        "enough_for_ma200": bar_count >= 200,
        "bars_after_warmup": max(0, bar_count - warmup),
    }


def profile(rows):
    """빠른 수치 프로필 — 종가 범위·평균, 총거래량, 기간."""
    if not rows:
        return {}
    closes = [r["close"] for r in rows]
    dates = [r["date"] for r in rows if DATE_RE.match(r["date"])]
    total = sum(closes)
    return {
        "close_min": str(min(closes)),
        "close_max": str(max(closes)),
        "close_mean": str((total / len(closes)).quantize(Decimal("0.01"))),
        "total_volume": sum(r["volume"] for r in rows),
        "date_first": min(dates) if dates else None,
        "date_last": max(dates) if dates else None,
    }


# ═══════════════════════════════════════════════ 실제 소비 경로 대조 (ai-regression-testing)

def load_via_consumer(csv_path: str, parsed_count: int) -> dict:
    """실제 데모·백테스트가 쓰는 market.price_feed.load_bars() 로 로드해 대조.

    독립 재파싱이 '괜찮아 보인다' 해도, 진짜 소비 경로가 이 CSV 를 소화 못 하면 데모가 깨진다.
    load_bars 는 첫 불량 행에서 ValueError 를 던지므로, 그 예외 메시지가 곧 '데모가 어디서
    깨지는가'의 좌표다. parsed_count(수집기가 훑은 유효 행 수)와 bar 수 차이도 함께 본다.
    """
    try:
        from market.price_feed import load_bars
    except Exception as e:  # noqa: BLE001
        return {"import_error": f"{type(e).__name__}: {e}"}
    try:
        bars = load_bars(csv_path)
        return {
            "loaded_ok": True,
            "bar_count": len(bars),
            "matches_parsed": (len(bars) == parsed_count),
            "first_date": bars[0].date, "last_date": bars[-1].date,
        }
    except Exception as e:  # noqa: BLE001 — load_bars 가 던지는 것 = 소비 실패 좌표
        return {"loaded_ok": False, "consumer_error": f"{type(e).__name__}: {e}"}


# ═══════════════════════════════════════════════ CSV 1개 종합

def inspect_csv(path: str) -> dict:
    """파일 1개를 읽어 전 판정 함수를 돌린 결과 dict. 파일 I/O 는 여기서만."""
    rel = os.path.relpath(path, REPO)
    base = os.path.basename(path)
    symbol = base.split("_")[0] if "_" in base else base
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as e:  # noqa: BLE001
        return {"file": rel, "symbol": symbol, "status": "error", "reason": f"읽기 실패: {e}"}

    header, rows, parse_errors = parse_rows(text)
    ohlc = check_ohlc(rows)
    gaps = find_gaps(rows)
    weekend = find_weekend_bars(rows)
    anomalies = find_anomalies(rows)
    dupes = find_duplicates(rows)
    zerovol = zero_volume_bars(rows)

    return {
        "file": rel,
        "symbol": symbol,
        "row_count": len(rows),
        "parse_errors": parse_errors[:SAMPLE_CAP],
        "parse_error_count": len(parse_errors),
        "schema": check_schema(header, rows),
        "ordering": check_ordering(rows),
        "duplicates": dupes,
        "ohlc_violations": ohlc[:SAMPLE_CAP],
        "ohlc_violation_count": len(ohlc),
        "gaps": gaps[:SAMPLE_CAP],
        "gap_count": len(gaps),
        "weekend_bars": weekend[:SAMPLE_CAP],
        "weekend_bar_count": len(weekend),
        "anomalies": anomalies[:SAMPLE_CAP],
        "anomaly_count": len(anomalies),
        "zero_volume": zerovol[:SAMPLE_CAP],
        "zero_volume_count": len(zerovol),
        "adequacy": adequacy(len(rows)),
        "profile": profile(rows),
        "consumer_load": load_via_consumer(path, len(rows)),
    }


# ═══════════════════════════════════════════════ 조립·요약·기록

def _safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {
            "status": "error",
            "reason": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc().splitlines()[-3:],
        }


def collect_csvs() -> dict:
    files = sorted(glob.glob(glob.escape(os.path.dirname(DATA_GLOB)) + os.sep + "*.csv"))
    items = [inspect_csv(f) for f in files]
    return {"glob": os.path.relpath(DATA_GLOB, REPO), "count": len(files), "items": items}


def _freshness() -> dict:
    """마지막 봉 기준 경과일(벽시계 의존 — 재실행마다 달라짐, 신선도 지표라 의도된 것)."""
    today = date.today()
    return {"today": today.isoformat()}


def _summary(csvs: dict) -> dict:
    """섹션에서 결정론적으로 유도한 빠른 지표(인스펙터 그라운딩용). 창작 없음."""
    items = csvs.get("items", []) if isinstance(csvs, dict) else []
    s = {
        "csv_count": len(items),
        "symbols": [it.get("symbol") for it in items if isinstance(it, dict)],
        "total_bars": sum(it.get("row_count", 0) for it in items if isinstance(it, dict)),
    }
    def _tot(key):
        return sum(it.get(key, 0) for it in items if isinstance(it, dict))
    s["schema_issues"] = [it.get("symbol") for it in items
                          if isinstance(it, dict) and not it.get("schema", {}).get("header_ok", True)]
    s["consumer_load_failures"] = [
        it.get("symbol") for it in items
        if isinstance(it, dict) and it.get("consumer_load", {}).get("loaded_ok") is False
    ]
    s["parse_error_total"] = _tot("parse_error_count")
    s["ohlc_violation_total"] = _tot("ohlc_violation_count")
    s["gap_total"] = _tot("gap_count")
    s["weekend_bar_total"] = _tot("weekend_bar_count")
    s["anomaly_total"] = _tot("anomaly_count")
    s["duplicate_total"] = sum(len(it.get("duplicates", [])) for it in items if isinstance(it, dict))
    s["zero_volume_total"] = _tot("zero_volume_count")
    s["not_enough_for_ma20"] = [
        it.get("symbol") for it in items
        if isinstance(it, dict) and not it.get("adequacy", {}).get("enough_for_ma20", True)
    ]
    s["any_load_failure"] = bool(s["consumer_load_failures"])
    return s


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sections = {
        "csvs": _safe(collect_csvs),
        "freshness": _safe(_freshness),
    }
    evidence = {
        "schema": "collect_dataquality/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "note": "데이터 부서(data-dept) 근거 스냅샷 — 모든 값은 파일/실제 load_bars() 유래. "
                "마커(OHLC 위반·갭·이상치)는 '점검 좌표'이지 결함 확정이 아니다(공휴일 갭·실적 실변동일 수 있음). 추정 금지.",
        "scope": "시세 입력 CSV(data/market/*.csv) · 읽기 전용 · 자동 수집 없음",
        "thresholds": {"gap_days": GAP_DAYS, "anomaly_pct": str(ANOMALY_PCT), "warmup": DEFAULT_WARMUP},
        "sections": sections,
        "summary": _summary(sections.get("csvs", {})),
    }

    out_dir = os.path.join(REPO, "docs", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"_dataquality_{ts}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)

    print("[collect_dataquality] 요약:", json.dumps(evidence["summary"], ensure_ascii=False))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
