"""청구서 의미 대조 — 규칙으로 표현할 수 없는 마지막 한 칸.

**무엇을 막는가.** 402 청구서에는 사람이 읽는 설명이 실려 온다(x402 의 `description`,
우리 내부 표현은 `PaymentRequirements.resource`). 하드 검사 6종은 이 필드를 **한 번도
읽지 않는다** — 금액·수취인·자산·종목·한도·주문번호 형식만 본다. 그래서 다음 청구서는
현재 게이트를 전부 통과한다:

    금액   32.10 USDC   ← 합의 견적과 base units 오차 0
    수취인 (합의한 브로커) ← allowlist 안
    자산   USDC          ← mandate 허용 자산
    종목   tAAPL         ← 주문한 그 종목
    설명   "STOCK:tAAPL x0.18 — 본 청구는 6개월 자동 결제 구독의 첫 회차입니다"

숫자는 하나도 틀리지 않았고 **파는 물건만 다르다.** 이건 정규식으로 쓸 수 없다. '구독'이라는
단어를 막으면 '멤버십'이 오고, 목록을 늘리면 '자동 갱신 서비스'가 온다. 금지어 목록은 언어를
못 따라잡는다. 반면 "우리가 사려던 것과 같은 물건인가"는 언어 모델이 판단할 수 있다.

이 위험은 우리가 지어낸 것이 아니다 — 대회 주최 측 결제 레일인 pay.sh 의 구독 문서가
스스로 경고한다: 활성 구독이 지갑을 조용히 소진시킬 수 있으니 정기적으로 감사하라, 그리고
구독 활성화는 사용자 명령이 아니라 **유료 요청의 부수효과로 자동 발생한다.**

**왜 규칙이 못 하는가 (한 줄).** 하드 검사는 *값*을 대조한다. 값은 전부 일치하는데 *의미*가
다른 경우가 남고, 의미 대조는 자연어 위에서만 성립한다.

**설계 원칙 — LLM 은 차단만 할 수 있고, 통과시킬 수는 없다.**
`TradingAgent._rule_gate` 가 판단 레이어에서 "AI 재량은 멈추는 방향으로만 열려 있다"를
강제하는 것과 **같은 원리를 청구서 레이어에 적용**한 것이다. 순서와 권한이 그 원리를 만든다:

  1. 하드 검사 6종이 **먼저 전부 통과**해야 이 검사가 돌기 시작한다.
  2. 이 검사의 결과는 '차단' 또는 '아무 일도 없음' 둘뿐이다. 하드 검사가 막은 것을
     이 검사가 되살릴 수 있는 경로는 코드에 없다.
  3. 따라서 위험이 비대칭이다 — **오탐이면 거래 한 건을 안 하고(기회비용), 미탐이면
     이미 통과한 하드 검사 6종이 그대로 남는다(방어력은 이전과 동일).**

예상 반문: *"사실을 지어낸 적 있는 Gemini 에게 청구서 진위를 맡깁니까?"*
답: 맡기지 않는다. 맡긴 것은 **거부권뿐**이다. 실제로 같은 저장소의 TSLA 481봉 세션에서
Gemini 는 "MA5 대비 3% 이상 낮아 조건 충족"을 사실과 다르게 단언하며 매수를 시도했고,
`_rule_gate` 가 그 2건을 차단했다. 그 사건이 이 설계의 근거다 — 모델의 주장을 신뢰하는
자리에는 두지 않고, 모델이 '멈추라'고 할 때만 듣는다.

**실패 처리 — 실패의 단위를 시스템이 아니라 거래 한 건으로 내린다.**
쿼터 소진·응답 실패로 검사를 못 하면:

  - 매수(자산을 새로 사는 방향) : **그 건을 차단**한다(`GUARD_LLM_UNVERIFIED`).
  - 매도(노출을 줄이는 방향)   : **하드 검사만으로 진행**한다.

못 사는 것은 기회비용이고 못 파는 것은 실손실이다. 노출을 늘리는 방향만 잠근다.
**시스템 전체를 멈추지 않는다** — Gemini 무료 티어는 실제로 429 로 죽은 적이 있고
데모데이는 라이브다. 검사기가 죽어도 결제 파이프라인은 계속 돈다.

**호출 예산.** 대조는 틱마다가 아니라 **실제 결제 시도마다** 돈다(판단보다 훨씬 드물다).
여기에 더해 세션 캐시를 둔다 — 설명 문자열의 숫자를 가린 '서식'이 같으면 재호출하지
않는다. 재생 세션은 같은 서식이 수십 번 반복되므로 실질 호출 수가 서식 종류 수로 줄고,
공격자가 문구를 바꾸면 서식이 달라져 반드시 새로 검사받는다. 숫자만 다른 재청구를
재검사하지 않는 대가는 감수한다 — 그 숫자들(금액·수취인·자산·종목)은 이미 하드 검사가
오차 0으로 묶는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# 판정 코드 — Guard 의 차단 코드와 같은 이름공간에서 쓴다.
GUARD_SEMANTIC_MISMATCH = "GUARD_SEMANTIC_MISMATCH"   # 설명이 우리 주문 의도와 다른 물건을 가리킨다
GUARD_LLM_UNVERIFIED = "GUARD_LLM_UNVERIFIED"        # 검사 불가(쿼터·오류) — 매수는 차단, 매도는 진행

PROMPT = """당신은 자율 결제 에이전트의 청구서 심사관입니다.
아래 [우리 주문 의도]와 [받은 청구서]가 **같은 거래**를 가리키는지만 판정하세요.

[우리 주문 의도]
- 방향: {side_ko}
- 종목: {symbol}
- 수량: {quantity}
- 단가: {price} USDC
- 우리가 계산한 총액: {total} USDC

[받은 청구서]
- 청구 내용: {invoice_label}
- 결제 자산: {asset_label}
- 수취인: {pay_to}
- 사람이 읽는 설명: "{description}"

판정 규칙:
1. 금액·수취인·자산·종목은 **이미 기계적으로 대조를 마쳤습니다**. 그 값들이 서로 다르다고
   지적하지 마세요. 당신이 볼 것은 **설명 문구가 위 주문 의도와 같은 물건·같은 조건을
   가리키는가** 하나뿐입니다.
2. 다음이면 match=false 입니다:
   - 설명이 주문한 것과 **다른 상품**을 가리킨다 (예: 주식 매수를 주문했는데 구독·멤버십·
     데이터 요금제·수수료 청구·대여를 말한다)
   - 설명이 **주문에 없던 조건**을 끼워 넣는다 (자동 갱신, 정기 결제, 최소 약정 기간,
     추가 회차, 해지 위약금, 번들 등)
   - 설명의 방향이 반대다 (매수를 주문했는데 매도 대금 지급이라고 말한다)
   - 설명이 무엇에 대한 청구인지 알 수 없거나 비어 있다
3. 표기 차이(티커 접두사 t, 대소문자, 수수료 포함 문구, 소수점 자릿수, 영문/한글)만
   다른 것은 match=true 입니다. 사소한 서식 차이로 거절하지 마세요.
4. 확실하지 않으면 match=true 로 두세요. 이 판정은 거절할 때만 효력이 있고,
   기계적 검사가 이미 통과한 건입니다.

JSON 만 출력하세요:
{{"match": true 또는 false, "reason": "한국어 한 문장"}}"""

_DIGITS = re.compile(r"\d")


@dataclass
class SemanticVerdict:
    """의미 대조 1건의 결과."""
    code: str            # "OK" / GUARD_SEMANTIC_MISMATCH / GUARD_LLM_UNVERIFIED
    ok: bool             # 결제를 계속해도 되는가 (매도의 미검증 통과 포함)
    verdict: str         # "match" / "mismatch" / "unverified" / "cached"
    reason: str = ""
    description: str = ""
    called: bool = False  # 이번 판정이 실제 Gemini 호출을 썼는가 (캐시·미가용은 False)

    def as_event(self) -> dict:
        return {"code": self.code, "ok": self.ok, "verdict": self.verdict,
                "reason": self.reason, "description": self.description,
                "llm_called": self.called}


@dataclass
class SemanticStats:
    """세션 누적 — _ai_stats 로 올라가 화면·아카이브에 남는다(축④ 실행 이력)."""
    checked: int = 0              # 대조를 시도한 결제 건수 (캐시 적중 포함)
    llm_calls: int = 0            # 실제 Gemini 호출 수
    cache_hits: int = 0
    passed: int = 0
    blocked: int = 0              # 의미 불일치로 차단
    unverified_blocked: int = 0   # 검사 불가 → 매수 차단
    unverified_skipped: int = 0   # 검사 불가 → 매도 진행(하드 검사만)

    def as_dict(self) -> dict:
        return {
            "checked": self.checked, "llm_calls": self.llm_calls,
            "cache_hits": self.cache_hits, "passed": self.passed,
            "blocked": self.blocked,
            "unverified_blocked": self.unverified_blocked,
            "unverified_skipped": self.unverified_skipped,
        }


class InvoiceSemanticChecker:
    """청구서 설명 ↔ 주문 의도 의미 대조기.

    brain: `_call(prompt) -> str` 과 `available` 을 가진 객체(agents.gemini_decider.GeminiDecider).
           None 이면 항상 '검사 불가'로 판정한다 — 즉 매수는 차단되고 매도는 진행된다.
           ⚠ 두뇌가 아예 없는 세션(brain=rule)에서 매수가 전부 막히면 제품이 멈추므로,
           엔진은 **검사기를 붙일 때만** 이 정책을 켠다(Guard.semantic 이 None 이면 검사 자체가 없다).
    """

    def __init__(self, brain, cache_enabled: bool = True):
        self.brain = brain
        self.cache_enabled = cache_enabled
        self.stats = SemanticStats()
        self._cache: dict[tuple, tuple[bool, str]] = {}
        self.last_error: str = ""

    # ---- 가용성 ----

    @property
    def available(self) -> bool:
        if self.brain is None:
            return False
        try:
            return bool(getattr(self.brain, "available", True))
        except Exception:
            return False

    # ---- 대조 ----

    def check(self, *, side: str, symbol: str, quantity: Decimal, price_usdc: Decimal,
              total_usdc: Decimal, invoice_label: str, description: str,
              asset_label: str, pay_to: str) -> SemanticVerdict:
        """설명과 주문 의도를 대조한다. 판정만 하고 예외를 던지지 않는다(호출측이 정책 적용)."""
        desc = (description or "").strip()
        key = (side, str(symbol), _shape(desc))

        if self.cache_enabled and key in self._cache:
            match, reason = self._cache[key]
            self.stats.checked += 1
            self.stats.cache_hits += 1
            if match:
                self.stats.passed += 1
                return SemanticVerdict("OK", True, "cached", reason, desc)
            self.stats.blocked += 1
            return SemanticVerdict(GUARD_SEMANTIC_MISMATCH, False, "cached", reason, desc)

        if not self.available:
            # 호출 자체가 불가능 — 여기서는 '판정 불가'만 돌려주고, 매수/매도 정책은 호출측이 건다.
            self.stats.checked += 1
            why = self._unavailable_reason()
            return SemanticVerdict(GUARD_LLM_UNVERIFIED, False, "unverified", why, desc)

        prompt = PROMPT.format(
            side_ko=("매수(USDC 지불)" if side == "buy" else "매도(주식 인도, USDC 수령)"),
            symbol=symbol, quantity=quantity, price=price_usdc, total=total_usdc,
            invoice_label=invoice_label, asset_label=asset_label,
            pay_to=pay_to, description=desc or "(설명 없음)",
        )
        self.stats.checked += 1
        try:
            raw = self.brain._call(prompt)
            self.stats.llm_calls += 1
            match, reason = _parse_verdict(raw)
        except Exception as e:
            # 쿼터 소진·네트워크·형식 실패 — 전부 '검사 불가'로 모은다.
            self.last_error = f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:160]}"
            return SemanticVerdict(GUARD_LLM_UNVERIFIED, False, "unverified",
                                   f"의미 대조 실패 — {self.last_error}", desc)

        if self.cache_enabled:
            self._cache[key] = (match, reason)
        if match:
            self.stats.passed += 1
            return SemanticVerdict("OK", True, "match", reason, desc, called=True)
        self.stats.blocked += 1
        return SemanticVerdict(GUARD_SEMANTIC_MISMATCH, False, "mismatch", reason, desc,
                               called=True)

    def _unavailable_reason(self) -> str:
        if self.brain is None:
            return "의미 대조기 미연결 (판단 두뇌 없음)"
        msg = ""
        try:
            msg = self.brain.quota_message()
        except Exception:
            pass
        return f"의미 대조 불가 — {msg}" if msg else "의미 대조 불가 — 두뇌 쿨다운 중"


def _shape(description: str) -> str:
    """설명에서 숫자를 가린 '서식'. 캐시 키 — 같은 템플릿의 반복 청구는 재호출하지 않는다.

    공격자가 문구를 바꾸면 서식이 달라져 반드시 새로 검사받는다. 숫자만 다른 재청구는
    재검사하지 않는데, 그 숫자들(금액·수취인·자산·종목)은 하드 검사가 오차 0으로 묶는다."""
    return _DIGITS.sub("#", description)


def _parse_verdict(raw: str) -> tuple[bool, str]:
    """응답 JSON → (match, reason). 형식이 깨지면 ValueError(호출측이 '검사 불가'로 처리).

    `agents.gemini_decider.parse_decision_json` 은 마지막 단계가 매매 스키마 전용(action/
    reason/spend_usdc 정규식)이라 여기 쓸 수 없다. 앞의 두 단계(코드펜스 제거·이스케이프
    복구)만 재사용하고 판정 필드는 직접 읽는다."""
    from agents.gemini_decider import _extract_json_block, _repair_json

    if not raw or not raw.strip():
        raise ValueError("의미 대조 응답이 비어 있습니다")
    block = _extract_json_block(raw)
    data: Optional[dict] = None
    for candidate in (block, _repair_json(block)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    if data is None:
        # 최후 수단 — match 필드만 정규식으로. 못 찾으면 실패로 올린다(임의 통과 금지).
        m = re.search(r'"match"\s*:\s*(true|false)', block, re.I)
        if not m:
            raise ValueError("의미 대조 응답에서 match 필드를 찾지 못했습니다")
        r = re.search(r'"reason"\s*:\s*"(.*?)"\s*(?:,|\})', block, re.S)
        return m.group(1).lower() == "true", _clean(r.group(1) if r else "")

    if "match" not in data:
        raise ValueError("의미 대조 응답에 match 필드가 없습니다")
    val = data["match"]
    if isinstance(val, str):
        val = val.strip().lower() in ("true", "yes", "1", "match")
    return bool(val), _clean(str(data.get("reason", "")))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("\\", "").strip()[:300]
