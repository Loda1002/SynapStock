"""B2 데일리 브리핑 — 세션의 거래·판단 데이터를 Gemini 가 한국어 리포트로.

Gemini 호출 실패 시 템플릿 요약으로 폴백한다(데모 무중단 원칙).
호출은 blocking 이므로 엔진에서 asyncio.to_thread 로 감싼다.
"""
from __future__ import annotations
import json
from typing import Any, Dict, Tuple

from config import CFG

PROMPT = """너는 자율 주식매매 에이전트 서비스의 리포트 작성자다. 아래 세션 데이터를 근거로
사용자에게 보내는 한국어 데일리 브리핑을 작성하라. 이것은 테스트 토큰 데모이며 투자 조언이 아니다.

[세션 데이터(JSON)]
{stats}

[작성 규칙]
- 4~7문장, 친근한 존댓말. 마크다운·이모지·제목 없이 순수 문장으로만.
- 반드시 포함: 거래 횟수(매수/매도), 실현손익과 수익률, 남은 예산, 누적 브로커 수수료.
- AP2 거부(ap2_reject_count)나 긴급정지(pause_count)가 1건 이상이면 언급하고, 0이면 생략.
- 숫자는 데이터에 있는 값만 사용하라(지어내기 금지). 단위는 USDC.
"""


def _fallback_text(stats: Dict[str, Any]) -> str:
    """Gemini 없이도 항상 나오는 숫자 요약."""
    lines = [
        f"오늘 세션 요약입니다 ({stats['date']}, {stats['symbol']}).",
        f"매수 {stats['buy_count']}건({stats['buy_total_usdc']} USDC), "
        f"매도 {stats['sell_count']}건({stats['sell_total_usdc']} USDC)을 체결했습니다.",
        f"실현손익은 {stats['realized_pnl_usdc']} USDC (수익률 {stats['return_pct']}%)이고, "
        f"남은 예산은 {stats['budget_remaining_usdc']} USDC 입니다.",
        f"누적 브로커 수수료는 {stats['cum_fee_usdc']} USDC 입니다.",
    ]
    if stats.get("ap2_reject_count"):
        lines.append(f"AP2 한도 거부가 {stats['ap2_reject_count']}건 있었습니다 (한도 밖 결제는 기계적으로 차단).")
    if stats.get("pause_count"):
        lines.append(f"긴급정지가 {stats['pause_count']}회 있었습니다.")
    return " ".join(lines)


def generate_briefing_text(stats: Dict[str, Any]) -> Tuple[str, str]:
    """리포트 생성 → (본문, 출처 'gemini'/'template')."""
    if not CFG.gemini_api_key:
        return _fallback_text(stats), "template"
    try:
        from google import genai
        from google.genai import types
        mode = CFG.gemini_mode or ("developer" if CFG.gemini_api_key.startswith("AIza") else "vertex")
        kwargs: Dict[str, Any] = {"api_key": CFG.gemini_api_key}
        if mode == "vertex":
            kwargs["vertexai"] = True
        client = genai.Client(**kwargs)
        resp = client.models.generate_content(
            model=CFG.gemini_model,
            contents=PROMPT.format(stats=json.dumps(stats, ensure_ascii=False, default=str)),
            config=types.GenerateContentConfig(temperature=0.4),
        )
        text = (resp.text or "").strip()
        if text:
            return text, "gemini"
        return _fallback_text(stats), "template"
    except Exception as e:
        detail = str(e).replace("\n", " ")[:80]
        return (_fallback_text(stats)
                + f" (Gemini 호출 실패 {type(e).__name__}: {detail} → 템플릿 요약)"), "template"
