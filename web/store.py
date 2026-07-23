"""세션 영속화 스토어 — 포지션·거래·브리핑을 서버 재시작 너머로 보존한다.

Cloud Run 은 배포·스케일 때마다 인스턴스가 갈리므로 인메모리 상태가 사라진다.
이 모듈이 그 간극을 메운다:

  BaseStore      기본(no-op) — 로컬 개발은 GCP 없이 기존 그대로 동작
  FirestoreStore FIRESTORE_ENABLED=1 일 때 — Cloud Run 에서는 ADC 로 자동 인증

컬렉션(접두사 FIRESTORE_PREFIX, 기본 autotrader):
  {p}_sessions   세션 1건 = 문서 1개 (거래·판단 전체 포함 — artifacts/tx 아카이브의 DB판)
  {p}_trades     체결 1건 = 문서 1개 (주간/월별 수익 집계용 — 세션 넘어 조회)
  {p}_briefings  데일리 브리핑 1건 = 문서 1개
  {p}_state      defaults 문서 — 한도 기본값 등 (재시작 시 복원)

원칙: 저장 실패가 매매 루프를 멈추지 않는다(엔진이 fire-and-forget + 1회 경고).
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Dict, List, Optional


def jsonable(obj: Any) -> Any:
    """Decimal·datetime 등을 Firestore/JSON 안전값(문자열)으로 깊은 변환."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BaseStore:
    """no-op 스토어 — 로컬 기본. 모든 저장은 조용히 무시, 조회는 빈 결과."""

    enabled: bool = False
    backend: str = "memory"
    detail: str = "영속화 비활성 (FIRESTORE_ENABLED=1 로 켬)"
    last_error: str = ""

    async def ping(self) -> bool:
        return True

    # --- 저장 (엔진이 호출) ---
    async def save_session(self, session_id: str, summary: Dict[str, Any]) -> None:
        pass

    async def save_trade(self, session_id: str, trade: Dict[str, Any]) -> None:
        pass

    async def save_briefing(self, rec: Dict[str, Any], stats: Dict[str, Any]) -> None:
        pass

    async def save_defaults(self, doc: Dict[str, Any]) -> None:
        pass

    # --- 조회 (부팅 복원 + /api/history) ---
    async def load_defaults(self) -> Optional[Dict[str, Any]]:
        return None

    async def load_last_briefing(self) -> Optional[Dict[str, Any]]:
        return None

    async def recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        return []

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        return []

    async def recent_briefings(self, limit: int = 10) -> List[Dict[str, Any]]:
        return []


class FirestoreStore(BaseStore):
    """Firestore(Native 모드) 영속화. Cloud Run: 서비스 계정 ADC 자동 인증.
    로컬 실검증: `gcloud auth application-default login` 후 FIRESTORE_ENABLED=1."""

    enabled = True
    backend = "firestore"

    def __init__(self, project: str = "", database: str = "", prefix: str = "autotrader"):
        # 지연 임포트 — google-cloud-firestore 미설치 로컬에서도 모듈 로드는 가능해야 한다
        from google.cloud import firestore
        kwargs: Dict[str, Any] = {}
        if project:
            kwargs["project"] = project
        if database and database != "(default)":
            kwargs["database"] = database
        self._db = firestore.AsyncClient(**kwargs)
        self._desc = firestore.Query.DESCENDING
        self._prefix = prefix
        self.detail = f"firestore project={self._db.project} prefix={prefix}"

    def _col(self, name: str):
        return self._db.collection(f"{self._prefix}_{name}")

    async def ping(self) -> bool:
        """부팅 시 1회 연결·권한 확인 (문서 없음도 성공)."""
        await self._col("state").document("defaults").get()
        return True

    # --- 저장 ---
    async def save_session(self, session_id: str, summary: Dict[str, Any]) -> None:
        await self._col("sessions").document(session_id).set(
            {**jsonable(summary), "saved_at": _now()})

    async def save_trade(self, session_id: str, trade: Dict[str, Any]) -> None:
        # 문서 id = 세션_주문 — 재시도돼도 중복 문서가 생기지 않는다(멱등)
        doc_id = f"{session_id}_{trade.get('order_id', '')}"
        await self._col("trades").document(doc_id).set(
            {**jsonable(trade), "session_id": session_id, "saved_at": _now()})

    async def save_briefing(self, rec: Dict[str, Any], stats: Dict[str, Any]) -> None:
        doc_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        await self._col("briefings").document(doc_id).set(
            {**jsonable(rec), "stats": jsonable(stats), "saved_at": _now()})

    async def save_defaults(self, doc: Dict[str, Any]) -> None:
        await self._col("state").document("defaults").set(
            {**jsonable(doc), "saved_at": _now()}, merge=True)

    # --- 조회 ---
    async def load_defaults(self) -> Optional[Dict[str, Any]]:
        snap = await self._col("state").document("defaults").get()
        return snap.to_dict() if snap.exists else None

    async def load_last_briefing(self) -> Optional[Dict[str, Any]]:
        rows = await self.recent_briefings(limit=1)
        return rows[0] if rows else None

    async def recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        q = self._col("sessions").order_by("saved_at", direction=self._desc).limit(limit)
        out = []
        async for snap in q.stream():
            d = snap.to_dict()
            # 목록에는 요약만 — 거래·판단 전체는 get_session(상세)으로
            d.pop("trades", None)
            d.pop("decisions", None)
            d.pop("mandate_history", None)
            out.append(d)
        return out

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        snap = await self._col("sessions").document(session_id).get()
        return snap.to_dict() if snap.exists else None

    async def recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        q = self._col("trades").order_by("saved_at", direction=self._desc).limit(limit)
        return [snap.to_dict() async for snap in q.stream()]

    async def recent_briefings(self, limit: int = 10) -> List[Dict[str, Any]]:
        q = self._col("briefings").order_by("saved_at", direction=self._desc).limit(limit)
        return [snap.to_dict() async for snap in q.stream()]


def build_store() -> BaseStore:
    """환경설정 기반 스토어 선택. 초기화 실패는 경고 후 no-op 폴백(서버는 뜬다)."""
    from config import CFG
    if not CFG.firestore_enabled:
        return BaseStore()
    try:
        store = FirestoreStore(
            project=CFG.firestore_project,
            database=CFG.firestore_database,
            prefix=CFG.firestore_prefix,
        )
        print(f"[store] Firestore 영속화 활성: {store.detail}")  # cp949 콘솔 안전(ASCII 구두점)
        return store
    except Exception as e:
        fallback = BaseStore()
        fallback.detail = f"Firestore 초기화 실패로 메모리 폴백: {type(e).__name__}: {e}"
        fallback.last_error = fallback.detail
        print(f"[store] {fallback.detail}")
        return fallback
