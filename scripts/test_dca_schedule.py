"""적립식(DCA) 주기 테스트 — 틱/분/매일 시각 세 기준 (가짜 시계, 온체인 호출 없음).

실행: python -m scripts.test_dca_schedule
"""
import sys

# 판단 근거(자유 문장)에는 cp949 밖 문자가 올 수 있다(예: TA 근거의 em-dash).
# 한국어 Windows 콘솔에서 크래시하지 않게, 인코딩 불가 문자만 ? 로 대체해 출력한다.
sys.stdout.reconfigure(errors="replace")

from datetime import datetime, timedelta
from decimal import Decimal

from solders.keypair import Keypair

from agents.trading_agent import TradingAgent, Strategy
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer

SYMBOL = "tAAPL"
PRICE = Decimal("180")


def make_agent(**strategy_kwargs) -> TradingAgent:
    kp = Keypair()
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=str(Keypair().pubkey()),
        budget_total_usdc=Decimal("1000"), per_trade_max_usdc=Decimal("100"),
        allowed_symbols=[SYMBOL],
    ).sign(kp)
    strategy = Strategy(
        buy_dip_pct=Decimal("2"), take_profit_pct=Decimal("3"),
        spend_per_trade_usdc=Decimal("30"), mode="dca",
        dca_amount_usdc=Decimal("10"), **strategy_kwargs,
    )
    return TradingAgent(kp, PaymentAuthorizer(mandate, agent_kp=kp), strategy, 6, "test")


def actions(agent: TradingAgent, count: int) -> list:
    return [agent.decide(SYMBOL, PRICE).action for _ in range(count)]


def check(name: str, got, want) -> int:
    ok = got == want
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}\n       기대 {want}\n       실제 {got}")
    return 0 if ok else 1


def main() -> int:
    bad = 0

    # ① 틱 기준 — 3틱마다 (기존 동작 유지)
    a = make_agent(dca_unit="ticks", dca_every_ticks=3)
    bad += check("틱 기준: 3틱마다 매수", actions(a, 7),
                 ["hold", "hold", "buy", "hold", "hold", "buy", "hold"])

    # ② 시간 기준 — 30분마다, 가짜 시계를 앞으로 돌려가며 확인
    clock = {"now": datetime(2026, 7, 23, 9, 0, 0)}
    a = make_agent(dca_unit="minutes", dca_every_minutes=30)
    a._now = lambda: clock["now"]
    seq = []
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:00 첫 틱 → 즉시 1회차
    clock["now"] += timedelta(minutes=10)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:10 → 대기
    clock["now"] += timedelta(minutes=15)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:25 → 대기
    clock["now"] += timedelta(minutes=10)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:35 → 2회차
    clock["now"] += timedelta(minutes=5)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:40 → 대기
    bad += check("시간 기준: 30분마다 매수", seq, ["buy", "hold", "hold", "buy", "hold"])

    # 대기 문구에 남은 시간이 보이는지
    d = a.decide(SYMBOL, PRICE)
    print(f"       대기 문구: {d.reason}")
    bad += check("시간 기준 대기 문구", "분" in d.reason and "적립 대기" in d.reason, True)

    # ③ 매일 지정 시각 — 09:30, 도래 전/후/다음날
    clock = {"now": datetime(2026, 7, 23, 9, 0, 0)}
    a = make_agent(dca_unit="daily", dca_at_time="09:30")
    a._now = lambda: clock["now"]
    seq = [a.decide(SYMBOL, PRICE).action]              # 09:00 → 미도래
    clock["now"] = datetime(2026, 7, 23, 9, 30, 0)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 09:30 → 당일 1회차
    clock["now"] = datetime(2026, 7, 23, 15, 0, 0)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 같은 날 → 재매수 없음
    clock["now"] = datetime(2026, 7, 24, 9, 31, 0)
    seq.append(a.decide(SYMBOL, PRICE).action)          # 다음날 시각 경과 → 매수
    bad += check("매일 09:30 기준", seq, ["hold", "buy", "hold", "buy"])

    # ④ 잘못된 시각 형식은 매수하지 않고 사유를 남긴다
    a = make_agent(dca_unit="daily", dca_at_time="이상한값")
    d = a.decide(SYMBOL, PRICE)
    bad += check("시각 형식 오류 → 보류", (d.action, "형식 오류" in d.reason), ("hold", True))

    # ⑤ 주기 문구
    bad += check("주기 문구(분)", make_agent(dca_unit="minutes", dca_every_minutes=60)
                 .dca_schedule_label(), "60분마다")
    bad += check("주기 문구(매일)", make_agent(dca_unit="daily", dca_at_time="09:00")
                 .dca_schedule_label(), "매일 09:00")

    print("\n결과:", "전부 통과" if bad == 0 else f"{bad}건 실패")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
