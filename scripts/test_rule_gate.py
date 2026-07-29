"""규칙 게이트 검증 — "규칙 신호 없는 개시 금지"가 프롬프트가 아니라 코드로 강제되는가.

배경(심사 리포트 축② 갭): 규칙 밖 매매 금지는 Gemini 프롬프트(MODE_RULES)에만 있었고
코드에는 없었다. 모델이 프롬프트를 어기면 규칙 밖 매수·매도가 그대로 집행돼, "AI 는
규칙 안에서만 판단한다"는 주장이 모델의 순응에 의존했다. TradingAgent._rule_gate 가
그 제약을 코드에서 기계적으로 강제한다.

검증 방식: 실제 Gemini 를 부르지 않고, 지시한 판단을 그대로 돌려주는 가짜 두뇌(FakeBrain)를
brain 자리에 꽂아 decide() 전체 경로를 태운다. 즉 게이트만 따로 부르는 게 아니라
"모델이 이렇게 답했을 때 시스템이 무엇을 집행하는가"를 본다.

재현: python scripts/test_rule_gate.py
"""
from __future__ import annotations
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402

from agents.trading_agent import TradingAgent, Strategy, Decision  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer  # noqa: E402
from shared.a2a_messages import PaymentCompleted  # noqa: E402

SYMBOL = "TEST"
ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    """통과·실패 표기는 다른 test_*.py 및 증거 수집기(collect_evidence.py 의 [OK/[FAIL 집계)와
    같은 형식을 쓴다."""
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK  ] {label}")
    else:
        fail += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


class FakeBrain:
    """지시한 판단을 그대로 돌려주는 가짜 Gemini. 실제 API 호출 없음."""

    def __init__(self, action: str, spend: str = "10", reason: str = "가짜 판단"):
        self.action = action
        self.spend = Decimal(spend)
        self.reason = reason
        self.calls = 0

    def decide(self, *args, **kwargs) -> Decision:
        self.calls += 1
        self.last_kwargs = dict(kwargs)   # 프롬프트에 실려 나간 값 확인용(회고 문자열 등)
        return Decision(self.action, self.reason, self.spend, source="gemini")


class BoomBrain:
    """호출 시 예외를 던지는 두뇌 — 규칙 폴백 경로 확인용."""

    def decide(self, *args, **kwargs) -> Decision:
        raise RuntimeError("무료 티어 한도 초과 — 쿨다운")


def make_agent(brain=None, budget: str = "100") -> TradingAgent:
    kp = Keypair()
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset="11111111111111111111111111111111",
        budget_total_usdc=Decimal(budget), per_trade_max_usdc=Decimal("50"),
        allowed_symbols=[SYMBOL]).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    strat = Strategy(buy_dip_pct=Decimal("3"), take_profit_pct=Decimal("5"),
                     spend_per_trade_usdc=Decimal("10"), max_hold_bars=0)
    ag = TradingAgent(kp, auth, strat, 6, "test", brain=brain)
    return ag


def warm(agent: TradingAgent, prices: list[str]) -> None:
    """MA5/MA20 이 성립하도록 워밍업 종가를 주입한다(decide 를 거치지 않음)."""
    agent.preload_history([Decimal(p) for p in prices])


FLAT = ["100"] * 20          # MA5 = 100 → 매수기준 97.00 (3% 딥)


def main() -> int:
    print("=== 규칙 게이트 (규칙 신호 없는 개시 금지) ===\n")

    # ---------- 1) 매수: 규칙 미충족인데 AI 가 사겠다고 하면 보류로 강등 ----------
    print("[1] 매수 신호 없는데 AI 가 buy → hold 강등")
    brain = FakeBrain("buy")
    ag = make_agent(brain)
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("100"))     # 100 > 매수기준 97 → 규칙 미충족
    check("행동이 hold 로 강등", d.action == "hold", f"action={d.action}")
    check("출처가 rule-gate", d.source == "rule-gate", f"source={d.source}")
    check("이유에 원 AI 판단이 보존됨", "가짜 판단" in d.reason, d.reason)
    check("Gemini 는 실제로 호출됐음(게이트는 사후 강등)", brain.calls == 1)
    check("지출 금액 0", d.spend_usdc == 0, str(d.spend_usdc))

    # ---------- 2) 매수: 규칙 충족이면 AI 판단 그대로 통과 ----------
    print("\n[2] 매수 신호 충족 시 AI buy 통과")
    ag = make_agent(FakeBrain("buy"))
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("95"))      # 95 ≤ 97 → 규칙 충족
    check("행동이 buy 유지", d.action == "buy", f"action={d.action}")
    check("출처가 gemini 유지", d.source == "gemini", f"source={d.source}")
    check("지출이 한도 안", 0 < d.spend_usdc <= Decimal("10"), str(d.spend_usdc))

    # ---------- 3) 매수 경계값 — 정확히 매수기준이면 통과 ----------
    # MA5 는 현재가를 포함해 계산된다: 워밍업 100×20 에 현재가 p 를 넣으면
    # MA5 = (400+p)/5, 매수기준 = MA5 × 0.97. p=96.28 이 그 부동점(기준 == 가격)이다.
    print("\n[3] 경계값: 가격 == 매수기준")
    ag = make_agent(FakeBrain("buy"))
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("96.28"))
    check("경계값 산정 확인(기준 == 가격)",
          ag.indicators()["buy_threshold"] == Decimal("96.28"),
          str(ag.indicators()["buy_threshold"]))
    check("경계값은 통과(≤ 비교)", d.action == "buy" and d.source == "gemini",
          f"{d.action}/{d.source}")

    # ---------- 4) 매도: 익절 미충족인데 AI 가 팔겠다고 하면 보류로 강등 ----------
    print("\n[4] 익절 신호 없는데 AI 가 sell → hold 강등")
    brain = FakeBrain("sell")
    ag = make_agent(brain)
    warm(ag, FLAT)
    ag.position.symbol = SYMBOL
    ag.position.apply_buy(Decimal("1"), Decimal("100"))   # 평단 100 → 익절기준 105
    d = ag.decide(SYMBOL, Decimal("101"))                 # 101 < 105 → 규칙 미충족
    check("행동이 hold 로 강등", d.action == "hold", f"action={d.action}")
    check("출처가 rule-gate", d.source == "rule-gate", f"source={d.source}")
    check("포지션은 그대로 보존", ag.position.quantity == Decimal("1"))

    # ---------- 5) 매도: 익절 충족이면 AI sell 통과 ----------
    print("\n[5] 익절 신호 충족 시 AI sell 통과")
    ag = make_agent(FakeBrain("sell"))
    warm(ag, FLAT)
    ag.position.symbol = SYMBOL
    ag.position.apply_buy(Decimal("1"), Decimal("100"))
    d = ag.decide(SYMBOL, Decimal("106"))                 # 106 ≥ 105
    check("행동이 sell 유지", d.action == "sell", f"action={d.action}")
    check("출처가 gemini 유지", d.source == "gemini", f"source={d.source}")

    # ---------- 6) 보류는 언제나 통과 (재량은 '멈추는 방향'으로만) ----------
    print("\n[6] AI 의 hold 는 규칙이 충족돼도 통과 (보류 재량 보존)")
    ag = make_agent(FakeBrain("hold", reason="추세가 아직 꺾이는 중이라 보류"))
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("95"))       # 매수 신호는 떠 있음
    check("행동이 hold", d.action == "hold", f"action={d.action}")
    check("출처가 gemini 유지(강등 아님)", d.source == "gemini", f"source={d.source}")
    check("추세 보류 근거가 살아 있음", "추세" in d.reason, d.reason)

    # ---------- 7) 규칙 두뇌(brain=None)는 게이트 무영향 ----------
    print("\n[7] 규칙 판단(brain 없음)은 게이트 대상 아님")
    ag = make_agent(None)
    warm(ag, FLAT)
    d_buy = ag.decide(SYMBOL, Decimal("95"))
    check("규칙 매수 정상 동작", d_buy.action == "buy" and d_buy.source == "rule",
          f"{d_buy.action}/{d_buy.source}")
    ag2 = make_agent(None)
    warm(ag2, FLAT)
    d_hold = ag2.decide(SYMBOL, Decimal("100"))
    check("규칙 미충족 시 hold", d_hold.action == "hold" and d_hold.source == "rule",
          f"{d_hold.action}/{d_hold.source}")

    # ---------- 8) Gemini 호출 실패 → 규칙 폴백은 게이트 무영향 ----------
    print("\n[8] Gemini 호출 실패 시 규칙 폴백 경로 보존")
    ag = make_agent(BoomBrain())
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("95"))
    check("폴백이 규칙대로 매수", d.action == "buy", f"action={d.action}")
    check("출처가 rule-fallback", d.source == "rule-fallback", f"source={d.source}")
    check("실패 원인이 이유에 표면화", "Gemini 호출 실패" in d.reason, d.reason)

    # ---------- 9) 시간청산(안전레일)은 게이트보다 앞이라 그대로 작동 ----------
    print("\n[9] 시간청산 안전레일은 AI 판단 이전에 발화")
    ag = make_agent(FakeBrain("buy"))
    ag.strategy.max_hold_bars = 2
    warm(ag, FLAT)
    ag.position.symbol = SYMBOL
    ag.position.apply_buy(Decimal("1"), Decimal("100"))
    ag.decide(SYMBOL, Decimal("100"))          # 1봉 보유 (여기서는 AI 가 호출됨)
    calls_before = ag.brain.calls
    d = ag.decide(SYMBOL, Decimal("100"))      # 2봉 → 시간청산
    check("시간청산 매도 발화", d.action == "sell", f"action={d.action}")
    check("출처가 rule", d.source == "rule", f"source={d.source}")
    check("그 틱에는 Gemini 호출 없음(안전레일이 먼저 반환)",
          ag.brain.calls == calls_before, f"{calls_before}→{ag.brain.calls}")

    # ---------- 10) 예산 소진 시 _sanitize 가 먼저 잡고 게이트는 덮어쓰지 않음 ----------
    print("\n[10] 예산 소진 매수는 _sanitize 단계에서 보류 (게이트가 가리지 않음)")
    ag = make_agent(FakeBrain("buy"), budget="0")
    warm(ag, FLAT)
    d = ag.decide(SYMBOL, Decimal("95"))       # 규칙은 충족, 예산 0
    check("보류 처리", d.action == "hold", f"action={d.action}")
    check("예산 소진 사유 유지", "예산 소진" in d.reason, d.reason)

    # ---------- 11) 무산된 판단이 '직전 행동 회고'에 사실로 남지 않는가 (BUG-06) ----------
    # 판단은 이후 402 Guard 차단·AP2 거부·브로커 실패로 얼마든지 무산되는데, 예전에는
    # 판단 시점에 기록해서 다음 틱 프롬프트가 [지표]에서는 "몇 봉 전 매수"라고 단언하고
    # [현재 상태]에서는 보유 0 을 말했다. 모델에게 모순된 컨텍스트가 들어가는 자리다.
    print("\n[11] 체결되지 않은 판단은 회고에 남지 않는다 (BUG-06)")
    brain = FakeBrain("buy")
    ag = make_agent(brain)
    warm(ag, FLAT)
    ag.decide(SYMBOL, Decimal("95"))     # 규칙 충족 매수 판단 — 결제는 한 번도 실행하지 않는다
    ag.decide(SYMBOL, Decimal("96"))     # 다음 틱: 앞의 판단이 회고에 실렸는지 본다
    retro = brain.last_kwargs.get("retrospective", "")
    check("무산된 매수는 회고에 없음", retro == "이번 세션 매수·매도 이력 없음", retro)
    check("보유 0 인데 '몇 봉 전 매수'라고 단언하지 않음",
          "봉 전 매수" not in retro and ag.position.quantity == 0,
          f"{retro} / 보유 {ag.position.quantity}")

    # 실제로 체결되면 그때 실린다 — 기록 조건은 포지션 변동과 같다
    done = PaymentCompleted(order_id="o1", tx_signature="sig", confirmed=False,
                            delivered_asset="mint", delivered_amount=100_000, status="settled")
    ag.on_completed(done, SYMBOL, Decimal("0.1"), Decimal("96"), Decimal("9.6"))
    ag.decide(SYMBOL, Decimal("100"))
    retro2 = brain.last_kwargs.get("retrospective", "")
    check("체결된 매수는 다음 틱 회고에 실린다(1봉 전)",
          "1봉 전 매수 @ 96" in retro2, retro2)
    check("회고와 실제 보유가 일치", ag.position.quantity > 0 and "매수" in retro2,
          f"{retro2} / 보유 {ag.position.quantity}")

    # 정산 실패(failed)는 포지션도 회고도 건드리지 않는다
    ag2 = make_agent(FakeBrain("buy"))
    warm(ag2, FLAT)
    ag2.on_completed(
        PaymentCompleted(order_id="o2", tx_signature="", confirmed=False, delivered_asset="mint",
                         delivered_amount=0, status="failed"),
        SYMBOL, Decimal("0.1"), Decimal("96"), Decimal("9.6"))
    check("정산 실패는 회고에 남지 않음", ag2._last_action is None, str(ag2._last_action))

    # 추세추종도 '진입 판단'만으로 기록하지 않는다. 이 모드는 회고를 프롬프트에 쓰지
    # 않지만(규칙 신호로만 판단), 상태가 거짓이면 나중에 회고를 쓰는 순간 되살아난다.
    tr = make_agent()
    tr.strategy.mode = "trend"
    warm(tr, FLAT)
    d = tr.decide(SYMBOL, Decimal("100"))
    check("추세 진입 판단만으로는 기록 없음",
          d.action == "buy" and tr._last_action is None, f"action={d.action} / {tr._last_action}")

    print(f"\n===== 결과: 통과 {ok} · 실패 {fail} =====")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
