"""B2 데일리 브리핑 — 세션의 거래·판단 데이터를 Gemini 가 한국어 리포트로.

Gemini 호출 실패 시 템플릿 요약으로 폴백한다(데모 무중단 원칙).
호출은 blocking 이므로 엔진에서 asyncio.to_thread 로 감싼다.
"""
from __future__ import annotations
import json
from typing import Any, Dict, Tuple

from config import CFG

PROMPT = """너는 '402 Guard' 의 세션 리포트 작성자다. 이 서비스는 AI 에이전트가 사람이 정한
한도 안에서 스스로 주식을 사고팔고 USDC 로 정산하되, 결제에 서명하기 직전에 청구서를 대조해
승인하는 지출 승인 게이트다. 아래 세션 데이터를 근거로 사용자에게 보내는 한국어 데일리
브리핑을 작성하라. 이것은 테스트 토큰 데모이며 투자 조언이 아니다.

[세션 데이터(JSON)]
{stats}

[작성 규칙]
- 4~7문장, 친근한 존댓말. 마크다운·이모지·제목 없이 순수 문장으로만.
- 반드시 포함: 거래 횟수(매수/매도), 실현손익과 수익률, 남은 예산, 누적 브로커 수수료.
- 보유 수량이 남아 있으면 평가손익(unrealized_pnl_usdc)과 총자산(total_asset_usdc)도 언급하라.
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
    if stats.get("position_qty") not in (None, "0"):
        lines.append(
            f"보유 {stats['position_qty']} 주의 평가손익은 {stats.get('unrealized_pnl_usdc')} USDC, "
            f"총자산은 {stats.get('total_asset_usdc')} USDC 입니다.")
    if stats.get("ap2_reject_count"):
        lines.append(f"AP2 한도 거부가 {stats['ap2_reject_count']}건 있었습니다 (한도 밖 결제는 기계적으로 차단).")
    if stats.get("pause_count"):
        lines.append(f"긴급정지가 {stats['pause_count']}회 있었습니다.")
    return " ".join(lines)


def generate_briefing_text(stats: Dict[str, Any]) -> Tuple[str, str, str]:
    """리포트 생성 → (본문, 출처 'gemini'/'template', 실패 상세).

    실패 상세는 **본문에 섞지 않고 따로 돌려준다.** 예전에는 예외 문자열 80자를 본문 끝에
    이어 붙였는데, 그 본문이 그대로 화면(브리핑 카드)·Firestore·아카이브에 남았다.
    실측(배포본 `/api/history/briefings`)에서 사용자가 읽는 문장이 이렇게 끝났다:
      "... (Gemini 호출 실패 ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429,
       'message': 'You exceeded your cu → 템플릿 요약)"
    쿼터가 소진된 상태로 시연·촬영하면 심사위원이 이 raw 문자열을 읽게 된다.
    그래서 본문에는 사람이 읽는 한 문장만 남기고, 원문은 세 번째 값으로 올려보내
    화면이 `title` 툴팁 등 눈에 띄지 않는 자리에 보관하게 한다(진단 정보를 버리지 않는다).
    """
    if not CFG.gemini_api_key:
        return _fallback_text(stats), "template", ""
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
            return text, "gemini", ""
        return _fallback_text(stats), "template", "빈 응답"
    except Exception as e:
        detail = str(e).replace("\n", " ")[:80]
        return (_fallback_text(stats)
                + " (AI 요약을 만들지 못해 자동 계산 요약으로 대체했습니다.)"), \
            "template", f"{type(e).__name__}: {detail}"
