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
  - 추세추종(올인)+멀티: 종목별 예산 슬라이스(예산/N)로 독립 운용·완전 격리(한 종목 손실이 남을 잠식 못함)

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
from payments.ap2_mandate import MandateError  # noqa: E402
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
    await engine.start(mode, strategy or {"type": "condition", "brain": "rule"}, feed, autostart=False)


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
    # attempts 는 '가드를 태운 청구서 전체'다 — 매수·매도 양 레그를 같은 분모에 넣는다.
    # 예전 공식(blocked + 매수 체결 수)은 모집단이 섞여 있었다: 매도 차단은 분자에 들어가는데
    # 매도 성공은 분모에 없었고, 기준선 조회 실패로 중단된 매수는 어느 쪽에도 없었다
    # (bug-dept BUG-08 — 오차 방향이 하필 제품에 유리했다).
    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("가드 attempts = 양 레그 청구서 전체 (매수·매도 대칭)",
          g["attempts"] >= g["blocked"] + len(buys) + len(sells),
          f"attempts={g['attempts']} buys={len(buys)} sells={len(sells)} blocked={g['blocked']}")
    check("레그별 차단 분해가 합계와 일치",
          g["blocked_buy"] + g["blocked_sell"] == g["blocked"],
          f"{g['blocked_buy']}+{g['blocked_sell']} vs {g['blocked']}")
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
    print("\n[7] spend 분할 · 추세 멀티 슬라이스 격리 · 목시세 멀티 거부")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA", "NVDA"])
    check("멀티는 1회 매수 = 총 spend/N (30/3=10.00)",
          engine.strategy_info["spend_per_symbol_usdc"] == "10.00",
          f"spend_per_symbol={engine.strategy_info['spend_per_symbol_usdc']}")
    check("mandate 허용 종목 = 전 종목", set(engine._mandate.allowed_symbols) == {"AAPL", "TSLA", "NVDA"})

    # 추세추종 + 멀티 → 허용 (종목별 예산 슬라이스로 독식 방지·완전 격리)
    e2 = _engine()
    await e2.start("dry", {"type": "trend"},
                   {"type": "replay", "symbols": ["AAPL", "TSLA"]}, autostart=False)
    a_auth, t_auth = e2.agents["AAPL"].auth, e2.agents["TSLA"].auth
    check("추세추종 멀티 — 종목별 독립 auth (공유 예산 아님)", a_auth is not t_auth)
    check("추세추종 멀티 — 슬라이스 합 = 총예산", e2._total_remaining() == e2.budget_total,
          f"AAPL {a_auth.remaining_usdc} + TSLA {t_auth.remaining_usdc} = {e2._total_remaining()} (예산 {e2.budget_total})")
    check("추세추종 멀티 — 각 슬라이스 <= 예산/N (한 종목 독식 불가)",
          a_auth.remaining_usdc > 0 and t_auth.remaining_usdc > 0
          and a_auth.remaining_usdc <= e2.budget_total / 2 + Decimal("0.01")
          and t_auth.remaining_usdc <= e2.budget_total / 2 + Decimal("0.01"))
    # 한 종목 소진이 다른 종목 슬라이스를 건드리지 않는가 (완전 격리)
    t_before = t_auth.remaining_usdc
    a_auth.spent_usdc += a_auth.remaining_usdc     # AAPL 슬라이스 전액 소진(시뮬)
    check("추세추종 멀티 — 한 종목 소진이 다른 종목 예산을 안 건드림 (완전 격리)",
          t_auth.remaining_usdc == t_before and a_auth.remaining_usdc == Decimal("0"),
          f"AAPL 소진후 {a_auth.remaining_usdc}, TSLA {t_auth.remaining_usdc}(전 {t_before})")
    a_auth.spent_usdc = Decimal(0)                 # 격리 확인용 원복
    # 한도 변경도 종목별 슬라이스를 재산정하는가 (실행 중 긴급정지 상태에서)
    e2.pause()
    e2.update_limits(Decimal("200"), Decimal("80"))
    na, nt = e2.agents["AAPL"].auth, e2.agents["TSLA"].auth
    check("추세추종 멀티 한도변경 — 여전히 종목별 독립 auth", na is not nt)
    check("추세추종 멀티 한도변경 — 슬라이스 합 = 새 예산(200)",
          e2._total_remaining() == Decimal("200"), f"합 {e2._total_remaining()}")
    check("추세추종 멀티 한도변경 — 가드 mandate 허용종목 = 전 종목",
          set(e2._guard.mandate.allowed_symbols) == {"AAPL", "TSLA"})

    # 잘못된 종목 코드 → 거부
    e3 = _engine()
    bad = False
    try:
        await e3.start("dry", {"type": "condition", "brain": "rule"},
                       {"type": "replay", "symbols": ["AAPL", "../etc"]}, autostart=False)
    except EngineError:
        bad = True
    check("비정상 종목 코드는 거부됨(경로 주입 차단)", bad)

    # 목 시세 + 멀티 → 거부 (전 종목 동일 가격 = 분산 무의미)
    e4 = _engine()
    mock_bad = False
    try:
        await e4.start("dry", {"type": "condition", "brain": "rule"},
                       {"type": "mock", "symbols": ["AAPL", "TSLA"]}, autostart=False)
    except EngineError:
        mock_bad = True
    check("목 시세 + 멀티 종목은 거부됨(실데이터 재생만)", mock_bad)


# ---------- 한도 변경이 공유 가드까지 정합시키는가 ----------
async def test_limit_change_syncs_guard() -> None:
    print("\n[8] 한도 변경 → 공유 가드 정합")
    engine = _engine()
    await _start(engine, ["AAPL", "TSLA"])
    engine.pause()   # 실행 중 한도 변경은 긴급정지 상태에서만 허용
    engine.update_limits(Decimal("200"), Decimal("80"))
    check("가드가 재서명된 활성 mandate 를 참조", engine._guard.mandate is engine._mandate)
    check("가드 mandate 의 건별 한도가 새 값(80)으로 갱신",
          engine._guard.mandate.per_trade_max_usdc == Decimal("80"))
    check("전 종목 auth 가 새 공유 auth 로 교체",
          engine.agents["AAPL"].auth is engine._auth and engine.agents["TSLA"].auth is engine._auth)


# ---------- 한도 인하가 종목 몫을 사용액 밑으로 밀어 총예산 집행을 깨지 않는가 (BUG-07) ----------
async def test_limit_cut_below_symbol_spend() -> None:
    """추세추종 멀티에서 예산을 낮출 때, 총액만 보면 AP2 총예산 집행이 깨진다.

    예산 100(몫 50/50) → AAPL 45 사용 → 60 으로 인하하면 AAPL 몫이 30 이 되어 잔여 −15.
    그 음수가 합산에서 TSLA 의 +30 과 상계돼 화면은 15 를 남았다고 말하고, 각 종목의
    authorize 는 제 슬라이스만 보므로 TSLA 로 30 을 더 승인해 총 75 > 새 예산 60 이 된다.
    "한도를 낮추면 그만큼 줄어든다"는 헤드라인 주장과 정면으로 어긋나는 자리다."""
    print("\n[9] 한도 인하 — 종목 몫 < 이미 사용 (BUG-07)")
    e = _engine()
    e.update_limits(Decimal("100"), Decimal("50"))   # 대기 상태 → 다음 세션 기본값
    await e.start("dry", {"type": "trend"},
                  {"type": "replay", "symbols": ["AAPL", "TSLA"]}, autostart=False)
    a, t = e.agents["AAPL"].auth, e.agents["TSLA"].auth
    a.authorize("ord_a1", "AAPL", Decimal("45"), "broker")   # AAPL 몫 50 중 45 사용
    a.settle("ord_a1")
    e.pause()   # 실행 중 한도 변경은 긴급정지 상태에서만

    raised = ""
    try:
        e.update_limits(Decimal("60"), Decimal("30"))
    except EngineError as ex:
        raised = str(ex)
    check("종목 몫이 이미 쓴 금액보다 작아지는 인하는 거부", bool(raised), raised)
    # 거부는 mandate 재서명 '앞'에서 나야 한다 — 뒤면 세션 mandate 만 새 예산으로 바뀌고
    # 종목별 auth 는 옛 몫으로 남아, 화면과 집행이 서로 다른 예산을 말하게 된다.
    check("거부 시 세션 예산 그대로(부분 적용 없음)", e.budget_total == Decimal("100"),
          str(e.budget_total))
    check("거부 시 세션 mandate 도 그대로",
          e._mandate.budget_total_usdc == Decimal("100")
          and e._guard.mandate.budget_total_usdc == Decimal("100"),
          f"mandate {e._mandate.budget_total_usdc} / guard {e._guard.mandate.budget_total_usdc}")
    check("거부 시 종목 슬라이스 그대로",
          e.agents["AAPL"].auth.remaining_usdc == Decimal("5")
          and e.agents["TSLA"].auth.remaining_usdc == Decimal("50"),
          f"AAPL {e.agents['AAPL'].auth.remaining_usdc} / TSLA {e.agents['TSLA'].auth.remaining_usdc}")

    # 대조군 — 몫이 사용액 이상으로 남는 인하는 그대로 통과해야 한다(과잉 차단 방지)
    e.update_limits(Decimal("96"), Decimal("40"))
    a2, t2 = e.agents["AAPL"].auth, e.agents["TSLA"].auth
    check("[대조군] 몫이 사용액 이상이면 인하 허용 (몫 48 >= 사용 45)",
          e.budget_total == Decimal("96") and a2.remaining_usdc == Decimal("3")
          and t2.remaining_usdc == Decimal("48"),
          f"예산 {e.budget_total} · AAPL {a2.remaining_usdc} · TSLA {t2.remaining_usdc}")
    check("[대조군] 인하 후 총 사용 가능액이 새 예산을 넘지 않는다",
          e._total_spent() + e._total_remaining() <= Decimal("96"),
          f"사용 {e._total_spent()} + 잔여 {e._total_remaining()}")

    # 방어선 — 어떤 경로로든 음수 몫이 생기면 합산에서 양수와 상계되면 안 된다
    e.agents["AAPL"].auth.spent_usdc = Decimal("60")   # 몫 48 < 사용 60 (인위적 상태)
    check("음수 몫은 0 으로 바닥 처리 — 다른 종목 양수와 상계되지 않는다",
          e._total_remaining() == Decimal("48"), str(e._total_remaining()))


# ---------- 지불액이 1단위 미만일 때 0원 청구서를 만들지 않는가 (BUG-12) ----------
async def test_dust_spend_no_zero_trade() -> None:
    """`spend / (price × (1+fee))` 가 0.0001 미만이면 수량이 0.0000 으로 내림되고
    청구액도 0 이 된다. 예전에는 그 0원 청구서가 어디에도 안 걸렸다 — Guard 는
    '청구액 == 견적'이라 통과, AP2 는 하한이 없어 authorize(0) 통과, 정산은 settled.
    spent 가 0 이라 잔여 예산이 줄지 않아 **틱마다 반복**됐다(481틱 세션이면 가짜 체결
    481건이 거래 내역·아카이브에 쌓인다)."""
    print("\n[10] dust 지불액 — 0원 청구서를 만들지 않는다 (BUG-12)")
    engine = _engine()
    await _start(engine, ["AAPL"])
    a = engine.agents["AAPL"]
    price = Decimal(a._history[-1])
    dust = Decimal("0.01")
    q = engine._broker.quote("AAPL", dust, price)
    check("전제: dust 지불액이면 견적 수량이 0", q.quantity == 0, f"수량 {q.quantity} · 가격 {price}")

    before_spent = a.auth.spent_usdc
    for _ in range(3):   # 반복성이 이 결함의 핵심이라 3틱 태운다
        await engine._buy_cycle("AAPL", a, price, Decision("buy", "forced-dust", dust))
    check("체결이 생기지 않는다", len(engine.trades) == 0, f"체결 {len(engine.trades)}건")
    check("포지션이 늘지 않는다", a.position.quantity == 0, str(a.position.quantity))
    check("예산도 그대로", a.auth.spent_usdc == before_spent, str(a.auth.spent_usdc))
    # 아래 계층에도 하한을 넣었지만 그쪽이 먼저 잡으면 자기가 만든 잡음이 대표 지표를
    # 더럽힌다 — "시도 N건 중 M건 차단"의 분모·분자가 우리 dust 견적으로 부풀면 안 된다.
    check("자기가 만든 잡음이 가드 KPI 에 잡히지 않는다",
          engine.guard_block_count == 0 and engine._guard_checked == 0,
          f"차단 {engine.guard_block_count} / 시도 {engine._guard_checked}")
    check("AP2 거부 KPI 에도 잡히지 않는다", engine.reject_count == 0, str(engine.reject_count))

    # 하한은 계층마다 독립으로 성립해야 한다 — 엔진을 우회해도 AP2 가 0원을 거부한다
    rejected = ""
    try:
        a.auth.authorize("ord_zero", "AAPL", Decimal(0), "broker")
    except MandateError as ex:
        rejected = str(ex)
    check("엔진을 우회해도 AP2 가 0원 승인을 거부", bool(rejected), rejected)

    # [대조군] 정상 지불액은 그대로 체결된다 (과잉 차단 방지)
    await engine._buy_cycle("AAPL", a, price, Decision("buy", "forced", Decimal("10")))
    check("[대조군] 정상 지불액은 체결된다",
          len(engine.trades) == 1 and a.position.quantity > 0,
          f"체결 {len(engine.trades)}건 · 보유 {a.position.quantity}")


async def _main() -> int:
    for t in (test_shared_budget_and_isolation, test_feed_exhaustion_isolation,
              test_all_exhausted_ends_session, test_guard_kpi_aggregation,
              test_single_symbol_backcompat, test_multi_guards,
              test_limit_change_syncs_guard, test_limit_cut_below_symbol_spend,
              test_dust_spend_no_zero_trade):
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
