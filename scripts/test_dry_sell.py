"""드라이런 매도 레그 회귀 — STOCK_MINT 미설정에서도 매도 견적이 만들어져야 한다.

재현한 결함 (Cloud Run 첫 배포에서 발견, 2026-07-26):
  STOCK_MINT 가 없으면 engine.start 가 stock_mint=None 을 브로커에 넘겼고, 매도 견적을 만드는
  매 틱마다  ValueError: stock_mint 미설정 — 매도 견적 불가  (broker_agent.make_stock_required)
  가 터졌다. 매수만 체결되고 익절이 전혀 되지 않아 수익률 시연 자체가 불가능했다.
  로컬은 .env 에 STOCK_MINT 가 있어 드러나지 않았고, 배포본(환경변수 미설정)에서만 나타났다.

수정: 드라이런은 온체인을 건드리지 않으므로 자리표시 민트(DRY_STOCK_MINT)를 쓴다.
      라이브는 그대로 세션 시작에서 fail-fast 한다(자리표시가 체인에 닿는 경로 없음).
      가드의 자산 대조(guard.check_stock_transfer)는 자리표시에서도 동일하게 작동한다.

재현: python scripts/test_dry_sell.py   (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import dataclasses
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.trading_agent import Decision  # noqa: E402
from config import CFG as REAL_CFG  # noqa: E402
from web import engine as eng  # noqa: E402
from web import events as ev  # noqa: E402
from web.engine import TradingEngine, EngineError  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class _RecordingBus(EventBus):
    """ERROR 이벤트를 수집해, 매 틱 조용히 실패하는 회귀를 잡는다."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []

    def emit(self, kind, payload):  # type: ignore[override]
        if kind == ev.ERROR:
            self.errors.append(str((payload or {}).get("message", "")))
        return super().emit(kind, payload)


def _engine(bus=None) -> TradingEngine:
    return TradingEngine(bus or _RecordingBus(), BaseStore())


async def _start(engine: TradingEngine, mode="dry", symbols=("AAPL",)):
    feed = {"type": "replay", "dataset": "daily", "symbols": list(symbols)}
    await engine.start(mode, {"type": "condition", "brain": "rule"}, feed, autostart=False)


# ---------- 1) 결함 재현 (음성 대조) — 자리표시가 없으면 매도 견적이 실제로 터진다 ----------
async def test_regression_reproduces() -> None:
    print("\n[1] 결함 재현 — stock_mint=None 이면 매도 견적 불가")
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint="")
    engine = _engine()
    await _start(engine)
    agent = engine.agents["AAPL"]
    price = Decimal(agent._history[-1])
    await engine._buy_cycle("AAPL", agent, price, Decision("buy", "forced", Decimal("10")))
    check("강제 매수로 포지션 확보", agent.position.quantity > 0,
          f"보유 {agent.position.quantity}")

    # 수정 전 상태를 인위적으로 만든다 (브로커의 민트를 None 으로)
    engine._broker.stock_mint = None
    raised = ""
    try:
        await engine._sell_cycle("AAPL", agent, price, Decision("sell", "forced", Decimal("0")))
    except ValueError as e:
        raised = str(e)
    check("민트가 None 이면 매도 견적이 ValueError 로 실패(과거 증상 재현)",
          "stock_mint 미설정" in raised, raised or "예외 없음")


# ---------- 2) 수정 확인 — STOCK_MINT 미설정 드라이런에서 매도가 체결된다 ----------
async def test_dry_sell_works_without_stock_mint() -> None:
    print("\n[2] 수정 확인 — STOCK_MINT 미설정 드라이런에서 매도 체결")
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint="")
    engine = _engine()
    await _start(engine)
    check("엔진이 자리표시 민트를 배선(None 아님)", engine._stock_mint is not None,
          str(engine._stock_mint))
    check("자리표시 값이 DRY_STOCK_MINT 와 일치", engine._stock_mint == eng.DRY_STOCK_MINT)

    agent = engine.agents["AAPL"]
    price = Decimal(agent._history[-1])
    await engine._buy_cycle("AAPL", agent, price, Decision("buy", "forced", Decimal("10")))
    qty_before = agent.position.quantity
    await engine._sell_cycle("AAPL", agent, price, Decision("sell", "forced", Decimal("0")))

    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("매도 체결이 1건 기록됨", len(sells) == 1, f"{len(sells)}건")
    check("매도가 settled 상태", bool(sells) and sells[0]["status"] == "settled",
          sells[0]["status"] if sells else "없음")
    check("포지션이 청산됨", agent.position.quantity < qty_before,
          f"{qty_before} → {agent.position.quantity}")
    mint_errors = [m for m in engine.bus.errors if "stock_mint" in m]
    check("stock_mint 관련 오류 이벤트 0건", not mint_errors, "; ".join(mint_errors))
    check("가드 유출 0", engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))


# ---------- 3) 라이브는 여전히 fail-fast ----------
async def test_live_still_fails_fast() -> None:
    print("\n[3] 라이브는 STOCK_MINT 없이 시작 금지(fail-fast 유지)")
    # 3-a) 웹 라이브 잠금(BUG-04 수정, 기본 차단)이 먼저 걸린다
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint="", allow_live_from_web=False)
    msg = ""
    try:
        await _start(_engine(), mode="live")
    except EngineError as e:
        msg = str(e)
    check("웹 라이브 잠금이 먼저 거부", "ALLOW_LIVE_FROM_WEB" in msg, msg or "거부되지 않음")

    # 3-b) 잠금을 열어도 STOCK_MINT 가 없으면 세션이 시작되지 않는다(자리표시는 드라이 전용)
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint="", allow_live_from_web=True)
    msg = ""
    try:
        await _start(_engine(), mode="live")
    except EngineError as e:
        msg = str(e)
    check("잠금 해제 후에도 라이브 시작이 거부됨", bool(msg), msg or "거부되지 않음")
    check("거부 사유가 STOCK_MINT 미설정", "STOCK_MINT" in msg, msg)


# ---------- 4) 설정된 민트는 자리표시로 덮지 않는다 ----------
async def test_configured_mint_wins() -> None:
    print("\n[4] STOCK_MINT 가 설정돼 있으면 그 값을 그대로 쓴다")
    real = "So11111111111111111111111111111111111111112"   # 임의의 유효 주소
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint=real)
    engine = _engine()
    await _start(engine)
    check("설정값이 그대로 배선됨", str(engine._stock_mint) == real, str(engine._stock_mint))
    check("자리표시로 덮이지 않음", engine._stock_mint != eng.DRY_STOCK_MINT)


async def main() -> int:
    try:
        await test_regression_reproduces()
        await test_dry_sell_works_without_stock_mint()
        await test_live_still_fails_fast()
        await test_configured_mint_wins()
    finally:
        eng.CFG = REAL_CFG   # 다른 테스트에 영향 주지 않도록 원복

    ok = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    print(f"\n결과: {ok}/{total} 통과")
    for name, cond, detail in _results:
        if not cond:
            print(f"  실패: {name} — {detail}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
