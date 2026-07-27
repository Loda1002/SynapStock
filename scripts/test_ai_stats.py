"""판단 출처 계측 검증 — "이 세션이 정말 AI 로 구동됐는가"를 증빙할 수 있는가.

배경(심사 리포트 축② 갭): 온체인 tx 아티팩트가 판단 출처를 기록하지 않아, 9건의 tx 중
Gemini 관여분이 몇 건인지 확인할 수 없었다. TradingEngine._ai_stats() 가 세션 전체의
출처 집계를 만들고 상태 스냅샷·세션 요약(Firestore)·tx 아카이브가 모두 이 값을 싣는다.

검증 방식: 실제 Gemini 를 부르지 않고 가짜 두뇌를 agent.brain 에 꽂아 드라이 세션을
결정론적으로 스텝한다(test_multistock 과 같은 autostart=False 하네스).

재현: python scripts/test_ai_stats.py  (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.trading_agent import Decision  # noqa: E402
from web import engine as engine_mod  # noqa: E402
from web.engine import TradingEngine  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeBrain:
    """지시한 판단만 돌려주는 가짜 Gemini (실제 API 호출 없음).

    두뇌는 두 곳에서 쓰인다 — 판단(decide)과 청구서 의미 대조(_call). 가짜도 둘 다
    구현해야 세션이 실제 API 를 부르지 않는다(engine.replace_brain 참조)."""

    def __init__(self, action: str, spend: str = "10"):
        self.action = action
        self.spend = Decimal(spend)
        self.calls = 0
        self.semantic_calls = 0

    @property
    def available(self) -> bool:
        return True

    def decide(self, *args, **kwargs) -> Decision:
        self.calls += 1
        return Decision(self.action, "가짜 AI 판단", self.spend, source="gemini")

    def _call(self, prompt: str) -> str:
        """의미 대조 — 이 테스트의 청구서는 정직하므로 항상 일치 판정."""
        self.semantic_calls += 1
        return '{"match": true, "reason": "주문 의도와 같은 청구서"}'


class BoomBrain:
    @property
    def available(self) -> bool:
        return True

    def decide(self, *args, **kwargs) -> Decision:
        raise RuntimeError("무료 티어 한도 초과 — 쿨다운 30초 남음")

    def _call(self, prompt: str) -> str:
        raise RuntimeError("무료 티어 한도 초과 — 쿨다운 30초 남음")


def _engine() -> TradingEngine:
    return TradingEngine(EventBus(), BaseStore())


async def _start(engine: TradingEngine, symbols, brain=None):
    await engine.start("dry", {"type": "condition"},
                       {"type": "replay", "dataset": "daily", "symbols": symbols},
                       autostart=False)
    if brain is not None:
        # 판단·의미 대조 두 곳 모두 교체한다 — 한쪽만 바꾸면 나머지가 실제 API 를 부른다.
        engine.replace_brain(brain)
    return engine


# ---------- 1) 규칙 밖 개시를 시도한 AI 는 rule-gate 로 계측된다 ----------
async def test_gated_counted() -> None:
    print("\n[1] 규칙 밖 매수를 시도하는 AI — rule-gate 계측")
    engine = await _start(_engine(), ["AAPL"], FakeBrain("buy"))
    for _ in range(6):
        await engine._tick_once()
    ai = engine.state_snapshot()["ai"]
    check("판단 총계 == 틱 수", ai["decisions_total"] == 6, str(ai["decisions_total"]))
    check("Gemini 호출 수가 총계와 일치", ai["gemini_calls"] == 6, str(ai["gemini_calls"]))
    check("규칙 밖 개시가 rule-gate 로 집계됨", ai["gemini_gated"] > 0, str(ai["gemini_gated"]))
    check("집행된 AI 판단 + 강등 = 호출 수",
          ai["gemini_decisions"] + ai["gemini_gated"] == ai["gemini_calls"],
          f"{ai['gemini_decisions']}+{ai['gemini_gated']} vs {ai['gemini_calls']}")
    check("두뇌 라벨이 기록됨", bool(ai["brain"]), ai["brain"])
    check("by_source 합계가 총계와 일치", sum(ai["by_source"].values()) == ai["decisions_total"])
    await engine._finalize()


# ---------- 2) AI 의 hold 는 gemini 로 그대로 집계 ----------
async def test_hold_counted_as_gemini() -> None:
    print("\n[2] AI 의 보류(hold) — gemini 로 집계")
    engine = await _start(_engine(), ["AAPL"], FakeBrain("hold"))
    for _ in range(5):
        await engine._tick_once()
    ai = engine.state_snapshot()["ai"]
    check("전건이 gemini 출처", ai["gemini_decisions"] == 5, str(ai["gemini_decisions"]))
    check("강등 0건", ai["gemini_gated"] == 0, str(ai["gemini_gated"]))
    check("AI 판단 비중 100%", ai["gemini_share_pct"].startswith("100"), ai["gemini_share_pct"])
    check("행동 집계에 hold 5건", ai["by_action"].get("hold") == 5, str(ai["by_action"]))
    await engine._finalize()


# ---------- 3) 호출 실패는 rule-fallback 으로 구분 계측 ----------
async def test_fallback_counted() -> None:
    print("\n[3] Gemini 호출 실패 — rule-fallback 구분 계측")
    engine = await _start(_engine(), ["AAPL"], BoomBrain())
    for _ in range(4):
        await engine._tick_once()
    ai = engine.state_snapshot()["ai"]
    check("폴백 4건", ai["rule_fallbacks"] == 4, str(ai["rule_fallbacks"]))
    check("집행된 AI 판단 0건", ai["gemini_decisions"] == 0, str(ai["gemini_decisions"]))
    check("호출 시도는 4건으로 잡힘(실패도 시도)", ai["gemini_calls"] == 4, str(ai["gemini_calls"]))
    await engine._finalize()


# ---------- 4) 두뇌 없는 결정론 경로(추세추종)는 AI 호출 0 ----------
async def test_rule_only_session() -> None:
    print("\n[4] 추세추종(결정론 규칙) 세션 — AI 호출 0")
    engine = _engine()
    await engine.start("dry", {"type": "trend", "trend_signal": "pxma20"},
                       {"type": "replay", "dataset": "bear", "symbol": "TSLA"},
                       autostart=False)
    for _ in range(5):
        await engine._tick_once()
    ai = engine.state_snapshot()["ai"]
    check("Gemini 호출 0", ai["gemini_calls"] == 0, str(ai["gemini_calls"]))
    check("전건 rule 출처", ai["by_source"].get("rule") == 5, str(ai["by_source"]))
    check("두뇌 라벨이 'Gemini 미사용' 을 명시", "미사용" in ai["brain"], ai["brain"])
    await engine._finalize()


# ---------- 5) 타임라인 상한(500)에 잘려도 집계는 세션 전체 ----------
async def test_counts_survive_truncation() -> None:
    print("\n[5] 판단 타임라인이 잘려도 집계는 전체 세션 유지")
    original = engine_mod.MAX_DECISIONS
    engine_mod.MAX_DECISIONS = 3          # 일부러 아주 작게
    try:
        engine = await _start(_engine(), ["AAPL"], FakeBrain("hold"))
        for _ in range(7):
            await engine._tick_once()
        ai = engine.state_snapshot()["ai"]
        check("타임라인은 상한까지만 보관", len(engine.decisions) == 3, str(len(engine.decisions)))
        check("집계는 세션 전체(7건)", ai["decisions_total"] == 7, str(ai["decisions_total"]))
        await engine._finalize()
    finally:
        engine_mod.MAX_DECISIONS = original


# ---------- 6) 체결이 어느 판단에서 나왔는지 tx 단위로 남는다 ----------
async def test_trades_by_source() -> None:
    print("\n[6] 체결의 판단 출처 — tx 단위 AI 관여 증빙")
    engine = await _start(_engine(), ["AAPL"], FakeBrain("hold"))
    agent = engine.agents["AAPL"]
    for _ in range(3):        # 집계가 비어 있지 않은 상태에서 대조하기 위해 먼저 몇 틱 진행
        await engine._tick_once()
    price = Decimal(agent._history[-1])
    # 규칙 신호를 기다리지 않고 매수 사이클을 직접 태워 체결을 만든다(계측 대상은 기록 경로)
    await engine._buy_cycle("AAPL", agent, price,
                            Decision("buy", "가짜 AI 판단", Decimal("10"), source="gemini"))
    ai = engine.state_snapshot()["ai"]
    check("체결 1건이 gemini 판단으로 기록",
          ai["trades_by_decision_source"].get("gemini") == 1,
          str(ai["trades_by_decision_source"]))
    check("거래 행에도 decision_source 가 있음",
          engine.trades[0].get("decision_source") == "gemini", str(engine.trades[0].get("decision_source")))
    # 세션 요약(Firestore 문서)·브리핑 근거에도 같은 블록이 실린다
    summary = engine._session_summary("", None)
    check("세션 요약에 ai 블록 포함", isinstance(summary.get("ai"), dict), str(type(summary.get("ai"))))
    check("세션 요약의 두뇌 라벨 일치", summary["ai"]["brain"] == ai["brain"])
    stats = engine._briefing_stats()
    check("브리핑 근거에도 ai 블록 포함", isinstance(stats.get("ai"), dict))
    check("브리핑 출처 집계가 계측과 일치", stats["decisions_by_source"] == ai["by_source"],
          f"{stats['decisions_by_source']} vs {ai['by_source']}")
    await engine._finalize()


# ---------- 7) 새 세션은 계측이 0에서 시작 ----------
async def test_reset_between_sessions() -> None:
    print("\n[7] 새 세션 시작 시 계측 리셋")
    engine = await _start(_engine(), ["AAPL"], FakeBrain("hold"))
    for _ in range(3):
        await engine._tick_once()
    check("첫 세션 집계 3건", engine.state_snapshot()["ai"]["decisions_total"] == 3)
    await engine._finalize()
    await _start(engine, ["AAPL"], FakeBrain("hold"))
    ai = engine.state_snapshot()["ai"]
    check("두 번째 세션은 0에서 시작", ai["decisions_total"] == 0, str(ai["decisions_total"]))
    check("출처 집계도 비어 있음", ai["by_source"] == {}, str(ai["by_source"]))
    await engine._finalize()


async def main() -> int:
    print("=== 판단 출처 계측 (축② AI 활용 증빙) ===")
    await test_gated_counted()
    await test_hold_counted_as_gemini()
    await test_fallback_counted()
    await test_rule_only_session()
    await test_counts_survive_truncation()
    await test_trades_by_source()
    await test_reset_between_sessions()
    failed = [r for r in _results if not r[1]]
    print(f"\n===== 결과: 통과 {len(_results) - len(failed)} · 실패 {len(failed)} =====")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
