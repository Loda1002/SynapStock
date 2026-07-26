"""Gemini 판단 모듈 — TradingAgent 의 규칙 스텁을 대체하는 두뇌.

Gemini API(무료 티어)를 호출해 시세·규칙·예산을 보고 매수/매도/보류를 판단한다.
호출이 실패하면 TradingAgent 가 규칙 기반으로 폴백하므로(데모데이 네트워크 대비)
데모는 어떤 상황에도 멈추지 않는다.

주의: 이 판단은 사용자가 정의한 규칙의 실행이며 투자 조언이 아니다(데모용).
"""
from __future__ import annotations
import json
import re
import time
from decimal import Decimal
from typing import List

from agents.trading_agent import Decision, Strategy
from market.indicators import format_ta_block
from shared.models import Position

# 일일(RPD) 한도 소진 시 쿨다운. 태평양 자정에 풀리므로 응답의 retryDelay(수십 초)는
# 의미가 없다 — 그 값을 믿고 재시도하면 하루 종일 헛호출한다. 30분마다 한 번만
# 두드려 보고, 초기화된 뒤에는 스스로 회복하게 한다.
_DAILY_QUOTA_COOLDOWN_SEC = 1800.0

PROMPT = """너는 자율 주식매매 데모 시스템의 판단 모듈이다. 매수(buy)/매도(sell)/보류(hold)를
판단한다. 이것은 테스트 토큰 데모이며 투자 조언이 아니다.

[사용자 규칙 — 지표 기준]
- 매수: 현재가가 5일 이동평균(MA5) 대비 {buy_dip_pct}% 이상 낮으면(즉 {buy_threshold} USDC 이하) 매수를 고려한다
- 매도: 현재가가 보유 평균단가 대비 {take_profit_pct}% 이상 높으면(즉 {take_profit_line}) 매도를 고려한다
- 1회 매수 금액은 {spend} USDC 를 넘지 않는다
- 매수·매도 조건이 동시에 성립하면 이익 확정(매도)이 우선이다

[판단 모드 — {mode_name}]
{mode_rules}

[지표]
- MA5 {ma5} USDC · MA20 {ma20} USDC
- 최근 5봉 등락률 {change5} · 봉간 수익률 변동성(표준편차) {volatility}
- 보유 평단 대비 손익률 {position_pnl}
- 직전 행동 회고: {retrospective}
{ta_block}
[현재 상태]
- 종목: {symbol}
- 현재가: {price} USDC
- 직전 가격 흐름(과거→최근): {history}
- 남은 총예산: {remaining} USDC
- 보유 수량: {position_qty} (평균단가 {position_avg} USDC)

[거래 비용 — 브로커 수수료 {fee_pct}%]
- 매수 시 실제 지불액 = 대금 + 수수료 (실효 매수가 {eff_buy} USDC/주)
- 매도 시 실제 수령액 = 대금 - 수수료 (실효 매도가 {eff_sell} USDC/주)
- 수수료를 반영한 실효 가격 기준으로 손익을 판단하라

가격 흐름과 지표를 근거로 한국어 한 문장의 이유를 만들어라.
JSON 만 출력: {{"action":"buy"|"sell"|"hold","reason":"한국어 한 문장","spend_usdc":숫자}}
reason 은 한글을 그대로 쓰고 역슬래시(\\)·따옴표·줄바꿈을 넣지 마라."""

# TA 보강(ta_mode) 시에만 프롬프트에 붙는 판단 기준 — 지표 계산은 코드가 이미 끝냈고
# (모델이 산수하지 않게), 모델은 이 매핑과 종합 규칙으로 해석만 한다.
TA_RULES = """
[TA 판단 기준 — 패턴→신호 매핑·종합 규칙]
- 골든크로스=매수 · 데드크로스=매도. 중기선(MA10) 하락 중엔 신규 매수 보류(사지마),
  장기선 상승 중엔 성급한 매도 금지(팔지마).
- 지지 반등·저항 돌파=매수 / 저항 거부·지지 이탈=매도
  (이탈된 지지는 저항으로, 돌파된 저항은 지지로 전환된다)
- 매도 신호: M형(이중천장)·삼중천장·헤드앤숄더·상승쐐기·하락깃발·역V자·다이아몬드천장
  / 캔들: 유성·흑삼병·석별·장대음선
- 매수 신호: 역헤드앤숄더·삼중바닥·W바닥·하락쐐기·상승깃발·상승삼각형
  / 캔들: 샛별·적삼병·상승장악·망치
- 대기 패턴(삼각수렴·박스권·확산삼각형·상승채널) 탐지 시 방향 확정까지 hold.
  도지는 단독으로는 보류 신호다.
- 같은 방향 신호가 겹치면 신뢰를 높이고, 신호가 충돌하면 hold 가 기본이다.
- TA 는 휴리스틱 참고 자료다. 위 [사용자 규칙] 게이트와 판단 모드 범위 안에서 활용하라.
"""

# 판단 모드별 재량 조항 — strict(규칙 그대로) / trend(보류 재량).
# 어느 모드든 "규칙 미충족 상태에서의 신규 개시"는 금지 = 한도는 AP2가 기계적으로,
# 판단은 AI가 맡는 경계를 유지한다 (docs/next_round_plan.md §2.1).
MODE_RULES = {
    "strict": ("엄격 (규칙 그대로)",
               "규칙을 엄격히 적용한다. 규칙 조건이 충족되면 해당 행동을, 아니면 hold 를 "
               "선택한다. 규칙 위반 판단은 금지."),
    "trend": ("추세 (보류 재량)",
              "기본은 규칙 준수이지만, 규칙 신호가 떠도 추세가 반대라고 판단하면 보류(hold)할 "
              "재량이 있다. 예: 매수 신호지만 낙폭이 계속 커지는 중이면 바닥 확인까지 보류, "
              "매도 신호지만 상승 추세가 이어지면 더 큰 이익을 위해 보류. "
              "단, 규칙 조건이 충족되지 않았는데 새로 매수·매도를 시작하는 것은 금지다. "
              "보류 재량을 쓸 때는 이유에 추세 근거를 명시하라."),
}

# 형식이 깨진 응답을 받았을 때의 1회 재요청 지시 (쿼터 절약 위해 재시도는 한 번만)
RETRY_SUFFIX = """

[중요] 직전 응답의 JSON 형식이 잘못됐다. 설명·코드블록 없이 JSON 객체 한 줄만,
역슬래시와 줄바꿈 없이 다시 출력하라."""

_ESCAPES = '"\\/bfnrt'


def _is_hex4(s: str) -> bool:
    return len(s) == 4 and all(c in "0123456789abcdefABCDEF" for c in s)


def _extract_json_block(raw: str) -> str:
    """코드펜스(```json …```)나 앞뒤 설명을 걷어내고 JSON 객체 부분만 남긴다."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text


def _repair_json(raw: str) -> str:
    """문자열 안의 잘못된 이스케이프·제어문자를 고친다.

    보고된 실패: `Invalid \\uXXXX escape` — 모델이 `\\u` 뒤에 16진수 4자리가 아닌
    한글·공백을 붙여 내보내는 경우. 잘못된 역슬래시는 리터럴 역슬래시로 바꾸고,
    문자열 안에 그대로 들어온 줄바꿈·탭은 정식 이스케이프로 치환한다."""
    out: List[str] = []
    in_str = False
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = False
            out.append(c)
            i += 1
        elif c == "\\":
            nxt = raw[i + 1] if i + 1 < n else ""
            if nxt == "u" and _is_hex4(raw[i + 2:i + 6]):
                out.append(raw[i:i + 6])       # 정상 유니코드 이스케이프 — 보존
                i += 6
            elif nxt and nxt in _ESCAPES:
                out.append(c + nxt)            # 정상 이스케이프 — 보존
                i += 2
            else:
                out.append("\\\\")             # 잘못된 이스케이프 — 리터럴 역슬래시로
                i += 1
        elif ord(c) < 0x20:
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(c, " "))
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _fields_by_regex(raw: str) -> dict:
    """JSON 복구도 실패했을 때의 최후 수단 — 필요한 필드만 정규식으로 긁어낸다."""
    m_action = re.search(r'"action"\s*:\s*"?(buy|sell|hold)"?', raw, re.I)
    if not m_action:
        raise ValueError("응답에서 action 필드를 찾지 못했습니다")
    m_reason = re.search(r'"reason"\s*:\s*"(.*?)"\s*(?:,|\})', raw, re.S)
    m_spend = re.search(r'"spend_usdc"\s*:\s*"?(-?\d+(?:\.\d+)?)"?', raw)
    reason = re.sub(r"\s+", " ", (m_reason.group(1) if m_reason else "")).replace("\\", "").strip()
    data = {"action": m_action.group(1).lower(), "reason": reason}
    if m_spend:
        data["spend_usdc"] = m_spend.group(1)
    return data


def parse_decision_json(raw: str) -> dict:
    """Gemini 응답 텍스트 → dict. 원문 파싱 → 정화 후 재파싱 → 필드 추출 순으로 시도.

    세 단계가 모두 실패하면 ValueError — 호출부가 1회 재요청하고, 그래도 실패하면
    TradingAgent 의 규칙 폴백으로 넘어간다."""
    if not raw or not raw.strip():
        raise ValueError("Gemini 응답이 비어 있습니다")
    block = _extract_json_block(raw)
    for candidate in (block, _repair_json(block)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return _fields_by_regex(block)


class GeminiDecider:
    """Gemini API 호출 래퍼. mode: developer(AIza 키) / vertex(AQ. 등 새 형식 키)."""

    def __init__(self, api_key: str, model: str, mode: str = ""):
        from google import genai  # 지연 임포트 — 미설치여도 규칙 모드는 동작
        self.mode = mode or ("developer" if api_key.startswith("AIza") else "vertex")
        kwargs = {"api_key": api_key}
        if self.mode == "vertex":
            kwargs["vertexai"] = True
        self.client = genai.Client(**kwargs)
        self.model = model
        # 무료 티어 429(쿼터 초과) 시 API 재호출을 잠시 멈추는 쿨다운 (틱마다 헛호출 방지)
        self._cooldown_until: float = 0.0
        self.format_retries = 0   # 형식 위반으로 재요청한 횟수 (운영 관찰용)
        # 마지막 429 의 성격 — "daily"(일일 RPD) / "rate"(분당 RPM) / "" (아직 없음).
        # 둘은 대응이 다르다: 분당은 수십 초 뒤 자동 회복, 일일은 태평양 자정까지 안 풀린다.
        self.quota_scope: str = ""
        self.quota_limit: str = ""     # 한도 값 (예: "500")
        self.quota_id: str = ""        # 예: GenerateRequestsPerDayPerProjectPerModel-FreeTier

    def _call(self, prompt: str) -> str:
        """Gemini 호출 → 응답 텍스트. 429 는 쿨다운을 걸고 '요약된' 예외로 바꿔 올린다.

        원본 ClientError 는 본문이 1,000자가 넘는 JSON 이라 호출부(TradingAgent)가
        앞부분만 잘라 기록했고, 그 결과 **정작 필요한 quotaId·quotaValue 가 통째로
        버려졌다**(분당 초과인지 일일 소진인지 화면에서 구분 불가). 여기서 한 줄로
        요약해 올리면 잘려도 사유가 남는다."""
        from google.genai import types
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception as e:
            text = str(e)
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                self._enter_cooldown(text)
                raise RuntimeError(self.quota_message()) from e
            raise
        return resp.text or ""

    def _enter_cooldown(self, error_text: str) -> None:
        """429 를 성격별로 나눠 쿨다운을 건다.

        일일(RPD) 한도는 태평양 자정에만 풀리는데, 구글은 그 경우에도 retryDelay 로
        수십 초짜리 값을 돌려준다. 그 값을 그대로 믿으면 하루 종일 40초마다 헛호출하고
        영영 회복하지 못한다 — 그래서 quotaId 에 'PerDay' 가 보이면 길게 재운다."""
        qid = re.search(r"quotaId'?\s*:\s*'?([A-Za-z0-9_\-]+)", error_text)
        qval = re.search(r"quotaValue'?\s*:\s*'?(\d+)", error_text)
        if not qval:
            qval = re.search(r"limit:\s*(\d+)", error_text)
        self.quota_id = qid.group(1) if qid else ""
        self.quota_limit = qval.group(1) if qval else ""

        if "perday" in self.quota_id.lower() or "PerDay" in error_text:
            self.quota_scope = "daily"
            self._cooldown_until = time.time() + _DAILY_QUOTA_COOLDOWN_SEC
            return

        self.quota_scope = "rate"
        m = (re.search(r"retry in (\d+(?:\.\d+)?)s", error_text)
             or re.search(r"retryDelay'?: '?(\d+(?:\.\d+)?)s", error_text))
        delay = float(m.group(1)) if m else 60.0
        self._cooldown_until = time.time() + max(delay, 30.0)

    def quota_message(self) -> str:
        """마지막 429 를 사람이 읽을 한 문장으로. 잘려도 사유가 남게 앞쪽에 성격을 둔다."""
        if self.quota_scope == "daily":
            limit = f"{self.quota_limit}건/일" if self.quota_limit else "일일 한도"
            return (f"무료 티어 일일 한도 소진({limit}, {self.model}) — 태평양 자정까지 "
                    f"회복되지 않습니다. 이 세션은 규칙 판단으로 진행합니다.")
        remaining = max(0, int(self._cooldown_until - time.time()))
        limit = f", 한도 {self.quota_limit}" if self.quota_limit else ""
        return f"무료 티어 분당 한도 초과{limit} — 쿨다운 {remaining}초 남음(자동 재시도)"

    def decide(
        self,
        symbol: str,
        price: Decimal,
        history: List[Decimal],
        strategy: Strategy,
        remaining_usdc: Decimal,
        position: Position,
        fee_bps: int = 0,
        indicators: dict | None = None,
        retrospective: str = "",
    ) -> Decision:
        if self._cooldown_until - time.time() > 0:
            raise RuntimeError(self.quota_message())

        ind = indicators or {}
        tp = ind.get("take_profit")
        mode_name, mode_rules = MODE_RULES.get(
            getattr(strategy, "decision_mode", "strict"), MODE_RULES["strict"])

        def pct_or(key: str, absent: str = "산출 전") -> str:
            v = ind.get(key)
            return f"{'+' if v >= 0 else ''}{v}%" if v is not None else absent

        # TA 보강 — ta_mode 세션에서만 신호 요약 + 매핑 규칙을 주입 (쿼터 절약)
        ta = ind.get("ta")
        ta_block = ""
        if ta:
            ta_block = ("\n[TA 신호 요약 — 코드가 계산한 결정적 값, 산수 불필요]\n"
                        + format_ta_block(ta) + "\n" + TA_RULES)

        fee_rate = Decimal(fee_bps) / Decimal(10000)
        prompt = PROMPT.format(
            ta_block=ta_block,
            buy_dip_pct=strategy.buy_dip_pct,
            take_profit_pct=strategy.take_profit_pct,
            buy_threshold=ind.get("buy_threshold", "-"),
            take_profit_line=(f"{tp} USDC 이상" if tp is not None
                              else "현재 보유 없음 — 매도 불가"),
            mode_name=mode_name,
            mode_rules=mode_rules,
            ma5=ind.get("ma5", "-"),
            ma20=ind.get("ma20") or "-(워밍업 부족)",
            change5=pct_or("change5_pct"),
            volatility=pct_or("volatility_pct"),
            position_pnl=pct_or("position_pnl_pct", "보유 없음"),
            retrospective=retrospective or "이력 없음",
            spend=strategy.spend_per_trade_usdc,
            symbol=symbol,
            price=price,
            history=" → ".join(str(p) for p in history) if history else "(첫 틱)",
            remaining=remaining_usdc,
            position_qty=position.quantity,
            position_avg=position.avg_price_usdc,
            fee_pct=fee_rate * 100,
            eff_buy=(price * (1 + fee_rate)).quantize(Decimal("0.01")),
            eff_sell=(price * (1 - fee_rate)).quantize(Decimal("0.01")),
        )
        raw = self._call(prompt)
        try:
            data = parse_decision_json(raw)
        except ValueError:
            # 형식 위반은 1회만 재요청한다 (무료 티어 쿼터 절약) — 그래도 실패하면
            # 예외가 올라가 TradingAgent 의 규칙 폴백이 받는다
            self.format_retries += 1
            data = parse_decision_json(self._call(prompt + RETRY_SUFFIX))

        action = str(data.get("action", "hold")).lower()
        if action not in ("buy", "sell", "hold"):
            action = "hold"
        # 정화 과정에서 남은 역슬래시 잔해를 지워 타임라인에 깨진 문자가 보이지 않게 한다
        reason = re.sub(r"\s+", " ", str(data.get("reason", "")).replace("\\", " ")).strip()
        reason = reason or "이유 미제공"
        spend = Decimal(0)
        if action == "buy":
            try:
                spend = Decimal(str(data.get("spend_usdc", strategy.spend_per_trade_usdc)))
            except Exception:
                spend = strategy.spend_per_trade_usdc
        return Decision(action=action, reason=reason, spend_usdc=spend, source="gemini")
