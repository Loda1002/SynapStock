"""Gemini 429 처리 검증 — 일일(RPD) 한도와 분당(RPM) 한도를 구분하는가.

배경(2026-07-27 실측 버그 2건):
  A) 일일 한도 소진인데 응답의 retryDelay(41초)만 보고 쿨다운을 걸어, 하루 종일
     40초마다 헛호출하고 영영 회복하지 못했다. 무료 티어 일일 한도는 태평양 자정에만
     풀리므로 retryDelay 는 이 경우 의미가 없다.
  B) 원본 ClientError 본문이 1,000자가 넘는 JSON 인데 호출부가 100자에서 잘라 기록해,
     정작 필요한 quotaId·quotaValue 가 통째로 사라졌다. 화면만 보고는 분당 초과인지
     일일 소진인지 구분할 수 없어 원인 추적에 별도 스크립트가 필요했다.
     (artifacts/backtests/·artifacts/tx/ 의 과거 기록이 전부 이 잘린 형태로 남아 있다.)

재현: python -m scripts.test_quota_cooldown   (프로젝트 루트)
네트워크·API 키 불필요 — 실제 429 응답 본문을 문자열로 넣어 파서를 직접 검증한다.
"""
from __future__ import annotations
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.gemini_decider import (  # noqa: E402
    GeminiDecider, _DAILY_QUOTA_COOLDOWN_SEC,
)

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def _decider() -> GeminiDecider:
    """API 클라이언트 없이 파서만 쓰는 인스턴스 (__init__ 우회 — 네트워크·키 불필요)."""
    d = object.__new__(GeminiDecider)
    d.model = "gemini-flash-lite-latest"
    d._cooldown_until = 0.0
    d.format_retries = 0
    d.quota_scope = ""
    d.quota_limit = ""
    d.quota_id = ""
    return d


# 2026-07-27 라이브에서 실제로 받은 일일 한도 응답 (키·프로젝트 식별자 없음)
DAILY_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
    "quota, please check your plan and billing details. For more information on this error, "
    "head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current "
    "usage, head to: https://ai.dev/rate-limit. \\n* Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 500, "
    "model: gemini-3.5-flash-lite\\nPlease retry in 41.267844587s.', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', "
    "'violations': [{'quotaMetric': "
    "'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': "
    "'gemini-3.5-flash-lite', 'location': 'global'}, 'quotaValue': '500'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}"
)

# 분당 한도 형태 (quotaId 에 PerMinute)
RATE_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
    "quota. limit: 15, model: gemini-3.5-flash-lite\\nPlease retry in 12.5s.', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaId': "
    "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '15'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '12s'}]}}"
)

# quotaId 가 아예 없는 형태 (구형/축약 응답) — 기존 동작(retryDelay 존중)이 유지되어야 한다
BARE_429 = "429 RESOURCE_EXHAUSTED. Please retry in 20s."


def test_daily() -> None:
    print("\n== 일일(RPD) 한도 — 태평양 자정까지 안 풀린다 ==")
    d = _decider()
    t0 = time.time()
    d._enter_cooldown(DAILY_429)
    wait = d._cooldown_until - t0

    check("성격을 daily 로 판정", d.quota_scope == "daily", f"scope={d.quota_scope!r}")
    check("한도 값 500 추출", d.quota_limit == "500", f"limit={d.quota_limit!r}")
    check("quotaId 추출", d.quota_id == "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
          d.quota_id)
    # 결함 A 의 핵심: retryDelay 41초를 그대로 쓰면 하루 종일 헛호출한다
    check("retryDelay(41초)를 따르지 않는다", wait > 300, f"쿨다운 {wait:.0f}초")
    check(f"일일 쿨다운({_DAILY_QUOTA_COOLDOWN_SEC:.0f}초) 적용",
          abs(wait - _DAILY_QUOTA_COOLDOWN_SEC) < 5, f"쿨다운 {wait:.0f}초")

    msg = d.quota_message()
    check("메시지에 '일일' 명시", "일일" in msg, msg[:70])
    check("메시지에 한도 값 노출", "500" in msg, msg[:70])
    check("메시지에 회복 시점 안내", "태평양" in msg, msg[:70])


def test_rate() -> None:
    print("\n== 분당(RPM) 한도 — 수십 초 뒤 자동 회복 ==")
    d = _decider()
    t0 = time.time()
    d._enter_cooldown(RATE_429)
    wait = d._cooldown_until - t0

    check("성격을 rate 로 판정", d.quota_scope == "rate", f"scope={d.quota_scope!r}")
    check("한도 값 15 추출", d.quota_limit == "15", f"limit={d.quota_limit!r}")
    check("짧은 쿨다운 유지(<300초)", wait < 300, f"쿨다운 {wait:.0f}초")
    check("최소 30초는 재운다", wait >= 30, f"쿨다운 {wait:.0f}초")

    msg = d.quota_message()
    check("메시지에 '분당' 명시", "분당" in msg, msg[:70])
    check("메시지에 '일일' 오표기 없음", "일일" not in msg, msg[:70])


def test_bare() -> None:
    print("\n== quotaId 없는 응답 — 기존 동작(retryDelay 존중) 유지 ==")
    d = _decider()
    t0 = time.time()
    d._enter_cooldown(BARE_429)
    wait = d._cooldown_until - t0
    check("성격을 rate 로 취급(안전측)", d.quota_scope == "rate", f"scope={d.quota_scope!r}")
    check("retryDelay 20초 → 최소 30초로 올림", 29 <= wait <= 31, f"쿨다운 {wait:.0f}초")
    check("한도 값 없으면 빈 문자열", d.quota_limit == "", f"limit={d.quota_limit!r}")


def test_truncation() -> None:
    """결함 B: 호출부가 200자로 잘라도 사유가 남아야 한다."""
    print("\n== 잘림 내성 — TradingAgent 가 200자로 잘라도 사유가 남는가 ==")

    d = _decider()
    d._enter_cooldown(DAILY_429)
    summarized = d.quota_message().replace("\n", " ")[:200]
    check("요약 메시지가 200자 안에 들어간다", len(d.quota_message()) <= 200,
          f"{len(d.quota_message())}자")
    check("잘라도 '일일' 이 남는다", "일일" in summarized, summarized[:70])
    check("잘라도 한도 값이 남는다", "500" in summarized, summarized[:70])

    # 대조군: 원본 ClientError 를 그대로 잘랐을 때 (수정 전 동작)
    old_style = DAILY_429.replace("\n", " ")[:100]
    check("[대조] 원본 100자 절단은 quotaId 를 잃는다", "quotaId" not in old_style,
          f"…{old_style[-40:]}")
    check("[대조] 원본 200자 절단도 quotaId 를 잃는다",
          "GenerateRequestsPerDay" not in DAILY_429.replace("\n", " ")[:200],
          "요약 없이는 200자로도 부족하다")


def test_recovery() -> None:
    """쿨다운이 지나면 스스로 회복한다 (일일도 영구 차단이 아니다)."""
    print("\n== 회복 — 쿨다운 경과 후 다시 호출 가능 ==")
    d = _decider()
    d._enter_cooldown(DAILY_429)
    check("쿨다운 중에는 대기 상태", d._cooldown_until - time.time() > 0)
    d._cooldown_until = time.time() - 1      # 시간이 지난 상황을 모사
    check("쿨다운 경과 후 해제", d._cooldown_until - time.time() <= 0)


def main() -> int:
    print("Gemini 429 처리 검증 (네트워크·API 키 불필요)")
    test_daily()
    test_rate()
    test_bare()
    test_truncation()
    test_recovery()

    failed = [r for r in _results if not r[1]]
    print(f"\n{'=' * 60}")
    print(f"  총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
    if failed:
        for name, _, detail in failed:
            print(f"    실패: {name} — {detail}")
    print(f"{'=' * 60}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
