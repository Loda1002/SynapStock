"""유출 KPI(guard_leak_usdc) 실측 배선 회귀 테스트 — 첫 화면의 '유출 0.00' 이 상수가 아님을 증명한다.

재현한 결함 (2026-07-27 자해 점검에서 발견):
  engine.guard_leak_usdc 는 초기화(engine.py:108)·세션 리셋(engine.py:616)·스냅샷 읽기
  (state_snapshot 의 guard.leak_usdc) 3곳에만 존재했고, **값을 증가시키는 코드가 저장소에
  단 한 줄도 없었다.** 즉 첫 화면 KPI 의 '유출 0.00 USDC' 는 측정 결과가 아니라 상수였다.
  실제로 USDC 가 온체인에서 떠났는데 주식이 도착하지 않아도(check_delivery 실패) 화면은
  계속 0.00 을 표시했다. 소스를 여는 사람에게는 그 자체가 제품 주장을 무너뜨리는 지점이다.

수정: TradingEngine._record_leak(amount) 를 추가하고 '막지 못한' 3경로에서 호출한다.
  (a) _buy_cycle  : 정산은 됐는데 주식 배송 미확인(check_delivery 실패) → quote.total_usdc
  (b) _sell_cycle : 주식은 나갔는데 대금 도착 미확인(check_delivery 실패) → quote.total_usdc
  (c) _sell_cycle : 대금 기준선 조회 실패(GUARD_BASELINE_UNREAD) → 확인 못 한 것은
                    도착하지 않은 것으로 계상(보수적) → quote.total_usdc
  세 경로 모두 live 에서만 도는 check_delivery 계층이라 드라이런은 이 경로에 오지 않는다.
  서명 전 차단(check_demand·check_stock_transfer)은 트랜잭션 자체가 생성되지 않으므로
  여기 오지 않는다 — 그 계층의 유출 0 은 측정치가 아니라 구조적 사실이고, 섞으면 안 된다.

검증 방법(네트워크 0):
  드라이로 세션을 구성한 뒤 engine.mode 를 "live" 로, engine._client 를 가짜 객체로 바꾸고
  payments.x402_solana 의 RPC 3함수(get_latest_blockhash / submit_and_confirm /
  get_token_balance_base)만 스텁으로 교체한다. 나머지(트랜잭션 생성·서명·verify_payment·
  Guard·AP2)는 전부 실제 코드가 그대로 돈다 = '실제 결제 경로'를 태운 검증이다.

★ 회귀 방지의 핵심(9): 유출이 발생해야 하는 시나리오마다 "값이 0 이면 실패"를 단언한다.
  _record_leak 호출이 사라져 옛 상태(항상 0)로 되돌아가면 이 테스트가 즉시 실패한다.

재현: python -m scripts.test_leak_kpi   (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import dataclasses
import os
import sys
from decimal import Decimal
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.hash import Hash  # noqa: E402

from agents.trading_agent import Decision  # noqa: E402
from config import CFG as REAL_CFG, to_base_units  # noqa: E402
from payments import x402_solana as x  # noqa: E402
from payments.invoice_semantics import (  # noqa: E402
    GUARD_LLM_UNVERIFIED, SemanticStats, SemanticVerdict,
)
from web import engine as eng  # noqa: E402
from web import events as ev  # noqa: E402
from web.engine import TradingEngine  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []

# 세션 구성용 고정값 — .env 에 의존하지 않게 테스트가 직접 못 박는다.
STOCK_MINT = "So11111111111111111111111111111111111111112"
SYMBOL = "AAPL"
PRICE = Decimal("200.00")
SPEND = Decimal("10")


def _p(s: str) -> None:
    """콘솔 인코딩(cp949 등)에 없는 문자가 섞여도 죽지 않게 출력한다."""
    enc = sys.stdout.encoding or "utf-8"
    try:
        s.encode(enc)
    except (UnicodeEncodeError, LookupError):
        s = s.encode(enc, "replace").decode(enc, "replace")
    print(s)


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    _p(f"  [{PASS if cond else FAIL}] {name}" + (f" - {detail}" if detail else ""))


class _RecordingBus(EventBus):
    """GUARD_PENDING(유출 계상 지점)·ERROR·정지 이벤트를 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []
        self.pending: list[dict] = []
        self.paused: list[dict] = []

    def emit(self, kind, payload):  # type: ignore[override]
        if kind == ev.ERROR:
            self.errors.append(str((payload or {}).get("message", "")))
        elif kind == ev.GUARD_PENDING:
            self.pending.append(dict(payload or {}))
        elif kind == ev.TRADING_PAUSED:
            self.paused.append(dict(payload or {}))
        return super().emit(kind, payload)


class _FakeChain:
    """네트워크 없는 가짜 원장.

    첫 조회 = 정산 직전 기준선(before), 이후 조회 = 정산 후 잔액.
    delivered_units 를 0 으로 두면 '대가가 끝내 도착하지 않은' 상황이 된다.
    """

    def __init__(self, *, delivered_units: int = 0, fail_first_read: bool = False,
                 confirm_seq: Optional[list] = None):
        self.base = 1_000_000
        self.delivered_units = delivered_units
        self.fail_first_read = fail_first_read
        self.reads = 0
        # submit_and_confirm 의 확정 결과 시퀀스(소진 후에는 True).
        # 매수 레그는 이 함수를 두 번 부른다 — ①구매자 USDC 결제 tx ②브로커 주식 전달 tx
        # (broker_agent.settle:254·263). [True, False] 를 주면 "대금은 확정 수령했는데
        # 주식 전달 tx 가 실패" = 브로커가 스스로 partial 을 신고하는 상황이 재현된다.
        self.confirm_seq = list(confirm_seq or [])
        self.confirms = 0

    async def read(self) -> int:
        self.reads += 1
        if self.fail_first_read and self.reads == 1:
            raise RuntimeError("RPC 잔액 조회 실패(테스트 주입)")
        return self.base + (self.delivered_units if self.reads > 1 else 0)

    def next_confirm(self) -> bool:
        i = self.confirms
        self.confirms += 1
        return self.confirm_seq[i] if i < len(self.confirm_seq) else True


_CHAIN: list[_FakeChain] = [_FakeChain()]
_RPC_NAMES = ("get_latest_blockhash", "submit_and_confirm", "get_token_balance_base")


class _FakeClient:
    """engine/broker 가 'None 이 아님'만 보는 자리표시 RPC 클라이언트."""

    async def close(self) -> None:
        return None


def _install_rpc_stubs() -> dict:
    """실 RPC 3함수만 스텁으로 교체한다(원본 반환). 결제 로직·Guard 는 손대지 않는다."""
    orig = {n: getattr(x, n) for n in _RPC_NAMES}

    async def _blockhash(client):
        return Hash.default()

    async def _submit(client, tx):
        # 기본은 '온체인 확정 성공'. 실제 자산 도착 여부는 원장이 따로 말한다
        # (= 정산은 됐다는데 물건이 안 온 상황을 그대로 재현).
        # confirm_seq 를 주면 개별 tx 의 확정 실패까지 재현할 수 있다.
        return x.signature_str(tx), _CHAIN[0].next_confirm()

    async def _balance(client, owner, mint):
        return await _CHAIN[0].read()

    x.get_latest_blockhash = _blockhash
    x.submit_and_confirm = _submit
    x.get_token_balance_base = _balance
    return orig


def _restore_rpc(orig: dict) -> None:
    for n, fn in orig.items():
        setattr(x, n, fn)


async def _new_session(bus=None) -> TradingEngine:
    """드라이로 세션을 구성한다(백그라운드 루프 없이)."""
    engine = TradingEngine(bus or _RecordingBus(), BaseStore())
    await engine.start("dry", {"type": "condition", "brain": "rule"},
                       {"type": "replay", "dataset": "daily", "symbols": [SYMBOL]},
                       autostart=False)
    # 매수 레그를 인프로세스 A2A 로 고정한다(.env 에 BROKER_HTTP_URL 이 있어도 결정론 유지).
    engine._broker_http = None
    return engine


def _go_live(engine: TradingEngine, chain: _FakeChain) -> None:
    """engine.py:1081·1215 의 `live = self.mode == "live"` 를 켜고 가짜 원장을 물린다."""
    engine.mode = "live"
    engine._client = _FakeClient()
    _CHAIN[0] = chain


async def _buy(engine: TradingEngine, spend: Decimal = SPEND):
    agent = engine.agents[SYMBOL]
    quote = engine._broker.quote(SYMBOL, spend, PRICE)   # 엔진이 만들 견적과 동일(결정론)
    await engine._buy_cycle(SYMBOL, agent, PRICE, Decision("buy", "테스트 강제", spend))
    return quote


async def _sell(engine: TradingEngine):
    agent = engine.agents[SYMBOL]
    quote = engine._broker.sell_quote(SYMBOL, agent.position.quantity, PRICE)
    await engine._sell_cycle(SYMBOL, agent, PRICE, Decision("sell", "테스트 강제", Decimal("0")))
    return quote


# ---------- 1) 드라이런은 유출 경로에 오지 않는다 ----------
async def test_dry_stays_zero() -> None:
    _p("\n[1] 드라이런 - 매수·매도가 체결돼도 유출은 0 (check_delivery 는 라이브 전용)")
    engine = await _new_session()
    check("세션 시작 직후 유출 0", engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))

    await _buy(engine)
    check("드라이 매수 체결", engine.agents[SYMBOL].position.quantity > 0,
          f"보유 {engine.agents[SYMBOL].position.quantity}")
    check("매수 후에도 유출 0", engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))

    await _sell(engine)
    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("드라이 매도 체결(settled)", bool(sells) and sells[0]["status"] == "settled",
          sells[0]["status"] if sells else "없음")
    check("매도 후에도 유출 0", engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))
    check("GUARD_PENDING 이벤트 0건", not engine.bus.pending, str(len(engine.bus.pending)))


# ---------- 2) 매수 배송 미확인 → quote.total_usdc 만큼 증가 ----------
async def test_buy_delivery_unconfirmed_leaks() -> None:
    _p("\n[2] 라이브 매수 - 결제는 나갔는데 주식 미도착 → 유출 가산")
    engine = await _new_session()
    _go_live(engine, _FakeChain(delivered_units=0))   # 잔액이 안 늘어난다 = 미배송

    quote = await _buy(engine)
    leak = engine.guard_leak_usdc
    check("★회귀 방지 - 유출이 0 이 아니다(가산 코드 존재)", leak != 0, str(leak))
    check("유출이 청구 총액과 정확히 일치", leak == quote.total_usdc,
          f"leak={leak} / total={quote.total_usdc}")
    trades = [t for t in engine.trades if t["side"] == "buy"]
    check("거래가 settled 가 아니라 partial 로 강등",
          bool(trades) and trades[0]["status"] == "partial",
          trades[0]["status"] if trades else "없음")
    check("포지션 미반영(미배송분을 보유로 세지 않음)",
          engine.agents[SYMBOL].position.quantity == 0,
          str(engine.agents[SYMBOL].position.quantity))
    check("유출 발생 시 세션 정지", bool(engine.bus.paused) and not engine.trading_enabled,
          str(engine.bus.paused))


# ---------- 2b) 브로커가 스스로 partial 을 신고한 매수도 계상된다 (CODE-01) ----------
async def test_buy_broker_partial_leaks() -> None:
    """매수 레그의 검증 진입 조건이 settled 만 보던 비대칭(CODE-01)의 회귀 테스트.

    브로커는 '대금은 확정 수령했는데 주식 전달 tx 가 미확정'이면 스스로 partial 을 신고한다.
    예전 조건(`status == "settled"`)에서는 그 건이 check_delivery 블록에 아예 못 들어와
    유출 KPI 도 GUARD_PENDING 도 없이 조용히 넘어갔다 — USDC 는 이미 온체인에서 떠난 뒤인데
    첫 화면은 계속 '유출 0.00'. 매도 레그에는 이미 있던 대칭이라 매수만 뚫려 있었다.
    """
    _p("\n[2b] 라이브 매수 - 브로커 자기신고 partial(대금 수령·주식 전달 실패) → 유출 가산")
    engine = await _new_session()
    # confirm_seq=[True, False] → ①구매자 USDC 결제 확정 ②브로커 주식 전달 tx 미확정
    chain = _FakeChain(delivered_units=0, confirm_seq=[True, False])
    _go_live(engine, chain)

    quote = await _buy(engine)
    check("전제 - 브로커가 전달 tx 를 시도했고 미확정이었다(partial 신고 조건)",
          chain.confirms >= 2, f"confirms={chain.confirms}")
    leak = engine.guard_leak_usdc
    check("★회귀 방지(CODE-01) - 브로커 자기신고 partial 도 유출에 계상된다",
          leak != 0, str(leak))
    check("유출이 청구 총액과 정확히 일치", leak == quote.total_usdc,
          f"leak={leak} / total={quote.total_usdc}")
    codes = [p.get("code") for p in engine.bus.pending]
    check("GUARD_PENDING 방출(활동 로그·화면에 남는다)",
          "GUARD_DELIVERY_UNCONFIRMED" in codes, str(codes))
    trades = [t for t in engine.trades if t["side"] == "buy"]
    check("거래가 partial 로 기록", bool(trades) and trades[0]["status"] == "partial",
          trades[0]["status"] if trades else "없음")
    check("포지션 미반영", engine.agents[SYMBOL].position.quantity == 0,
          str(engine.agents[SYMBOL].position.quantity))
    check("세션 정지", not engine.trading_enabled, str(engine.bus.paused))


# ---------- 2c) 브로커 partial 이어도 자산이 실제 도착했으면 유출 아님 (오탐 0) ----------
async def test_buy_broker_partial_but_delivered_no_leak() -> None:
    """'브로커가 partial 이면 무조건 유출' 로 고치지 않은 이유의 회귀 테스트.

    전달 tx 가 늦게 확정되면 브로커의 자기신고는 partial 이지만 자산은 실제로 도착해 있다.
    그 경우까지 유출로 찍으면 KPI 가 반대 방향으로 거짓이 된다 — 판정은 온체인 재조회
    (check_delivery)가 하고, 진입 조건은 '검사 대상에 넣을지'만 정한다.
    """
    _p("\n[2c] 라이브 매수 - 브로커는 partial 인데 자산은 도착 → 유출 0 (판정은 check_delivery)")
    engine = await _new_session()
    q = engine._broker.quote(SYMBOL, SPEND, PRICE)
    chain = _FakeChain(delivered_units=to_base_units(q.quantity, eng.CFG.stock_decimals),
                       confirm_seq=[True, False])
    _go_live(engine, chain)

    await _buy(engine)
    check("전제 - 브로커는 전달 tx 미확정으로 partial 신고", chain.confirms >= 2,
          f"confirms={chain.confirms}")
    check("온체인에 자산이 도착했으면 유출 0(오탐 없음)", engine.guard_leak_usdc == 0,
          str(engine.guard_leak_usdc))
    check("GUARD_PENDING 이벤트 0건", not engine.bus.pending, str(len(engine.bus.pending)))


# ---------- 3) 정상 배송에서는 증가하지 않는다 (오탐 0) ----------
async def test_buy_delivered_no_leak() -> None:
    _p("\n[3] 라이브 매수 - 주식이 실제로 도착하면 유출 0 (오탐 0)")
    engine = await _new_session()
    # 도착할 수량을 미리 계산해 원장에 심는다 (엔진이 만들 견적과 같은 입력이라 값이 같다)
    q = engine._broker.quote(SYMBOL, SPEND, PRICE)
    _go_live(engine, _FakeChain(delivered_units=to_base_units(q.quantity, eng.CFG.stock_decimals)))

    quote = await _buy(engine)
    trades = [t for t in engine.trades if t["side"] == "buy"]
    check("라이브 매수가 settled", bool(trades) and trades[0]["status"] == "settled",
          trades[0]["status"] if trades else "없음")
    check("정상 배송이면 유출 0(오탐 없음)", engine.guard_leak_usdc == 0,
          str(engine.guard_leak_usdc))
    check("포지션 반영됨", engine.agents[SYMBOL].position.quantity == quote.quantity,
          f"{engine.agents[SYMBOL].position.quantity} / {quote.quantity}")
    check("GUARD_PENDING 이벤트 0건", not engine.bus.pending, str(len(engine.bus.pending)))


# ---------- 4) 매수 기준선 조회 실패는 유출이 아니다 (서명 전 보류) ----------
async def test_buy_baseline_unread_no_leak() -> None:
    _p("\n[4] 라이브 매수 - 기준선 조회 실패는 결제 전 보류라 유출 아님")
    engine = await _new_session()
    _go_live(engine, _FakeChain(fail_first_read=True))

    await _buy(engine)
    codes = [p.get("code") for p in engine.bus.pending]
    check("GUARD_BASELINE_UNREAD 로 보류", "GUARD_BASELINE_UNREAD" in codes, str(codes))
    check("매수 기준선 실패는 유출 0(온체인에 나간 돈이 없음)",
          engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))
    check("거래 기록 자체가 없음", not engine.trades, str(len(engine.trades)))


# ---------- 5) 매도 대금 미도착 → 증가 ----------
async def test_sell_payout_unconfirmed_leaks() -> None:
    _p("\n[5] 라이브 매도 - 주식은 나갔는데 대금 미도착 → 유출 가산")
    engine = await _new_session()
    await _buy(engine)                      # 드라이로 포지션 확보(유출 0)
    check("매도 전 유출 0", engine.guard_leak_usdc == 0, str(engine.guard_leak_usdc))

    _go_live(engine, _FakeChain(delivered_units=0))   # USDC 가 안 들어온다
    quote = await _sell(engine)

    leak = engine.guard_leak_usdc
    check("★회귀 방지 - 유출이 0 이 아니다", leak != 0, str(leak))
    check("유출이 매도 대금과 정확히 일치", leak == quote.total_usdc,
          f"leak={leak} / total={quote.total_usdc}")
    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("매도가 partial 로 강등", bool(sells) and sells[0]["status"] == "partial",
          sells[0]["status"] if sells else "없음")
    check("실현손익 미반영", engine.realized_pnl == 0, str(engine.realized_pnl))


# ---------- 6) 매도 기준선 조회 실패(GUARD_BASELINE_UNREAD) → 증가 ----------
async def test_sell_baseline_unread_leaks() -> None:
    _p("\n[6] 라이브 매도 - 대금 기준선 조회 실패도 보수적으로 유출 계상")
    engine = await _new_session()
    await _buy(engine)                      # 드라이 매수로 포지션 확보

    _go_live(engine, _FakeChain(fail_first_read=True, delivered_units=10**12))
    quote = await _sell(engine)

    leak = engine.guard_leak_usdc
    codes = [p.get("code") for p in engine.bus.pending]
    check("GUARD_BASELINE_UNREAD 로 보류", "GUARD_BASELINE_UNREAD" in codes, str(codes))
    check("★회귀 방지 - 유출이 0 이 아니다", leak != 0, str(leak))
    check("유출이 매도 대금과 정확히 일치(확인 못 한 것은 미도착으로 계상)",
          leak == quote.total_usdc, f"leak={leak} / total={quote.total_usdc}")
    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("매도가 partial 로 강등", bool(sells) and sells[0]["status"] == "partial",
          sells[0]["status"] if sells else "없음")


# ---------- 7) 정상 매도는 증가하지 않는다 (오탐 0) ----------
async def test_sell_paid_no_leak() -> None:
    _p("\n[7] 라이브 매도 - 대금이 실제로 도착하면 유출 0 (오탐 0)")
    engine = await _new_session()
    await _buy(engine)
    qty = engine.agents[SYMBOL].position.quantity
    q = engine._broker.sell_quote(SYMBOL, qty, PRICE)
    _go_live(engine, _FakeChain(delivered_units=to_base_units(q.total_usdc, eng.CFG.usdc_decimals)))

    await _sell(engine)
    sells = [t for t in engine.trades if t["side"] == "sell"]
    check("라이브 매도가 settled", bool(sells) and sells[0]["status"] == "settled",
          sells[0]["status"] if sells else "없음")
    check("정상 대금 도착이면 유출 0(오탐 없음)", engine.guard_leak_usdc == 0,
          str(engine.guard_leak_usdc))
    check("GUARD_PENDING 이벤트 0건", not engine.bus.pending, str(len(engine.bus.pending)))


# ---------- 8) 여러 건이면 누적 + 스냅샷 반영 + 세션 재시작 리셋 ----------
async def test_accumulates_snapshot_and_reset() -> None:
    _p("\n[8] 누적·스냅샷 반영·세션 재시작 리셋")
    engine = await _new_session()

    _go_live(engine, _FakeChain(delivered_units=0))
    q1 = await _buy(engine)
    after1 = engine.guard_leak_usdc

    _CHAIN[0] = _FakeChain(delivered_units=0)        # 다음 사이클용 새 원장(기준선 재캡처)
    q2 = await _buy(engine)
    after2 = engine.guard_leak_usdc

    check("★회귀 방지 - 1건째에서 이미 0 이 아니다", after1 != 0, str(after1))
    check("2건이면 합계로 누적", after2 == q1.total_usdc + q2.total_usdc,
          f"{after1} + {q2.total_usdc} = {after2}")
    check("누적값이 1건째보다 크다", after2 > after1, f"{after1} → {after2}")

    snap = engine.state_snapshot()
    check("state_snapshot.guard.leak_usdc 가 실제 값을 그대로 반영",
          snap["guard"]["leak_usdc"] == str(after2),
          f"{snap['guard']['leak_usdc']} / {after2}")
    check("★회귀 방지 - 화면에 뜨는 값도 0.00 이 아니다",
          Decimal(snap["guard"]["leak_usdc"]) > 0, snap["guard"]["leak_usdc"])

    # 세션 재시작 — 실제로는 _finalize 가 status 를 idle 로 되돌린 뒤 다음 start 가 온다.
    # (_finalize 는 브리핑 백그라운드 태스크를 띄우므로 여기서는 그 결과 상태만 만든다.)
    engine.status, engine._task = "idle", None
    await engine.start("dry", {"type": "condition", "brain": "rule"},
                       {"type": "replay", "dataset": "daily", "symbols": [SYMBOL]},
                       autostart=False)
    check("새 세션 시작 시 유출이 0 으로 리셋", engine.guard_leak_usdc == 0,
          str(engine.guard_leak_usdc))
    check("새 세션 스냅샷도 0.00", engine.state_snapshot()["guard"]["leak_usdc"] == "0",
          engine.state_snapshot()["guard"]["leak_usdc"])


# ---------- 9) GUARD_PENDING 이벤트에 leak_usdc 가 실린다 ----------
async def test_pending_event_carries_leak_field() -> None:
    _p("\n[9] GUARD_PENDING 이벤트 payload 에 leak_usdc 필드")
    # 9-a) 매수 배송 미확인
    engine = await _new_session()
    _go_live(engine, _FakeChain(delivered_units=0))
    quote = await _buy(engine)
    ev_buy = engine.bus.pending[-1] if engine.bus.pending else {}
    check("매수 pending 이벤트에 leak_usdc 존재", "leak_usdc" in ev_buy, str(sorted(ev_buy)))
    check("매수 leak_usdc 값이 청구 총액과 일치",
          ev_buy.get("leak_usdc") == str(quote.total_usdc),
          f"{ev_buy.get('leak_usdc')} / {quote.total_usdc}")
    check("매수 pending 코드가 GUARD_DELIVERY_UNCONFIRMED",
          ev_buy.get("code") == "GUARD_DELIVERY_UNCONFIRMED", str(ev_buy.get("code")))

    # 9-b) 매도 대금 미도착
    engine2 = await _new_session()
    await _buy(engine2)
    _go_live(engine2, _FakeChain(delivered_units=0))
    squote = await _sell(engine2)
    ev_sell = engine2.bus.pending[-1] if engine2.bus.pending else {}
    check("매도 pending 이벤트에 leak_usdc 존재", "leak_usdc" in ev_sell, str(sorted(ev_sell)))
    check("매도 leak_usdc 값이 매도 대금과 일치",
          ev_sell.get("leak_usdc") == str(squote.total_usdc),
          f"{ev_sell.get('leak_usdc')} / {squote.total_usdc}")

    # 9-c) 매도 기준선 실패
    engine3 = await _new_session()
    await _buy(engine3)
    _go_live(engine3, _FakeChain(fail_first_read=True, delivered_units=10**12))
    bquote = await _sell(engine3)
    ev_base = engine3.bus.pending[-1] if engine3.bus.pending else {}
    check("기준선 실패 pending 이벤트에 leak_usdc 존재", "leak_usdc" in ev_base,
          str(sorted(ev_base)))
    check("기준선 실패 leak_usdc 값이 매도 대금과 일치",
          ev_base.get("leak_usdc") == str(bquote.total_usdc),
          f"{ev_base.get('leak_usdc')} / {bquote.total_usdc}")


# ---------- 10) '검사 불가' 보류는 공격 차단과 같은 칸에 합산되지 않는다 (CODE-03) ----------
async def test_unverified_separated_from_blocked() -> None:
    """첫 화면 '가드 차단' 이 두 종류의 사건을 한 칸에 더하던 문제의 회귀 테스트.

    Gemini 쿼터가 소진되면 의미 대조가 GUARD_LLM_UNVERIFIED 로 매수를 보류하는데, 그것이
    guard_block_count 에 그대로 더해져 악성 청구서를 판정으로 막은 건수와 구별되지 않았다.
    계층을 합치지 않는 것이 이 제품의 미덕인데(red_team 은 blocked_by_policy 로 분리한다)
    대표 지표가 그걸 어기고 있었다. 총계는 그대로 두고 부분집합으로 방출한다.
    """
    _p("\n[10] 의미 대조 '검사 불가'(쿼터 소진) 보류 - 판정 차단과 분리 계상")

    class _AlwaysUnverified:
        """쿼터 소진·응답 실패로 판정을 못 내리는 상태를 고정 재현한다(네트워크 0)."""

        def __init__(self) -> None:
            self.stats = SemanticStats()

        def check(self, **kw):
            return SemanticVerdict(GUARD_LLM_UNVERIFIED, False, "unverified",
                                   "테스트 주입: 판정자 응답 실패", kw.get("description", ""))

    engine = await _new_session()
    engine._guard.semantic = _AlwaysUnverified()
    await _buy(engine)

    g = engine.state_snapshot()["guard"]
    check("매수가 보류됐다(blocked 1)", g["blocked"] == 1, str(g["blocked"]))
    check("★그 1건이 '검사 불가'로 분리 계상", g["blocked_unverified"] == 1,
          str(g["blocked_unverified"]))
    check("결제가 실제로 나가지 않았다(체결 0건)", not engine.trades, str(len(engine.trades)))

    # 대조군 — 판정으로 막은 차단(수취인 위반)은 '검사 불가'에 섞이지 않는다
    engine2 = await _new_session()
    engine2._guard.payees = {"NoBodyKnowsThisPubkey11111111111111111111111"}
    await _buy(engine2)
    g2 = engine2.state_snapshot()["guard"]
    check("대조군 - 수취인 위반도 blocked 1", g2["blocked"] == 1, str(g2["blocked"]))
    check("★대조군 - 검사 불가는 0 (판정 차단이 섞이지 않는다)",
          g2["blocked_unverified"] == 0, str(g2["blocked_unverified"]))


async def main() -> int:
    eng.CFG = dataclasses.replace(REAL_CFG, stock_mint=STOCK_MINT)
    orig = _install_rpc_stubs()
    try:
        await test_dry_stays_zero()
        await test_buy_delivery_unconfirmed_leaks()
        await test_buy_broker_partial_leaks()
        await test_buy_broker_partial_but_delivered_no_leak()
        await test_buy_delivered_no_leak()
        await test_buy_baseline_unread_no_leak()
        await test_sell_payout_unconfirmed_leaks()
        await test_sell_baseline_unread_leaks()
        await test_sell_paid_no_leak()
        await test_accumulates_snapshot_and_reset()
        await test_pending_event_carries_leak_field()
        await test_unverified_separated_from_blocked()
    finally:
        _restore_rpc(orig)
        eng.CFG = REAL_CFG

    ok = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    _p(f"\n결과: {ok}/{total} 통과")
    for name, cond, detail in _results:
        if not cond:
            _p(f"  실패: {name} - {detail}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
