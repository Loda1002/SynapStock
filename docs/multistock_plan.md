# 멀티 종목(동시 매수) 구현 계획 — 402 Guard 다중 지출 승인

> 작성 2026-07-25. 범위(사용자 확정): **드라이 + 대시보드 + 백테스트** 멀티. 라이브 온체인 멀티 제외
> (종목별 devnet 민트 발행이 무겁고 라이브 단일종목 증빙은 이미 있음). 큰 리팩터라 새 대화에서 실행.
> 근거 데이터: `docs/reports/strategy_validation.md` — 분산이 짧은 세션 흑자율 55→82%, 최악 손실 절반.

## 왜 (402 Guard 정합)

멀티 종목은 "수익 극대화"가 아니라 두 가지로 프레이밍한다:
1. **동시 다중 지출 승인** — 하나의 402 Guard·하나의 예산 한도(mandate) 아래, N개 자산을 동시에 사는
   여러 판단이 각각 서명 직전 같은 게이트를 통과한다. "에이전트가 여러 곳에 동시에 결제하는" 현실적 시나리오에서
   지출 통제가 작동함을 보인다(가드 이벤트 수도 N배 → 데모 밀도↑, dip3/profit5 로 줄어든 거래빈도 보완).
2. **분산으로 신뢰도↑** — 같은 예산을 N종목에 나눠 변동성을 줄인다(실측 근거 위 리포트).

## 핵심 설계 — 공유 예산 / 종목별 상태

```
                      ┌───────────── 공유(세션 1개) ─────────────┐
                      │  OpenPaymentMandate(allowed_symbols=[A,B,C])│
                      │  PaymentAuthorizer(auth)  ← 예산 한 개 공유  │
                      │  Guard(신뢰 수취인=브로커)  ← 게이트 한 개    │
                      │  BrokerAgent(broker)      ← 드라이는 1개로 충분│
                      └───────────────────────────────────────────┘
                              ▲            ▲            ▲
                    ┌─────────┴──┐ ┌───────┴────┐ ┌────┴────────┐
                    │ 종목 A      │ │ 종목 B      │ │ 종목 C      │  ← 종목별(N개)
                    │ ReplayFeed  │ │ ReplayFeed  │ │ ReplayFeed  │
                    │ TradingAgent│ │ TradingAgent│ │ TradingAgent│  (각자 position·
                    │ position    │ │ position    │ │ position    │   _history·_bars·
                    └─────────────┘ └─────────────┘ └─────────────┘   _bars_held)
```

- **공유**: mandate(allowed_symbols=전 종목), auth(예산 1개 = 총 한도), guard(수취인 allowlist 1개), broker(드라이 1개).
  → 한 예산 안에서 N종목이 경쟁 소비. 이게 "다중 지출을 하나의 가드로 통제" 서사의 핵심.
- **종목별**: `TradingAgent` 를 종목마다 1개(각자 position·_history·_bars·_bars_held·시간청산 카운터).
  **모두 같은 auth·guard 객체를 공유** → `TradingAgent.__init__` 에 이미 auth·guard 주입 구조라 그대로 재사용.
- **1회 매수 금액**: 종목별 `spend_per_trade = 총 spend / N` (검증 포트폴리오와 동형 — 한 종목이 예산 독식 방지).
  per_trade_max·budget 은 공유 auth 가 관리. 시간청산(max_hold_bars)은 종목별로 독립 카운트.

## 데이터 흐름 (엔진 틱)

`web/engine.py` 현재 `_tick_once()` 는 단일 심볼. 멀티는:

```python
async def _tick_once(self):
    for sym in self.symbols:                      # 종목 순회
        feed = self.feeds[sym]
        if isinstance(feed, ReplayPriceFeed) and feed.exhausted:
            continue                              # 이 종목만 소진 → 건너뜀
        price = feed.get_price(sym); bar = feed.last_bar
        # 종목별 시세/캔들/이벤트 (payload 에 symbol 이미 존재)
        self._emit_price(sym, price, bar)
        if not self.trading_enabled: continue
        agent = self.agents[sym]
        decision = await asyncio.to_thread(agent.decide, sym, price, bar)
        self._emit_decision(sym, decision)
        try:
            if decision.action == "sell" and agent.position.quantity > 0:
                await self._sell_cycle(sym, agent, price, decision)
            elif decision.action == "buy":
                await self._buy_cycle(sym, agent, price, decision)
        except Exception as e:                    # 한 종목 오류 격리 — 나머지 계속
            self.bus.emit(ev.ERROR, {"symbol": sym, "message": f"{type(e).__name__}: {e}"})
    # 전 종목 소진 시 세션 자동 종료
    if all(f.exhausted for f in self.feeds.values() if isinstance(f, ReplayPriceFeed)):
        self.bus.emit(ev.REPLAY_ENDED, {...}); self._stop_event.set()
```

`_buy_cycle`/`_sell_cycle` 는 `agent` 인자를 받도록 시그니처만 바꾸면 내부 로직(guard·auth·broker 공유)은 거의 그대로.
실현손익·수수료·cum_buy 는 **전 종목 합산**으로 누적(현재도 스칼라라 그대로 더하면 됨).

## 파일별 변경

1. **config.py**: `stock_symbols: list = _get("STOCK_SYMBOLS", "").split(",")` (빈값=STOCK_SYMBOL 단일 폴백).
   드라이 멀티는 민트 불필요(정산 미브로드캐스트) — stock_mint 는 라이브에서만 종목별 필요(범위 밖).
2. **web/engine.py** (핵심):
   - `start()`: symbols 리스트 확정 → 종목별 `feeds[sym]`·`agents[sym]` 생성, 공유 mandate(allowed_symbols=symbols)·auth·guard.
     종목별 spend = 총 spend/N. 워밍업 preload_bars 종목별.
   - 세션 상태: `self.positions` 대신 `self.agents[sym].position`. price_history·candles 를 `dict[sym]` 로.
   - `_tick_once`·`_buy_cycle`·`_sell_cycle`: symbol/agent 파라미터화(위 흐름).
   - `state_snapshot()`: `symbols` 배열 + 종목별 {price·position·valuation·candles} + **aggregate**(총자산·실현손익·가드 KPI).
   - `_valuation`·`_briefing_stats`·`_archive`·`_session_summary`: 종목별 합산.
   - 하위호환: symbols=[단일] 이면 현재와 동일 동작(N=1).
3. **web/server.py**: 세션 시작 API(`POST /api/start`)가 `symbols: list[str]`(정규식 검증, data/market 존재 확인) 수용.
   기본은 .env STOCK_SYMBOLS 또는 단일.
4. **web/static/js/app.js** (대시보드): 시세·포지션·판단 타임라인을 **종목 탭 또는 종목별 카드**로. 최소안:
   종목 선택 드롭다운(N개 중 1개 포커스) + 상단에 종목별 미니 요약 행 + aggregate 카드(총자산·총 실현손익·가드 KPI 합산).
   차트는 포커스 종목 1개만 그려도 됨(캔들 데이터는 종목별 dict). 대규모 재배치 회피 = data-card 모듈 재사용.
5. **scripts/backtest.py**: `--symbols A,B,C` 포트폴리오 모드(검증 하네스 sweep_portfolio 로직 이식) — 선택(검증 도구에 이미 있음).
6. **테스트**: `scripts/test_multistock.py` — 2종목 세션에서 ①예산 공유(A 매수가 B 잔여예산 차감) ②종목별 포지션 독립
   ③한 종목 피드 소진해도 다른 종목 계속 ④전 종목 소진 시 세션 종료 ⑤가드 KPI 합산. `test_store` 류 no-op 스토어로 구동.

## 실패 처리 (반드시 구현)

- 한 종목 피드 없음/소진 → 그 종목만 skip, 나머지 진행. 전부 소진 시에만 세션 종료.
- 한 종목 decide/정산 예외 → try/except 로 격리, ERROR 이벤트(symbol 포함), 틱 계속.
- 공유 예산 소진 → 전 종목 자연히 hold(auth.remaining ≤ 0). 종목 간 경쟁은 순회 순서대로(공정성 이슈 없음 — 드라이).
- 종목 수 상한(예: 5) — data/market 에 CSV 있는 종목만. 없는 종목은 시작 시 거부.

## 검증 순서 (CLAUDE.md 루틴)

1. 백테스트/검증: `validate_strategy --symbols A,B,C` 는 이미 분산 실증(완료).
2. 엔진 드라이 스모크: 2~3종목 세션을 no-op 스토어로 완주 → 종목별 매매·공유예산 차감·세션 종료 로그 확인.
3. 대시보드: 브라우저에서 종목 전환·종목별 차트·aggregate KPI 확인(preview 도구).
4. 회귀: 기존 단일종목(symbols=[하나]) 경로가 동일 동작하는지 전체 테스트.

## 예상 규모

엔진 backend ≈ 반나절, 대시보드 ≈ 반나절, 테스트·검증 ≈ 2~3시간. **새 대화 1개 분량.**
프론트를 "포커스 1종목 + aggregate 요약"으로 최소화하면 더 짧음(디자인 시안 도착 후 종목 탭 고도화).
