"""판단 두뇌 선택(strategy.brain) 검증 — auto / rule / gemini.

배경: 두뇌 선택이 "GEMINI_API_KEY 가 있으면 무조건 Gemini"로 고정돼 있어, 같은 데이터에서
규칙 판단과 AI 판단을 나란히 돌려 비교할 방법이 없었다(백테스트에는 --brain 이 있는데
웹/API 에는 없었다). 심사 축②의 판단 출처 대조를 화면에서 재현하려면 스위치가 필요하다.

규칙:
  auto   — 키가 있으면 Gemini, 없으면 규칙 (기존 동작 그대로)
  rule   — 키가 있어도 Gemini 를 호출하지 않는다
  gemini — 키가 없으면 조용히 규칙으로 떨어지지 않고 즉시 오류 (설정 무시 버그 방지)
적립형(dca)·추세추종(trend)은 결정론적 규칙이라 brain 값과 무관하다.

재현: python -m scripts.test_brain_select   (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import dataclasses
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import CFG as REAL_CFG  # noqa: E402
from web import engine as eng  # noqa: E402
from web.engine import TradingEngine, EngineError  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def _label(strategy: dict, *, api_key: str) -> str:
    """주어진 전략 설정으로 드라이 세션을 구성하고 두뇌 표기를 돌려준다."""
    eng.CFG = dataclasses.replace(REAL_CFG, gemini_api_key=api_key, stock_mint="")
    engine = TradingEngine(EventBus(), BaseStore())
    await engine.start("dry", strategy,
                       {"type": "replay", "dataset": "daily", "symbols": ["AAPL"]},
                       autostart=False)
    return engine.brain_label


async def _error(strategy: dict, *, api_key: str) -> str:
    eng.CFG = dataclasses.replace(REAL_CFG, gemini_api_key=api_key, stock_mint="")
    engine = TradingEngine(EventBus(), BaseStore())
    try:
        await engine.start("dry", strategy,
                           {"type": "replay", "dataset": "daily", "symbols": ["AAPL"]},
                           autostart=False)
    except EngineError as e:
        return str(e)
    return ""


FAKE_KEY = "AIzaTestKeyForUnitTestOnly_NotReal_000000"   # developer 모드 분기용 더미


async def main() -> int:
    try:
        print("\n[1] auto — 키가 있으면 Gemini, 없으면 규칙")
        lab = await _label({"type": "condition"}, api_key=FAKE_KEY)
        check("auto + 키 있음 → Gemini 두뇌", "Gemini" in lab and "규칙 기반" not in lab, lab)
        lab = await _label({"type": "condition"}, api_key="")
        check("auto + 키 없음 → 규칙", "규칙 기반" in lab, lab)

        print("\n[2] rule — 키가 있어도 Gemini 를 쓰지 않는다")
        lab = await _label({"type": "condition", "brain": "rule"}, api_key=FAKE_KEY)
        check("rule + 키 있음 → 규칙", "규칙 기반" in lab, lab)
        check("사용자 지정임이 표기에 드러남", "사용자 지정" in lab, lab)

        print("\n[3] gemini — 키가 없으면 조용히 넘어가지 않는다")
        msg = await _error({"type": "condition", "brain": "gemini"}, api_key="")
        check("gemini + 키 없음 → 오류", bool(msg), msg or "오류 없음")
        check("오류 사유가 GEMINI_API_KEY", "GEMINI_API_KEY" in msg, msg)
        lab = await _label({"type": "condition", "brain": "gemini"}, api_key=FAKE_KEY)
        check("gemini + 키 있음 → Gemini 두뇌", "Gemini" in lab, lab)

        print("\n[4] 잘못된 값은 거부")
        msg = await _error({"type": "condition", "brain": "chatgpt"}, api_key=FAKE_KEY)
        check("허용 목록 밖 값 거부", "판단 두뇌" in msg, msg or "거부되지 않음")

        print("\n[5] 결정론적 전략은 brain 과 무관")
        lab = await _label({"type": "trend", "brain": "gemini"}, api_key=FAKE_KEY)
        check("추세추종은 brain 무시(Gemini 미사용)", "Gemini 미사용" in lab, lab)
        lab = await _label({"type": "dca", "brain": "gemini"}, api_key=FAKE_KEY)
        check("적립형은 brain 무시(Gemini 미사용)", "Gemini 미사용" in lab, lab)
    finally:
        eng.CFG = REAL_CFG

    ok = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    print(f"\n결과: {ok}/{total} 통과")
    for name, cond, detail in _results:
        if not cond:
            print(f"  실패: {name} — {detail}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
