"""이벤트 버스 — 엔진의 모든 상태 변화를 대시보드(SSE)로 중계한다.

feature_spec.md 공통 원칙("모든 상태 변화는 이벤트 로그에 남긴다")의 구현.
인메모리 히스토리(최근 1000건) + 구독자별 asyncio.Queue. 이벤트 id 는 1부터
증가하는 정수로, SSE `id:` 필드와 Last-Event-ID 재전송(새로고침 복원)에 쓴다.

주의: emit 은 서버 이벤트 루프 스레드에서만 호출한다(엔진 태스크·API 핸들러).
"""
from __future__ import annotations
import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Dict, List, Set

# --- 이벤트 타입 ---
ENGINE_STARTED = "engine_started"
ENGINE_STOPPED = "engine_stopped"
PRICE_TICK = "price_tick"
DECISION = "decision"                # A6 판단 타임라인 ([gemini]/[rule]/[rule-fallback])
QUOTE = "quote"                      # A2A 견적 (협상 로그)
X402_REQUIRED = "x402_required"      # x402 3단계
X402_SUBMITTED = "x402_submitted"
X402_COMPLETED = "x402_completed"
TRADE = "trade"                      # A5 체결 요약 (거래 테이블 행)
MANDATE_REJECTED = "mandate_rejected"  # AP2 거부
MANDATE_UPDATED = "mandate_updated"  # A3 한도 변경 (재서명, 변경 이력)
TRADING_PAUSED = "trading_paused"    # A2 긴급정지 (actor: human / risk-guard)
TRADING_RESUMED = "trading_resumed"
BALANCES = "balances"                # 라이브 온체인 잔액 스냅샷
REPLAY_ENDED = "replay_ended"        # 실데이터 재생 소진 → 세션 자동 종료 예고
BRIEFING = "briefing"                # B2 데일리 브리핑 (Gemini 리포트/템플릿 폴백)
ERROR = "error"


@dataclass
class Event:
    id: int
    ts: str
    type: str
    data: Dict[str, Any]

    def to_json(self) -> str:
        # Decimal 등 비직렬화 값은 문자열로 강제 (default=str)
        return json.dumps(
            {"id": self.id, "ts": self.ts, "type": self.type, "data": self.data},
            ensure_ascii=False, default=str,
        )

    def to_sse(self) -> str:
        return f"id: {self.id}\ndata: {self.to_json()}\n\n"


class EventBus:
    def __init__(self, history_size: int = 1000):
        self._history: Deque[Event] = deque(maxlen=history_size)
        self._subscribers: Set[asyncio.Queue] = set()
        self._next_id = 1

    def emit(self, type: str, data: Dict[str, Any] | None = None) -> Event:
        ev = Event(
            id=self._next_id,
            ts=datetime.now().isoformat(timespec="seconds"),
            type=type,
            data=data or {},
        )
        self._next_id += 1
        self._history.append(ev)
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # 느린 구독자는 유실 — 재접속 시 since() 히스토리로 복구
        return ev

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def since(self, last_id: int) -> List[Event]:
        """last_id 이후의 히스토리 (SSE 재접속 재전송용)."""
        return [e for e in self._history if e.id > last_id]
