"""멀티 종목(동시 매수) 엔진 검증 — 하나의 예산·가드 아래 N종목 독립 운용.

드라이 세션을 백그라운드 루프 없이(autostart=False) 결정론적으로 스텝해, 계획서
(docs/multistock_plan.md §테스트)의 5가지를 확인한다:
  1) 예산 공유       — A 매수가 B 의 잔여 예산을 차감(같은 auth 공유)
  2) 포지션 독립     — 종목마다 독립 Position (한 종목 매수가 다른 종목에 안 섞임)
  3) 피드 소진 격리  — 한 종목 피드가 소진돼도 다른 종목은 계속 진행
  4) 세션 종료       — 전 종목 피드 소진 시 세션 자동 종료(REPLAY_ENDED + stop)
  5) 가드 KPI 합산   — attempts/blocked/leak 이 전 종목 합산이고, 정상 흐름 유출 0

추가:
  - 하위호환: 단일 종목(N=1) 스냅샷이 per_symbol 1개 = top-level 과 일치
  - 방어: 추세추종(올인)+멀티는 거부(먼저 진입한 종목의 예산 독식 방지)

재현: python scripts/test_multistock.py  (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.trading_agent import Decision  # noqa: E402
from web import events as ev  # noqa: E402
from web.engine import TradingEngine, EngineError  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def _engine() -> TradingEngine:
    return TradingEngine(EventBus(), BaseStore())


async def _start(engine: TradingEngine, symbols, dataset="daily", strategy=None, mode="dry"):
    feed = {"type": "replay", "dataset": dataset, "symbols": symbols}
    await engine.start(mode, strategy or {"type": "condition"}, feed, autostart=False)


async def _step_until_done(engine: TradingEngine, cap: int = 400) -> int:
    n = 0
    while not engine._stop_event.is_set() and n < cap:
        await engine._tick_once()
        n += 1
    return n


def _exhaust(engine: TradingEngine, sym: str) -> None:
    """해당 종목 재생 피드를 소진 상태로 만든다(남은 봉을 모두 소비한 것으로)."""
    feed = engine.feeds[sym]
    feed._idx = feed.total_bars


# ---------- 1) 예산 공유 + 2) 포지션 독립 (강제 매수로 결정론 확인) ----------
async def test_shared_budget_and_isolation() -> None:
    print("\n[1+2] 예산 공유 · 포지션 독립")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA"])
    a, b = engine.agents["AAPL"], engine.agents["TSLA"]

    check("두 종목 에이전트가 같은 auth(공유 예산)를 참조", a.auth is b.auth and a.auth is engine._auth)
    check("두 종목이 같은 guard(공유 게이트)를 참조", a.guard is b.guard and a.guard is engine._guard)
    check("종목마다 독립 Position 객체", a.position is not b.position)

    # AAPL 만 강제 매수 → 공유 예산에서 차감되고 B 의 잔여도 함께 줄어야 한다
    price = a._history[-1]  # 워밍업 프리로드로 이미 종가 이력이 있다
    before = b.auth.remaining_usdc
    await engine._buy_cycle("AAPL", a, Decimal(price), Decision("buy", "forced", Decimal("10")))
    after = b.auth.remaining_usdc
    check("A 매수가 B 의 잔여 예산을 차감(공유 예산)", after < before,
          f"잔여 {before} → {after} USDC")
    check("A 만 보유 증가, B 는 무보유(포지션 독립)",
          a.position.quantity > 0 and b.position.quantity == 0,
          f"A={a.position.quantity} B={b.position.quantity}")
    check("공유 예산 잔여가 음수로 새지 않음", engine._auth.remaining_usdc >= 0)


# ---------- 3) 한 종목 피드 소진 → 다른 종목 계속 ----------
async def test_feed_exhaustion_isolation() -> None:
    print("\n[3] 피드 소진 격리")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA"])
    _exhaust(engine, "AAPL")   # AAPL 만 소진

    a_before = len(engine._price_history["AAPL"])
    b_before = len(engine._price_history["TSLA"])
    await engine._tick_once()
    check("소진된 종목(AAPL)은 시세가 멈춤", len(engine._price_history["AAPL"]) == a_before)
    check("살아있는 종목(TSLA)은 시세가 계속", len(engine._price_history["TSLA"]) == b_before + 1)
    check("일부만 소진이면 세션은 계속(stop 아님)", not engine._stop_event.is_set())


# ---------- 4) 전 종목 소진 → 세션 자동 종료 ----------
async def test_all_exhausted_ends_session() -> None:
    print("\n[4] 전 종목 소진 → 세션 종료")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA"])
    _exhaust(engine, "AAPL")
    _exhaust(engine, "TSLA")
    await engine._tick_once()
    ended = [e for e in engine.bus.since(0) if e.type == ev.REPLAY_ENDED]
    check("전 종목 소진 시 REPLAY_ENDED 발행", len(ended) == 1)
    check("전 종목 소진 시 세션 종료 신호(stop)", engine._stop_event.is_set())
    if ended:
        check("REPLAY_ENDED 에 종목 목록 포함", ended[-1].data.get("symbols") == ["AAPL", "TSLA"])


# ---------- 5) 가드 KPI 합산 + 정상 흐름 유출 0 (실제 매매가 도는 세션) ----------
async def test_guard_kpi_aggregation() -> None:
    print("\n[5] 가드 KPI 합산 · 유출 0 (실 세션)")
    engine = _engine()
    # 하락장(bear) 데이터는 눌림목이 잦아 두 종목 모두 매수가 발생한다(조건형 dip3/profit5)
    await _start(engine, ["AAPL", "TSLA", "NVDA"], dataset="bear")
    ticks = await _step_until_done(engine, cap=600)   # bear ≈ 481 재생봉
    snap = engine.state_snapshot()

    buys = [t for t in engine.trades if t["side"] == "buy"]
    traded_syms = {t["symbol"] for t in engine.trades}
    g = snap["guard"]
    check("세션이 전 종목 소진으로 종료됨", engine._stop_event.is_set(), f"{ticks}틱")
    check("여러 종목에서 매매 발생(다중 지출)", len(traded_syms) >= 2, f"종목 {sorted(traded_syms)}")
    check("가드 attempts = 차단 + 전 종목 매수 시도 합산",
          g["attempts"] == g["blocked"] + len(buys), f"attempts={g['attempts']} buys={len(buys)}")
    check("정상 흐름에서 유출 0.00 USDC", Decimal(g["leak_usdc"]) == 0)
    check("정상 흐름에서 가드 차단 0(정직한 브로커)", g["blocked"] == 0)
    check("실현손익은 전 종목 합산 스칼라", "realized_usdc" in snap["pnl"])
    check("총자산 = 공유 현금 + 전 종목 평가액",
          Decimal(snap["valuation"]["total_asset_usdc"])
          == Decimal(snap["budget"]["remaining_usdc"])
          + Decimal(snap["valuation"]["position_net_value_usdc"]))


# ---------- 하위호환: 단일 종목(N=1) 스냅샷 형태 ----------
async def test_single_symbol_backcompat() -> None:
    print("\n[6] 하위호환 — 단일 종목(N=1)")
    engine = _engine()
    await _start(engine, ["AAPL"])
    await engine._tick_once()
    snap = engine.state_snapshot()
    check("symbols 길이 1", snap["symbols"] == ["AAPL"])
    check("per_symbol 항목 1개", list(snap["per_symbol"].keys()) == ["AAPL"])
    check("top-level price == per_symbol[AAPL].price(포커스=단일)",
          snap["price"]["current"] == snap["per_symbol"]["AAPL"]["price"]["current"])
    check("단일 spend 분할 없음(30 그대로)",
          snap["strategy"]["spend_per_symbol_usdc"] == "30")


# ---------- 방어: 멀티 spend 분할 + 추세추종/라이브 멀티 거부 ----------
async def test_multi_guards() -> None:
    print("\n[7] 방어 — spend 분할 · 추세/라이브 멀티 거부")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA", "NVDA"])
    check("멀티는 1회 매수 = 총 spend/N (30/3=10.00)",
          engine.strategy_info["spend_per_symbol_usdc"] == "10.00",
          f"spend_per_symbol={engine.strategy_info['spend_per_symbol_usdc']}")
    check("mandate 허용 종목 = 전 종목", set(engine._mandate.allowed_symbols) == {"AAPL", "TSLA", "NVDA"})

    # 추세추종 + 멀티 → 거부 (올인이 예산 독식)
    e2 = _engine()
    rejected = False
    try:
        await e2.start("dry", {"type": "trend"},
                       {"type": "replay", "symbols": ["AAPL", "TSLA"]}, autostart=False)
    except EngineError:
        rejected = True
    check("추세추종 + 멀티 종목은 거부됨", rejected)

    # 잘못된 종목 코드 → 거부
    e3 = _engine()
    bad = False
    try:
        await e3.start("dry", {"type": "condition"},
                       {"type": "replay", "symbols": ["AAPL", "../etc"]}, autostart=False)
    except EngineError:
        bad = True
    check("비정상 종목 코드는 거부됨(경로 주입 차단)", bad)


async def _main() -> int:
    for t in (test_shared_budget_and_isolation, test_feed_exhaustion_isolation,
              test_all_exhausted_ends_session, test_guard_kpi_aggregation,
              test_single_symbol_backcompat, test_multi_guards):
        await t()
    bad = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 60)
    print(f"멀티 종목 엔진 검증: {'전부 통과' if not bad else f'{len(bad)}건 실패'} "
          f"({len(_results)}개 확인)")
    for n in bad:
        print(f"  - 실패: {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
