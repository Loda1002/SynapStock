"""시간 기반 청산(안전레일) 단위 테스트 — 실제 TradingAgent.decide() 경로로 검증.

- max_hold_bars 봉 이상 보유하면 규칙/Gemini 판단보다 우선해 자동 청산되는가
- 익절이 시간청산보다 먼저 성립하면 익절이 우선되는가 (백스톱이 정상 신호를 가로채지 않음)
- max_hold_bars=0 이면 비활성(무한 보유)인가
- 청산 후 보유 카운터가 초기화돼 다음 사이클이 새로 세는가
"""
from __future__ import annotations
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402

from config import CFG  # noqa: E402
from market.price_feed import Bar  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from agents.broker_agent import BrokerAgent  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError  # noqa: E402

SYMBOL = "AAPL"


def make_agent(max_hold: int) -> tuple:
    kp = Keypair()
    strategy = Strategy(buy_dip_pct=Decimal("3"), take_profit_pct=Decimal("5"),
                        spend_per_trade_usdc=Decimal("30"), decision_mode="strict",
                        max_hold_bars=max_hold)
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=Decimal("100"), per_trade_max_usdc=Decimal("50"),
        allowed_symbols=[SYMBOL]).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    agent = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "test",
                         brain=None, fee_bps=CFG.broker_fee_bps)
    # 워밍업 20봉 @100 → MA5=MA20=100 성립
    agent.preload_bars([Bar(date="", open=Decimal("100"), high=Decimal("100"),
                            low=Decimal("100"), close=Decimal("100")) for _ in range(20)])
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "test", fee_bps=CFG.broker_fee_bps)
    return agent, auth, broker


def run(agent, auth, broker, prices) -> list:
    """가격 시퀀스를 흘리며 decide() → 매수/매도를 포지션에 반영(엔진 실행 흉내)."""
    out = []
    for i, p in enumerate(prices):
        p = Decimal(str(p))
        d = agent.decide(SYMBOL, p, Bar(date="", open=p, high=p, low=p, close=p))
        out.append(d)
        if d.action == "buy":
            q = broker.quote(SYMBOL, d.spend_usdc, p)
            try:
                auth.authorize(order_id=f"t{i}", symbol=SYMBOL,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError:
                pass
            else:
                eff = (q.total_usdc / q.quantity) if q.quantity else p
                agent.position.apply_buy(q.quantity, eff)
        elif d.action == "sell" and agent.position.quantity > 0:
            q = broker.sell_quote(SYMBOL, agent.position.quantity, p)
            auth.credit_sale(q.total_usdc)
            agent.position.apply_sell(agent.position.quantity)
    return out


def check(name: str, got, want) -> int:
    ok = got == want
    print(f"  [{'통과' if ok else '실패'}] {name}" + ("" if ok else f"  기대={want} 실제={got}"))
    return 0 if ok else 1


def main() -> int:
    bad = 0

    # 1) 시간청산 발화 — 3봉 보유 후 자동 청산
    agent, auth, broker = make_agent(max_hold=3)
    ds = run(agent, auth, broker, [96, 98, 98, 98])
    actions = [d.action for d in ds]
    bad += check("3봉 보유 후 시간청산", actions, ["buy", "hold", "hold", "sell"])
    bad += check("청산 사유가 시간청산(안전레일)", "시간청산" in ds[-1].reason, True)

    # 2) 익절이 시간청산보다 먼저 성립하면 익절 우선 (백스톱이 정상 신호를 가로채지 않음)
    agent, auth, broker = make_agent(max_hold=3)
    ds = run(agent, auth, broker, [96, 105])
    bad += check("급등 시 익절이 먼저", ds[1].action, "sell")
    bad += check("익절 사유(시간청산 아님)",
                 ("시간청산" not in ds[1].reason) and ("익절기준" in ds[1].reason), True)

    # 3) max_hold_bars=0 → 비활성(무한 보유, 시간청산 없음)
    agent, auth, broker = make_agent(max_hold=0)
    ds = run(agent, auth, broker, [96, 98, 98, 98, 98, 98, 98, 98])
    sells = [d for d in ds if d.action == "sell"]
    bad += check("비활성 시 시간청산 없음", len(sells), 0)
    bad += check("비활성 시 계속 보유", agent.position.quantity > 0, True)

    # 4) 청산 후 카운터 초기화 — 재매수 후 새로 3봉 세어 다시 청산 (두 독립 사이클)
    agent, auth, broker = make_agent(max_hold=3)
    ds = run(agent, auth, broker, [96, 99, 99, 99, 93, 96, 96, 96])
    buys = [d for d in ds if d.action == "buy"]
    sells = [d for d in ds if d.action == "sell"]
    bad += check("두 사이클 각각 매수·청산 (카운터 초기화)", (len(buys), len(sells)), (2, 2))
    bad += check("두 청산 모두 시간청산", all("시간청산" in d.reason for d in sells), True)

    print(f"\n시간청산 테스트: {'전부 통과' if bad == 0 else f'{bad}건 실패'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
