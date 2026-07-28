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
    guardAttempts: $("[data-guard-attempts]"),
    guardBlocked: $("[data-guard-blocked]"),
    guardAp2: $("[data-guard-ap2]"),
    guardLeak: $("[data-guard-leak]"),
    guardLegs: $("[data-guard-legs]"),
    guardUnverified: $("[data-guard-unverified]"),
    aiBrain: $("[data-ai-brain]"),
    aiShare: $("[data-ai-share]"),
    aiGated: $("[data-ai-gated]"),
    aiFallbacks: $("[data-ai-fallbacks]"),
    aiSources: $("[data-ai-sources]"),
    aiSemChecked: $("[data-ai-sem-checked]"),
    aiSemBlocked: $("[data-ai-sem-blocked]"),
    aiSemUnverified: $("[data-ai-sem-unverified]"),
    aiSemNote: $("[data-ai-sem-note]"),
    modeSelect: $("[data-mode-select]"),
    feedSelect: $("[data-feed-select]"),
    feedDataset: $("[data-feed-dataset]"),
    subBars: $("[data-sub-bars]"),
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
    historyBody: $("[data-history-body]"),
    historyNote: $("[data-history-note]"),
    connStatus: $("[data-conn-status]"),
    wallets: $("[data-wallets]"),
    walletTrading: $("[data-wallet-trading]"),
    walletBroker: $("[data-wallet-broker]"),
    sessionCard: $('[data-card="session"]'),
    adv: $("[data-adv]"),
    btnNotify: $("[data-btn-notify]"),
    toasts: $("[data-toasts]"),
    btnBriefing: $("[data-btn-briefing]"),
    briefingMeta: $("[data-briefing-meta]"),
    briefingText: $("[data-briefing-text]"),
    grid: $("main.grid"),
    btnLayoutReset: $("[data-btn-layout-reset]"),
    slimBar: $("[data-slim-bar]"),
    slimPause: $("[data-slim-pause]"),
    slimResume: $("[data-slim-resume]"),
    guardDock: $("[data-guard-dock]"),
    guardPanel: $("[data-guard-panel]"),
    guardTab: $("[data-guard-toggle]"),
    guardHit: $("[data-guard-hit]"),
    guardHitText: $("[data-guard-hit-text]"),
    guardHitMore: $("[data-guard-hit-more]"),
    aiMoreBtns: Array.from(document.querySelectorAll("[data-ai-more]")),
    logFilterNote: $("[data-log-filter-note]"),
    logFilterClear: $("[data-log-filter-clear]"),
    logFilterEmpty: $("[data-log-filter-empty]"),
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
  let lastEngineStatus = "";        // 실행→대기 전환 감지용 (세션 이력 갱신 시점)
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

  /* ---------- 지난 세션 이력 (P1-3) ----------
     서버에는 세션 기록이 쌓여 있는데 화면이 한 줄도 안 읽어서, 첫 방문자에게는
     모든 카드가 0 인 "동작 안 하는 목업"으로 보였다. 인증도 필요 없는 GET 이다.

     ⚠ 열을 늘리지 말 것. 세션 레코드에 유출 계열 필드가 없어서 '유출' 열은 undefined 가
     그대로 나가고, '실현손익' 열은 "수익률이 아니라 지출 통제"라는 이 제품의 첫 문장과
     정면으로 충돌한다(같은 이유로 랜딩에서 수익률 카드를 이미 치웠다). */
  const HISTORY_LIMIT = 10;

  /* 세션ID(`20260727_135727_dry`)는 기계 식별자라 표 첫 칸에 그대로 두면 이 카드 전체가
     "개발 로그"로 읽힌다. 사람이 읽는 말로 바꾸되 원문은 셀의 title 로 남긴다(축④ 증거).
     형식이 다르면 원문을 그대로 쓴다 — 못 읽는 것보다 낫다. */
  function sessionLabel(id) {
    if (!id) return "—";
    const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(id);
    return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]} 세션` : id;
  }

  function renderHistory(data) {
    const rows = (data && data.sessions) || [];
    el.historyBody.replaceChildren();
    if (!data || !data.enabled) {
      el.historyNote.textContent = "— 이 인스턴스는 영속화가 꺼져 있습니다";
      el.historyBody.appendChild(emptyRow("영속화 비활성 — 세션 기록을 저장하지 않는 실행입니다."));
      return;
    }
    if (!rows.length) {
      el.historyNote.textContent = "— 아직 기록이 없습니다";
      el.historyBody.appendChild(emptyRow("아직 저장된 세션이 없습니다 — 세션을 한 번 실행하면 여기에 남습니다."));
      return;
    }
    el.historyNote.textContent = `— 최근 ${rows.length}건 (서버에 저장된 실행 기록)`;
    for (const s of rows) {
      const tr = make("tr");
      const share = s.ai && s.ai.gemini_share_pct;
      const cells = [
        sessionLabel(s.session_id),
        // 모드 이름은 실행 모드 드롭다운(S1)과 같은 말을 쓴다 — 같은 것을 두 이름으로
        // 부르면 심사위원이 다른 기능으로 읽는다('드라이런'은 개발 용어라 걷어냈다).
        s.mode === "live" ? "라이브(온체인)" : "샌드박스",
        s.symbol || "—",
        String(num(s.ticks)),
        String(num(s.trade_count)),
        share === undefined || share === null ? "—" : `${share}%`,
        (s.started_at || "").replace("T", " ").slice(0, 19) || "—",
      ];
      cells.forEach((c, i) => {
        const td = make("td", i === 0 ? "mono" : "", c);
        // 축④ 증거라 원문 세션ID 를 지우지 않는다 — 표에는 사람이 읽는 말, 원문은 툴팁.
        if (i === 0 && s.session_id) td.title = s.session_id;
        tr.appendChild(td);
      });
      el.historyBody.appendChild(tr);
    }
  }

  function emptyRow(text) {
    const tr = make("tr", "empty-row");
    const td = make("td", "", text);
    td.colSpan = 7;
    tr.appendChild(td);
    return tr;
  }

  async function fetchHistory() {
    try {
      const r = await fetch(`/api/history/sessions?limit=${HISTORY_LIMIT}`);
      renderHistory(await r.json());
    } catch (e) {
      el.historyNote.textContent = "— 불러오지 못했습니다";
      el.historyBody.replaceChildren(emptyRow("이력을 불러오지 못했습니다."));
    }
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

  /* ---------- AI 두 레이어 (state.ai) ----------
     이 카드가 말하는 것은 "AI 가 얼마나 똑똑한가"가 아니라 "AI 재량이 어디까지 열려 있는가"다.
     ①판단 레이어의 rule-gate 와 ②청구서 레이어의 의미 대조는 같은 원리로 만들어졌다 —
     둘 다 AI 는 **차단만** 할 수 있고 통과시킬 수는 없다. 숫자가 0 이어도 의미가 있다:
     '이번 세션에는 되돌릴 일이 없었다'는 뜻이지 계층이 없다는 뜻이 아니다. */
  const SOURCE_LABEL = {
    gemini: "Gemini 판단", "rule-gate": "규칙 게이트가 되돌림",
    "rule-fallback": "규칙 대체(호출 실패)", rule: "규칙", dca: "적립 스케줄",
  };

  function renderAi(ai) {
    if (!ai || !el.aiBrain) return;
    el.aiBrain.textContent = ai.brain || "—";
    el.aiShare.textContent = Math.round(num(ai.gemini_share_pct));
    el.aiGated.textContent = ai.gemini_gated;
    el.aiFallbacks.textContent = ai.rule_fallbacks;

    const parts = Object.entries(ai.by_source || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${SOURCE_LABEL[k] || k} ${v}`);
    el.aiSources.textContent = parts.length
      ? `판단 ${ai.decisions_total}건 — ` + parts.join(" · ")
      : "판단 출처: 아직 없음";

    const sem = ai.invoice_semantics || {};
    el.aiSemChecked.textContent = sem.checked || 0;
    el.aiSemBlocked.textContent = sem.blocked || 0;
    el.aiSemUnverified.textContent = sem.unverified_blocked || 0;
    el.aiSemBlocked.classList.toggle("neg", (sem.blocked || 0) > 0);

    if (!sem.enabled) {
      el.aiSemNote.textContent =
        "이 세션은 판단 두뇌가 없어 의미 대조 계층이 붙지 않았습니다 (하드 검사 8종만 작동).";
    } else {
      el.aiSemNote.textContent =
        `실제 LLM 호출 ${sem.llm_calls || 0}회 · 서식 캐시 적중 ${sem.cache_hits || 0}회` +
        (sem.unverified_skipped ? ` · 매도 ${sem.unverified_skipped}건은 검증 불가라 하드 검사만으로 진행` : "") +
        " — 하드 검사 8종을 전부 통과한 청구서만 이 검사를 받습니다.";
    }
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

  /* ---------- 세션 설정 한 문장 (.rules) ----------
     이 줄은 두 가지를 겸한다: ①고급을 접은 채 보는 "지금 설정" ②시작 버튼을 누르기 전에
     읽는 유일한 설명문.
     ⚠ 그래서 **대기 중에는 서버 값이 아니라 화면 드롭다운 값**으로 문장을 만든다.
     예전에는 대기 상태의 state.strategy.type 이 아직 condition 이라, 드롭다운이 추세추종인데
     조건형 설명이 떠 있었다 — 실제로 시작될 세션과 다른 설명을 읽히던 실측 불일치다.
     실행 중에는 지금처럼 서버 값을 쓴다(그때는 서버가 정본이다). */
  const SIGNAL_LABEL = {
    pxma20: "가격>MA20", cross_5_20: "골든크로스5/20",
    cross_1_5: "골든크로스1/5", cross_5_20_1_5: "5/20+1/5 결합",
  };

  // 라벨에서 부제(— 뒤)·괄호를 떼어낸 짧은 이름. 옵션 문구를 고쳐도 여기가 따라온다.
  function shortOpt(sel) {
    const o = sel && sel.selectedOptions && sel.selectedOptions[0];
    return o ? o.textContent.trim().split(" — ")[0].split(" (")[0].trim() : "";
  }

  function settingsSummary() {
    const parts = [shortOpt(el.modeSelect), shortOpt(el.feedSelect)];
    if (el.feedSelect.value === "replay") parts.push(shortOpt(el.feedDataset));
    parts.push(shortOpt(el.strategySelect), shortOpt(el.speedSelect));
    return parts.filter(Boolean).join(" · ");
  }

  function renderRules(s) {
    if (!s || !s.rules || !s.budget) return;
    const idle = !s.engine || s.engine.status === "idle";
    const feePct = s.fees ? (s.fees.fee_bps / 100) : 0;
    const strat = idle ? {
      type: el.strategySelect.value,
      decision_mode: el.decisionMode.value,
      trend_signal: el.trendSignal.value,
      ta_mode: el.taMode.checked,
      dca_unit: el.dcaUnit.value,
      dca_every_ticks: el.dcaTicks.value,
      dca_every_minutes: el.dcaMinutes.value,
      dca_at_time: el.dcaTime.value,
      dca_amount_usdc: el.dcaAmount.value,
    } : (s.strategy || { type: "condition" });
    const picked = idle ? pickedSymbols() : [];
    const syms = idle ? (picked.length ? picked : (s.symbol ? [s.symbol] : [])) : sessionSymbols;
    const multi = syms.length > 1;
    const symLabel = multi ? syms.join("·") : (syms[0] || s.symbol || "—");
    const modeLabel = (strat.decision_mode === "trend" ? "AI 추세·보류 재량" : "AI 엄격")
      + (strat.ta_mode ? "+TA" : "");
    // 멀티면 종목별 1회 매수 금액(총 spend/N)을 쓰고, 종목 여러 개임을 문구에 드러낸다.
    const spendText = (!idle && strat.spend_per_symbol_usdc) ? strat.spend_per_symbol_usdc
      : (multi ? (num(s.rules.spend_per_trade) / syms.length).toFixed(2) : s.rules.spend_per_trade);
    const multiTag = multi ? ` · ${syms.length}종목 동시(각자 독립 포지션, 예산·가드 공유)` : "";
    /* 전략 이름(label)과 설명(body)을 나눠 둔다. 대기 중에는 앞의 "지금 설정" 요약이 이미
       전략 이름을 말하고 있어서, label 까지 붙이면 같은 말이 한 줄에 두 번 나온다.
       실행 중 문장은 예전과 한 글자도 다르지 않다. */
    let label, body;
    if (strat.type === "dca") {
      // 적립형 종목별 금액은 회당 amount/N (조건형 spend/N 과 다르다)
      const dcaAmt = multi ? (strat.dca_amount_per_symbol_usdc || strat.dca_amount_usdc) + " USDC(종목별)"
                           : strat.dca_amount_usdc + " USDC";
      label = "적립형";
      body = `${dcaSchedule(strat)} ${dcaAmt} 정액 매수 (매도 없음)`;
    } else if (strat.type === "trend") {
      const sig = strat.trend_signal_label || SIGNAL_LABEL[strat.trend_signal] || "가격>MA20";
      label = `추세추종(${sig})`;
      body = `${symLabel} 이 상승세면 전량 보유, 하락세로 꺾이면 전량 매도(자본 보존)·재상승 시 재매수 (올인/올아웃)`;
    } else {
      label = `AI 판단(${modeLabel})`;
      body = `${symLabel} 가격이 5일 평균(MA5)보다 ${s.rules.buy_dip_pct}% 싸지면 ${spendText} USDC 어치${multi ? "(종목별)" : ""} 매수, 평균단가보다 ${s.rules.take_profit_pct}% 오르면 전량 매도(익절)`;
    }
    const perTradeText = s.budget.all_in ? "전량(올인)" : s.budget.per_trade_max_usdc;
    const tail = ` · 예산 ${s.budget.total_usdc} USDC (건별 최대 ${perTradeText})${multiTag} · 브로커 수수료 ${feePct}%`;
    el.rules.textContent = idle
      ? `지금 설정 — ${settingsSummary()} · ${body}${tail}`
      : `규칙: ${label}: ${body}${tail}`;
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
    /* 세션 이력 갱신 타이밍: ENGINE_STOPPED 는 세션 요약 **저장 전에** 나가므로
       그 이벤트에서 바로 이력을 다시 읽으면 방금 끝난 세션이 아직 없을 수 있다.
       서버는 저장이 끝난 **뒤에야** status 를 idle 로 내리므로(engine._finalize),
       실행→대기 전환을 본 시점이 문서가 확정된 시점이다. */
    if (lastEngineStatus && lastEngineStatus !== "idle" && eng.status === "idle") fetchHistory();
    lastEngineStatus = eng.status || "";
    el.net.textContent = (eng.network || "—") + (eng.mode ? " · " + (eng.mode === "live" ? "라이브" : "샌드박스") : "");
    el.engineStatus.textContent = { idle: "엔진 대기", running: "엔진 실행 중", stopping: "종료 중…" }[eng.status] || eng.status;
    el.engineStatus.classList.toggle("badge-ok", eng.status === "running");
    el.brain.textContent = "판단: " + (eng.brain || "—");
    const feePct = s.fees ? (s.fees.fee_bps / 100) : 0;
    renderRules(s);

    // 포커스 종목의 시세/포지션/차트를 그린다 (멀티 정리는 renderState 상단에서 끝냈다).
    const fp = (perSymbol[focusSymbol] && perSymbol[focusSymbol].price) || s.price || {};
    const fpos = (perSymbol[focusSymbol] && perSymbol[focusSymbol].position) || s.position || {};
    populateFocusSelect(sessionSymbols);
    el.focusWrap.classList.toggle("hidden", !multi);

    el.symbol.textContent = focusSymbol || s.symbol;
    el.posSymbol.textContent = focusSymbol || s.symbol;
    /* 온체인 실물 심볼이라 바꾸면 tx 증빙과 어긋난다 — 설명만 붙인다.
       'tAAPL' 의 t 가 무엇인지 화면 어디에도 없었다. */
    const symTip = `devnet 테스트 토큰 — ${(focusSymbol || s.symbol || "").replace(/^t/, "")} 을 토큰화한 자산`;
    el.symbol.title = symTip;
    el.posSymbol.title = symTip;
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

    // 402 Guard KPI — leak_usdc 는 "0"(정수문자열)로 올 수 있어 0.00 으로 포맷한다.
    if (s.guard) {
      el.guardAttempts.textContent = s.guard.attempts;
      el.guardBlocked.textContent = s.guard.blocked;
      // 레그별 분해 — 매수/매도 양 레그 대칭이 이 제품의 차별점이라 지표에서도 보여준다.
      if (el.guardLegs) {
        const b = s.guard.blocked_buy, sl = s.guard.blocked_sell;
        el.guardLegs.textContent = (b || sl) ? `· 매수 ${b} / 매도 ${sl}` : "";
      }
      // '검사 불가'는 blocked 의 부분집합이다 — 악성 청구서를 판정으로 막은 것이 아니라
      // 의미 대조를 못 해서(Gemini 쿼터 소진·응답 실패) 매수를 보류한 건수. 0 이면 감춘다.
      if (el.guardUnverified) {
        const u = num(s.guard.blocked_unverified);
        el.guardUnverified.textContent = u > 0 ? ` · 그중 검사 불가 ${u}` : "";
        el.guardUnverified.title = u > 0
          ? "의미 대조를 수행하지 못해 보류한 건수입니다(판정에 의한 차단이 아닙니다)." : "";
      }
      el.guardAp2.textContent = s.guard.ap2_rejected;
      const leak = num(s.guard.leak_usdc);
      el.guardLeak.textContent = leak.toFixed(2);
      el.guardLeak.classList.toggle("neg", leak > 0);  // 유출 발생 시에만 빨강(기본 녹색)
    }

    renderAi(s.ai);

    if (s.wallets.trading) el.walletTrading.textContent = shortKey(s.wallets.trading);
    if (s.wallets.broker) el.walletBroker.textContent = shortKey(s.wallets.broker);
    // 연결 전에는 대시 두 개만 남아 고장난 것처럼 보인다 — 값이 있을 때만 켠다.
    el.wallets.classList.toggle("hidden", !(s.wallets.trading || s.wallets.broker));

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
    el.subBars.disabled = running;
    // 세션 종목은 실행 중 변경 불가 · 잠금 조건은 엔진이 실제로 거부하는 것과 정확히 같게 둔다:
    // 라이브(engine.py "라이브 세션은 여러 종목을 동시에 지원하지 않습니다")와
    // 목 시세(engine.py "목 시세는 멀티 종목을 지원하지 않습니다") 두 가지뿐이다.
    // ⚠ 예전에는 여기서 추세추종도 함께 잠갔다 — 2026-07-25 에 종목별 독립 authorizer 로
    // 추세추종 멀티가 구현됐는데(engine.py multi_trend) UI 잠금만 남아, 검증에서 가장 좋았던
    // 3종목 하락장 추세추종(+77.55%) 경로를 웹에서 고를 수 없었다. 화면이 엔진보다 좁으면
    // 그만큼의 기능은 없는 것과 같다. 포커스 전환은 항상 허용.
    for (const c of document.querySelectorAll("[data-sym-check]"))
      c.disabled = running || el.modeSelect.value === "live" || el.feedSelect.value === "mock";
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
    // 얇은 헤더의 같은 버튼도 함께 맞춘다 (동작은 진짜 버튼에 위임하지만 표시는 각자 한다)
    el.slimPause.classList.toggle("hidden", !s.trading_enabled);
    el.slimResume.classList.toggle("hidden", s.trading_enabled);
    el.slimPause.disabled = !running;
    el.slimResume.disabled = !running;
    const sessionHint = running ? "" : " (세션 실행 중에만 사용할 수 있습니다)";
    el.btnPause.title = "신규 판단·결제를 즉시 중단합니다" + sessionHint;
    el.btnResume.title = "매매를 다시 시작합니다" + sessionHint;
    if (s.pause_info) {
      // 가드가 스스로 멈춘 순간이 이 제품에서 가장 중요한 화면인데, 예전에는 주체 코드
      // (`guard`)만 있고 왜 멈췄는지가 어디에도 없었다. 사유는 서버가 문장으로 준다.
      const p = s.pause_info;
      const who = p.actor_label || p.actor;
      el.pausedBadge.textContent = `🛑 매매 정지됨 (${who}, ${timeOf(p.ts)})`
        + (p.reason ? ` — ${p.reason}` : "");
      el.pausedBadge.title = p.reason || "";
    }
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

  /* kind="review" 를 붙인 줄만 ② 청구서 레이어의 "심사 내역 보기"에서 살아남는다.
     색(cls)으로 거르지 않는 이유: log-muted·log-danger 는 다른 이벤트도 함께 쓴다. */
  function addLog(ts, text, cls, kind) {
    const li = make("li", cls || null);
    if (kind) li.dataset.kind = kind;
    li.appendChild(make("time", null, timeOf(ts)));
    li.appendChild(make("span", null, text));
    el.eventLog.prepend(li);
    capList(el.eventLog, MAX_LOG_ITEMS);
    // 필터를 켜 둔 채로 새 줄이 들어오면 "0줄" 안내를 다시 판정한다 — 비어 있던 목록이
    // 방금 채워졌을 수 있다. feedPrefs 대신 DOM 클래스를 보는 이유는 이 함수가 그보다
    // 먼저 선언돼 있어서다(호출 시점엔 어차피 초기화가 끝나 있지만 참조를 안 만든다).
    if (el.eventLog.classList.contains("filter-review")) renderLogFilter();
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
    tr.appendChild(make("td", null, t.confirmed ? "온체인 확정" : (t.status === "settled" ? "샌드박스(미전송)" : "실패")));
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
  /* 메타 줄에서 두 가지를 뺐다.
     ① 저장 경로(`artifacts/briefings/…`) — Cloud Run 컨테이너 안 경로라 방문자가 열 수 없는
        죽은 문자열이었다. 지우지는 않고 title 로 내린다(이벤트 로그에는 그대로 남는다).
     ② 폴백 사유 — 서버가 본문에서 분리해 `fallback_detail` 로 따로 내려준다(web/briefing.py).
        raw 예외 문자열이라 **본문·메타에 찍지 않고 title 에만** 넣는다. */
  function renderBriefing(b) {
    el.briefingMeta.textContent =
      `${timeOf(b.ts)} 생성 · ${TRIGGER_LABEL[b.trigger] || b.trigger} · 출처 ${b.source === "gemini" ? "Gemini" : "자동 계산 요약"}`;
    el.briefingMeta.title = [
      b.fallback_detail ? `AI 요약 실패 사유: ${b.fallback_detail}` : "",
      b.archive ? `저장 ${b.archive}` : "",
    ].filter(Boolean).join("\n");
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

  /* ⚠ '차단됨'을 쓰지 않는다 — 같은 화면에 '가드 차단' KPI 가 있어서, 브라우저가 알림을
     막은 것을 402 Guard 가 무언가를 막은 것으로 읽는다(실제 오독 지점). */
  const NOTIFY_LABEL = {
    on: "🔔 알림 켜짐", off: "🔔 알림 받기",
    denied: "🔕 브라우저가 알림을 막았습니다", unsupported: "🔕 이 브라우저는 알림 미지원",
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

  /* 새로고침하면 SSE 가 히스토리를 처음부터 재전송한다. 피드(로그·타임라인)는 그걸로
     복원하지만, 알림·배너까지 다시 터지면 "방금 일어난 일"처럼 보여 혼란스럽다.
     그래서 페이지를 연 시점보다 오래된 이벤트는 알리지 않는다. */
  function isFresh(evt) {
    const t = Date.parse(evt.ts);
    return isNaN(t) || t >= pageLoadedAt - 2000;
  }

  function notify(evt, title, body, cls) {
    if (!isFresh(evt)) return;
    if (document.hidden) {
      // 백그라운드 탭 — 브라우저 알림 (켜져 있을 때)
      if (notifyEnabled()) {
        const n = new Notification("402 Guard — " + title, { body });
        n.onclick = () => { window.focus(); n.close(); };
      }
    } else {
      toast(title, body, cls);  // 보고 있는 탭 — 인앱 토스트
    }
  }

  /* ---------- 402 Guard 알림창 ----------
     첫 화면 KPI 4개(지출 시도·가드 차단·한도 거부·유출)를 본문 위에 떠 있는 패널로 띄운다.
     숫자 자체는 renderState 가 카드 때와 똑같은 data-guard-* 훅으로 채운다.
     시간이 지나 사라지지 않는다 — 제목칸 오른쪽 아래 탭으로 내렸다 올렸다 한다.
     탭은 패널과 한 덩어리라 스크롤 위치와 상관없이 늘 손에 닿는다.
     ⚠ 접은 상태를 기억하지 않는다(예전에는 기억했다). 이 패널이 이 제품의 첫 문장이라
     한 번 접어 둔 사람이 다음에 열었을 때 안 보이면 안 된다 — 페이지를 열 때는 늘 펴진
     상태로 시작하고, 접기는 그 세션 안에서만 유효하다. */
  function setGuardPanel(open) {
    el.guardPanel.classList.toggle("is-collapsed", !open);
    el.guardTab.setAttribute("aria-expanded", open ? "true" : "false");
    el.guardTab.title = open ? "가드 요약 올리기" : "가드 요약 내리기";
  }

  el.guardTab.addEventListener("click", () => {
    setGuardPanel(el.guardPanel.classList.contains("is-collapsed"));
  });

  // 늘 펴진 채로 시작한다. 첫 적용은 애니메이션 없이 — 페이지를 열자마자 패널이 움직이면
  // 깜빡임처럼 느껴진다.
  el.guardPanel.style.transition = "none";
  setGuardPanel(true);
  setTimeout(() => { el.guardPanel.style.transition = ""; }, 0);

  /* 가드가 막은 건(차단)·확인 못한 건(보류)의 상세 한 줄. 다음 사건이 올 때까지 남는다.
     접혀 있었다면 펴서 보여준다 — 지출이 막힌 건 조용히 넘길 일이 아니다. */
  function showGuardHit(d, pending, expand) {
    const side = d.side === "sell" ? "매도" : "매수";
    const diff = d.expected ? ` (기대 ${d.expected} · 청구 ${d.actual})` : "";
    el.guardHitText.textContent =
      `402 Guard ${pending ? "보류" : "차단"} — ${side}${d.code ? " · " + d.code : ""}: `
      + (d.detail || "") + diff;
    el.guardHit.classList.toggle("is-warn", !!pending);
    el.guardHit.classList.remove("hidden");
    // 방금 일어난 일일 때만 내려 준다. 새로고침으로 히스토리가 재생될 때까지 내리면
    // 사용자가 올려 둔 상태를 매번 뒤집는다(내용은 그대로 채워 두므로 내리면 보인다).
    if (expand) setGuardPanel(true);
  }

  /* ---------- 얇은 헤더 + 알림창 위치 ----------
     제목 블록이 화면 위로 완전히 지나가면 얇은 헤더가 대신 내려온다.
     가드 알림창(과 그 탭)은 "지금 보이는 제목칸" 바로 아래에 붙어 있어야 하므로,
     스크롤에 따라 기준이 제목 블록 → 얇은 헤더로 바뀌는 것을 여기서 함께 맞춘다. */
  function syncHeaderUI() {
    const bar = document.querySelector(".topbar");
    const gone = bar ? bar.getBoundingClientRect().bottom <= 0 : window.scrollY > 120;
    el.slimBar.classList.toggle("is-shown", gone);
    const anchor = gone
      ? el.slimBar.getBoundingClientRect().height        // 얇은 헤더 아래
      : Math.max(0, bar ? bar.getBoundingClientRect().bottom : 0);  // 제목 블록 아래
    el.guardDock.style.top = anchor + "px";
  }
  window.addEventListener("scroll", syncHeaderUI, { passive: true });
  window.addEventListener("resize", syncHeaderUI);
  syncHeaderUI();

  // 얇은 헤더의 버튼은 진짜 버튼을 대신 눌러 준다 — 확인 절차·API 호출이 한 벌로 유지된다.
  el.slimPause.addEventListener("click", () => el.btnPause.click());
  el.slimResume.addEventListener("click", () => el.btnResume.click());

  /* 특정 카드로 화면을 옮긴다. 도착 지점에서는 얇은 헤더가 내려와 있고(스크롤한 상태)
     알림창이 내려와 있으면 그것까지 화면 위를 덮으므로, 둘의 높이를 합쳐 비워 둔다.
     그러지 않으면 카드 제목이 헤더·알림창에 가린 채 멈춘다. 숨은 요소도 높이를 잴 수
     있어(화면 위로 밀어 둔 fixed) 도착 후의 상태를 미리 계산할 수 있다. */
  function scrollToCard(id) {
    const card = el.grid.querySelector(`[data-card="${id}"]`);
    if (!card) return;
    const panelH = el.guardPanel.classList.contains("is-collapsed")
      ? 0 : el.guardPanel.getBoundingClientRect().height;
    const offset = el.slimBar.getBoundingClientRect().height
      + panelH + el.guardTab.getBoundingClientRect().height + 12;
    window.scrollTo({
      top: Math.max(0, card.getBoundingClientRect().top + window.scrollY - offset),
      behavior: "smooth",
    });
    card.classList.add("card-focused");
    setTimeout(() => card.classList.remove("card-focused"), 2200);
  }

  /* ---------- 자세히 보기 (AI 카드 → 두 피드) ----------
     AI 카드의 두 레이어는 각각 아래 피드 하나를 세션 단위로 요약한 값이다
     (① 판단 → 판단 타임라인 · ② 청구서 → 협상·이벤트 로그의 심사 줄).
     같은 사실을 화면에 두 번 두지 않으려고 피드는 접어 두고, 레이어마다 자기 몫만
     펼치는 버튼을 둔다 — 버튼이 놓인 자리가 곧 "이 숫자는 저 피드"라는 설명이다.
     ② 로 열 때만 심사 줄 필터가 붙는다(로그에는 견적·x402·AP2 도 함께 흐른다).
     ⚠ 펼침 상태를 기억하지 않는다 — 페이지를 열 때는 늘 둘 다 접힌 채로 시작한다.
     위쪽 요약이 먼저 눈에 들어와야 하는 화면이라, 지난번에 펼쳐 뒀다는 이유로 흐르는
     기록 두 판이 먼저 깔려 있으면 안 된다. 접기는 그 세션 안에서만 유효하다. */
  const MORE_LABEL = {
    decisions: ["판단 타임라인 보기", "판단 타임라인 접기"],
    log: ["심사 내역 보기", "심사 내역 접기"],
  };
  const feedPrefs = {};   // { decisions, log, logFilter } — 새로고침하면 비워진다

  const REDUCED_MOTION = !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  /* 분열 모션이 끝난(또는 건너뛴) 뒤의 확정 상태.
     어느 쪽 애니메이션이 끝났는지가 아니라 "지금 원하는 상태"(feedPrefs)를 보고 정하므로,
     빠르게 두 번 눌러 순서가 엉켜도 마지막 의도대로 남고 여러 번 불려도 결과가 같다.
     펼친 카드에 is-done 을 붙이는 이유: 등장 모션이 카드를 opacity:0 으로 두고 시작하는데
     (skeleton.css 의 .reveal-ready 규칙) 접혀 있던 카드는 화면에 든 적이 없어 아직 그
     상태다. 확정하지 않으면 "눌렀는데 빈 칸"이 된다. */
  function settleFeedCard(card, id) {
    card.classList.remove("is-splitting", "is-merging");
    card.style.removeProperty("--cell-h");
    if (feedPrefs[id]) {
      card.classList.remove("hidden");
      card.classList.add("is-done");
    } else {
      card.classList.add("hidden");
    }
  }

  function renderFeedToggle(id, animate) {
    const card = el.grid.querySelector(`[data-card="${id}"]`);
    const btn = el.aiMoreBtns.find((b) => b.dataset.aiMore === id);
    const open = !!feedPrefs[id];
    if (btn) {
      btn.textContent = MORE_LABEL[id][open ? 1 : 0];
      btn.setAttribute("aria-expanded", String(open));
    }
    if (!card) return;
    // 첫 로드·상태 복원은 모션 없이 곧바로 확정한다(펼침이 이미 끝나 있던 상태다)
    if (!animate || REDUCED_MOTION) { settleFeedCard(card, id); return; }

    card.classList.remove("is-splitting", "is-merging", "is-done");
    if (open) card.classList.remove("hidden");
    /* 최종 높이를 재서 넘긴다 — height:auto 로는 애니메이션이 걸리지 않는다.
       펼칠 때는 방금 드러난 자연 높이가 도착점이고, 접을 때는 지금 높이가 출발점이다.
       (offsetHeight 를 읽는 것 자체가 배치를 다시 계산시켜 모션이 처음부터 재생된다.) */
    card.style.setProperty("--cell-h", card.offsetHeight + "px");
    card.classList.add(open ? "is-splitting" : "is-merging");

    let settled = false;
    const finish = () => { if (settled) return; settled = true; settleFeedCard(card, id); };
    card.addEventListener("animationend", finish, { once: true });
    // 모션이 어떤 이유로든 끝나지 않아도 카드가 반쯤 접힌 채 남지 않게 하는 안전장치
    setTimeout(finish, 900);
  }

  function renderLogFilter() {
    const on = !!(feedPrefs.log && feedPrefs.logFilter);
    el.eventLog.classList.toggle("filter-review", on);
    el.logFilterNote.classList.toggle("hidden", !on);
    /* 걸러 낸 결과가 0줄이면 왜 비었는지 말해 준다. 차단·보류가 0건인 것은 이 제품에서
       정상이고 오히려 자랑인데, 눌렀더니 빈 칸이면 기능이 고장 난 것으로 읽힌다.
       (심사 줄은 가드 차단·보류와 의미 대조뿐이라, 두뇌 없이 무사히 끝난 세션에서는
       실제로 0줄이 정상이다.) */
    const none = on && !el.eventLog.querySelector('li[data-kind="review"]');
    el.logFilterEmpty.classList.toggle("hidden", !none);
  }

  for (const btn of el.aiMoreBtns) {
    const id = btn.dataset.aiMore;
    btn.addEventListener("click", () => {
      feedPrefs[id] = !feedPrefs[id];
      if (id === "log") feedPrefs.logFilter = feedPrefs.log;
      renderFeedToggle(id, true);
      renderLogFilter();
      // 화면은 움직이지 않는다 — 버튼이 AI 카드 안에 있고 펼쳐지는 카드가 바로 아래라
      // 이미 시야에 들어온다. 여기서 스크롤까지 하면 방금 누른 버튼을 놓친다.
      // (가드 패널의 "자세히 보기"는 멀리 있는 카드로 보내는 것이라 그쪽은 그대로 둔다.)
    });
  }

  el.logFilterClear.addEventListener("click", () => {
    feedPrefs.logFilter = false;
    renderLogFilter();
  });

  el.guardHitMore.addEventListener("click", () => {
    // 가드 바에서 올 때는 걸러내지 않고 통째로 편다 — 차단 한 건의 앞뒤 맥락
    // (견적·x402 단계)까지 같이 읽어야 하는 자리다. 로그는 이제 기본으로 접혀
    // 있으므로 스크롤 전에 반드시 펼쳐야 한다.
    feedPrefs.log = true;
    feedPrefs.logFilter = false;
    renderFeedToggle("log", true);
    renderLogFilter();
    scrollToCard("log");
  });

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
      /* 402 Guard — 이 제품의 주인공이다. 서명 직전 청구서 검증에서 막힌 건(차단)과
         배송을 확인하지 못한 건(보류). 상세는 상단 가드 요약 바에 한 줄로 남기고(접혀
         있었으면 펴 준다), 탭이 백그라운드면 브라우저 알림도 보낸다. 전체 내용은
         협상·이벤트 로그에 쌓인다 — 예전에는 이 이벤트들에 로그 처리가 아예 없어서
         가드가 실제로 차단해도 활동 로그에는 한 줄도 남지 않았다(심사 축④ 직결). */
      case "guard_blocked":
      case "guard_pending": {
        const pending = evt.type === "guard_pending";
        const side = d.side === "sell" ? "매도" : "매수";
        const detail = d.detail || "";
        addLog(evt.ts,
          `[402 Guard ${pending ? "보류" : "차단"}] (${side}) ${d.order_id || ""} — ${d.code || ""}: ${detail}`
          + (d.expected ? ` · 기대 ${d.expected} / 청구 ${d.actual}` : "")
          + (d.where ? ` · ${d.where}` : "")
          // 이 꼬리표가 두 사건의 무게 차이다: 차단은 서명 자체를 안 만들었으니 유출 0,
          // 보류는 대금이 이미 나간 뒤라 되찾을 경로가 없어 세션을 멈춘다.
          + (pending ? " · 정산 후 확인 실패라 세션을 멈춥니다(회수 경로 없음)"
                     : " · 서명 미생성(유출 0)"),
          "log-danger", "review");
        const fresh = isFresh(evt);
        showGuardHit(d, pending, fresh);
        // 보고 있는 탭에서는 가드 요약 바가 이미 같은 내용을 띄우므로 토스트를 겹치지 않는다.
        if (fresh && document.hidden) {
          notify(evt, `402 Guard ${pending ? "보류" : "차단"}`, detail || "정산 후 확인 실패", "danger");
        }
        fetchState();   // 가드 KPI(시도·차단 건수) 갱신
        break;
      }
      case "guard_semantic":
        // 차단 건은 바로 위 guard_blocked 가 이미 자세히 남기므로 여기서는 중복을 피한다.
        // 통과·검증불가만 조용히 기록한다 — '이 계층이 평소에도 일하고 있다'가 보여야 한다.
        if (d.ok) {
          addLog(evt.ts,
            d.verdict === "unverified"
              ? `[의미 대조] ${d.order_id} — 검증 불가라 하드 검사만으로 진행(매도) · ${d.reason}`
              : `[의미 대조] ${d.order_id} — 청구서가 주문과 같은 물건 확인` +
                (d.verdict === "cached" ? "(같은 서식 재사용)" : "") +
                (d.reason ? ` · "${d.reason}"` : ""),
            "log-muted", "review");
        }
        break;
      case "mandate_updated":
        addLog(evt.ts, `[AP2 한도 변경] 예산 ${d.old.budget_total_usdc}→${d.new.budget_total_usdc} · 건별 ${d.old.per_trade_max_usdc}→${d.new.per_trade_max_usdc} USDC (${d.applied === "immediate" ? "재서명·즉시 적용" : "다음 세션부터 적용"} · 주체: ${d.actor})`, "log-ok");
        fetchState();
        break;
      case "trading_paused": {
        const who = d.actor_label || d.actor;
        const why = d.reason ? ` — ${d.reason}` : "";
        addLog(evt.ts, `[긴급정지] 신규 판단·결제 중단 (주체: ${who})${why}`, "log-danger");
        notify(evt, "긴급정지", `신규 판단·결제 중단 (주체: ${who})${why}`, "danger");
        fetchState();
        break;
      }
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
        addLog(evt.ts, `[세션 시작] ${d.mode === "live" ? "라이브" : "샌드박스"} · ${d.network} · ${d.symbol} · 시세: ${d.feed ? d.feed.label : "—"} · 전략: ${stText} · 판단: ${d.brain} · AP2 mandate 서명검증 ${d.mandate_verified ? "OK" : "FAIL"}`, "log-ok");
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
        fetchState();   // 이력 갱신은 여기서 하지 않는다 — renderState 의 idle 전환에서 한다
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

  /* ---------- 고급(검증용) 모드 ----------
     세션 설정의 고급은 기본으로 접혀 있고, 주소에 ?lab=1 이 붙어 있으면 펼쳐진 채로 열린다
     (우리 검증용 북마크 · ?lab=0 이면 해제). 별도 경로(/lab)는 만들지 않는다 — 라우트 신설은
     백엔드 파일을 건드리고, 대시보드가 사실상 두 벌이 되어 회귀 표면이 2배가 된다.
     구현 방식은 아래 #token 과 같다: 값을 localStorage 로 옮기고 주소창에서 지운다.
     ⚠ 순서 — 아래 captureTokenFromHash() 는 replaceState 에 location.search 를 그대로 넘겨
     search 를 유지하므로, lab 은 **그 함수보다 먼저** 지워야 서로의 정리를 밟지 않는다.
     반대로 여기서는 location.hash 를 보존한다(#token 을 읽기 전에 지우면 토큰이 유실된다). */
  const LAB_KEY = "autotrader_lab";
  (function captureLab() {
    const p = new URLSearchParams(location.search);
    if (!p.has("lab")) return;
    if (p.get("lab") === "0") localStorage.removeItem(LAB_KEY);
    else localStorage.setItem(LAB_KEY, "1");
    p.delete("lab");
    const q = p.toString();
    history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + location.hash);
  })();

  /* 고급을 펼치면 감춰 둔 **옵션**(⚡초고속·느림·적립형)도 함께 나타난다. 값은 접혀 있어도
     DOM 에 살아 있으므로 세션 시작 payload 는 한 줄도 바뀌지 않는다(재현성이 분리의 전제). */
  function syncAdvOptions() {
    const open = el.adv.open;
    for (const o of document.querySelectorAll("[data-adv-option]")) o.hidden = !open;
  }
  el.adv.addEventListener("toggle", syncAdvOptions);
  if (localStorage.getItem(LAB_KEY) === "1") {
    el.adv.open = true;
    // 검증용에서만 푸는 잠금: 라이브 모드 옵션 · 푸터의 배치 초기화 줄
    for (const n of document.querySelectorAll("[data-lab-only]")) {
      n.classList.remove("hidden");
      if (n.tagName === "OPTION") n.disabled = false;
    }
  }
  syncAdvOptions();

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
        // 방문자가 마지막으로 보는 문장이다. 예전 문안은 갖고 있지도 않은 토큰을 붙이라고만
        // 해서 막다른 길로 읽혔다 — 왜 막혔는지(단일 공용 인스턴스)와 무엇은 되는지(관전)를
        // 먼저 말하고, 토큰 안내는 실제로 토큰을 가진 사람을 위해 괄호로 남긴다.
        msg = "이 데모는 단일 공용 인스턴스입니다. 세션 조작(시작·정지)은 심사용 링크에서만 "
            + "열려 있고, 관전(실시간 로그·지표)은 그대로 보실 수 있습니다."
            + "\n\n(접근 토큰이 있다면 주소 끝에 #token=<접근토큰> 을 붙여 한 번 접속하세요.)";
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
    el.subBars.classList.toggle("hidden", !replay);
    // 멀티 종목 선택은 실데이터 재생에서만. 라이브(온체인)만 단일 종목이라 잠근다.
    const isLive = el.modeSelect.value === "live";
    const singleOnly = isLive;   // 추세추종도 멀티 가능(종목별 예산 슬라이스). 라이브(온체인)만 단일.
    el.symPicker.classList.toggle("hidden", !replay);
    for (const c of document.querySelectorAll("[data-sym-check]")) {
      c.disabled = singleOnly;
      if (singleOnly) c.checked = false;
    }
    // '예산/N 슬라이스'는 내부 용어다. 추세추종·AI 판단이 같은 문구가 되므로 분기도 줄였다.
    el.symPickerLabel.textContent = isLive
      ? "라이브(온체인)는 단일 종목만 지원합니다 — 여러 종목은 샌드박스 전용"
      : "살 종목 — 여러 개 고르면 예산을 나눠 동시에 굴립니다";
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
  /* 대기 중에는 .rules 줄이 드롭다운을 그대로 비춘다(renderRules) — 세션 설정 카드 안에서
     무엇이 바뀌든 다시 그린다. select·checkbox·number 입력이 전부 change 로 올라온다. */
  el.sessionCard.addEventListener("change", () => renderRules(lastState));
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
      feed: { type: el.feedSelect.value, dataset: el.feedDataset.value, sub_bars: parseInt(el.subBars.value, 10) || 1, symbols: pickedSymbols() },
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
  /* ⚠ DEFAULT_LAYOUT 을 바꾸면 이 키도 반드시 올린다. 안 올리면 이미 방문한 적 있는
     브라우저(= 촬영용 브라우저 포함)가 localStorage 에 저장된 옛 배치를 계속 쓴다. */
  const LAYOUT_KEY = "autotrader_layout_v8";  // v8: 세션 설정 카드를 첫 자리에서 시세 뒤로 — 기존 저장 배치 리셋
  // 가드 KPI 는 더 이상 카드가 아니다(상단 알림창 .guard-panel 로 이동). 나머지 흐름은 시안대로
  // (컨트롤) → 오늘의 결과 → AI 판단 근거 → 시세 → 거래 내역 → 한도/브리핑. 세션·멀티종목
  // 컨트롤은 시안에 없지만 데모에 필수라 맨 앞에 둔다(symbols 는 멀티일 때만 표시).
  // ai 카드가 결과 바로 다음인 이유: 상단 가드 바의 "돈이 새지 않았다" 다음에 오는 질문이
  // "그걸 누가 어떻게 막았나"다. 협상 로그·판단 타임라인은 그 근거라 ai 바로 뒤에 두되
  // 기본은 접어 둔다 — 펼치면 자기를 부른 숫자 바로 밑에서 열린다(v7 이전에는 맨 아래였다).
  // history 는 맨 뒤다 — 첫 화면(가드 KPI → 결과 → AI 근거)을 밀어내지 않으면서,
  // 스크롤하면 "이 시스템은 전에도 돌았다"는 증거가 나오게 한다.
  // v8 에서 session 을 맨 앞에서 price 뒤로 내렸다 — 심사위원이 처음 보는 카드가 설정
  // 컨트롤 덩어리일 이유가 없다. 간단 모드라 카드가 작아져 아래에 둬도 조작에 지장이 없다.
  // (decisions·log 는 기본 접힘이라 눈에 보이는 순서는 pnl→valuation→position→ai→price→session.)
  const DEFAULT_LAYOUT = ["pnl", "valuation", "position", "ai",
                          "decisions", "log",
                          "price", "session", "symbols",
                          "trades", "budget", "mandate", "briefing", "history"];

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
  renderFeedToggle("decisions");
  renderFeedToggle("log");
  renderLogFilter();
  initCardDrag();
  renderNotifyBtn();
  fetchState();
  fetchHistory();
  connect();
})();
