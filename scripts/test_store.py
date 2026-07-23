"""영속화 스토어 통합 테스트 — FakeStore 로 엔진의 저장·복원 지점을 검증한다.

온체인·Gemini·GCP 호출 없음(드라이런 + 목 시세 + 가짜 스토어).
실행: python -m scripts.test_store
"""
import os

# CFG 는 임포트 시점에 고정되므로 반드시 config 임포트 전에 환경을 만든다:
# 빠른 틱(0.05초) + Gemini 미사용(브리핑 템플릿 폴백) + Firestore 비활성.
os.environ["WEB_TICK_INTERVAL_SEC"] = "0.05"
os.environ["GEMINI_API_KEY"] = ""
os.environ["FIRESTORE_ENABLED"] = ""

import asyncio
from datetime import datetime
from decimal import Decimal

from web.engine import TradingEngine
from web.events import EventBus
from web.store import BaseStore, jsonable


class FakeStore(BaseStore):
    """호출 내용을 기록만 하는 스토어 — Firestore 없이 통합 지점을 검증."""
    enabled = True
    backend = "fake"
    detail = "fake store (test)"

    def __init__(self, defaults=None, last_briefing=None):
        self.sessions: dict = {}
        self.trades: list = []
        self.briefings: list = []
        self.defaults = defaults
        self._boot_briefing = last_briefing

    async def ping(self) -> bool:
        return True

    async def save_session(self, session_id, summary):
        self.sessions[session_id] = summary

    async def save_trade(self, session_id, trade):
        self.trades.append({**trade, "session_id": session_id})

    async def save_briefing(self, rec, stats):
        self.briefings.append({**rec, "stats": stats})

    async def save_defaults(self, doc):
        self.defaults = doc

    async def load_defaults(self):
        return self.defaults

    async def load_last_briefing(self):
        return self._boot_briefing


def check(name: str, ok: bool, note: str = "") -> int:
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" ({note})" if note else ""))
    return 0 if ok else 1


DCA_STRATEGY = {"type": "dca", "dca_unit": "ticks", "dca_every_ticks": 1,
                "dca_amount_usdc": "10"}


async def run_session(engine: TradingEngine, seconds: float = 0.6) -> None:
    """드라이런 DCA 세션(1틱마다 10 USDC 매수 — 판단 결정적) 실행 후 종료."""
    await engine.start("dry", DCA_STRATEGY, {"type": "mock"})
    await asyncio.sleep(seconds)
    if engine.status == "running":
        await engine.stop()
    await asyncio.sleep(0.3)  # 세션 종료 자동 브리핑(백그라운드 태스크) 완료 대기


async def main_async() -> int:
    bad = 0

    # ① jsonable — Decimal·datetime 이 Firestore 안전값(str)으로 변환되는가
    out = jsonable({"d": Decimal("1.23"), "t": datetime(2026, 7, 23), "n": [Decimal(2)]})
    bad += check("jsonable Decimal/datetime 변환", out["d"] == "1.23" and out["n"] == ["2"]
                 and isinstance(out["t"], str))

    # ② FakeStore 세션 — 거래·세션 요약·자동 브리핑이 모두 저장되는가
    store = FakeStore()
    engine = TradingEngine(EventBus(), store)
    await run_session(engine)
    sid = engine.session_id
    bad += check("세션 ID 발급 (YYYYMMDD_HHMMSS_dry)", sid.endswith("_dry") and len(sid) == 19, sid)
    bad += check("체결이 트레이드 컬렉션에 저장", len(store.trades) >= 1,
                 f"{len(store.trades)}건")
    bad += check("트레이드 문서에 세션 ID·주문 ID 포함",
                 all(t.get("session_id") == sid and t.get("order_id") for t in store.trades))
    summary = store.sessions.get(sid)
    bad += check("세션 요약 문서 저장 (dry 포함)", summary is not None)
    if summary:
        bad += check("요약 trade_count = 실제 저장 건수",
                     summary["trade_count"] == len(store.trades),
                     f"{summary['trade_count']} vs {len(store.trades)}")
        bad += check("요약에 포지션·전략·피드 포함",
                     Decimal(summary["position_qty"]) > 0
                     and summary["strategy"]["type"] == "dca"
                     and summary["feed"]["type"] == "mock")
        bad += check("요약이 JSON 안전값(모든 금액 str)",
                     isinstance(summary["realized_pnl_usdc"], str)
                     and isinstance(summary["budget_total_usdc"], str))
    bad += check("세션 종료 자동 브리핑 저장 (템플릿 폴백)",
                 len(store.briefings) >= 1
                 and store.briefings[-1]["source"] == "template")

    # ③ 한도 변경(대기 상태) → 기본값 저장 → 새 엔진 부팅 복원
    engine.update_limits(Decimal("77"), Decimal("33"), actor="human")
    await asyncio.sleep(0.1)  # fire-and-forget 태스크 완료 대기
    bad += check("한도 변경이 defaults 로 저장",
                 store.defaults == {"budget_total_usdc": "77",
                                    "per_trade_max_usdc": "33", "updated_by": "human"})
    boot_briefing = {"ts": "2026-07-23T09:00:00", "trigger": "manual",
                     "source": "template", "text": "복원 브리핑", "archive": "x.md",
                     "saved_at": "2026-07-23T09:00:01", "stats": {}}
    store2 = FakeStore(defaults=store.defaults, last_briefing=boot_briefing)
    engine2 = TradingEngine(EventBus(), store2)
    await engine2.restore_from_store()
    bad += check("부팅 복원: 한도 기본값", engine2.budget_total == Decimal("77")
                 and engine2.per_trade_max == Decimal("33"))
    bad += check("부팅 복원: 최근 브리핑(+restored 표시, stats 미포함)",
                 engine2.last_briefing["text"] == "복원 브리핑"
                 and engine2.last_briefing.get("restored") is True
                 and "stats" not in engine2.last_briefing)
    snap = engine2.state_snapshot()
    bad += check("state_snapshot 에 persistence 블록",
                 snap["persistence"]["enabled"] is True
                 and snap["persistence"]["backend"] == "fake")

    # ④ 저장 실패 내성 — 스토어가 죽어도 세션은 정상 완주, ERROR 이벤트 1회
    class BrokenStore(FakeStore):
        async def save_trade(self, session_id, trade):
            raise RuntimeError("파이어스토어 장애 흉내")

        async def save_session(self, session_id, summary):
            raise RuntimeError("파이어스토어 장애 흉내")

    bus3 = EventBus()
    engine3 = TradingEngine(bus3, BrokenStore())
    await run_session(engine3)
    msgs = [e.data["message"] for e in bus3.since(0) if e.type == "error"]
    warn_trade = [m for m in msgs if m.startswith("영속 저장 실패")]      # fire-and-forget 경고
    warn_sess = [m for m in msgs if m.startswith("세션 영속 저장 실패")]  # finalize 세션 저장
    trades3 = [e for e in bus3.since(0) if e.type == "trade"]
    bad += check("스토어 장애에도 체결은 계속", len(trades3) >= 2, f"체결 {len(trades3)}건")
    bad += check("저장 실패 경고는 1회만 (스팸 방지)", len(warn_trade) == 1, f"{len(warn_trade)}회")
    bad += check("세션 저장 실패도 이벤트로 보고", len(warn_sess) == 1, f"{len(warn_sess)}회")
    bad += check("장애 후 엔진 정상 종료", engine3.status == "idle")

    # ⑤ no-op(BaseStore) — 로컬 기본 경로: 저장 없이 기존과 동일하게 동작
    engine4 = TradingEngine(EventBus())  # store 미지정 = BaseStore
    await run_session(engine4, seconds=0.3)
    snap4 = engine4.state_snapshot()
    bad += check("no-op 스토어: 세션 정상 완주 + persistence.enabled=false",
                 engine4.status == "idle" and snap4["persistence"]["enabled"] is False)

    return bad


def main() -> int:
    bad = asyncio.run(main_async())
    print(f"\n{'전부 통과' if bad == 0 else f'실패 {bad}건'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
