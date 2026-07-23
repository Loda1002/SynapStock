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
    modeSelect: $("[data-mode-select]"),
    strategySelect: $("[data-strategy-select]"),
    dcaParams: $("[data-dca-params]"),
    dcaTicks: $("[data-dca-ticks]"),
    dcaAmount: $("[data-dca-amount]"),
    btnStart: $("[data-btn-start]"),
    btnStop: $("[data-btn-stop]"),
    btnPause: $("[data-btn-pause]"),
    btnResume: $("[data-btn-resume]"),
    rules: $("[data-rules]"),
    symbol: $("[data-symbol]"),
    price: $("[data-price]"),
    sparkline: $("[data-sparkline]"),
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
  };

  const MAX_FEED_ITEMS = 100;
  const MAX_LOG_ITEMS = 200;
  let prices = [];          // 스파크라인용 최근 가격
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

  function renderState(s) {
    const eng = s.engine || {};
    el.net.textContent = (eng.network || "—") + (eng.mode ? " · " + (eng.mode === "live" ? "라이브" : "드라이런") : "");
    el.engineStatus.textContent = { idle: "엔진 대기", running: "엔진 실행 중", stopping: "종료 중…" }[eng.status] || eng.status;
    el.engineStatus.classList.toggle("badge-ok", eng.status === "running");
    el.brain.textContent = "판단: " + (eng.brain || "—");
    const feePct = s.fees ? (s.fees.fee_bps / 100) : 0;
    const strat = s.strategy || { type: "condition" };
    const ruleText = strat.type === "dca"
      ? `적립형: ${strat.dca_every_ticks}틱마다 ${strat.dca_amount_usdc} USDC 정액 매수 (매도 없음)`
      : `조건형: ${s.symbol} 이 ${s.rules.buy_below} USDC 이하면 ${s.rules.spend_per_trade} USDC 어치 매수, ${s.rules.sell_above} USDC 이상이면 전량 매도`;
    el.rules.textContent = `규칙: ${ruleText} · 예산 ${s.budget.total_usdc} USDC (건별 최대 ${s.budget.per_trade_max_usdc}) · 브로커 수수료 ${feePct}%`;

    el.symbol.textContent = s.symbol;
    el.posSymbol.textContent = s.symbol;
    if (s.price.current != null) el.price.textContent = s.price.current + " USDC";
    prices = (s.price.history || []).map((p) => num(p.price));
    drawSparkline();
    if (eng.tick) el.tickInfo.textContent = `틱 ${eng.tick} · 간격 ${eng.tick_interval_sec}s`;

    el.posQty.textContent = s.position.quantity;
    el.posAvg.textContent = s.position.avg_price_usdc;

    const total = num(s.budget.total_usdc), spent = num(s.budget.spent_usdc);
    el.budgetFill.style.width = total > 0 ? Math.min(100, (spent / total) * 100).toFixed(1) + "%" : "0%";
    el.budgetSpent.textContent = s.budget.spent_usdc;
    el.budgetTotal.textContent = s.budget.total_usdc;
    el.budgetRemaining.textContent = s.budget.remaining_usdc;
    el.budgetPerTrade.textContent = s.budget.per_trade_max_usdc;

    const pnl = num(s.pnl.realized_usdc);
    el.pnl.textContent = (pnl > 0 ? "+" : "") + s.pnl.realized_usdc;
    el.pnl.className = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
    el.returnPct.textContent = s.pnl.return_pct;
    el.cumBuy.textContent = s.pnl.cum_buy_usdc;
    if (s.fees) {
      el.feeRate.textContent = feePct + "%";
      el.cumFee.textContent = s.fees.cum_fee_usdc;
    }

    if (s.wallets.trading) el.walletTrading.textContent = shortKey(s.wallets.trading);
    if (s.wallets.broker) el.walletBroker.textContent = shortKey(s.wallets.broker);

    if (s.last_briefing) renderBriefing(s.last_briefing);  // B2 새로고침 복원

    // A3 한도 설정 카드 — 입력 중(포커스)일 때는 값을 덮어쓰지 않는다
    const running = eng.status === "running";
    if (document.activeElement !== el.mandateBudget) el.mandateBudget.value = s.budget.total_usdc;
    if (document.activeElement !== el.mandatePerTrade) el.mandatePerTrade.value = s.budget.per_trade_max_usdc;
    el.mandateSymbols.textContent = s.symbol;
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
    el.strategySelect.disabled = running;
    el.dcaTicks.disabled = running;
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

  function drawSparkline() {
    const w = 300, h = 60, pad = 4;
    el.sparkline.textContent = "";
    if (prices.length < 2) return;
    const min = Math.min(...prices), max = Math.max(...prices);
    const span = max - min || 1;
    const step = (w - pad * 2) / (prices.length - 1);
    const pts = prices.map((p, i) =>
      `${(pad + i * step).toFixed(1)},${(h - pad - ((p - min) / span) * (h - pad * 2)).toFixed(1)}`
    ).join(" ");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", pts);
    el.sparkline.appendChild(line);
  }

  // ---------- 피드 렌더 ----------
  function addDecision(d) {
    const li = make("li");
    li.appendChild(make("time", null, timeOf(d.ts)));
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
      case "price_tick":
        el.price.textContent = d.price + " USDC";
        el.tickInfo.textContent = `틱 ${d.tick}`;
        prices.push(num(d.price));
        if (prices.length > 60) prices.shift();
        drawSparkline();
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
        const stText = st.type === "dca"
          ? `적립형(${st.dca_every_ticks}틱마다 ${st.dca_amount_usdc} USDC)` : "조건형";
        const srcNote = st.type === "dca"
          ? "판단 출처 dca — 적립 스케줄이 매수, Gemini 미사용"
          : "판단 출처 gemini / rule";
        sessionBoundary(evt.ts, `─── 새 세션 시작 · ${stText} · ${srcNote} ───`, true);
        addLog(evt.ts, `[세션 시작] ${d.mode === "live" ? "라이브" : "드라이런"} · ${d.network} · ${d.symbol} · 전략: ${stText} · 판단: ${d.brain} · AP2 mandate 서명검증 ${d.mandate_verified ? "OK" : "FAIL"}`, "log-ok");
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
  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* 그대로 */ }
      alert(msg);
      return null;
    }
    return r.json();
  }

  el.strategySelect.addEventListener("change", () => {
    el.dcaParams.classList.toggle("hidden", el.strategySelect.value !== "dca");
  });
  el.btnStart.addEventListener("click", async () => {
    el.btnStart.disabled = true;
    const s = await post("/api/engine/start", {
      mode: el.modeSelect.value,
      strategy: {
        type: el.strategySelect.value,
        dca_every_ticks: parseInt(el.dcaTicks.value, 10) || 5,
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

  // ---------- 시작 ----------
  renderNotifyBtn();
  fetchState();
  connect();
})();
