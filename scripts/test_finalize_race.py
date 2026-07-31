"""세션 마무리(_finalize)가 '다음 세션'을 덮어쓰지 않는지 검증한다 — BUG-09 회귀.

재현한 결함 (버그 부서 2026-07-27 스캔, BUG-09 / 중간 / 엣지케이스):
  `_finalize` 의 finally 는 **status 를 먼저 "idle" 로 내린 뒤** 세션 요약 영속저장을
  await 하고(타임아웃 5초), 그 다음에야 `trading_enabled=True`·`pause_info=None` 을
  실행했다. 그런데 다음 세션의 유일한 관문은 `start()` 의 `status != "idle"` 하나뿐이라,
  그 await 구간(Firestore 배포본에서는 실제로 수백 ms~5초)에 새 세션이 통과할 수 있었다.

  결과: 끝난 세션 A 의 마무리 코드가 **실행 중인 세션 B** 의 긴급정지를 해제하고,
  B 에게 ENGINE_STOPPED 를 쏘며(그 페이로드의 trades·ticks 는 B 의 값이라 A 의 실적으로
  보고됨), B 가 이미 비운 trades 를 보고 A 의 종료 브리핑이 조용히 스킵됐다.
  데모 대본이 '공격 세션 → 운용 세션'으로 이어지면 실제로 밟는 경로다.

  ⚠ 리포트가 함께 기록한 것: 후보 수정안 1순위였던 "finalizing_sid = self.session_id 를
  캡처해 비교" 는 이 버그를 **못 막는다** — session_id 가 초 단위라 같은 초에 재시작하면
  A·B 가 동일해지고, 그게 정확히 이 버그의 발생 조건이다.

수정: 엔진 소유권을 놓는 두 줄(`self._task = None`·`self.status = "idle"`)을 finally 의
  **맨 마지막**으로 옮겼다. 세션 단위 상태 리셋·ENGINE_STOPPED·브리핑 예약·영속 저장이
  전부 끝난 뒤에야 다음 세션이 들어올 수 있다. `stop()` 은 어차피 `await self._task` 로
  이 함수의 완료를 기다리므로 응답이 느려지지 않는다.

검증 방법(네트워크 0):
  save_session 이 느린 스토어(_SlowStore)를 물려 배포본의 Firestore 왕복을 흉내 내고,
  그 await 가 흐르는 동안 별도 태스크가 '다음 세션 시작'을 시도한다(HTTP start 핸들러 흉내).
  실제 `TradingEngine.start()` 를 그대로 호출하므로 관문 로직에 스텁이 끼지 않는다.

★ 회귀 방지의 핵심: '저장이 호출된 시점의 status 가 idle 이 아님' 과 '그 창에서 시작 시도가
  거부됨' 을 단언한다. 순서를 예전으로 되돌리면 두 단언이 즉시 실패한다.

재현: python -m scripts.test_finalize_race   (프로젝트 루트)
"""
from __future__ import annotations
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from web import events as ev  # noqa: E402
from web.engine import EngineError, TradingEngine  # noqa: E402
from web.events import EventBus  # noqa: E402
from web.store import BaseStore  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: List[tuple[str, bool, str]] = []

SYMBOL = "AAPL"
STRATEGY = {"type": "condition", "brain": "rule"}
FEED = {"type": "replay", "dataset": "daily", "symbols": [SYMBOL]}


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
    """세션 종료·정지 이벤트를 순서까지 보존해 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.order: List[str] = []
        self.stopped: List[dict] = []

    def emit(self, kind, payload):  # type: ignore[override]
        if kind == ev.ENGINE_STOPPED:
            self.order.append("ENGINE_STOPPED")
            self.stopped.append(dict(payload or {}))
        elif kind == ev.TRADING_PAUSED:
            self.order.append("TRADING_PAUSED")
        return super().emit(kind, payload)


class _SlowStore(BaseStore):
    """Firestore 배포본을 흉내 내는 느린 스토어.

    save_session 이 호출된 **그 순간의 엔진 status** 를 기록하고, 그동안 다른 코루틴이
    돌 수 있게 여러 번 양보한다(실제 네트워크 왕복과 같은 성질 — 이벤트 루프가 열린다).
    """

    enabled = True
    backend = "slow-fake"

    def __init__(self, engine_ref: List[Optional[TradingEngine]], *, yields: int = 6):
        self._engine_ref = engine_ref
        self._yields = yields
        self.calls = 0
        self.saw_status: str = ""
        self.saw_summary: Dict[str, Any] = {}
        self.saw_session_id: str = ""

    async def save_session(self, session_id: str, summary: Dict[str, Any]) -> None:
        self.calls += 1
        engine = self._engine_ref[0]
        self.saw_status = engine.status if engine is not None else "<없음>"
        self.saw_session_id = session_id
        self.saw_summary = dict(summary or {})
        for _ in range(self._yields):
            await asyncio.sleep(0)  # 다음 세션 시작 시도에 실행 기회를 준다


async def _seed_session(engine: TradingEngine) -> None:
    """드라이 세션을 구성한다(백그라운드 루프 없이 — _finalize 를 직접 스텝하기 위함)."""
    await engine.start("dry", STRATEGY, FEED, autostart=False)
    engine._broker_http = None   # .env 에 BROKER_HTTP_URL 이 있어도 결정론 유지


async def test_finalize_does_not_release_next_session() -> None:
    """A 의 마무리가 흐르는 동안 B 가 시작될 수 없고, A 의 리셋은 정상 수행된다."""
    _p("\n[1] 마무리 중 다음 세션 시작 시도 — 거부되어야 한다")

    ref: List[Optional[TradingEngine]] = [None]
    bus = _RecordingBus()
    store = _SlowStore(ref)
    engine = TradingEngine(bus, store)
    ref[0] = engine

    await _seed_session(engine)
    engine.tick = 7                       # 영속 저장 조건(tick or trades or decisions) 충족
    engine.pause("human")                 # 세션 A 를 긴급정지 상태로 둔다
    check("세션 A 긴급정지 적용", engine.trading_enabled is False,
          f"trading_enabled={engine.trading_enabled}")

    attempt: Dict[str, Any] = {}

    async def _next_session_attempt() -> None:
        # A 의 마무리가 끝나기 전에 새 세션을 켜려는 시도(= HTTP start 핸들러).
        # 예전 순서에서는 여기가 통과하고, 그 뒤에 A 의 리셋이 B 를 덮어썼다.
        await asyncio.sleep(0)            # _finalize 가 저장 await 에 들어갈 때까지 양보
        try:
            await engine.start("dry", STRATEGY, FEED, autostart=False)
            attempt["started"] = True
        except EngineError as e:
            attempt["rejected"] = str(e)
        except Exception as e:            # 예상 밖 예외도 실패로 드러나게 남긴다
            attempt["error"] = f"{type(e).__name__}: {e}"

    attacker = asyncio.create_task(_next_session_attempt())
    await engine._finalize()
    await attacker

    check("영속 저장이 실제로 호출됨", store.calls == 1, f"calls={store.calls}")
    check("저장 시점의 status 가 idle 이 아니다 (핵심 회귀 단언)",
          store.saw_status not in ("", "idle"), f"status={store.saw_status!r}")
    check("마무리 중 시작 시도가 거부됨", "rejected" in attempt and "started" not in attempt,
          f"attempt={attempt}")
    check("거부 사유가 '이미 실행 중' 계열", "실행 중" in attempt.get("rejected", ""),
          attempt.get("rejected", "<없음>"))
    check("마무리 후 status=idle", engine.status == "idle", f"status={engine.status}")
    check("마무리 후 _task 해제", engine._task is None)

    _p("\n[2] 세션 단위 상태 리셋은 그대로 수행된다")
    check("긴급정지 해제됨", engine.trading_enabled is True)
    check("pause_info 비워짐", engine.pause_info is None, f"pause_info={engine.pause_info}")
    check("ENGINE_STOPPED 1회 방출", len(bus.stopped) == 1, f"n={len(bus.stopped)}")
    check("was_paused=True 로 보고", bool(bus.stopped and bus.stopped[0].get("was_paused")),
          str(bus.stopped[:1]))
    check("ENGINE_STOPPED 의 ticks 가 A 의 값(7)", bool(bus.stopped) and bus.stopped[0].get("ticks") == 7,
          str(bus.stopped[0].get("ticks") if bus.stopped else None))

    _p("\n[3] 순서 — 종료 이벤트가 저장 await 보다 앞선다")
    check("TRADING_PAUSED → ENGINE_STOPPED 순서 보존",
          bus.order == ["TRADING_PAUSED", "ENGINE_STOPPED"], str(bus.order))
    check("저장에 넘어간 session_id 가 A 의 것", store.saw_session_id == engine.session_id,
          f"saved={store.saw_session_id} engine={engine.session_id}")


async def test_next_session_starts_after_finalize() -> None:
    """마무리가 끝난 뒤에는 정상적으로 다음 세션이 시작된다(게이트를 막아 버리지 않았는지)."""
    _p("\n[4] 마무리 완료 후에는 다음 세션이 정상 시작된다")

    ref: List[Optional[TradingEngine]] = [None]
    engine = TradingEngine(_RecordingBus(), _SlowStore(ref, yields=2))
    ref[0] = engine

    await _seed_session(engine)
    engine.tick = 3
    await engine._finalize()
    check("첫 세션 마무리 후 idle", engine.status == "idle", f"status={engine.status}")

    try:
        await _seed_session(engine)
        started = True
        detail = ""
    except Exception as e:
        started = False
        detail = f"{type(e).__name__}: {e}"
    check("다음 세션 시작 성공", started, detail)
    check("새 세션은 실행 상태", engine.status == "running", f"status={engine.status}")
    check("새 세션에서 긴급정지 가능", engine.pause("human").get("trading_enabled") is False)

    await engine._finalize()   # 뒷정리 — 남은 세션을 닫아 둔다


def test_archive_path_does_not_clobber() -> None:
    """같은 분에 끝난 두 세션의 증빙 파일이 서로를 덮어쓰지 않는다 — L1 회귀.

    예전 파일명은 분 단위(%Y%m%d_%H%M)라, 41초 간격으로 두 세션을 돌리면 파일이 1개만
    남고 앞 세션의 payment_tx 가 사라졌다. 잃는 것이 온체인 tx 증빙이라 재촬영 중에
    조용히 일어나면 되돌릴 방법이 없다."""
    _p("\n== 아카이브 파일명 — 같은 분의 두 세션이 덮어쓰지 않는가 (L1) ==")
    import tempfile
    from datetime import datetime as _dt
    from web.engine import _archive_path

    t1 = _dt(2026, 7, 31, 16, 3, 10)
    t2 = _dt(2026, 7, 31, 16, 3, 51)          # 41초 뒤 — 옛 형식이면 같은 이름
    check("같은 분·다른 초 → 다른 파일명",
          _archive_path(t1, "solana-devnet") != _archive_path(t2, "solana-devnet"),
          _archive_path(t1, "solana-devnet"))
    check("초가 파일명에 들어간다", "160310" in _archive_path(t1, "solana-devnet"),
          _archive_path(t1, "solana-devnet"))

    # 같은 '초'까지 겹쳐도 기존 파일을 덮어쓰지 않는다
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            os.makedirs(os.path.join("artifacts", "tx"), exist_ok=True)
            p1 = _archive_path(t1, "solana-devnet")
            with open(p1, "w", encoding="utf-8") as f:
                f.write('{"payment_tx": "세션 #1 증빙"}')
            p2 = _archive_path(t1, "solana-devnet")   # 같은 초로 한 번 더
            check("같은 초여도 기존 파일을 비켜 간다", p1 != p2, f"{p1} / {p2}")
            with open(p1, encoding="utf-8") as f:
                check("세션 #1 증빙이 살아 있다", "세션 #1 증빙" in f.read())
        finally:
            os.chdir(cwd)


async def main() -> int:
    _p("=" * 74)
    _p("BUG-09 회귀 — 세션 마무리가 다음 세션을 덮어쓰지 않는가")
    _p("=" * 74)
    await test_finalize_does_not_release_next_session()
    await test_next_session_starts_after_finalize()
    test_archive_path_does_not_clobber()

    ok = sum(1 for _, c, _ in _results if c)
    n = len(_results)
    _p("\n" + "=" * 74)
    _p(f"결과: {ok}/{n} 통과")
    if ok != n:
        for name, cond, detail in _results:
            if not cond:
                _p(f"  실패: {name}" + (f" - {detail}" if detail else ""))
    _p("=" * 74)
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
