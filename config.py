"""전역 설정 로드 (.env 기반). devnet RPC, 민트 주소, 예산 한도 등."""
from __future__ import annotations
import os
from dataclasses import dataclass
from decimal import Decimal

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

    wallet_dir: str = _get("WALLET_DIR", "secrets")

    # Gemini (무료 티어) — 키가 있으면 매매 판단을 Gemini 가 수행, 없으면 규칙 기반
    gemini_api_key: str = _get("GEMINI_API_KEY", "")
    gemini_model: str = _get("GEMINI_MODEL", "gemini-flash-latest")
    # "developer"(AIza 키) / "vertex"(AQ. 등 express 키) / 빈값=키 형식으로 자동 판별
    gemini_mode: str = _get("GEMINI_MODE", "")


CFG = Config()


def to_base_units(amount: Decimal, decimals: int) -> int:
    """사람이 읽는 금액(예: 5.0 USDC) -> 온체인 base units(정수)."""
    return int((Decimal(amount) * (Decimal(10) ** decimals)).to_integral_value())


def from_base_units(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)
