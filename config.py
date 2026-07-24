"""전역 설정 로드 (.env 기반). devnet RPC, 민트 주소, 예산 한도 등."""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass
from decimal import Decimal


def enable_console_safe_output() -> None:
    """콘솔 출력 인코딩 안전화 — 모든 진입점이 config 를 임포트하므로 여기서 1회 적용.

    한국어 Windows 에서 stdout 이 파이프/파일로 리다이렉트되면 인코딩이 cp949 가 되고,
    판단 근거·로그에 섞인 em-dash(—) 같은 문자가 UnicodeEncodeError 로 프로그램을 죽인다.
    (`python run_demo.py > log.txt` 는 CLAUDE.md 검증 루틴이 요구하는 로그 아카이빙 방식이다.)

    인코딩 자체는 바꾸지 않는다 — UTF-8 로 강제하면 cp949 콘솔에서 한글이 전부 깨진다.
    표현 불가 문자만 '?' 로 대체해, 어떤 환경에서도 죽지 않고 한글은 그대로 보이게 한다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")   # type: ignore[union-attr]
        except Exception:
            pass  # reconfigure 불가(테스트 캡처 등) — 원래 동작 유지


enable_console_safe_output()

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # dotenv 미설치 시에도 os.environ 로 동작


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Config:
    rpc_url: str = _get("SOLANA_RPC_URL", "https://api.devnet.solana.com")
    network: str = _get("SOLANA_NETWORK", "solana-devnet")

    usdc_mint: str = _get("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
    usdc_decimals: int = int(_get("USDC_DECIMALS", "6"))

    stock_symbol: str = _get("STOCK_SYMBOL", "tAAPL")
    stock_mint: str = _get("STOCK_MINT", "")
    stock_decimals: int = int(_get("STOCK_DECIMALS", "6"))

    budget_usdc: Decimal = Decimal(_get("BUDGET_USDC", "100"))
    per_trade_max_usdc: Decimal = Decimal(_get("PER_TRADE_MAX_USDC", "50"))

    # A8 브로커 수수료 (bps, 30 = 0.3%) — 소개서 수익모델 수치와 일치시킬 것
    # ※ 0.1%(10) 인하안은 제출 전 재검토 (docs/feature_spec.md 미결정 메모)
    broker_fee_bps: int = int(_get("BROKER_FEE_BPS", "30"))

    wallet_dir: str = _get("WALLET_DIR", "secrets")
    # Cloud Run 대안 주입 경로 — Secret Manager 파일 마운트 대신 환경변수로 키페어 JSON.
    # (시크릿 2개를 같은 디렉터리에 마운트하는 구성이 플랫폼 제약에 걸릴 수 있어 우회로를 둔다)
    trading_keypair_json: str = _get("TRADING_KEYPAIR_JSON", "")
    broker_keypair_json: str = _get("BROKER_KEYPAIR_JSON", "")

    # 조작 API 보호 — 값이 설정되면 POST /api/* 에 X-Control-Token 헤더를 요구한다.
    # 로컬 개발은 미설정(빈값) = 무인증이라 기존 흐름이 그대로 유지된다.
    control_token: str = _get("CONTROL_TOKEN", "")
    # 서버측 한도 상한 — 외부에서 예산을 무한대로 올리는 것을 기계적으로 차단
    max_budget_usdc: Decimal = Decimal(_get("MAX_BUDGET_USDC", "10000"))
    # 웹에서 라이브(온체인) 세션 시작 허용 여부 — 기본 차단, 시연 직전에만 켠다
    allow_live_from_web: bool = _get("ALLOW_LIVE_FROM_WEB", "1").lower() in ("1", "true", "yes")

    # Alpha Vantage (무료 키) — scripts/fetch_market_data.py 일봉 수집용 (런타임 미사용)
    alphavantage_api_key: str = _get("ALPHAVANTAGE_API_KEY", "")

    # 시세 피드 기본값 — mock(8스텝 데모) / replay(실데이터 CSV 재생).
    # 세션 시작 시 UI(피드 선택)가 지정하면 그 값이 우선한다.
    price_feed: str = _get("PRICE_FEED", "replay")
    replay_symbol: str = _get("REPLAY_SYMBOL", "")      # 빈값 = STOCK_SYMBOL 에서 유도(tAAPL→AAPL)
    replay_file: str = _get("REPLAY_FILE", "")          # CSV 직접 지정 시 우선 (기본 data/market/)
    replay_start: str = _get("REPLAY_START", "")        # 재생 시작일 YYYY-MM-DD (빈값=워밍업 직후부터)
    replay_end: str = _get("REPLAY_END", "")            # 재생 종료일 (빈값=마지막 봉까지)
    replay_warmup: int = int(_get("REPLAY_WARMUP", "20"))  # 지표 워밍업 봉 수 (MA20 기준)

    # Gemini (무료 티어) — 키가 있으면 매매 판단을 Gemini 가 수행, 없으면 규칙 기반
    gemini_api_key: str = _get("GEMINI_API_KEY", "")
    # flash-latest(=3.6-flash)는 무료 티어가 하루 20회뿐 — 라이트 별칭이 한도가 넉넉함
    gemini_model: str = _get("GEMINI_MODEL", "gemini-flash-lite-latest")
    # "developer"(AIza 키) / "vertex"(AQ. 등 express 키) / 빈값=키 형식으로 자동 판별
    gemini_mode: str = _get("GEMINI_MODE", "")

    # Firestore 영속화 (Cloud Run 배포용) — 기본 OFF: 로컬은 GCP 없이 기존 그대로 동작.
    # Cloud Run 에서는 FIRESTORE_ENABLED=1 만 주면 서비스 계정(ADC)으로 자동 인증된다.
    firestore_enabled: bool = _get("FIRESTORE_ENABLED", "").lower() in ("1", "true", "yes")
    firestore_project: str = _get("FIRESTORE_PROJECT", "")      # 빈값 = ADC 프로젝트 자동
    firestore_database: str = _get("FIRESTORE_DATABASE", "")    # 빈값 = (default)
    firestore_prefix: str = _get("FIRESTORE_PREFIX", "autotrader")  # 컬렉션 접두사

    # 웹 대시보드 (web/server.py)
    web_port: int = int(_get("WEB_PORT", "8000"))
    # 시세 틱 간격(초) — Gemini 무료 티어 분당 호출 제한을 고려한 기본값
    web_tick_interval_sec: float = float(_get("WEB_TICK_INTERVAL_SEC", "8"))
    # B2 데일리 브리핑 자동 생성 시각(HH:MM, 서버 로컬) — 장 마감 시각, 하루 1회
    daily_briefing_time: str = _get("DAILY_BRIEFING_TIME", "16:00")


CFG = Config()


def to_base_units(amount: Decimal, decimals: int) -> int:
    """사람이 읽는 금액(예: 5.0 USDC) -> 온체인 base units(정수)."""
    return int((Decimal(amount) * (Decimal(10) ** decimals)).to_integral_value())


def from_base_units(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)
