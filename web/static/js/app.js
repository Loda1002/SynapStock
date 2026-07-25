/* app.js — 대시보드 로직 (프레임워크 없음).
   초기 /api/state 로드 → SSE(/api/events) 구독 → 카드·피드 갱신.
   DOM 접근은 전부 data-속성 훅 — 디자인 스킨 교체 시 클래스는 자유롭게 바꿔도 된다.
   외부 CDN 의존 없음(데모데이 오프라인 폴백 원칙). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const el = {
    net: $("[data-net]"),
    engineStatus: $("[data-engine-status]"),
    brain: $("[data-brain]"),
    pausedBadge: $("[data-paused-badge]"),
    feedBadge: $("[data-feed-badge]"),
    modeSelect: $("[data-mode-select]"),
    feedSelect: $("[data-feed-select]"),
    feedDataset: $("[data-feed-dataset]"),
    symPicker: $("[data-sym-picker]"),
    symPickerLabel: $("[data-sym-picker-label]"),
    focusWrap: $("[data-focus-wrap]"),
    focusSelect: $("[data-focus-select]"),
    symSummary: $("[data-sym-summary]"),
    strategySelect: $("[data-strategy-select]"),
    speedSelect: $("[data-speed-select]"),
    trendSignal: $("[data-trend-signal]"),
    decisionMode: $("[data-decision-mode]"),
    taWrap: $("[data-ta-wrap]"),
    taMode: $("[data-ta-mode]"),
    dcaParams: $("[data-dca-params]"),
    dcaUnit: $("[data-dca-unit]"),
    dcaTicks: $("[data-dca-ticks]"),
    dcaTicksWrap: $("[data-dca-ticks-wrap]"),
    dcaMinutes: $("[data-dca-minutes]"),
    dcaMinutesWrap: $("[data-dca-minutes-wrap]"),
    dcaTime: $("[data-dca-time]"),
    dcaTimeWrap: $("[data-dca-time-wrap]"),
    dcaAmount: $("[data-dca-amount]"),
    btnStart: $("[data-btn-start]"),
    btnStop: $("[data-btn-stop]"),
    btnPause: $("[data-btn-pause]"),
    btnResume: $("[data-btn-resume]"),
    rules: $("[data-rules]"),
    symbol: $("[data-symbol]"),
    price: $("[data-price]"),
    priceChange: $("[data-price-change]"),
    changeBasis: $("[data-change-basis]"),
    chart: $("[data-chart]"),
    candleInfo: $("[data-candle-info]"),
    tickInfo: $("[data-tick-info]"),
    posQty: $("[data-pos-qty]"),
    posSymbol: $("[data-pos-symbol]"),
    posAvg: $("[data-pos-avg]"),
    budgetFill: $("[data-budget-fill]"),
    budgetSpent: $("[data-budget-spent]"),
    budgetTotal: $("[data-budget-total]"),
    budgetRemaining: $("[data-budget-remaining]"),
    budgetPerTrade: $("[data-budget-per-trade]"),
    pnl: $("[data-pnl]"),
    returnPct: $("[data-return-pct]"),
    cumBuy: $("[data-cum-buy]"),
    feeRate: $("[data-fee-rate]"),
    cumFee: $("[data-cum-fee]"),
    unrealized: $("[data-unrealized]"),
    unrealizedPct: $("[data-unrealized-pct]"),
    positionValue: $("[data-position-value]"),
    positionValue2: $("[data-position-value2]"),
    totalAsset: $("[data-total-asset]"),
    cash: $("[data-cash]"),
    onchainRow: $("[data-onchain-row]"),
    onchainUsdc: $("[data-onchain-usdc]"),
    mandateForm: $("[data-mandate-form]"),
    mandateBudget: $("[data-mandate-budget]"),
    mandatePerTrade: $("[data-mandate-per-trade]"),
    mandateSymbols: $("[data-mandate-symbols]"),
    btnMandate: $("[data-btn-mandate]"),
    mandateHint: $("[data-mandate-hint]"),
    decisionFeed: $("[data-decision-feed]"),
    eventLog: $("[data-event-log]"),
    tradesBody: $("[data-trades-body]"),
    connStatus: $("[data-conn-status]"),
    walletTrading: $("[data-wallet-trading]"),
    walletBroker: $("[data-wallet-broker]"),
    btnNotify: $("[data-btn-notify]"),
    toasts: $("[data-toasts]"),
    btnBriefing: $("[data-btn-briefing]"),
    briefingMeta: $("[data-briefing-meta]"),
    briefingText: $("[data-briefing-text]"),
    grid: $("main.grid"),
    btnLayoutReset: $("[data-btn-layout-reset]"),
  };

  const MAX_FEED_ITEMS = 100;
  const MAX_LOG_ITEMS = 200;
  let candles = [];         // 캔들차트 데이터 (서버 집계 + 틱마다 로컬 갱신)
  let ticksPerCandle = 2;   // 서버(engine.TICKS_PER_CANDLE)와 같은 집계 규칙
  let sessionOpen = null;   // 세션 시작가 — 목 시세의 등락 표시 기준
  let prevClose = null;     // 직전 봉 종가 — 실데이터 재생의 등락 표시 기준
  let changeBasis = "session-open";  // 서버 state.price.change_basis
  let lastState = null;     // 최근 /api/state — 틱 사이 평가손익 재계산에 사용
  // 멀티 종목: 세션 종목 목록·포커스(차트 표시 종목)·종목별 스냅샷
  let sessionSymbols = [];  // state.symbols
  let focusSymbol = null;   // 차트/가격/포지션 카드가 보여줄 종목 (기본 = 첫 종목)
  let perSymbol = {};       // state.per_symbol (종목별 price·position·valuation)
  let lastEventId = 0;
  const pageLoadedAt = Date.now();  // A4: SSE 히스토리 재생분 알림 제외 기준

  // ---------- 유틸 ----------
  const timeOf = (ts) => (ts || "").slice(11, 19);
  const num = (v) => Number(v || 0);

  function shortKey(k) {
    return k && k.length > 12 ? k.slice(0, 4) + "…" + k.slice(-4) : (k || "—");
  }

  function make(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text; // 항상 textContent (XSS 방지)
    return n;
  }

  function capList(listEl, max) {
    while (listEl.children.length > max) listEl.removeChild(listEl.lastChild);
  }

  // ---------- 상태 렌더 ----------
  async function fetchState() {
    try {
      const r = await fetch("/api/state");
      renderState(await r.json());
    } catch (e) { /* 서버 재기동 중 등 — SSE 재연결이 복구 */ }
  }

  /* ---------- 평가손익(미실현) · 총자산 ----------
     기준은 실현손익과 동일: 지금 전량 매도하면 받을 금액(수수료 차감) − 실효 평단 × 수량.
     서버(web/engine.py _valuation)가 같은 식으로 계산하며, 여기서는 틱 사이 갱신용으로
     같은 식을 다시 적용한다(두 곳을 함께 고쳐야 한다). */
  function renderValuation(v) {
    if (!v) return;
    const u = num(v.unrealized_pnl_usdc);
    el.unrealized.textContent = (u > 0 ? "+" : "") + v.unrealized_pnl_usdc;
    el.unrealized.className = u > 0 ? "pos" : u < 0 ? "neg" : "";
    el.unrealizedPct.textContent = (u > 0 ? "+" : "") + v.unrealized_pct + "%";
    el.positionValue.textContent = v.position_net_value_usdc;
    el.positionValue2.textContent = v.position_net_value_usdc;
    el.totalAsset.textContent = v.total_asset_usdc;
    el.cash.textContent = v.cash_usdc;
    el.onchainRow.classList.toggle("hidden", !v.onchain_usdc);
    if (v.onchain_usdc) el.onchainUsdc.textContent = v.onchain_usdc;
  }

  function valuationAtPrice(price) {
    if (!lastState || !lastState.valuation) return null;
    const s = lastState;
    const qty = num(s.position.quantity), avg = num(s.position.avg_price_usdc);
    const feeRate = (s.fees ? s.fees.fee_bps : 0) / 10000;
    const net = qty * price * (1 - feeRate), cost = qty * avg;
    const unreal = net - cost, cash = num(s.budget.remaining_usdc);
    const f2 = (n) => n.toFixed(2);
    return Object.assign({}, s.valuation, {
      market_price_usdc: String(price),
      position_value_usdc: f2(qty * price),
      position_net_value_usdc: f2(net),
      unrealized_pnl_usdc: f2(unreal),
      unrealized_pct: cost > 0 ? f2((unreal / cost) * 100) : "0.00",
      cash_usdc: s.budget.remaining_usdc,
      total_asset_usdc: f2(cash + net),
    });
  }

  // 적립 주기 문구 — 서버가 준 schedule_label 우선(틱/분/매일 시각 공용)
  function dcaSchedule(st) {
    if (!st) return "";
    if (st.schedule_label) return st.schedule_label;
    if (st.dca_unit === "minutes") return `${st.dca_every_minutes}분마다`;
    if (st.dca_unit === "daily") return `매일 ${st.dca_at_time}`;
    return `${st.dca_every_ticks}틱마다`;
  }

  // ---------- 멀티 종목: 포커스 선택 · 종목별 요약 ----------
  function setFocus(sym) {
    if (!sessionSymbols.includes(sym)) return;
    focusSymbol = sym;
    if (el.focusSelect) el.focusSelect.value = sym;
    if (lastState) renderState(lastState);   // 포커스 종목 시세·차트·포지션 다시 그림
  }

  function populateFocusSelect(syms) {
    const want = (syms || []).join(",");
    if (el.focusSelect.dataset.syms !== want) {   // 목록이 바뀔 때만 옵션 재생성
      el.focusSelect.dataset.syms = want;
      el.focusSelect.textContent = "";
      for (const sym of syms || []) {
        const o = make("option", null, sym);
        o.value = sym;
        el.focusSelect.appendChild(o);
      }
    }
    if (focusSymbol) el.focusSelect.value = focusSymbol;
  }

  // 종목별 요약 표 (멀티에서만 보임) — 종목명을 누르면 그 종목으로 포커스 전환
  function renderSymbolStrip() {
    const multi = sessionSymbols.length > 1;
    const card = el.symSummary.closest("[data-card]");
    if (card) card.classList.toggle("hidden", !multi);
    if (!multi) return;
    el.symSummary.textContent = "";
    for (const sym of sessionSymbols) {
      const d = perSymbol[sym] || {};
      const p = d.price || {}, pos = d.position || {}, v = d.valuation || {};
      const tr = make("tr", sym === focusSymbol ? "focus-row" : null);
      const symTd = make("td");
      const btn = make("button", "linklike", sym + (sym === focusSymbol ? " ●" : ""));
      btn.title = "이 종목을 차트에 표시";
      btn.addEventListener("click", () => setFocus(sym));
      symTd.appendChild(btn);
      tr.appendChild(symTd);
      tr.appendChild(make("td", null, p.current != null ? p.current : "—"));
      tr.appendChild(make("td", null, pos.quantity != null ? pos.quantity : "0"));
      tr.appendChild(make("td", null, pos.avg_price_usdc != null ? pos.avg_price_usdc : "0"));
      tr.appendChild(make("td", null, v.position_net_value_usdc != null ? v.position_net_value_usdc : "0"));
      const u = num(v.unrealized_pnl_usdc);
      tr.appendChild(make("td", u > 0 ? "pos" : u < 0 ? "neg" : null,
        (u > 0 ? "+" : "") + (v.unrealized_pnl_usdc || "0") + " (" + (v.unrealized_pct || "0") + "%)"));
      el.symSummary.appendChild(tr);
    }
  }

  function renderState(s) {
    lastState = s;
    // 멀티 종목 정리 (rules 텍스트·포커스에서 함께 쓰이므로 먼저) — 포커스는 첫 종목이 기본
    sessionSymbols = s.symbols || (s.symbol ? [s.symbol] : []);
    perSymbol = s.per_symbol || {};
    if (!focusSymbol || !sessionSymbols.includes(focusSymbol)) focusSymbol = sessionSymbols[0] || s.symbol;
    const multi = sessionSymbols.length > 1;
    const symLabel = multi ? sessionSymbols.join("·") : (s.symbol || focusSymbol || "—");
    const eng = s.engine || {};
    el.net.textContent = (eng.network || "—") + (eng.mode ? " · " + (eng.mode === "live" ? "라이브" : "드라이런") : "");
    el.engineStatus.textContent = { idle: "엔진 대기", running: "엔진 실행 중", stopping: "종료 중…" }[eng.status] || eng.status;
    el.engineStatus.classList.toggle("badge-ok", eng.status === "running");
    el.brain.textContent = "판단: " + (eng.brain || "—");
    const feePct = s.fees ? (s.fees.fee_bps / 100) : 0;
    const strat = s.strategy || { type: "condition" };
    const modeLabel = (strat.decision_mode === "trend" ? "AI 추세·보류 재량" : "AI 엄격")
      + (strat.ta_mode ? "+TA" : "");
    // 멀티면 종목별 1회 매수 금액(총 spend/N)을 쓰고, 종목 여러 개임을 문구에 드러낸다.
    const spendText = strat.spend_per_symbol_usdc || s.rules.spend_per_trade;
    const multiTag = multi ? ` · ${sessionSymbols.length}종목 동시(각자 독립 포지션, 예산·가드 공유)` : "";
    let ruleText;
    if (strat.type === "dca") {
      // 적립형 종목별 금액은 회당 amount/N (조건형 spend/N 과 다르다)
      const dcaAmt = multi ? (strat.dca_amount_per_symbol_usdc || strat.dca_amount_usdc) + " USDC(종목별)"
                           : strat.dca_amount_usdc + " USDC";
      ruleText = `적립형: ${dcaSchedule(strat)} ${dcaAmt} 정액 매수 (매도 없음)`;
    } else if (strat.type === "trend") {
      const sig = strat.trend_signal_label
        || (strat.trend_signal === "cross_5_20" ? "골든크로스5/20" : "가격>MA20");
      ruleText = `추세추종(${sig}): ${symLabel} 이 상승세면 전량 보유, 하락세로 꺾이면 전량 매도(자본 보존)·재상승 시 재매수 (올인/올아웃)`;
    } else {
      ruleText = `조건형(${modeLabel}): ${symLabel} 가격이 5일 평균(MA5)보다 ${s.rules.buy_dip_pct}% 싸지면 ${spendText} USDC 어치${multi ? "(종목별)" : ""} 매수, 평균단가보다 ${s.rules.take_profit_pct}% 오르면 전량 매도(익절)`;
    }
    const perTradeText = s.budget.all_in ? "전량(올인)" : s.budget.per_trade_max_usdc;
    el.rules.textContent = `규칙: ${ruleText} · 예산 ${s.budget.total_usdc} USDC (건별 최대 ${perTradeText})${multiTag} · 브로커 수수료 ${feePct}%`;

    // 포커스 종목의 시세/포지션/차트를 그린다 (멀티 정리는 renderState 상단에서 끝냈다).
    const fp = (perSymbol[focusSymbol] && perSymbol[focusSymbol].price) || s.price || {};
    const fpos = (perSymbol[focusSymbol] && perSymbol[focusSymbol].position) || s.position || {};
    populateFocusSelect(sessionSymbols);
    el.focusWrap.classList.toggle("hidden", !multi);

    el.symbol.textContent = focusSymbol || s.symbol;
    el.posSymbol.textContent = focusSymbol || s.symbol;
    if (fp.current != null) el.price.textContent = fp.current + " USDC";
    ticksPerCandle = fp.ticks_per_candle || ticksPerCandle;
    sessionOpen = fp.session_open != null ? num(fp.session_open) : null;
    prevClose = fp.prev_close != null ? num(fp.prev_close) : prevClose;
    changeBasis = fp.change_basis || changeBasis;
    el.changeBasis.textContent = changeBasis === "prev-close" ? "전일 종가 대비" : "세션 시작가 대비";
    const feed = s.price.feed || {};   // 피드 라벨은 세션 공통(top-level)
    el.feedBadge.textContent = "시세: " + (feed.label || "—");
    candles = (fp.candles || []).map((c) => ({
      o: num(c.open), h: num(c.high), l: num(c.low), c: num(c.close), n: c.count,
      t: c.ts || null, w: !!c.warmup,
    }));
    drawChart();
    renderPriceChange(fp.current);
    renderSymbolStrip();   // 종목별 요약 행 (멀티에서만 표시)
    if (eng.tick) el.tickInfo.textContent = `틱 ${eng.tick} · 간격 ${eng.tick_interval_sec}s`;

    // 실데이터 CSV 가 없으면 재생 옵션을 잠근다 (fetch_market_data.py 안내)
    const replayOpt = el.feedSelect.querySelector('option[value="replay"]');
    replayOpt.disabled = !s.replay_available;
    replayOpt.textContent = s.replay_available
      ? "실데이터 재생 (일봉)" : "실데이터 재생 — CSV 없음 (fetch 필요)";
    if (!s.replay_available && el.feedSelect.value === "replay") { el.feedSelect.value = "mock"; syncDcaInputs(); }

    el.posQty.textContent = fpos.quantity;
    el.posAvg.textContent = fpos.avg_price_usdc;

    const total = num(s.budget.total_usdc), spent = num(s.budget.spent_usdc);
    // 추세추종은 이익 실현 시 spent 가 음수(운용현금>예산)가 될 수 있어 하한 0 으로 clamp
    // (없으면 음수 % 폭이 무효 CSS 값이라 게이지 막대가 갱신되지 않음).
    el.budgetFill.style.width = total > 0 ? Math.max(0, Math.min(100, (spent / total) * 100)).toFixed(1) + "%" : "0%";
    el.budgetSpent.textContent = s.budget.spent_usdc;
    el.budgetTotal.textContent = s.budget.total_usdc;
    el.budgetRemaining.textContent = s.budget.remaining_usdc;
    el.budgetPerTrade.textContent = s.budget.all_in ? "전량(올인)" : s.budget.per_trade_max_usdc;

    const pnl = num(s.pnl.realized_usdc);
    el.pnl.textContent = (pnl > 0 ? "+" : "") + s.pnl.realized_usdc;
    el.pnl.className = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
    el.returnPct.textContent = s.pnl.return_pct;
    el.cumBuy.textContent = s.pnl.cum_buy_usdc;
    if (s.fees) {
      el.feeRate.textContent = feePct + "%";
      el.cumFee.textContent = s.fees.cum_fee_usdc;
    }
    renderValuation(s.valuation);

    if (s.wallets.trading) el.walletTrading.textContent = shortKey(s.wallets.trading);
    if (s.wallets.broker) el.walletBroker.textContent = shortKey(s.wallets.broker);

    if (s.last_briefing) renderBriefing(s.last_briefing);  // B2 새로고침 복원

    // A3 한도 설정 카드 — 입력 중(포커스)일 때는 값을 덮어쓰지 않는다
    const running = eng.status === "running";
    if (document.activeElement !== el.mandateBudget) el.mandateBudget.value = s.budget.total_usdc;
    if (document.activeElement !== el.mandatePerTrade) el.mandatePerTrade.value = s.budget.per_trade_max_usdc;
    el.mandateSymbols.textContent = symLabel;
    el.btnMandate.disabled = running && s.trading_enabled;
    el.mandateHint.textContent = running
      ? (s.trading_enabled
          ? "실행 중 — 긴급정지 후에만 변경할 수 있습니다 (레이스 방지)"
          : "정지 상태 — 적용하면 새 mandate 를 재서명하고 즉시 반영합니다 (사용액 이월)")
      : "대기 상태 — 다음 세션 시작 시 적용됩니다";

    // 버튼 상태
    el.btnStart.disabled = running || eng.status === "stopping";
    el.btnStop.disabled = !running;
    el.modeSelect.disabled = running;
    el.feedSelect.disabled = running;
    el.feedDataset.disabled = running;
    // 세션 종목은 실행 중 변경 불가 · 추세추종·라이브는 대기 상태에서도 단일 잠금 · 포커스 전환은 항상 허용
    // (대기 상태 판정은 사용자가 고른 드롭다운 값을 쓴다 — 스냅샷 strategy 는 직전 세션 값일 수 있음)
    for (const c of document.querySelectorAll("[data-sym-check]"))
      c.disabled = running || el.strategySelect.value === "trend" || el.modeSelect.value === "live";
    el.strategySelect.disabled = running;
    el.trendSignal.disabled = running;
    el.decisionMode.disabled = running;
    el.taMode.disabled = running;
    el.dcaUnit.disabled = running;
    el.dcaTicks.disabled = running;
    el.dcaMinutes.disabled = running;
    el.dcaTime.disabled = running;
    el.dcaAmount.disabled = running;
    // 긴급정지·재개는 세션 실행 중에만 — 대기 중에는 비활성 (정지 상태는 세션 단위)
    el.pausedBadge.classList.toggle("hidden", s.trading_enabled || !running);
    el.btnPause.classList.toggle("hidden", !s.trading_enabled);
    el.btnResume.classList.toggle("hidden", s.trading_enabled);
    el.btnPause.disabled = !running;
    el.btnResume.disabled = !running;
    const sessionHint = running ? "" : " (세션 실행 중에만 사용할 수 있습니다)";
    el.btnPause.title = "신규 판단·결제를 즉시 중단합니다" + sessionHint;
    el.btnResume.title = "매매를 다시 시작합니다" + sessionHint;
    if (s.pause_info) el.pausedBadge.textContent = `🛑 매매 정지됨 (${s.pause_info.actor}, ${timeOf(s.pause_info.ts)})`;
  }

  /* ---------- 캔들차트 (SVG 직접 구현, 외부 라이브러리 없음) ----------
     목 시세는 틱당 단일 가격이라 서버가 N틱을 한 캔들(시가·고가·저가·종가)로 묶는다.
     여기서는 그 결과를 그리고, 틱이 올 때마다 같은 규칙으로 마지막 캔들을 갱신한다. */
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CH = { w: 640, h: 260, padL: 6, padR: 54, padT: 10, padB: 16 };

  function svgNode(tag, attrs, cls) {
    const n = document.createElementNS(SVG_NS, tag);
    if (cls) n.setAttribute("class", cls);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  function movingAvg(values, period) {
    let sum = 0;
    return values.map((v, i) => {
      sum += v;
      if (i >= period) sum -= values[i - period];
      return i >= period - 1 ? sum / period : null;
    });
  }

  function pushTickToCandle(price) {
    const cur = candles[candles.length - 1];
    if (cur && cur.n < ticksPerCandle) {
      cur.h = Math.max(cur.h, price);
      cur.l = Math.min(cur.l, price);
      cur.c = price;
      cur.n += 1;
    } else {
      candles.push({ o: price, h: price, l: price, c: price, n: 1 });
      if (candles.length > 60) candles.shift();
    }
    if (sessionOpen == null) sessionOpen = price;
  }

  // 실데이터 재생: 1틱 = 1봉 — 서버가 준 시가·고가·저가·종가를 그대로 캔들로
  function pushBarCandle(b) {
    candles.push({
      o: num(b.open), h: num(b.high), l: num(b.low), c: num(b.close),
      n: ticksPerCandle, t: b.ts || null, w: false,
    });
    if (candles.length > 60) candles.shift();
    if (sessionOpen == null) sessionOpen = num(b.close);
  }

  function drawChart() {
    const svg = el.chart;
    svg.textContent = "";
    // 일봉 판별: ts 가 날짜만(YYYY-MM-DD)이면 실데이터 일봉, ISO 시각이면 목 틱 집계
    const isDaily = candles.some((c) => c.t && !c.t.includes("T"));
    const warmCount = candles.filter((c) => c.w).length;
    el.candleInfo.textContent = candles.length
      ? (isDaily
          ? `일봉 ${candles.length}개` + (warmCount ? ` (이전 이력 ${warmCount})` : "")
          : `캔들 1개 = ${ticksPerCandle}틱 · ${candles.length}개`)
      : "캔들 집계 대기";
    if (!candles.length) {
      const note = svgNode("text", { x: CH.w / 2, y: CH.h / 2, "text-anchor": "middle" }, "empty-note");
      note.textContent = "세션을 시작하면 시세 캔들이 그려집니다";
      svg.appendChild(note);
      return;
    }

    const closes = candles.map((c) => c.c);
    // 표시 이동평균 1/5/10/20/50 — MA1은 종가 연결선(캔들이 흔들려도 흐름이 보이게),
    // 100/200일선은 판단용으로만 계산(차트 창이 60봉이라 미표시)
    const maLines = [[1, "ma1"], [5, "ma5"], [10, "ma10"], [20, "ma20"], [50, "ma50"]]
      .map(([p, cls]) => [movingAvg(closes, p), cls]);
    let min = Math.min(...candles.map((c) => c.l));
    let max = Math.max(...candles.map((c) => c.h));
    for (const [arr] of maLines) {
      for (const v of arr) { if (v != null) { min = Math.min(min, v); max = Math.max(max, v); } }
    }
    const pad = (max - min || 1) * 0.08;
    min -= pad; max += pad;

    const plotH = CH.h - CH.padT - CH.padB;
    const plotW = CH.w - CH.padL - CH.padR;
    const y = (v) => CH.padT + plotH * (1 - (v - min) / (max - min));
    const step = plotW / Math.max(candles.length, 12);   // 캔들이 적어도 과하게 넓어지지 않게
    const x = (i) => CH.padL + step * (i + 0.5);
    const bodyW = Math.max(2, Math.min(16, step * 0.6));

    // 가로 격자 + 오른쪽 가격 눈금
    for (let k = 0; k <= 4; k++) {
      const v = max - ((max - min) * k) / 4;
      const yy = y(v);
      svg.appendChild(svgNode("line", { x1: CH.padL, x2: CH.padL + plotW, y1: yy, y2: yy }, "grid-line"));
      const label = svgNode("text", { x: CH.padL + plotW + 6, y: yy + 4 }, "axis-label");
      label.textContent = v.toFixed(2);
      svg.appendChild(label);
    }

    // 하단 날짜 눈금 (일봉 재생) — 처음·중간·끝 3개
    if (isDaily && candles.length > 1) {
      const marks = [0, Math.floor(candles.length / 2), candles.length - 1];
      for (const i of new Set(marks)) {
        if (!candles[i].t) continue;
        const lbl = svgNode("text", {
          x: x(i), y: CH.h - 3, "text-anchor": i === 0 ? "start" : i === candles.length - 1 ? "end" : "middle",
        }, "axis-label");
        lbl.textContent = candles[i].t.slice(5);  // MM-DD
        svg.appendChild(lbl);
      }
    }

    // 캔들 — 심지(고가~저가) + 몸통(시가~종가), 종가 ≥ 시가면 양봉
    candles.forEach((c, i) => {
      const cls = (c.c >= c.o ? "candle-up" : "candle-down") + (c.w ? " candle-warmup" : "");
      const cx = x(i);
      svg.appendChild(svgNode("line", { x1: cx, x2: cx, y1: y(c.h), y2: y(c.l), "stroke-width": 1 }, cls));
      const top = y(Math.max(c.o, c.c));
      svg.appendChild(svgNode("rect", {
        x: cx - bodyW / 2, y: top, width: bodyW,
        height: Math.max(1, Math.abs(y(c.o) - y(c.c))),
      }, cls));
    });

    // 이동평균선 — 값이 채워진 구간만 그린다
    for (const [arr, cls] of maLines) {
      const pts = arr.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
        .filter(Boolean).join(" ");
      if (pts.split(" ").length > 1) svg.appendChild(svgNode("polyline", { points: pts }, cls));
    }

    // 현재가 기준선
    const last = closes[closes.length - 1];
    svg.appendChild(svgNode("line", {
      x1: CH.padL, x2: CH.padL + plotW, y1: y(last), y2: y(last),
    }, "last-price"));
  }

  function renderPriceChange(current) {
    const p = num(current);
    // 등락 기준: 실데이터 재생 = 전일(직전 봉) 종가, 목 시세 = 세션 시작가
    const base = changeBasis === "prev-close" ? prevClose : sessionOpen;
    if (!base || !p) { el.priceChange.textContent = "—"; el.priceChange.className = ""; return; }
    const diff = p - base, pct = (diff / base) * 100;
    el.priceChange.textContent =
      `${diff >= 0 ? "▲ +" : "▼ "}${diff.toFixed(2)} USDC (${diff >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
    el.priceChange.className = diff > 0 ? "pos" : diff < 0 ? "neg" : "";
  }

  // ---------- 피드 렌더 ----------
  function addDecision(d) {
    const li = make("li");
    li.appendChild(make("time", null, timeOf(d.ts)));
    if (sessionSymbols.length > 1 && d.symbol) li.appendChild(make("span", "sym", d.symbol));
    li.appendChild(make("span", "src src-" + (d.source || "rule"), d.source));
    li.appendChild(make("span", "act act-" + d.action, d.action.toUpperCase()));
    li.appendChild(make("span", null, `@ ${d.price} — ${d.reason}`));
    el.decisionFeed.prepend(li);
    capList(el.decisionFeed, MAX_FEED_ITEMS);
  }

  function addLog(ts, text, cls) {
    const li = make("li", cls || null);
    li.appendChild(make("time", null, timeOf(ts)));
    li.appendChild(make("span", null, text));
    el.eventLog.prepend(li);
    capList(el.eventLog, MAX_LOG_ITEMS);
  }

  /* ---------- 세션 경계 ----------
     새로고침하면 SSE 히스토리가 처음부터 재생돼 이전 세션 로그가 현재 피드에 섞인다.
     세션이 바뀌는 지점에 구분선을 넣고 이전 세션 항목은 흐리게 처리해
     "중간에 갑자기 판단 출처가 바뀐 것"처럼 보이는 혼동을 없앤다. */
  function addDivider(listEl, ts, text) {
    const li = make("li", "session-divider");
    li.appendChild(make("time", null, timeOf(ts)));
    li.appendChild(make("span", null, text));
    listEl.prepend(li);
  }

  function sessionBoundary(ts, text, markPast) {
    for (const listEl of [el.decisionFeed, el.eventLog]) {
      if (markPast) {
        for (const li of listEl.children) li.classList.add("past-session");
      }
      addDivider(listEl, ts, text);
    }
    capList(el.decisionFeed, MAX_FEED_ITEMS);
    capList(el.eventLog, MAX_LOG_ITEMS);
  }

  function addTradeRow(t) {
    const empty = el.tradesBody.querySelector(".empty-row");
    if (empty) empty.remove();
    const tr = make("tr");
    tr.appendChild(make("td", null, timeOf(t.ts)));
    tr.appendChild(make("td", "sym", t.symbol || "—"));   // 멀티: 어느 종목 체결인지
    tr.appendChild(make("td", "side-" + t.side, t.side === "buy" ? "매수" : "매도"));
    tr.appendChild(make("td", null, t.quantity));
    tr.appendChild(make("td", null, t.price_usdc));
    const feeTd = make("td", null, t.fee_usdc !== undefined ? (t.side === "buy" ? "+" : "−") + t.fee_usdc : "—");
    feeTd.title = t.subtotal_usdc !== undefined
      ? (t.side === "buy" ? `소계 ${t.subtotal_usdc} + 수수료 ${t.fee_usdc} = ${t.total_usdc}`
                          : `소계 ${t.subtotal_usdc} − 수수료 ${t.fee_usdc} = 수령 ${t.total_usdc}`)
      : "";
    tr.appendChild(feeTd);
    tr.appendChild(make("td", null, t.total_usdc + (t.realized_pnl_usdc ? ` (실현 ${num(t.realized_pnl_usdc) >= 0 ? "+" : ""}${t.realized_pnl_usdc})` : "")));
    const srcTd = make("td");
    srcTd.appendChild(make("span", "src src-" + (t.decision_source || "rule"), t.decision_source));
    srcTd.title = t.decision_reason || "";
    tr.appendChild(srcTd);
    tr.appendChild(make("td", null, t.confirmed ? "온체인 확정" : (t.status === "settled" ? "드라이런(미전송)" : "실패")));
    tr.appendChild(txCell(t.explorer_payment));
    tr.appendChild(txCell(t.explorer_delivery));
    el.tradesBody.prepend(tr);
  }

  function txCell(url) {
    const td = make("td");
    if (url) {
      const a = make("a", null, "explorer ↗");
      a.href = url; a.target = "_blank"; a.rel = "noopener";
      td.appendChild(a);
    } else {
      td.textContent = "—";
    }
    return td;
  }

  // ---------- B2 데일리 브리핑 ----------
  const TRIGGER_LABEL = { "manual": "수동", "session-end": "세션 종료 자동", "market-close": "장 마감 자동" };
  function renderBriefing(b) {
    el.briefingMeta.textContent =
      `${timeOf(b.ts)} 생성 · ${TRIGGER_LABEL[b.trigger] || b.trigger} · 출처 ${b.source === "gemini" ? "Gemini" : "템플릿 폴백"}` +
      (b.archive ? ` · 저장 ${b.archive}` : "");
    el.briefingText.textContent = b.text || "";
  }

  // ---------- A4 거래 알림 (토스트 + Web Notification) ----------
  const NOTIFY_KEY = "autotrader_notify";
  const DENY_HELP = "브라우저가 이 사이트의 알림을 차단한 상태입니다 — 주소창 왼쪽 자물쇠(ⓘ) → 사이트 설정 → 알림을 '허용'으로 바꾸고 새로고침하세요. (Windows 알림이 꺼져 있어도 표시되지 않습니다)";

  /* 버튼 상태 = 저장값 + 실제 브라우저 권한을 합친 결과.
     저장값만 보면 권한이 나중에 차단됐을 때 라벨이 바뀌지 않아
     "눌러도 아무 일이 없는" 버튼이 된다(사용자 보고 버그). */
  function notifyState() {
    if (!("Notification" in window)) return "unsupported";
    if (Notification.permission === "denied") return "denied";
    if (localStorage.getItem(NOTIFY_KEY) !== "on") return "off";
    return Notification.permission === "granted" ? "on" : "off";
  }
  const notifyEnabled = () => notifyState() === "on";

  const NOTIFY_LABEL = {
    on: "🔔 알림: 켜짐", off: "🔔 알림: 꺼짐",
    denied: "🔕 알림: 차단됨", unsupported: "🔕 알림: 미지원",
  };

  function renderNotifyBtn() {
    const st = notifyState();
    el.btnNotify.textContent = NOTIFY_LABEL[st];
    el.btnNotify.title =
      st === "denied" ? DENY_HELP
      : st === "unsupported" ? "이 브라우저는 Web Notification 을 지원하지 않습니다 — 인앱 토스트만 표시됩니다."
      : st === "on" ? "탭이 백그라운드일 때 체결·거부·정지 이벤트를 브라우저 알림으로 받습니다 (누르면 끕니다)"
      : "누르면 브라우저 알림 권한을 요청합니다 (탭이 백그라운드일 때만 알림)";
  }

  function toast(title, body, cls) {
    const t = make("div", "toast" + (cls ? " toast-" + cls : ""));
    t.appendChild(make("strong", null, title));
    t.appendChild(make("span", null, body));
    el.toasts.appendChild(t);
    while (el.toasts.children.length > 4) el.toasts.removeChild(el.toasts.firstChild);
    setTimeout(() => t.remove(), 5000);
  }

  function notify(evt, title, body, cls) {
    // 새로고침 시 SSE 히스토리 재전송분은 알림 제외 (피드 복원만)
    const t = Date.parse(evt.ts);
    if (!isNaN(t) && t < pageLoadedAt - 2000) return;
    if (document.hidden) {
      // 백그라운드 탭 — 브라우저 알림 (켜져 있을 때)
      if (notifyEnabled()) {
        const n = new Notification("AutoTrader — " + title, { body });
        n.onclick = () => { window.focus(); n.close(); };
      }
    } else {
      toast(title, body, cls);  // 보고 있는 탭 — 인앱 토스트
    }
  }

  // 어떤 분기로 가든 라벨 갱신 + 토스트 피드백 — 눌렀는데 아무 반응 없는 경우를 없앤다
  el.btnNotify.addEventListener("click", async () => {
    const st = notifyState();
    if (st === "unsupported") {
      toast("알림 미지원", "이 브라우저는 브라우저 알림을 지원하지 않습니다 — 화면 토스트로만 알려드립니다.", "danger");
    } else if (st === "denied") {
      localStorage.setItem(NOTIFY_KEY, "off");
      toast("알림이 차단되어 있습니다", DENY_HELP, "danger");
    } else if (st === "on") {
      localStorage.setItem(NOTIFY_KEY, "off");
      toast("알림 꺼짐", "브라우저 알림을 껐습니다 — 화면 토스트는 계속 표시됩니다.");
    } else {
      let perm = Notification.permission;
      if (perm !== "granted") {
        try { perm = await Notification.requestPermission(); }
        catch (e) { perm = Notification.permission; }
      }
      if (perm === "granted") {
        localStorage.setItem(NOTIFY_KEY, "on");
        toast("알림 켜짐", "탭이 백그라운드일 때 체결·거부·정지 이벤트를 브라우저 알림으로 받습니다.", "ok");
      } else if (perm === "denied") {
        localStorage.setItem(NOTIFY_KEY, "off");
        toast("알림 권한 거부됨", DENY_HELP, "danger");
      } else {
        toast("알림 켜지 않음", "권한 요청 창을 닫으셨습니다 — 다시 누르면 재요청합니다.", "danger");
      }
    }
    renderNotifyBtn();
  });

  // 사이트 설정에서 권한을 바꾸면(다른 탭·설정창) 버튼 표시를 따라 갱신
  document.addEventListener("visibilitychange", () => { if (!document.hidden) renderNotifyBtn(); });
  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: "notifications" })
      .then((p) => { p.onchange = renderNotifyBtn; })
      .catch(() => { /* 미지원 브라우저 — 무시 */ });
  }

  // ---------- SSE 이벤트 처리 ----------
  function handleEvent(evt) {
    const d = evt.data || {};
    switch (evt.type) {
      case "price_tick": {
        // 종목별 최신가를 캐시에 반영하고 요약 표를 갱신 (모든 종목)
        if (d.symbol && perSymbol[d.symbol] && perSymbol[d.symbol].price) {
          perSymbol[d.symbol].price.current = d.price;
        }
        renderSymbolStrip();
        // 포커스 종목만 가격 카드·차트·평가손익을 갱신 (다른 종목 틱은 표만 갱신)
        if (!d.symbol || d.symbol === focusSymbol) {
          el.price.textContent = d.price + " USDC";
          el.tickInfo.textContent = `틱 ${d.tick}`
            + (d.date ? ` · ${d.date}` : "")
            + (d.progress ? ` (${d.progress.played}/${d.progress.total}봉)` : "");
          if (d.prev_close != null) prevClose = num(d.prev_close);
          if (d.bar) pushBarCandle(d.bar);        // 실데이터: 1틱 = 1봉 (실제 OHLC)
          else pushTickToCandle(num(d.price));    // 목 시세: N틱 집계
          // 포커스 종목의 누적 캔들을 캐시에 되써서, 포커스 전환 재구성이 최신 봉을 읽게 한다
          // (안 하면 마지막 fetchState 시점 캔들로 되감김). 키는 서버 캔들 형태로 맞춘다.
          if (perSymbol[focusSymbol] && perSymbol[focusSymbol].price) {
            perSymbol[focusSymbol].price.candles = candles.map((c) => ({
              open: c.o, high: c.h, low: c.l, close: c.c, count: c.n, ts: c.t, warmup: c.w,
            }));
          }
          drawChart();
          renderPriceChange(d.price);
          // 평가손익 즉시 갱신은 단일 종목만 (멀티 합산은 체결 시 fetchState 로 갱신)
          if (sessionSymbols.length <= 1) renderValuation(valuationAtPrice(num(d.price)));
        }
        break;
      }
      case "replay_ended":
        addLog(evt.ts, `[재생 완료] ${d.message} (${d.bars_played}봉, 마지막 ${d.last_date})`, "log-ok");
        notify(evt, "실데이터 재생 완료", "데이터 마지막 봉까지 재생해 세션을 자동 종료합니다.", "ok");
        break;
      case "decision":
        addDecision(d);
        break;
      case "quote": {
        const feePart = d.fee_usdc !== undefined
          ? (d.side === "buy" ? `${d.subtotal_usdc} + 수수료 ${d.fee_usdc} = 총 ${d.total_usdc}`
                              : `${d.subtotal_usdc} − 수수료 ${d.fee_usdc} = 수령 ${d.total_usdc}`)
          : `${d.total_usdc}`;
        addLog(evt.ts, `[A2A 견적] (${d.side === "buy" ? "매수" : "매도"}) ${d.request} → ${d.quantity} ${d.symbol} @ ${d.price_usdc} = ${feePart} USDC`);
        break;
      }
      case "x402_required":
        addLog(evt.ts, `[x402 ① 요구] ${d.order_id} — ${d.resource} · 수취 ${shortKey(d.pay_to)}`, "log-muted");
        break;
      case "x402_submitted":
        addLog(evt.ts, `[x402 ② 제출] ${d.order_id} — 서명 트랜잭션 제출${d.remaining_usdc !== undefined ? ` (AP2 승인, 잔여 예산 ${d.remaining_usdc} USDC)` : ""}`, "log-muted");
        break;
      case "x402_completed":
        addLog(evt.ts, `[x402 ③ 완결] ${d.order_id} — ${d.status}${d.confirmed ? " · 온체인 확정" : ""}`, d.status === "settled" ? "log-ok" : "log-danger");
        break;
      case "trade":
        addTradeRow(d);
        notify(evt, d.side === "buy" ? "매수 체결" : "매도 체결",
          `${d.quantity} ${d.symbol} @ ${d.price_usdc} · ${d.side === "buy" ? "총" : "수령"} ${d.total_usdc} USDC` +
          (d.realized_pnl_usdc ? ` (실현 ${num(d.realized_pnl_usdc) >= 0 ? "+" : ""}${d.realized_pnl_usdc})` : ""),
          d.status === "settled" ? "ok" : "danger");
        fetchState(); // 포지션·예산·손익 카드 갱신
        break;
      case "mandate_rejected":
        addLog(evt.ts, `[AP2 거부] ${d.order_id} — ${d.reason}`, "log-danger");
        notify(evt, "AP2 거부", d.reason, "danger");
        break;
      case "mandate_updated":
        addLog(evt.ts, `[AP2 한도 변경] 예산 ${d.old.budget_total_usdc}→${d.new.budget_total_usdc} · 건별 ${d.old.per_trade_max_usdc}→${d.new.per_trade_max_usdc} USDC (${d.applied === "immediate" ? "재서명·즉시 적용" : "다음 세션부터 적용"} · 주체: ${d.actor})`, "log-ok");
        fetchState();
        break;
      case "trading_paused":
        addLog(evt.ts, `[긴급정지] 신규 판단·결제 중단 (주체: ${d.actor})`, "log-danger");
        notify(evt, "긴급정지", `신규 판단·결제 중단 (주체: ${d.actor})`, "danger");
        fetchState();
        break;
      case "trading_resumed":
        addLog(evt.ts, `[재개] 매매 재개 (주체: ${d.actor})`, "log-ok");
        notify(evt, "매매 재개", `주체: ${d.actor}`, "ok");
        fetchState();
        break;
      case "engine_started": {
        const st = d.strategy || {};
        let stText, srcNote;
        if (st.type === "dca") {
          stText = `적립형(${dcaSchedule(st)} ${st.dca_amount_usdc} USDC)`;
          srcNote = "판단 출처 dca — 적립 스케줄이 매수, Gemini 미사용";
        } else if (st.type === "trend") {
          stText = `추세추종(${st.trend_signal_label || st.trend_signal} · 올인/올아웃)`;
          srcNote = `판단 출처 rule — 추세 신호(${st.trend_signal_label || st.trend_signal})로 전량 진입·청산, Gemini 미사용`;
        } else {
          stText = `조건형(${st.decision_mode === "trend" ? "AI 추세·보류 재량" : "AI 엄격"}${st.ta_mode ? "+TA" : ""})`;
          srcNote = "판단 출처 gemini / rule";
        }
        sessionBoundary(evt.ts, `─── 새 세션 시작 · ${stText} · ${srcNote} ───`, true);
        addLog(evt.ts, `[세션 시작] ${d.mode === "live" ? "라이브" : "드라이런"} · ${d.network} · ${d.symbol} · 시세: ${d.feed ? d.feed.label : "—"} · 전략: ${stText} · 판단: ${d.brain} · AP2 mandate 서명검증 ${d.mandate_verified ? "OK" : "FAIL"}`, "log-ok");
        fetchState();
        break;
      }
      case "engine_stopped":
        addLog(evt.ts, `[세션 종료] 틱 ${d.ticks} · 체결 ${d.trades}건` +
          (d.was_paused ? " · 긴급정지 상태로 종료 → 정지 해제됨(다음 세션은 매매 활성으로 시작)" : "") +
          (d.archive ? ` · 증빙 ${d.archive}` : "") +
          (d.cross_check ? ` · 교차검증 USDC ${d.cross_check.usdc_ok ? "PASS" : "FAIL"} / 주식 ${d.cross_check.stock_ok ? "PASS" : "FAIL"}` : ""),
          d.cross_check && !(d.cross_check.usdc_ok && d.cross_check.stock_ok) ? "log-danger" : "log-ok");
        sessionBoundary(evt.ts, "─── 세션 종료 ───", false);
        fetchState();
        break;
      case "balances":
        addLog(evt.ts, `[온체인 잔액·${d.stage === "before" ? "시작" : "종료"}] trading: ${d.balances.trading.usdc} USDC / ${d.balances.trading.stock} 주 · broker: ${d.balances.broker.usdc} USDC / ${d.balances.broker.stock} 주`, "log-muted");
        break;
      case "briefing":
        renderBriefing(d);
        addLog(evt.ts, `[브리핑] ${TRIGGER_LABEL[d.trigger] || d.trigger} 생성 (출처: ${d.source})${d.archive ? ` · ${d.archive}` : ""}`, "log-ok");
        notify(evt, "데일리 브리핑 도착", (d.text || "").slice(0, 80) + ((d.text || "").length > 80 ? "…" : ""), "ok");
        break;
      case "error":
        addLog(evt.ts, `[오류] ${d.message}`, "log-danger");
        notify(evt, "오류", d.message, "danger");
        break;
    }
  }

  // ---------- SSE 연결 ----------
  function connect() {
    const es = new EventSource("/api/events?since=" + lastEventId);
    es.onopen = () => { el.connStatus.textContent = "실시간 연결됨 (SSE)"; };
    es.onerror = () => { el.connStatus.textContent = "연결 끊김 — 재연결 중…"; };
    es.onmessage = (m) => {
      try {
        const evt = JSON.parse(m.data);
        lastEventId = evt.id;
        handleEvent(evt);
      } catch (e) { /* 형식 오류 무시 */ }
    };
  }

  // ---------- 컨트롤 ----------
  // 조작 API 접근 토큰 — 배포 서버가 CONTROL_TOKEN 을 켠 경우에만 필요하다.
  // 주소창에 #token=... 로 한 번 넣으면 저장하고 URL 에서 지운다(공유·녹화 시 노출 방지).
  const TOKEN_KEY = "autotrader_control_token";
  (function captureTokenFromHash() {
    const m = location.hash.match(/(?:^|[#&])token=([^&]+)/);
    if (!m) return;
    localStorage.setItem(TOKEN_KEY, decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + location.search);
  })();

  async function post(url, body) {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) headers["X-Control-Token"] = token;
    const r = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* 그대로 */ }
      if (r.status === 401) {
        msg = "조작 권한이 없습니다. 주소 끝에 #token=<접근토큰> 을 붙여 한 번 접속하세요.";
      }
      alert(msg);
      return null;
    }
    return r.json();
  }

  function syncDcaInputs() {
    const strat = el.strategySelect.value;
    el.dcaParams.classList.toggle("hidden", strat !== "dca");
    // 조건형만 AI 판단 모드·TA 보강 노출, 추세추종만 추세 신호 노출
    el.decisionMode.classList.toggle("hidden", strat !== "condition");
    el.taWrap.classList.toggle("hidden", strat !== "condition");
    el.trendSignal.classList.toggle("hidden", strat !== "trend");
    // 데이터셋(일봉/하락장)은 실데이터 재생일 때만 의미
    const replay = el.feedSelect.value === "replay";
    el.feedDataset.classList.toggle("hidden", !replay);
    // 멀티 종목 선택은 실데이터 재생에서만. 추세추종(올인)·라이브(온체인)는 단일만이라 잠근다.
    const isTrend = strat === "trend";
    const isLive = el.modeSelect.value === "live";
    const singleOnly = isTrend || isLive;
    el.symPicker.classList.toggle("hidden", !replay);
    for (const c of document.querySelectorAll("[data-sym-check]")) {
      c.disabled = singleOnly;
      if (singleOnly) c.checked = false;
    }
    el.symPickerLabel.textContent = isLive
      ? "라이브(온체인)는 단일 종목만 지원합니다 — 멀티 종목은 드라이 전용"
      : isTrend
        ? "추세추종은 단일 종목만 지원합니다 (여러 종목이 예산을 독식)"
        : "동시 매수 종목 (여러 개 = 멀티 · 비우면 기본 단일):";
    const unit = el.dcaUnit.value;
    el.dcaTicksWrap.classList.toggle("hidden", unit !== "ticks");
    el.dcaMinutesWrap.classList.toggle("hidden", unit !== "minutes");
    el.dcaTimeWrap.classList.toggle("hidden", unit !== "daily");
  }
  el.strategySelect.addEventListener("change", syncDcaInputs);
  el.feedSelect.addEventListener("change", syncDcaInputs);
  el.modeSelect.addEventListener("change", syncDcaInputs);   // 라이브 전환 시 종목 단일 잠금
  el.dcaUnit.addEventListener("change", syncDcaInputs);
  el.focusSelect.addEventListener("change", () => setFocus(el.focusSelect.value));
  syncDcaInputs();   // 초기 1회 — 기본 전략/피드에 맞춰 표시 정리
  function pickedSymbols() {
    // 멀티 종목은 실데이터 재생에서만 — 체크된 티커 목록(비면 단일 기본)
    if (el.feedSelect.value !== "replay") return [];
    return Array.from(document.querySelectorAll("[data-sym-check]:checked")).map((c) => c.value);
  }

  el.btnStart.addEventListener("click", async () => {
    el.btnStart.disabled = true;
    focusSymbol = null;   // 새 세션 — 포커스는 첫 종목으로 재설정
    const s = await post("/api/engine/start", {
      mode: el.modeSelect.value,
      tick_interval_sec: parseFloat(el.speedSelect.value),
      feed: { type: el.feedSelect.value, dataset: el.feedDataset.value, symbols: pickedSymbols() },
      strategy: {
        type: el.strategySelect.value,
        decision_mode: el.decisionMode.value,
        trend_signal: el.trendSignal.value,
        ta_mode: el.taMode.checked,
        dca_unit: el.dcaUnit.value,
        dca_every_ticks: parseInt(el.dcaTicks.value, 10) || 5,
        dca_every_minutes: parseInt(el.dcaMinutes.value, 10) || 60,
        dca_at_time: el.dcaTime.value || "09:00",
        dca_amount_usdc: el.dcaAmount.value || "10",
      },
    });
    if (s) renderState(s); else el.btnStart.disabled = false;
  });
  el.btnStop.addEventListener("click", async () => {
    el.btnStop.disabled = true;
    const s = await post("/api/engine/stop");
    if (s) renderState(s);
  });
  el.btnPause.addEventListener("click", async () => {
    const s = await post("/api/trading/pause", { actor: "human" });
    if (s) renderState(s);
  });
  el.btnResume.addEventListener("click", async () => {
    const s = await post("/api/trading/resume", { actor: "human" });
    if (s) renderState(s);
  });
  el.mandateForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const s = await post("/api/mandate", {
      budget_total_usdc: el.mandateBudget.value,
      per_trade_max_usdc: el.mandatePerTrade.value,
      actor: "human",
    });
    if (s) renderState(s);
  });
  el.btnBriefing.addEventListener("click", async () => {
    el.btnBriefing.disabled = true;
    el.briefingMeta.textContent = "브리핑 생성 중… (Gemini 호출)";
    const b = await post("/api/briefing");
    if (b) renderBriefing(b);
    else el.briefingMeta.textContent = "브리핑 생성 실패 — 세션 데이터가 있는지 확인하세요.";
    el.btnBriefing.disabled = false;
  });

  /* ---------- 카드 모듈 배치 ----------
     모든 기능 카드는 data-card 모듈이다. 순서의 단일 출처는 DEFAULT_LAYOUT —
     디자인 시안의 배치가 어떻게 오든 이 배열만 바꾸면 기본 배치가 바뀐다.
     사용자는 카드 제목(h2)을 끌어 재배치할 수 있고 localStorage 에 저장된다.
     (HTML5 드래그 앤 드롭 — 데스크톱 전용, 터치는 기본 배치 사용) */
  const LAYOUT_KEY = "autotrader_layout_v1";
  const DEFAULT_LAYOUT = ["price", "symbols", "session", "position", "budget", "pnl", "valuation",
                          "mandate", "decisions", "log", "briefing", "trades"];

  const cardEls = () => Array.from(el.grid.querySelectorAll("[data-card]"));

  function applyLayout(order) {
    const map = {};
    cardEls().forEach((c) => { map[c.dataset.card] = c; });
    const seen = new Set();
    for (const id of order) {
      if (map[id] && !seen.has(id)) { el.grid.appendChild(map[id]); seen.add(id); }
    }
    for (const id of Object.keys(map)) {
      if (!seen.has(id)) el.grid.appendChild(map[id]);  // 새로 생긴 카드는 맨 뒤로
    }
  }

  function saveLayout() {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(cardEls().map((c) => c.dataset.card)));
  }

  function loadLayout() {
    try {
      const v = JSON.parse(localStorage.getItem(LAYOUT_KEY));
      if (Array.isArray(v) && v.length) return v;
    } catch (e) { /* 손상된 저장값 — 기본 배치로 */ }
    return DEFAULT_LAYOUT;
  }

  let draggedCard = null;
  function initCardDrag() {
    for (const card of cardEls()) {
      const handle = card.querySelector("h2");
      if (!handle) continue;
      handle.classList.add("drag-handle");
      handle.title = "잡아 끌어 카드 위치를 바꿀 수 있습니다";
      // 제목을 누른 동안만 카드가 draggable — 카드 안 텍스트 선택·스크롤과 충돌 방지
      handle.addEventListener("mousedown", () => card.setAttribute("draggable", "true"));
      card.addEventListener("dragstart", (e) => {
        draggedCard = card;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        try { e.dataTransfer.setData("text/plain", card.dataset.card); } catch (err) { /* 일부 브라우저 */ }
      });
      card.addEventListener("dragend", () => {
        card.removeAttribute("draggable");
        card.classList.remove("dragging");
        draggedCard = null;
        cardEls().forEach((c) => c.classList.remove("drop-target"));
        saveLayout();
      });
      card.addEventListener("dragover", (e) => {
        if (!draggedCard || draggedCard === card) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        card.classList.add("drop-target");
      });
      card.addEventListener("dragleave", () => card.classList.remove("drop-target"));
      card.addEventListener("drop", (e) => {
        if (!draggedCard || draggedCard === card) return;
        e.preventDefault();
        card.classList.remove("drop-target");
        const cards = cardEls();
        const from = cards.indexOf(draggedCard), to = cards.indexOf(card);
        el.grid.insertBefore(draggedCard, from < to ? card.nextSibling : card);
        saveLayout();
      });
    }
    // 드래그 없이 제목만 클릭했다 뗀 경우 draggable 잔류 제거
    document.addEventListener("mouseup", () => {
      if (!draggedCard) cardEls().forEach((c) => c.removeAttribute("draggable"));
    });
  }

  el.btnLayoutReset.addEventListener("click", () => {
    localStorage.removeItem(LAYOUT_KEY);
    applyLayout(DEFAULT_LAYOUT);
    toast("배치 초기화", "카드 배치를 기본값으로 되돌렸습니다.");
  });

  // ---------- 시작 ----------
  applyLayout(loadLayout());
  initCardDrag();
  renderNotifyBtn();
  fetchState();
  connect();
})();
