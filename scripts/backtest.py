"""백테스트 러너 — 같은 실데이터 구간에서 규칙 vs Gemini(엄격/추세) 성과 비교.

드라이 시뮬레이션(온체인 미전송): 견적·AP2 한도 검사는 실전과 동일 코드
(BrokerAgent.quote / PaymentAuthorizer)를 그대로 지나고, x402 서명·전송만 생략한다.
소개서에 넣을 "규칙 vs AI(엄격) vs AI(추세)" 비교 숫자를 만드는 도구.

사용 예 (프로젝트 루트):
  python scripts/backtest.py --brain rule
  python scripts/backtest.py --brain gemini --mode strict --from 2025-02-03 --to 2025-04-30
  python scripts/backtest.py --brain gemini --mode trend --max-bars 60

무료 티어 보호: Gemini 호출 간 --pace 초(기본 4s) 간격 + 429 쿨다운 대기,
재생 봉 수는 --max-bars(기본 60)로 제한. rule-fallback 발생 수를 함께 기록한다
(0이 아니면 그 구간은 규칙 판단이 섞였다는 뜻 — 비교 순수성 주의).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402

from config import CFG  # noqa: E402
from market.price_feed import ReplayPriceFeed, IntradayReplayFeed, Bar, load_bars  # noqa: E402
from agents.trading_agent import TradingAgent, Strategy  # noqa: E402
from agents.broker_agent import BrokerAgent  # noqa: E402
from payments.ap2_mandate import (  # noqa: E402
    OpenPaymentMandate, PaymentAuthorizer, MandateError,
)

CENT = Decimal("0.01")


def build_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="리플레이 백테스트 (드라이, 온체인 미전송)")
    ap.add_argument("--symbol", default="AAPL", help="data/market/{SYMBOL}{SUFFIX}.csv (기본 AAPL)")
    ap.add_argument("--symbols", default="",
                    help="멀티 종목 포트폴리오(콤마 구분, 예: AAPL,TSLA,NVDA) — "
                         "엔진과 동일하게 하나의 예산·가드 아래 각 종목 독립 운용. 주면 --symbol 무시")
    ap.add_argument("--suffix", default="_daily",
                    help="CSV 접미사 (기본 _daily · 하락장 실증은 _bear)")
    ap.add_argument("--file", default="", help="CSV 경로 직접 지정 (symbol 보다 우선)")
    ap.add_argument("--from", dest="date_from", default="", help="재생 시작일 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default="", help="재생 종료일 YYYY-MM-DD")
    ap.add_argument("--strategy", choices=["condition", "trend"], default="condition",
                    help="condition(조건형 눌림목) / trend(추세추종 올인·올아웃)")
    ap.add_argument("--trend-signal", dest="trend_signal",
                    choices=["pxma20", "cross_5_20", "cross_1_5", "cross_5_20_1_5"], default="pxma20",
                    help="추세 신호 (--strategy trend 에서만): 가격>MA20 / 골든크로스5/20")
    ap.add_argument("--brain", choices=["rule", "gemini"], default="rule",
                    help="판단 두뇌 (--strategy trend 이면 규칙 신호로 강제)")
    ap.add_argument("--mode", choices=["strict", "trend"], default="strict",
                    help="gemini 재량 모드 (rule/trend 전략이면 무시)")
    ap.add_argument("--ta", action="store_true",
                    help="TA 보강 켜기 — MA 배열·크로스·지지/저항·패턴을 판단 근거로")
    ap.add_argument("--max-bars", type=int, default=60, help="최대 재생 봉 수 (기본 60)")
    ap.add_argument("--warmup", type=int, default=20, help="지표 워밍업 봉 수 (기본 20)")
    ap.add_argument("--sub-bars", dest="sub_bars", type=int, default=1,
                    help="1=일봉(기본) · >1=하루당 N개 합성 인트라바 (단일 종목 경로만)")
    ap.add_argument("--budget", default="100", help="AP2 총예산 USDC")
    ap.add_argument("--per-trade", default="50", help="AP2 건별 한도 USDC")
    ap.add_argument("--spend", default="30", help="1회 매수 금액 USDC")
    ap.add_argument("--dip", default="3", help="매수: MA5 대비 -%% (앱 기본 3)")
    ap.add_argument("--profit", default="5", help="매도: 평단 대비 +%% (앱 기본 5)")
    ap.add_argument("--max-hold", type=int, default=10,
                    help="시간청산 — N봉 이상 보유 시 자동 청산(안전레일, 0=비활성)")
    ap.add_argument("--pace", type=float, default=4.0, help="gemini 호출 간 최소 간격(초)")
    ap.add_argument("--quiet", action="store_true", help="봉별 로그 생략(요약만)")
    return ap.parse_args()


def run_portfolio(args) -> int:
    """멀티 종목 포트폴리오 백테스트 — web/engine.py 의 멀티 모델을 그대로 재현.

    공유 1개: mandate(allowed_symbols=전종목)·auth(예산)·broker. 종목별 N개: TradingAgent
    (position 독립). 1회 매수 = 총 spend/N. 같은 날짜 구간(교집합)을 봉 단위로 동시 진행한다.
    추세추종(올인)은 예산 독식이라 멀티 미지원(엔진과 동일하게 거부)."""
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    symbols = list(dict.fromkeys(symbols))   # 중복 제거(순서 유지)
    if args.strategy == "trend":
        print("[오류] 추세추종(올인/올아웃)은 멀티 종목을 지원하지 않습니다 — 단일 --symbol 로 실행하세요.")
        return 1

    bars_by: dict[str, list[Bar]] = {}
    for sym in symbols:
        path = os.path.join(ROOT, "data", "market", f"{sym}{args.suffix}.csv")
        try:
            bars_by[sym] = load_bars(path)
        except (FileNotFoundError, ValueError) as e:
            print(f"[오류] {sym}: {e}")
            return 1
    # 공통 날짜 교집합 → from/to 필터 → 봉 정렬
    date_maps = {s: {b.date: b for b in bs} for s, bs in bars_by.items()}
    common = sorted(set.intersection(*(set(m) for m in date_maps.values())))
    if args.date_from:
        common = [d for d in common if d >= args.date_from]
    if args.date_to:
        common = [d for d in common if d <= args.date_to]
    aligned = {s: [date_maps[s][d] for d in common] for s in symbols}
    n = len(symbols)
    warm = args.warmup
    if len(common) <= warm + 1:
        print(f"[오류] 공통 구간이 너무 짧습니다({len(common)}봉) — 워밍업 {warm}봉 이후 재생할 봉이 없습니다.")
        return 1

    fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
    budget = Decimal(args.budget)
    spend = (Decimal(args.spend) / n).quantize(CENT)   # 종목별 1회 매수 = 총 spend/N (엔진과 동일)

    # 공유 레이어
    kp = Keypair()
    from solders.pubkey import Pubkey
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=budget, per_trade_max_usdc=Decimal(args.per_trade),
        allowed_symbols=list(symbols)).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "backtest", fee_bps=CFG.broker_fee_bps)
    # 종목별 에이전트 (공유 auth) + 워밍업 프리로드
    agents: dict[str, TradingAgent] = {}
    for s in symbols:
        strat = Strategy(buy_dip_pct=Decimal(args.dip), take_profit_pct=Decimal(args.profit),
                         spend_per_trade_usdc=spend, decision_mode=args.mode, ta_mode=args.ta,
                         mode="condition", max_hold_bars=args.max_hold)
        ag = TradingAgent(kp, auth, strat, CFG.usdc_decimals, "backtest",
                          brain=None, fee_bps=CFG.broker_fee_bps)
        ag.preload_bars(aligned[s][:warm])
        agents[s] = ag

    realized = Decimal(0); fees = Decimal(0); cum_buy = Decimal(0); rejects = 0
    per_sym = {s: {"buys": 0, "sells": 0, "realized": Decimal(0)} for s in symbols}
    peak = budget; mdd = Decimal(0)
    play_dates = common[warm:]
    print(f"포트폴리오 백테스트 — {'+'.join(symbols)}{args.suffix} / 규칙 MA5 -{args.dip}% 매수·평단 +{args.profit}% 익절 / "
          f"예산 {budget} USDC (종목별 1회 {spend}) / {len(play_dates)}봉")

    for i in range(len(play_dates)):
        for s in symbols:
            bar = aligned[s][warm + i]
            price = bar.close
            d = agents[s].decide(s, price, bar)
            pos = agents[s].position
            if d.action == "buy":
                q = broker.quote(s, d.spend_usdc, price)
                try:
                    auth.authorize(order_id=f"bt_{s}_{i}", symbol=s,
                                   amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
                except MandateError:
                    rejects += 1
                else:
                    eff = (q.total_usdc / q.quantity).quantize(CENT) if q.quantity else price
                    pos.apply_buy(q.quantity, eff)
                    cum_buy += q.total_usdc; fees += q.fee_usdc; per_sym[s]["buys"] += 1
            elif d.action == "sell" and pos.quantity > 0:
                qty = pos.quantity; avg = pos.avg_price_usdc
                q = broker.sell_quote(s, qty, price)
                pnl = (q.total_usdc - avg * qty).quantize(CENT)
                realized += pnl; per_sym[s]["realized"] += pnl; fees += q.fee_usdc
                pos.apply_sell(qty); auth.credit_sale(q.total_usdc); per_sym[s]["sells"] += 1
        # 자산가치 곡선(공유 현금 + 전 종목 평가액) → 최대낙폭
        equity = auth.remaining_usdc + sum(
            (agents[s].position.quantity * aligned[s][warm + i].close * (1 - fee_rate)
             for s in symbols), Decimal(0))
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)

    # 미실현 + 최종 지표 (마지막 봉 종가 기준)
    unreal = Decimal(0)
    sym_rows = []
    for s in symbols:
        pos = agents[s].position
        last = aligned[s][-1].close
        u = ((pos.quantity * last * (1 - fee_rate)) - pos.avg_price_usdc * pos.quantity).quantize(CENT)
        unreal += u
        sym_rows.append({"symbol": s, "buys": per_sym[s]["buys"], "sells": per_sym[s]["sells"],
                         "realized_pnl_usdc": str(per_sym[s]["realized"]),
                         "unrealized_pnl_usdc": str(u), "left_qty": str(pos.quantity),
                         "last_price": str(last)})
    total_pnl = (realized + unreal).quantize(CENT)
    strat_pct = (total_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    # 벤치마크: 예산을 N등분해 각 종목 첫 봉 매수 → 마지막 봉 매도(등가중 매수후보유, 같은 수수료)
    bh_pnl = Decimal(0)
    per_bud = (budget / n).quantize(CENT)
    for s in symbols:
        f, l = aligned[s][warm].close, aligned[s][-1].close
        qty = ((per_bud / (f * (1 + fee_rate))).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
               if f > 0 else Decimal(0))
        bh_pnl += (qty * l * (1 - fee_rate)).quantize(CENT) - per_bud
    bh_pct = (bh_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {"symbols": symbols, "suffix": args.suffix,
                   "from": common[warm] if play_dates else "", "to": common[-1] if common else "",
                   "bars_played": len(play_dates), "strategy": "condition-portfolio",
                   "rules": {"buy_dip_pct": args.dip, "take_profit_pct": args.profit,
                             "spend_per_trade_total": args.spend, "spend_per_symbol": str(spend)},
                   "budget_usdc": args.budget, "per_trade_max_usdc": args.per_trade,
                   "fee_bps": CFG.broker_fee_bps, "shared_budget": True},
        "metrics": {
            "realized_pnl_usdc": str(realized.quantize(CENT)),
            "unrealized_pnl_usdc": str(unreal), "total_pnl_usdc": str(total_pnl),
            "return_on_budget_pct": str(strat_pct),
            "benchmark_equalweight_pct": str(bh_pct),
            "excess_return_pct": str((strat_pct - bh_pct).quantize(CENT)),
            "max_drawdown_pct": str(mdd.quantize(CENT)),
            "cum_buy_usdc": str(cum_buy), "fees_usdc": str(fees), "ap2_rejects": rejects,
            "final_remaining_usdc": str(auth.remaining_usdc),
        },
        "by_symbol": sym_rows,
    }
    m = result["metrics"]
    print(f"\n===== 포트폴리오 결과 ({'+'.join(symbols)}) =====")
    print(f"  총손익      : {m['total_pnl_usdc']} USDC (실현 {m['realized_pnl_usdc']} + 평가 {m['unrealized_pnl_usdc']})")
    print(f"  예산 수익률 : {m['return_on_budget_pct']}%  ·  최대낙폭(MDD) {m['max_drawdown_pct']}%")
    verdict = "우위" if Decimal(m["excess_return_pct"]) >= 0 else "열위"
    print(f"  벤치마크    : 등가중 매수후보유 {m['benchmark_equalweight_pct']}% → 초과수익 {m['excess_return_pct']}%p ({verdict})")
    print(f"  AP2 거부 {m['ap2_rejects']}건 · 수수료 {m['fees_usdc']} USDC · 잔여 예산 {m['final_remaining_usdc']} USDC")
    for r in sym_rows:
        print(f"    {r['symbol']:<6} 매수 {r['buys']} 매도 {r['sells']} · 실현 {r['realized_pnl_usdc']} · 평가 {r['unrealized_pnl_usdc']} USDC")

    out_dir = os.path.join(ROOT, "artifacts", "backtests")
    os.makedirs(out_dir, exist_ok=True)
    tag = "portfolio_" + "-".join(symbols) + args.suffix
    path = os.path.join(out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"  저장        : {os.path.relpath(path, ROOT)}")
    return 0


def main() -> int:
    args = build_args()
    if args.symbols.strip():
        return run_portfolio(args)   # 멀티 종목 포트폴리오 (엔진 모델 재현)
    is_trend = args.strategy == "trend"
    csv_path = args.file or os.path.join(
        ROOT, "data", "market", f"{args.symbol.upper()}{args.suffix}.csv")
    try:
        sub = max(1, int(args.sub_bars))
        feed = (IntradayReplayFeed(csv_path, start=args.date_from, end=args.date_to,
                                   warmup=args.warmup, sub=sub) if sub > 1
                else ReplayPriceFeed(csv_path, start=args.date_from, end=args.date_to, warmup=args.warmup))
    except (FileNotFoundError, ValueError) as e:
        # 스택트레이스 대신 사람이 읽는 안내 — 구간을 잘못 준 경우가 대부분이다
        print(f"[오류] {e}")
        print("  --from/--to 를 빼면 CSV 전체 구간으로 실행됩니다.")
        print("  데이터 수집: python scripts/fetch_market_data.py")
        return 1

    symbol = CFG.stock_symbol
    strategy = Strategy(
        buy_dip_pct=Decimal(args.dip), take_profit_pct=Decimal(args.profit),
        spend_per_trade_usdc=Decimal(args.spend), decision_mode=args.mode,
        ta_mode=args.ta,
        mode="trend" if is_trend else "condition",
        trend_signal=args.trend_signal,
        # 추세추종은 추세를 태워야 하므로 시간청산 미적용, 조건형은 인자값 사용
        max_hold_bars=0 if is_trend else args.max_hold,
    )
    kp = Keypair()
    # 추세추종(올인)은 '가진 현금 전량 매수'라 건별 한도가 총자산까지 열려야 한다(엔진과 동일).
    per_trade = CFG.max_budget_usdc if is_trend else Decimal(args.per_trade)
    mandate = OpenPaymentMandate(
        user_pubkey=str(kp.pubkey()), allowed_asset=CFG.usdc_mint,
        budget_total_usdc=Decimal(args.budget), per_trade_max_usdc=per_trade,
        allowed_symbols=[symbol],
    ).sign(kp)
    auth = PaymentAuthorizer(mandate, agent_kp=kp)

    ta_tag = "+ta" if args.ta else ""
    brain = None
    brain_desc = f"rule{ta_tag}"
    if is_trend:
        # 추세추종은 결정론적 규칙 신호(Gemini 미사용) — 검증(explore_trend)이 그대로 재현
        sig_label = {"pxma20": "가격>MA20", "cross_5_20": "골든크로스5/20",
                     "cross_1_5": "1/5크로스(가격>MA5)",
                     "cross_5_20_1_5": "5/20+1/5 결합"}[args.trend_signal]
        brain_desc = f"추세추종/{sig_label} (올인·올아웃)"
    elif args.brain == "gemini":
        if not CFG.gemini_api_key:
            print("GEMINI_API_KEY 미설정 — gemini 백테스트 불가 (.env 확인)")
            return 1
        from agents.gemini_decider import GeminiDecider
        brain = GeminiDecider(CFG.gemini_api_key, CFG.gemini_model, CFG.gemini_mode)
        brain_desc = f"gemini/{args.mode}{ta_tag} ({CFG.gemini_model})"

    trading = TradingAgent(kp, auth, strategy, CFG.usdc_decimals, "backtest",
                           brain=brain, fee_bps=CFG.broker_fee_bps)
    if feed.warmup_bars:
        trading.preload_bars(feed.warmup_bars)   # OHLC 째로 주입 — TA 지표 워밍업
    from solders.pubkey import Pubkey
    broker = BrokerAgent(Keypair(), Pubkey.from_string(CFG.usdc_mint), CFG.usdc_decimals,
                         None, CFG.stock_decimals, "backtest", fee_bps=CFG.broker_fee_bps)

    fee_rate = Decimal(CFG.broker_fee_bps) / Decimal(10000)
    budget = Decimal(args.budget)
    realized = Decimal(0)
    fees = Decimal(0)
    cum_buy = Decimal(0)
    rejects = 0
    fallbacks = 0
    wins = 0
    trades: list[dict] = []
    decisions: list[dict] = []
    by_action: dict[str, int] = {}
    by_source: dict[str, int] = {}
    peak = budget
    mdd = Decimal(0)
    last_price = Decimal(0)
    first_price = Decimal(0)   # 벤치마크(매수후보유) 진입가 = 재생 첫 봉 종가
    bars_in_position = 0       # 시장 노출 봉 수 (자본 유휴율 진단)
    last_call = 0.0
    played = 0

    rule_desc = ("추세: 상승세 전량 보유 · 하락세 전량 매도" if is_trend
                 else f"규칙: MA5 −{args.dip}% 매수 · 평단 +{args.profit}% 익절")
    print(f"백테스트 시작 — {feed.source_label} / 두뇌 {brain_desc} / "
          f"{rule_desc} / 예산 {budget} USDC (최대 {args.max_bars}봉)")

    while not feed.exhausted and played < args.max_bars:
        price = feed.get_price(symbol)
        bar = feed.last_bar
        last_price = price
        played += 1
        if played == 1:
            first_price = price

        if brain is not None:
            # 무료 티어 보호 — 호출 간격 유지 + 429 쿨다운이 걸려 있으면 기다렸다 재개
            wait = max(0.0, args.pace - (time.time() - last_call))
            cd = max(0.0, getattr(brain, "_cooldown_until", 0.0) - time.time())
            if cd > 0:
                print(f"  … 무료 티어 쿨다운 {cd:.0f}s 대기 (봉 {bar.date})")
            time.sleep(wait + cd)
            last_call = time.time()

        d = trading.decide(symbol, price, bar)   # 봉(OHLC) 전달 — 캔들·패턴 TA 근거
        by_action[d.action] = by_action.get(d.action, 0) + 1
        by_source[d.source] = by_source.get(d.source, 0) + 1
        if d.source == "rule-fallback":
            fallbacks += 1
        decisions.append({"date": bar.date, "price": str(price),
                          "action": d.action, "source": d.source, "reason": d.reason})

        if d.action == "buy":
            q = broker.quote(symbol, d.spend_usdc, price)
            try:
                auth.authorize(order_id=f"bt_{played}", symbol=symbol,
                               amount_usdc=q.total_usdc, pay_to=str(broker.pubkey))
            except MandateError as e:
                rejects += 1
                decisions[-1]["ap2_rejected"] = str(e)
            else:
                eff = (q.total_usdc / q.quantity).quantize(CENT) if q.quantity else price
                trading.position.apply_buy(q.quantity, eff)
                cum_buy += q.total_usdc
                fees += q.fee_usdc
                trades.append({"date": bar.date, "side": "buy", "qty": str(q.quantity),
                               "price": str(price), "total": str(q.total_usdc)})
        elif d.action == "sell" and trading.position.quantity > 0:
            qty = trading.position.quantity
            avg = trading.position.avg_price_usdc
            q = broker.sell_quote(symbol, qty, price)
            pnl = (q.total_usdc - avg * qty).quantize(CENT)
            realized += pnl
            fees += q.fee_usdc
            trading.position.apply_sell(qty)
            auth.credit_sale(q.total_usdc, allow_surplus=is_trend)  # 추세추종은 복리 재투자
            if pnl > 0:
                wins += 1
            trades.append({"date": bar.date, "side": "sell", "qty": str(qty),
                           "price": str(price), "total": str(q.total_usdc),
                           "realized": str(pnl)})

        if trading.position.quantity > 0:
            bars_in_position += 1

        # 자산가치 곡선 (가용 예산 + 보유 평가액, 수수료 차감) → 최대낙폭(MDD)
        equity = auth.remaining_usdc + (trading.position.quantity * price * (1 - fee_rate))
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)
        if not args.quiet:
            mark = {"buy": "🟢", "sell": "🔴"}.get(d.action, "·")
            print(f"  {bar.date} {price:>8} {mark} {d.action:<4} [{d.source}] {d.reason[:72]}")

    pos = trading.position
    unrealized = ((pos.quantity * last_price * (1 - fee_rate))
                  - pos.avg_price_usdc * pos.quantity).quantize(CENT)
    total_pnl = (realized + unrealized).quantize(CENT)
    sells = [t for t in trades if t["side"] == "sell"]

    # 벤치마크: 같은 구간 첫 봉에 예산 전액으로 매수 → 마지막 봉에 전량 매도 (매수후보유).
    # 브로커와 동일한 수수료 모델(매수 가산·매도 차감)을 적용해야 정직한 비교가 된다.
    # 심사 최다 예상 질문 "AI 없이 그냥 샀으면?" 에 대한 우리 쪽 기준선이다.
    bh_qty = ((budget / (first_price * (1 + fee_rate))).quantize(Decimal("0.0001"),
              rounding=ROUND_DOWN) if first_price > 0 else Decimal(0))
    bh_final = (bh_qty * last_price * (1 - fee_rate)).quantize(CENT)
    bh_pnl = (bh_final - budget).quantize(CENT)
    bh_pct = (bh_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    strat_pct = (total_pnl / budget * 100).quantize(CENT) if budget > 0 else Decimal(0)
    excess_pct = (strat_pct - bh_pct).quantize(CENT)
    exposure_pct = (Decimal(bars_in_position) / played * 100).quantize(CENT) if played else Decimal(0)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            # 저장소 상대경로로 기록 — 공개 저장소에 로컬 사용자명·디렉터리가 새지 않게
            "source": feed.source_label, "file": os.path.relpath(csv_path, ROOT).replace("\\", "/"),
            "from": args.date_from, "to": args.date_to, "bars_played": played,
            "strategy": args.strategy,
            "trend_signal": args.trend_signal if is_trend else "-",
            "brain": "rule" if is_trend else args.brain,
            "mode": args.mode if (args.brain == "gemini" and not is_trend) else "-",
            "ta_mode": args.ta,
            "rules": {"buy_dip_pct": args.dip, "take_profit_pct": args.profit,
                      "spend_per_trade": args.spend},
            "budget_usdc": args.budget, "per_trade_max_usdc": args.per_trade,
            "fee_bps": CFG.broker_fee_bps,
        },
        "metrics": {
            "realized_pnl_usdc": str(realized.quantize(CENT)),
            "unrealized_pnl_usdc": str(unrealized),
            "total_pnl_usdc": str(total_pnl),
            "return_on_budget_pct": str(strat_pct),
            # 벤치마크 대비 — excess 가 음수면 "그냥 사서 들고 있는 게 나았다"는 뜻이다
            "benchmark_buyhold_pct": str(bh_pct),
            "benchmark_buyhold_pnl_usdc": str(bh_pnl),
            "excess_return_pct": str(excess_pct),
            "first_price": str(first_price), "last_price": str(last_price),
            "exposure_pct": str(exposure_pct),   # 시장 노출 비율 (100 − 자본 유휴율)
            "buy_count": len(trades) - len(sells), "sell_count": len(sells),
            # 승률은 '실현된 매도'만 세므로 미실현 손실이 포지션에 잠기면 100%가 나온다.
            # 반드시 total_pnl·excess 와 함께 읽을 것 (단독 인용 금지).
            "win_rate_pct": str(Decimal(wins) / len(sells) * 100 if sells else Decimal(0))[:6],
            "max_drawdown_pct": str(mdd.quantize(CENT)),
            "cum_buy_usdc": str(cum_buy), "fees_usdc": str(fees),
            "ap2_rejects": rejects, "gemini_fallbacks": fallbacks,
            # 규칙 게이트가 AI 의 규칙 밖 개시를 되돌린 횟수 (0이면 모델이 규칙을 지켰다는 뜻)
            "gemini_gated": by_source.get("rule-gate", 0),
            "decisions_by_action": by_action, "decisions_by_source": by_source,
            "position_left_qty": str(pos.quantity),
        },
        "trades": trades,
        "decisions": decisions,
    }

    m = result["metrics"]
    print(f"\n===== 결과 ({brain_desc}) =====")
    print(f"  총손익      : {m['total_pnl_usdc']} USDC "
          f"(실현 {m['realized_pnl_usdc']} + 평가 {m['unrealized_pnl_usdc']})")
    print(f"  예산 수익률 : {m['return_on_budget_pct']}%  ·  최대낙폭(MDD) {m['max_drawdown_pct']}%")
    verdict = "우위" if Decimal(m["excess_return_pct"]) >= 0 else "열위"
    print(f"  벤치마크    : 매수후보유 {m['benchmark_buyhold_pct']}% "
          f"→ 초과수익 {m['excess_return_pct']}%p ({verdict}) · 시장노출 {m['exposure_pct']}%")
    print(f"  매매        : 매수 {m['buy_count']} / 매도 {m['sell_count']} "
          f"(승률 {m['win_rate_pct']}%) · 수수료 {m['fees_usdc']} USDC")
    print(f"  AP2 거부    : {m['ap2_rejects']}건 · Gemini 폴백 {m['gemini_fallbacks']}건")
    print(f"  판단 분포   : {m['decisions_by_action']} / 출처 {m['decisions_by_source']}")

    out_dir = os.path.join(ROOT, "artifacts", "backtests")
    os.makedirs(out_dir, exist_ok=True)
    tag = (f"{args.symbol.upper()}{args.suffix}_"
           + (f"trend-{args.trend_signal}" if is_trend
              else args.brain + (f"-{args.mode}" if args.brain == "gemini" else ""))
           + ("-ta" if args.ta and not is_trend else ""))
    path = os.path.join(out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"  저장        : {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
