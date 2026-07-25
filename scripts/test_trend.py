"""추세추종(mode="trend") 재현 검증 — 실제 decide() 경로가 탐색 도구를 그대로 재현하는가.

검증 대상(사용자 요청): scripts/explore_trend.py --suffix _bear 의 수치를 실제
TradingAgent.decide() → BrokerAgent → PaymentAuthorizer 경로가 재현하는지 대조한다.

두 축으로 확인한다:
  1) 진입/청산 시퀀스가 explore_trend 의 신호 전이(desired_long)와 **정확히** 일치하는가
     — 이것이 "판단 로직이 그대로다"의 강한 증거. 봉 하나라도 어긋나면 실패.
  2) 최종 수익률이 explore_trend.simulate() 와 허용오차 내인가 — 실제 경로는 브로커가
     소계·수수료를 각각 센트 반올림(탐색 도구는 합계를 한 번 반올림)하므로 봉당 최대
     1센트 차가 누적되지만, 방향·규모는 동일해야 한다.

데이터: data/market/{AAPL,TSLA,NVDA}_bear.csv (2022 폭락+2023 회복, yfinance 조정 일봉).
재현: python scripts/test_trend.py
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
from market.price_feed import Bar, load_bars  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from agents.broker_agent import BrokerAgent  # noqa: E402
from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError  # noqa: E402
from scripts.explore_trend import simulate, desired_long, ma  # noqa: E402

SYMBOLS = ["AAPL", "TSLA", "NVDA"]
VARIANTS = ["pxma20", "cross_5_20"]
WARMUP = 20
# 검증 예산 = 100(데모·앱 기본값). 이 예산에서 6개 조합 모두 AP2 거부 0(actual_run 이 MandateError 를
# AssertionError 로 승격해 강제)이라 진입/청산 시퀀스가 그대로 재현된다. 참고: 브로커 quote 가 소계·수수료를
# 각각 센트 반올림하므로 총액이 요청 spend 를 최대 0.01 초과할 수 있고, 올인(spend=remaining)이라 이 초과가
# 특정 다른 예산값에서 드물게 AP2 거부(페일세이프 — 자금·가드 무관, 진입 1봉 지연)를 낳을 수 있다.
BUDGET = Decimal("100")
# 실제 브로커 경로(소계/수수료 각각 센트 반올림) vs 탐색 도구(합계 1회 반올림)의 누적 반올림
# 차이 상한. 시퀀스가 정확히 같으면 남는 차이는 순수 반올림이라 이 안에 들어와야 한다.
# (실측 최대 0.08%p — 0.5 는 회귀를 잡되 반올림은 허용하는 여유값.)
RET_TOL = Decimal("0.5")   # %p


def expected_actions(warm, play, variant: str) -> list:
    """explore_trend.simulate 와 동일한 want/long 전이로 기대 행동 시퀀스를 만든다."""
    closes = [b.close for b in warm]
    prev_ma20 = ma(closes, 20)
    long = False
    out = []
    for bar in play:
        closes.append(bar.close)
        ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
        want = desired_long(variant, bar.close, ma5, ma10, ma20, prev_ma20, long)
        if want and not long:
            out.append("buy"); long = True
        elif not want and long:
            out.append("sell"); long = False
        else:
            out.append("hold")
        prev_ma20 = ma20
    return out


def actual_run(warm, play, variant: str) -> tuple:
    """실제 decide() → broker → auth 경로로 재생. (행동 시퀀스, 최종 수익률%) 반환.

    올인/올아웃이라 건별 한도는 넉넉히 두고(복리로 예산 초과 매수 가능), 매도 대금은
    allow_surplus 로 전액 환입해 운용현금이 복리로 불어난다(엔진 trend 세션과 같은 규칙)."""
    kp = Keypair()
    strategy = Strategy(mode="trend", trend_signal=variant)
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=BUDGET, per_trade_max_usdc=Decimal("1000000"),
        allowed_symbols=["AAPL"]).sign(kp)   # decide 는 CFG.stock_symbol 을 안 쓰고 인자 symbol 사용
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    agent = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "test",
                         brain=None, fee_bps=CFG.broker_fee_bps)
    agent.preload_bars(list(warm))
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "test", fee_bps=CFG.broker_fee_bps)
    symbol = "AAPL"
    fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
    actions = []
    last = play[0].close if play else Decimal(0)
    for i, bar in enumerate(play):
        last = bar.close
        d = agent.decide(symbol, bar.close, bar)
        actions.append(d.action)
        if d.action == "buy":
            q = broker.quote(symbol, d.spend_usdc, bar.close)
            try:
                auth.authorize(order_id=f"t{i}", symbol=symbol,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError as e:
                raise AssertionError(f"{variant} 봉{i}: 올인 매수가 한도에 막힘 — {e}")
            eff = (q.total_usdc / q.quantity) if q.quantity else bar.close
            agent.position.apply_buy(q.quantity, eff)
        elif d.action == "sell" and agent.position.quantity > 0:
            q = broker.sell_quote(symbol, agent.position.quantity, bar.close)
            agent.on_sale_completed(_settled(f"t{i}"), symbol, agent.position.quantity,
                                    bar.close, q.total_usdc)
    # 최종 자산 = 운용현금(auth.remaining, allow_surplus 로 복리) + 보유 평가액(수수료 차감)
    final = auth.remaining_usdc + agent.position.quantity * last * (1 - fee_rate)
    ret = ((final - BUDGET) / BUDGET * 100).quantize(Decimal("0.01"))
    return actions, ret


class _Completed:
    """on_sale_completed 가 요구하는 최소 인터페이스(status=settled)."""
    def __init__(self, order_id):
        self.order_id = order_id
        self.status = "settled"
        self.tx_signature = ""
        self.delivery_tx_signature = ""
        self.confirmed = False


def _settled(order_id):
    return _Completed(order_id)


def main() -> int:
    bad = 0
    print(f"추세추종 재현 검증 — 예산 {BUDGET} · 수수료 {CFG.broker_fee_bps}bps · 워밍업 {WARMUP}봉")
    print(f"{'심볼':<6}{'신호':<12}{'탐색수익%':>10}{'실제수익%':>10}{'차이%p':>9}{'매매(탐색/실제)':>16}  판정")
    print("-" * 78)
    for sym in SYMBOLS:
        bars = load_bars(os.path.join(ROOT, "data", "market", f"{sym}_bear.csv"))
        warm, play = bars[:WARMUP], bars[WARMUP:]
        for v in VARIANTS:
            ref = simulate(warm, play, v, BUDGET, CFG.broker_fee_bps)
            exp = expected_actions(warm, play, v)
            act, ret = actual_run(warm, play, v)
            seq_ok = act == exp
            ref_ret = ref["return_pct"]
            diff = (ret - ref_ret).quantize(Decimal("0.01"))
            ret_ok = abs(diff) <= RET_TOL
            act_trades = sum(1 for a in act if a in ("buy", "sell"))
            ok = seq_ok and ret_ok
            if not ok:
                bad += 1
            verdict = "통과" if ok else ("시퀀스 불일치" if not seq_ok else f"수익오차>{RET_TOL}")
            print(f"{sym:<6}{v:<12}{ref_ret:>10}{ret:>10}{diff:>+9}"
                  f"{str(ref['trades'])+'/'+str(act_trades):>16}  {verdict}")
            if not seq_ok:
                # 첫 불일치 봉을 짚어준다
                for k, (a, e) in enumerate(zip(act, exp)):
                    if a != e:
                        print(f"        └ 첫 불일치 봉 {k} ({play[k].date}): 실제={a} 기대={e}")
                        break

    print("-" * 78)
    print(f"추세추종 재현 검증: {'전부 통과' if bad == 0 else f'{bad}건 실패'} "
          f"(시퀀스 정확 일치 + 수익 오차 ≤ {RET_TOL}%p)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
