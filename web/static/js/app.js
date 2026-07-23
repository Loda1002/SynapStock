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
    decisionFeed: $("[data-decision-feed]"),
    eventLog: $("[data-event-log]"),
    tradesBody: $("[data-trades-body]"),
    connStatus: $("[data-conn-status]"),
    walletTrading: $("[data-wallet-trading]"),
    walletBroker: $("[data-wallet-broker]"),
  };

  const MAX_FEED_ITEMS = 100;
  const MAX_LOG_ITEMS = 200;
  let prices = [];          // 스파크라인용 최근 가격
  let lastEventId = 0;

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
    el.rules.textContent = `규칙: ${s.symbol} 이 ${s.rules.buy_below} USDC 이하면 ${s.rules.spend_per_trade} USDC 어치 매수, ${s.rules.sell_above} USDC 이상이면 전량 매도 · 예산 ${s.budget.total_usdc} USDC (건별 최대 ${s.budget.per_trade_max_usdc})`;

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

    if (s.wallets.trading) el.walletTrading.textContent = shortKey(s.wallets.trading);
    if (s.wallets.broker) el.walletBroker.textContent = shortKey(s.wallets.broker);

    // 버튼 상태
    const running = eng.status === "running";
    el.btnStart.disabled = running || eng.status === "stopping";
    el.btnStop.disabled = !running;
    el.modeSelect.disabled = running;
    el.pausedBadge.classList.toggle("hidden", s.trading_enabled);
    el.btnPause.classList.toggle("hidden", !s.trading_enabled);
    el.btnResume.classList.toggle("hidden", s.trading_enabled);
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

  function addTradeRow(t) {
    const empty = el.tradesBody.querySelector(".empty-row");
    if (empty) empty.remove();
    const tr = make("tr");
    tr.appendChild(make("td", null, timeOf(t.ts)));
    tr.appendChild(make("td", "side-" + t.side, t.side === "buy" ? "매수" : "매도"));
    tr.appendChild(make("td", null, t.quantity));
    tr.appendChild(make("td", null, t.price_usdc));
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
      case "quote":
        addLog(evt.ts, `[A2A 견적] (${d.side === "buy" ? "매수" : "매도"}) ${d.request} → ${d.quantity} ${d.symbol} @ ${d.price_usdc} = ${d.total_usdc} USDC`);
        break;
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
        fetchState(); // 포지션·예산·손익 카드 갱신
        break;
      case "mandate_rejected":
        addLog(evt.ts, `[AP2 거부] ${d.order_id} — ${d.reason}`, "log-danger");
        break;
      case "trading_paused":
        addLog(evt.ts, `[긴급정지] 신규 판단·결제 중단 (주체: ${d.actor})`, "log-danger");
        fetchState();
        break;
      case "trading_resumed":
        addLog(evt.ts, `[재개] 매매 재개 (주체: ${d.actor})`, "log-ok");
        fetchState();
        break;
      case "engine_started":
        addLog(evt.ts, `[세션 시작] ${d.mode === "live" ? "라이브" : "드라이런"} · ${d.network} · ${d.symbol} · 판단: ${d.brain} · AP2 mandate 서명검증 ${d.mandate_verified ? "OK" : "FAIL"}`, "log-ok");
        fetchState();
        break;
      case "engine_stopped":
        addLog(evt.ts, `[세션 종료] 틱 ${d.ticks} · 체결 ${d.trades}건` +
          (d.archive ? ` · 증빙 ${d.archive}` : "") +
          (d.cross_check ? ` · 교차검증 USDC ${d.cross_check.usdc_ok ? "PASS" : "FAIL"} / 주식 ${d.cross_check.stock_ok ? "PASS" : "FAIL"}` : ""),
          d.cross_check && !(d.cross_check.usdc_ok && d.cross_check.stock_ok) ? "log-danger" : "log-ok");
        fetchState();
        break;
      case "balances":
        addLog(evt.ts, `[온체인 잔액·${d.stage === "before" ? "시작" : "종료"}] trading: ${d.balances.trading.usdc} USDC / ${d.balances.trading.stock} 주 · broker: ${d.balances.broker.usdc} USDC / ${d.balances.broker.stock} 주`, "log-muted");
        break;
      case "error":
        addLog(evt.ts, `[오류] ${d.message}`, "log-danger");
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

  el.btnStart.addEventListener("click", async () => {
    el.btnStart.disabled = true;
    const s = await post("/api/engine/start", { mode: el.modeSelect.value });
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

  // ---------- 시작 ----------
  fetchState();
  connect();
})();
