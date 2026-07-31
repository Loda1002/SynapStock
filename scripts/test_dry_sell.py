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
import math
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



# ---------------------------------------------------------------------------
# BUG-24 회귀 — 숫자형 환경변수가 빈 값이어도 임포트가 죽지 않아야 한다.
#
# 이 파일에 두는 이유: 위 매도 레그 결함과 **같은 계열**이다 — 로컬 .env 에는 값이 있어
# 안 드러나고 배포 환경변수에서만 터지는 결함이다. config 는 임포트 시점에 필드를
# 평가하므로 같은 프로세스에서는 검사할 수 없다 → 자식 프로세스를 띄워 확인한다.
# ---------------------------------------------------------------------------

def _run_import(env_overrides: dict, code: str = "import config"):
    """수정된 환경변수로 자식 파이썬을 띄워 (반환코드, stderr) 를 준다."""
    import subprocess
    env = dict(os.environ)
    env.update(env_overrides)
    # .env 가 값을 되살리지는 않는다 — load_dotenv 는 override=False 라 이미 있는 키를
    # 건드리지 않고, 빈 문자열도 '있는 키'다(그래서 이 결함이 실제로 재현된다).
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, errors="replace")
    return r.returncode, (r.stderr or "")


def test_blank_numeric_env_boots():
    """빈 값이면 기본값으로 되돌아가고 앱이 뜬다 (예전에는 여기서 전부 죽었다)."""
    for key in ("BUDGET_USDC", "PER_TRADE_MAX_USDC", "MAX_HOLD_BARS",
                "BROKER_FEE_BPS", "USDC_DECIMALS", "WEB_TICK_INTERVAL_SEC"):
        rc, err = _run_import({key: ""})
        check(f"BUG-24 빈 값 {key} 에서도 임포트 성공", rc == 0, f"rc={rc} {err[-160:]}")


def test_blank_numeric_env_uses_default():
    """되돌아간 값이 '기본값' 이어야 한다 — 0 이나 빈 Decimal 로 새면 안 된다."""
    rc, err = _run_import(
        {"BUDGET_USDC": ""},
        "import config, sys; sys.exit(0 if str(config.CFG.budget_usdc) == '100' else 3)")
    check("BUG-24 빈 값은 기본값(100)으로 복원", rc == 0, f"rc={rc} {err[-160:]}")


def test_bad_numeric_env_names_the_variable():
    """진짜 잘못 쓴 값은 조용히 넘기지 않고 '어느 변수'인지 밝히며 멈춘다."""
    rc, err = _run_import({"BUDGET_USDC": "abc"})
    check("BUG-24 잘못된 값은 중단", rc != 0, f"rc={rc}")
    check("BUG-24 오류 문구에 변수명이 있다", "BUDGET_USDC" in err, err[-200:])
    check("BUG-24 예전의 불친절한 예외가 아니다", "InvalidOperation" not in err, err[-200:])


def test_max_budget_blank_still_fails():
    """⚠ 서버측 상한만은 빈 값도 오류 — 조용히 10000 으로 헐거워지면 안 된다."""
    rc, err = _run_import({"MAX_BUDGET_USDC": ""})
    check("BUG-24 MAX_BUDGET_USDC 빈 값은 중단(상한이 조용히 풀리지 않음)", rc != 0, f"rc={rc}")
    check("BUG-24 그 오류도 변수명을 밝힌다", "MAX_BUDGET_USDC" in err, err[-200:])


def test_server_port_blank_boots():
    """PORT 빈 값 — Cloud Run 진입점이 같은 이유로 죽지 않아야 한다."""
    rc, err = _run_import({"PORT": ""}, "import web.server")
    check("BUG-24 빈 PORT 에서도 web.server 임포트 성공", rc == 0, f"rc={rc} {err[-160:]}")

async def test_nan_session_params_rejected() -> None:
    """NaN·Infinity 가 세션 파라미터 검사를 통과하지 않는다 — BUG-18·BUG-19.

    둘 다 같은 계열이다: NaN 은 비교 연산이 전부 False(또는 InvalidOperation)라서
    '안전 범위로 클램프' · '0보다 큰가' 같은 검사를 **그냥 통과한다**.
      · BUG-18 틱 간격 NaN → min/max 클램프를 통과해 asyncio.sleep(nan) 이 즉시 돌아오고
        틱 루프가 폭주한다(재생 피드를 순식간에 소진하고 CPU 를 태운다).
      · BUG-19 적립식 회당 금액 NaN → `<= 0` 비교가 InvalidOperation 을 던져 500 이 되고,
        Infinity 는 그 비교를 통과해 세션이 실제로 시작된다.
    """
    print("\n[6] NaN·Infinity 세션 파라미터 (BUG-18·19)")
    eng.CFG = REAL_CFG

    # BUG-18 — 틱 간격
    for label, val in (("NaN", float("nan")), ("Infinity", float("inf"))):
        e = _engine()
        await e.start("dry", {"type": "condition", "brain": "rule"},
                      {"type": "replay", "dataset": "daily", "symbols": ["AAPL"]},
                      autostart=False, tick_interval_sec=val)
        ok = math.isfinite(e.tick_interval) and 0.05 <= e.tick_interval <= 60.0
        check(f"BUG-18 틱 간격 {label} 은 안전 범위로 대체", ok, str(e.tick_interval))

    # [대조군] 정상 값은 지금까지와 똑같이 그대로 쓰인다
    e = _engine()
    await e.start("dry", {"type": "condition", "brain": "rule"},
                  {"type": "replay", "dataset": "daily", "symbols": ["AAPL"]},
                  autostart=False, tick_interval_sec=0.3)
    check("[대조군] 정상 틱 간격은 그대로", e.tick_interval == 0.3, str(e.tick_interval))

    # BUG-19 — 적립식 회당 금액
    for label, val in (("NaN", "NaN"), ("Infinity", "Infinity")):
        e = _engine()
        raised = ""
        try:
            await e.start("dry",
                          {"type": "dca", "dca_unit": "ticks", "dca_every_ticks": 1,
                           "dca_amount_usdc": val},
                          {"type": "replay", "dataset": "daily", "symbols": ["AAPL"]},
                          autostart=False)
        except EngineError as ex:
            raised = str(ex)
        except Exception as ex:                      # InvalidOperation 등 = 500 으로 새는 경로
            raised = f"[{type(ex).__name__}] {ex}"
        check(f"BUG-19 적립식 금액 {label} 은 EngineError 로 거부",
              "유효한 숫자가 아닙니다" in raised, raised or "거부되지 않음(세션 시작됨)")


async def main() -> int:
    try:
        await test_regression_reproduces()
        await test_dry_sell_works_without_stock_mint()
        await test_live_still_fails_fast()
        await test_configured_mint_wins()
        test_blank_numeric_env_boots()
        test_blank_numeric_env_uses_default()
        test_bad_numeric_env_names_the_variable()
        test_max_budget_blank_still_fails()
        test_server_port_blank_boots()
        await test_nan_session_params_rejected()
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
