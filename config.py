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


def env_num(key: str, default: str, cast, *, blank_ok: bool = True):
    """숫자형 환경변수 — 빈 값은 기본값으로 보고, 못 읽는 값은 변수명을 밝히며 멈춘다.

    `_get` 만 쓰면 '키는 있는데 값이 빈 문자열'일 때 기본값이 아니라 "" 가 돌아와
    `Decimal("")`·`int("")` 가 터지고, config 를 임포트하는 **모든 진입점**(web.server·
    run_demo·scripts/*)이 부팅 전에 죽는다. 이 저장소의 `.env.example` 자체가 여러 키를
    `KEY=` 형태로 쓰고 python-dotenv 가 그것을 "" 로 주입하므로 실제로 밟을 수 있는 길이다.
    Cloud Run 도 `--set-env-vars` 안에 숫자 값을 콤마로 나열하므로 오타 하나면 같은 상태가
    되고, 그게 심사용 URL 이다.

    빈 값은 기본값으로 되돌리되, 진짜 잘못 쓴 값(예: `BUDGET_USDC=abc`)은 조용히 넘기지 않고
    **어느 변수가 문제인지 밝히며** 멈춘다 — 예전에는 `decimal.InvalidOperation` 트레이스백만
    남아서 배포 로그만 보고는 원인 변수를 찾을 수 없었다.

    ⚠ `blank_ok=False` 는 "빈 값도 오류"라는 뜻이고, **조용히 헐거워지면 안 되는 값**에만 쓴다.
    배포가 `MAX_BUDGET_USDC=1000` 으로 낮춰 둔 서버측 상한이 빈 값일 때 기본값 10000 으로
    되돌아가면, 런북이 설명한 통제가 10배 헐거워진 채 아무 경고 없이 기동한다.
    """
    raw = os.environ.get(key, default)
    if blank_ok and not raw.strip():
        raw = default
    try:
        return cast(raw)
    except (ValueError, ArithmeticError):
        raise SystemExit(f"환경변수 {key} 값이 잘못됐습니다: {raw!r} (숫자를 기대합니다)")


@dataclass(frozen=True)
class Config:
    rpc_url: str = _get("SOLANA_RPC_URL", "https://api.devnet.solana.com")
    network: str = _get("SOLANA_NETWORK", "solana-devnet")

    usdc_mint: str = _get("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
    usdc_decimals: int = env_num("USDC_DECIMALS", "6", int)

    stock_symbol: str = _get("STOCK_SYMBOL", "tAAPL")
    stock_mint: str = _get("STOCK_MINT", "")
    stock_decimals: int = env_num("STOCK_DECIMALS", "6", int)
    # 멀티 종목(동시 매수) — 콤마 구분 티커 목록(예: "AAPL,TSLA,NVDA"). 빈값이면 단일 폴백.
    # 범위(사용자 확정): 드라이 + 대시보드 + 백테스트. 라이브 온체인 멀티는 제외(종목별 민트 필요).
    # 웹 세션은 UI/API 가 넘긴 symbols 가 우선하고, 이 값은 .env 기본 목록이다.
    stock_symbols_env: str = _get("STOCK_SYMBOLS", "")

    budget_usdc: Decimal = env_num("BUDGET_USDC", "100", Decimal)
    per_trade_max_usdc: Decimal = env_num("PER_TRADE_MAX_USDC", "50", Decimal)

    # 시간 기반 청산(안전레일) — 조건형 세션에서 포지션을 이 봉 수 이상 보유하면 자동 청산.
    # 검증 실측(scripts/explore_strategy.py)에서 꼬리 위험을 크게 줄여 채택. 0=비활성.
    max_hold_bars: int = env_num("MAX_HOLD_BARS", "10", int)

    # A8 브로커 수수료 (bps, 30 = 0.3%) — 소개서 수익모델 수치와 일치시킬 것
    # ※ 0.1%(10) 인하안은 제출 전 재검토 (docs/feature_spec.md 미결정 메모)
    broker_fee_bps: int = env_num("BROKER_FEE_BPS", "30", int)

    wallet_dir: str = _get("WALLET_DIR", "secrets")
    # Cloud Run 대안 주입 경로 — Secret Manager 파일 마운트 대신 환경변수로 키페어 JSON.
    # (시크릿 2개를 같은 디렉터리에 마운트하는 구성이 플랫폼 제약에 걸릴 수 있어 우회로를 둔다)
    trading_keypair_json: str = _get("TRADING_KEYPAIR_JSON", "")
    broker_keypair_json: str = _get("BROKER_KEYPAIR_JSON", "")
    # 사용자(위임자) 키 — open mandate 서명자. 에이전트(trading) 키와 분리(결함 G 제거).
    # 온체인 자금 불필요(오프라인 ed25519 서명만). 없으면 secrets/user.json 으로 자동 생성.
    user_keypair_json: str = _get("USER_KEYPAIR_JSON", "")

    # 조작 API 보호 — 값이 설정되면 POST /api/* 에 X-Control-Token 헤더를 요구한다.
    # 로컬 개발은 미설정(빈값) = 무인증이라 기존 흐름이 그대로 유지된다.
    control_token: str = _get("CONTROL_TOKEN", "")
    # 서버측 한도 상한 — 외부에서 예산을 무한대로 올리는 것을 기계적으로 차단
    max_budget_usdc: Decimal = env_num("MAX_BUDGET_USDC", "10000", Decimal, blank_ok=False)
    # 웹에서 라이브(온체인) 세션 시작 허용 여부 — 기본 차단, 시연 직전에만 켠다.
    # (기본 "0": config.py:72 주석·web/engine.py 이중 안전장치 에러·배포 런북과 일치.
    #  웹 UI 에서 라이브 데모 시 ALLOW_LIVE_FROM_WEB=1 로 명시적으로 연다. BUG-04)
    allow_live_from_web: bool = _get("ALLOW_LIVE_FROM_WEB", "0").lower() in ("1", "true", "yes")

    # Alpha Vantage (무료 키) — scripts/fetch_market_data.py 일봉 수집용 (런타임 미사용)
    alphavantage_api_key: str = _get("ALPHAVANTAGE_API_KEY", "")

    # 시세 피드 기본값 — mock(8스텝 데모) / replay(실데이터 CSV 재생).
    # 세션 시작 시 UI(피드 선택)가 지정하면 그 값이 우선한다.
    price_feed: str = _get("PRICE_FEED", "replay")
    replay_symbol: str = _get("REPLAY_SYMBOL", "")      # 빈값 = STOCK_SYMBOL 에서 유도(tAAPL→AAPL)
    replay_file: str = _get("REPLAY_FILE", "")          # CSV 직접 지정 시 우선 (기본 data/market/)
    replay_start: str = _get("REPLAY_START", "")        # 재생 시작일 YYYY-MM-DD (빈값=워밍업 직후부터)
    replay_end: str = _get("REPLAY_END", "")            # 재생 종료일 (빈값=마지막 봉까지)
    replay_warmup: int = env_num("REPLAY_WARMUP", "20", int)  # 지표 워밍업 봉 수 (MA20 기준)

    # Gemini (무료 티어) — 키가 있으면 매매 판단을 Gemini 가 수행, 없으면 규칙 기반
    gemini_api_key: str = _get("GEMINI_API_KEY", "")
    # ⚠ 무료 티어 일일 한도(RPD)는 **모델별로 따로** 계산된다
    #   (quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier).
    #   즉 한 모델이 소진돼도 다른 모델은 살아 있다 — 2026-07-27 실측으로 확인했다.
    #   기본값은 한도가 넉넉한 라이트 별칭(약 500건/일). 소진되면 GEMINI_MODEL 로 갈아탄다
    #   (예: gemini-flash-latest — 한도는 훨씬 작으니 짧은 증빙 세션용).
    gemini_model: str = _get("GEMINI_MODEL", "gemini-flash-lite-latest")
    # "developer"(AIza 키) / "vertex"(AQ. 등 express 키) / 빈값=키 형식으로 자동 판별
    gemini_mode: str = _get("GEMINI_MODE", "")

    # Firestore 영속화 (Cloud Run 배포용) — 기본 OFF: 로컬은 GCP 없이 기존 그대로 동작.
    # Cloud Run 에서는 FIRESTORE_ENABLED=1 만 주면 서비스 계정(ADC)으로 자동 인증된다.
    firestore_enabled: bool = _get("FIRESTORE_ENABLED", "").lower() in ("1", "true", "yes")
    firestore_project: str = _get("FIRESTORE_PROJECT", "")      # 빈값 = ADC 프로젝트 자동
    firestore_database: str = _get("FIRESTORE_DATABASE", "")    # 빈값 = (default)
    firestore_prefix: str = _get("FIRESTORE_PREFIX", "autotrader")  # 컬렉션 접두사

    # 브로커 HTTP 402 서비스 (G5) — 값이 있으면 엔진의 매수 레그가 인프로세스 A2A 대신
    # 실제 HTTP 402 왕복(POST /broker/orders → 402 → X-PAYMENT 재시도)으로 결제한다.
    # 예: "http://127.0.0.1:8402". 빈값(기본)이면 기존 인프로세스 경로 그대로.
    broker_http_url: str = _get("BROKER_HTTP_URL", "")

    # 웹 대시보드 (web/server.py)
    web_port: int = env_num("WEB_PORT", "8000", int)
    # 시세 틱 간격(초) — Gemini 무료 티어 분당 호출 제한을 고려한 기본값
    web_tick_interval_sec: float = env_num("WEB_TICK_INTERVAL_SEC", "8", float)
    # B2 데일리 브리핑 자동 생성 시각(HH:MM, 서버 로컬) — 장 마감 시각, 하루 1회
    daily_briefing_time: str = _get("DAILY_BRIEFING_TIME", "16:00")

    @property
    def stock_symbols(self) -> list:
        """멀티 종목 티커 목록. STOCK_SYMBOLS(콤마 구분)가 있으면 그 목록, 없으면 [stock_symbol]."""
        syms = [s.strip() for s in self.stock_symbols_env.split(",") if s.strip()]
        return syms or [self.stock_symbol]


CFG = Config()


def to_base_units(amount: Decimal, decimals: int) -> int:
    """사람이 읽는 금액(예: 5.0 USDC) -> 온체인 base units(정수)."""
    return int((Decimal(amount) * (Decimal(10) ** decimals)).to_integral_value())


def from_base_units(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)
