// admin.js: the Léa evals console. One page per menu item, chosen by the URL hash:
// Overview, Eval, Trace, Cost, Guardrails, Agent, EU AI Act. Each page is a render
// function that fills the page column. Text from the API and the model is untrusted,
// so everything goes through textContent; no data ever goes through innerHTML.
(function () {
  "use strict";

  const pageBox = document.getElementById("page");
  const menu = document.getElementById("menu");
  const POLL_MS = 2000;

  // ------------------------------------------------------------------ helpers
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function badge(kind, text) { return el("span", "badge " + kind, text); }
  function errorBox(message) { const box = el("div", "error", message); box.setAttribute("role", "alert"); return box; }
  function section(title, ...content) { const block = el("div", "stack"); block.append(el("p", "label", title), ...content); return block; }

  // A table from header texts and rows. A row is a list of cells (text or element), or
  // {cells, onClick, selected}. A header starting with "#" is a right-aligned number column.
  function table(headers, rows) {
    const node = el("table", "table");
    const head = el("tr");
    const numeric = headers.map((h) => h.startsWith("#"));
    headers.forEach((h, i) => head.append(el("th", numeric[i] ? "num" : "", h.replace(/^#/, ""))));
    const thead = el("thead"); thead.append(head);
    const body = el("tbody");
    for (const row of rows) {
      const spec = Array.isArray(row) ? { cells: row } : row;
      const tr = el("tr", (spec.onClick ? "clickable" : "") + (spec.selected ? " selected" : ""));
      spec.cells.forEach((cell, i) => {
        const td = el("td", numeric[i] ? "num" : "");
        if (cell instanceof Node) td.append(cell); else td.textContent = cell == null ? "" : String(cell);
        tr.append(td);
      });
      if (spec.onClick) {
        tr.tabIndex = 0;
        tr.addEventListener("click", spec.onClick);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); spec.onClick(); } });
      }
      body.append(tr);
    }
    node.append(thead, body);
    return node;
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch (_e) { data = null; }
    if (!response.ok) {
      const error = new Error((data && (data.error || data.message)) || "HTTP " + response.status);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const fmt = {
    usd: (v, digits = 4) => (v == null ? "–" : "$" + Number(v).toFixed(digits)),
    secs: (ms) => (ms == null ? "–" : ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(1) + " s"),
    tokens: (i, o) => (i || 0).toLocaleString("en-GB") + " in / " + (o || 0) + " out",
    time: (iso) => {
      if (!iso) return "–";
      const d = new Date(iso);
      return isNaN(d) ? iso : d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    },
    rate: (layer) => (!layer || !layer.applicable ? "n/a" : layer.passed + "/" + layer.applicable),
    pct: (v) => (v == null ? "–" : Math.round(v * 100) + "%"),
  };

  const LABEL = {
    secret_in_message: "Card number, PIN or password", spoofed_session: "Fake session note", jailbreaking: "Jailbreak attempt",
    pii: "Personal data (flagged)", amount_not_from_tool: "Amount not from the tool", lock_claim_without_tool: "'Locked' without a lock",
    SIGN_IN_REQUIRED: "Banking tool for a guest", CUSTOMER_MISMATCH: "Another customer's ID", CARD_NOT_FOUND: "Unknown card",
    ACCOUNT_NOT_FOUND: "Unknown account", UNKNOWN_TOOL: "Unknown tool",
    allowed: "Lock allowed (tap + approval)", denied: "Lock denied (kept active)", not_confirmed: "Lock left unconfirmed",
  };
  const OUTCOME_BADGE = { pass: "ok", fail: "fail", flag: "warning", skip: "muted-badge" };

  function tile(label, value, note) {
    const card = el("div", "card stack tile");
    card.append(el("p", "label", label), el("p", "value", value));
    if (note) card.append(el("p", "small muted", note));
    return card;
  }

  // ------------------------------------------------------------------ health dot, in the header on every page
  const dot = document.getElementById("health-dot");
  const healthCard = document.getElementById("health-card");
  function healthList(health) {
    const list = el("div", "stack");
    for (const check of health.checks) {
      const row = el("div", "row");
      row.append(badge(check.ok ? "ok" : "fail", check.ok ? "ok" : "fail"), el("span", "", check.name));
      list.append(row, el("p", "small muted", check.detail));
    }
    return list;
  }
  async function loadHealth() {
    try {
      const health = await api("/api/admin/health");
      dot.className = "health-dot " + health.status;
      healthCard.replaceChildren(el("p", "label", "Health, checked every minute"), healthList(health));
      return health;
    } catch (error) {
      dot.className = "health-dot down";
      healthCard.replaceChildren(errorBox("Could not check the health: " + error.message));
      return null;
    }
  }
  dot.addEventListener("click", () => {
    healthCard.hidden = !healthCard.hidden;
    dot.setAttribute("aria-expanded", String(!healthCard.hidden));
  });
  document.addEventListener("click", (e) => { if (!healthCard.hidden && !healthCard.contains(e.target) && !dot.contains(e.target)) { healthCard.hidden = true; dot.setAttribute("aria-expanded", "false"); } });

  // ------------------------------------------------------------------ Overview
  async function renderOverview(box) {
    box.append(el("h1", "display", "Is Léa ready to ship?"));
    box.append(el("p", "muted", "The golden set, the traffic since the server started, and the health of every dependency. Visitors are people on the demo site; eval turns are golden-set runs."));
    const tiles = el("div", "grid-tiles"), chartBlock = el("div", "stack"), healthBlock = el("div");
    box.append(tiles, chartBlock, healthBlock);
    try {
      const [summary, health] = await Promise.all([api("/api/admin/summary"), loadHealth()]);
      if (!box.isConnected) return;
      const last = (summary.runs || []).find((r) => r.status === "done");
      const live = summary.live;
      tiles.append(
        tile("Latest golden set", last ? last.passed + " / " + last.total : "–", last ? fmt.time(last.finished_at || last.started_at) : "no run yet"),
        tile("Guardrail cases", last && last.guardrail_cases ? last.guardrail_cases.passed + " / " + last.guardrail_cases.total : "–", "attacks and secrets stopped"),
        tile("Cost of that run", fmt.usd(last && last.cost_usd, 3), "all model calls and library searches"),
        tile("Visitor turns", String(live.turns), live.conversations + " conversations"),
        tile("Latency p50 / p95", fmt.secs(live.latency_ms.p50) + " / " + fmt.secs(live.latency_ms.p95), "per visitor turn"),
        tile("Cost per visitor turn", fmt.usd(live.cost_usd.per_turn), "per conversation " + fmt.usd(live.cost_usd.per_conversation)));
      chartBlock.append(el("p", "label", "Latency per turn, last 60 turns"), legend(), latencyChart(summary.recent || []));
      if (health) healthBlock.append(section("Health", healthList(health)));
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the overview: " + error.message));
    }
  }

  function legend() {
    const row = el("div", "legend small muted");
    for (const [cls, text] of [["--series-visitor", "Visitor turn"], ["--series-eval", "Eval turn"]]) {
      const item = el("span", "", text); const swatch = el("i"); swatch.style.background = "var(" + cls + ")"; item.prepend(swatch); row.append(item);
    }
    return row;
  }

  function latencyChart(recent) {
    const box = el("div", "chart");
    const data = recent.slice(0, 60).reverse();
    if (!data.length) { box.append(el("p", "muted", "No turns yet. Chat with Léa on the demo site, or run the golden set.")); return box; }
    const W = 960, H = 220, L = 48, R = 8, T = 12, B = 28, NS = "http://www.w3.org/2000/svg";
    const top = Math.max(2, Math.ceil(Math.max(...data.map((d) => d.latency_ms)) / 2000) * 2);
    const step = (W - L - R) / 60, bw = Math.min(10, step - 2);
    const y = (s) => T + (H - T - B) * (1 - s / top);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Latency per turn in seconds, last " + data.length + " turns");
    const add = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; svg.append(n); return n; };
    for (let g = 0; g <= top; g += top / 4) {
      add("line", { x1: L, x2: W - R, y1: y(g), y2: y(g), class: "grid" });
      add("text", { x: L - 8, y: y(g) + 4, "text-anchor": "end", class: "axis" }, (g % 1 ? g.toFixed(1) : g) + " s");
    }
    const tip = el("div", "tip small"); tip.hidden = true;
    data.forEach((d, i) => {
      const x = L + i * step + (step - bw) / 2, yt = y(d.latency_ms / 1000), h = Math.max(2, y(0) - yt), r = Math.min(4, h / 2, bw / 2);
      add("path", { d: "M" + x + "," + y(0) + "V" + (yt + r) + "Q" + x + "," + yt + " " + (x + r) + "," + yt + "H" + (x + bw - r) + "Q" + (x + bw) + "," + yt + " " + (x + bw) + "," + (yt + r) + "V" + y(0) + "Z",
        fill: "var(" + (d.source === "eval" ? "--series-eval" : "--series-visitor") + ")" });
      const hit = add("rect", { x: L + i * step, y: T, width: step, height: H - T - B, fill: "transparent" });
      hit.addEventListener("mouseenter", () => {
        const k = svg.getBoundingClientRect().width / W;
        const tools = (d.tools || []).map((t) => t.name).join(", ") || "no tools";
        const guard = d.guardrail_in || d.guardrail_out;
        tip.replaceChildren(el("div", "", fmt.time(d.ts) + " · " + (d.source === "eval" ? "eval" : "visitor")),
          el("div", "", fmt.secs(d.latency_ms) + " · " + fmt.usd(d.cost_usd) + " · " + d.model_calls + " model calls"),
          el("div", "muted", tools + (guard ? " · stopped: " + (LABEL[guard] || guard) : "")));
        tip.style.left = (L + i * step + step / 2) * k + "px";
        tip.style.top = yt * k + "px";
        tip.hidden = false;
      });
      hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    });
    add("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), class: "grid" });
    add("text", { x: L, y: H - 8, class: "axis" }, "older");
    add("text", { x: W - R, y: H - 8, "text-anchor": "end", class: "axis" }, "newest");
    box.append(svg, tip);
    return box;
  }

  // ------------------------------------------------------------------ Eval
  const LAYERS = [
    ["tools", "Tools: the right tools ran, forbidden ones did not"],
    ["confirmation", "Confirmation: a lock waited for the tap"],
    ["guardrails", "Guardrails: the expected check stopped the message"],
    ["answer", "Answer: what it must and must not say"],
  ];

  async function renderEval(box, state = { selected: null, timer: null }) {
    const heading = el("h1", "heading", "Eval");
    const run = el("button", "button-primary", "Run the golden set"); run.type = "button";
    const progress = el("p", "small muted");
    const matrix = el("div"), history = el("div"), detail = el("div");
    const toolbar = el("div", "chips"); toolbar.append(run, progress);
    box.append(heading,
      el("p", "muted", "Sixteen scripted conversations against the live agent, checked by code on four layers; no model is the judge. A run costs about $0.10 and takes two to three minutes."),
      toolbar, matrix, history, detail);

    async function refresh() {
      if (!heading.isConnected) return; // the page was left: stop polling
      try {
        const summary = await api("/api/admin/summary");
        const runs = summary.runs || [];
        const running = runs.find((r) => r.status === "running");
        run.disabled = !!running;
        progress.textContent = running ? "Running: " + running.done + " of " + running.total + " cases done." : "";
        const done = runs.filter((r) => r.status === "done");
        matrix.replaceChildren(layerMatrix(done.slice(0, 4)));
        if (!state.selected && done[0]) state.selected = done[0].id;
        history.replaceChildren(section("All runs", runTable(runs, state, () => { showRun(); refresh(); })));
        if (running) { clearTimeout(state.timer); state.timer = setTimeout(refresh, POLL_MS); }
        else showRun();
      } catch (error) {
        matrix.replaceChildren(errorBox("Could not load the runs: " + error.message));
      }
    }

    async function showRun() {
      if (!state.selected) { detail.replaceChildren(); return; }
      try {
        const data = await api("/api/admin/runs/" + encodeURIComponent(state.selected));
        detail.replaceChildren(section("Cases in run " + data.id + (data.note ? " · " + data.note : ""), caseTable(data.cases || [])));
      } catch (error) {
        detail.replaceChildren(errorBox("Could not load the run: " + error.message));
      }
    }

    run.addEventListener("click", async () => {
      run.disabled = true; progress.textContent = "Starting…";
      try {
        const res = await api("/api/admin/runs", { method: "POST" });
        state.selected = res.run_id;
      } catch (error) {
        progress.textContent = error.message; run.disabled = false; return;
      }
      refresh();
    });
    refresh();
  }

  // Runs across, layers down, as the weather agent shows models across.
  function layerMatrix(runs) {
    if (!runs.length) return el("p", "muted", "No run yet. Press Run the golden set.");
    const headers = [""].concat(runs.map((r, i) => (i === 0 ? "Latest · " : "") + fmt.time(r.finished_at || r.started_at)));
    const row = (title, cell) => [title].concat(runs.map(cell));
    const rows = LAYERS.map(([key, title]) => row(title, (r) => (r.layers ? fmt.rate(r.layers[key]) : "n/a")));
    rows.push(row("Cases passed", (r) => badge(r.passed === r.total ? "ok" : "fail", r.passed + "/" + r.total)));
    rows.push(row("Mean time per case", (r) => fmt.secs(r.latency_ms_mean)));
    rows.push(row("Cost of the run", (r) => fmt.usd(r.cost_usd, 3)));
    rows.push(row("Source", (r) => el("span", "small muted", (r.label || "") + (r.note ? " · " + r.note : ""))));
    const node = table(headers, rows);
    node.classList.add("matrix");
    return node;
  }

  function runTable(runs, state, onSelect) {
    return table(["Run", "Started", "Result", "#Passed", "#Cost", "#Mean per case", "Source"], runs.map((r) => ({
      selected: r.id === state.selected,
      onClick: r.status === "done" ? () => { state.selected = r.id; onSelect(); } : null,
      cells: [el("span", "code", r.id), fmt.time(r.started_at),
        r.status === "running" ? badge("warning", "running " + r.done + "/" + r.total) : badge(r.passed === r.total ? "ok" : "fail", r.passed === r.total ? "all passed" : (r.total - r.passed) + " failed"),
        r.passed == null ? "–" : r.passed + "/" + r.total, fmt.usd(r.cost_usd, 3), fmt.secs(r.latency_ms_mean),
        el("span", "small muted", (r.label || "") + (r.committed ? " · committed" : ""))],
    })));
  }

  function caseTable(cases) {
    return table(["Case", "Result", "#Time", "#Cost", "#Calls", "Tools", "Guardrail"], cases.map((c) => {
      const name = el("div", "stack"); name.append(el("span", "", c.name));
      if (!c.passed) { name.append(el("span", "small", (c.problems || []).join("; "))); name.lastChild.style.color = "var(--color-error)"; name.append(el("span", "small muted", "Answer: " + (c.answer || ""))); }
      return [name, badge(c.passed ? "ok" : "fail", c.passed ? "pass" : "fail"), fmt.secs(c.latency_ms), fmt.usd(c.cost_usd),
        c.model_calls == null ? "–" : c.model_calls, el("span", "code", (c.tools || []).join(", ") || "–"), LABEL[c.guardrail] || c.guardrail || "–"];
    }));
  }

  // ------------------------------------------------------------------ Trace
  async function renderTrace(box, state = { id: "latest" }) {
    const heading = el("h1", "heading", "Trace");
    box.append(heading, el("p", "muted", "One request end to end: every guardrail check, every model call, library search and tool, with tokens, latency and cost. The last 50 requests, kept in the server's memory only."));
    const list = el("div"), body = el("div", "stack");
    box.append(list, body);
    try {
      const traces = await api("/api/admin/traces");
      if (!heading.isConnected) return;
      if (!traces.length) {
        const empty = el("div", "empty"); empty.append(el("h2", "heading", "Nothing to show yet"), el("p", "muted", "Chat with Léa on the demo site or run the golden set, then come back to see every step."));
        box.append(empty); return;
      }
      const show = async (id) => {
        state.id = id;
        list.replaceChildren(section("Recent requests", traceList(traces, state, show)));
        try { body.replaceChildren(...traceView(await api("/api/admin/traces/" + encodeURIComponent(id)))); }
        catch (error) { body.replaceChildren(errorBox("Could not load the trace: " + error.message)); }
      };
      show(state.id === "latest" ? traces[0].id : state.id);
    } catch (error) {
      if (heading.isConnected) box.append(errorBox("Could not load the traces: " + error.message));
    }
  }

  function traceList(traces, state, show) {
    const box = el("div", "scroll-box");
    box.append(table(["Time", "Source", "Question", "Outcome", "#Latency", "#Cost"], traces.map((t) => ({
      selected: t.id === state.id, onClick: () => show(t.id),
      cells: [fmt.time(t.ts), t.source === "eval" ? "eval" : "visitor", (t.question || "").slice(0, 80),
        badge(t.outcome === "blocked" ? "fail" : t.outcome === "corrected" || t.outcome === "waiting for confirmation" ? "warning" : "ok", t.outcome),
        fmt.secs(t.totals.latency_ms), fmt.usd(t.totals.cost_usd)],
    }))));
    return box;
  }

  function traceView(t) {
    const summary = el("div", "card stack");
    summary.append(el("p", "label", (t.signed_in ? "Signed-in customer" : "Guest") + " · " + (t.source === "eval" ? "eval run" : "visitor")),
      el("p", "", t.question), el("p", "muted", t.answer || "(no text: the page shows a card)"));
    const totals = t.totals;
    summary.append(el("p", "small muted", ["Outcome " + t.outcome, fmt.secs(totals.latency_ms), fmt.usd(totals.cost_usd),
      fmt.tokens(totals.input_tokens, totals.output_tokens), totals.model_calls + " model calls", totals.library_calls + " library searches",
      "trace " + t.id, fmt.time(t.ts)].join(" · ")));
    if (t.sources && t.sources.length) summary.append(el("p", "small muted", "Sources: " + t.sources.join(", ")));
    const events = section("Guardrail checks", table(["Rule", "Stage", "Outcome", "Detail"],
      (t.guardrails || []).map((g) => [g.rule, g.stage, badge(OUTCOME_BADGE[g.outcome] || "muted-badge", g.outcome), g.detail || ""])));
    const steps = (t.steps || []).map(stepCard);
    return [summary, events, section("Steps", ...steps)];
  }

  function stepCard(step) {
    const card = el("div", "card stack");
    const header = el("div", "row");
    header.append(el("span", "", step.name), el("span", "muted", step.handler || ""));
    const figures = [];
    if (step.latency_ms != null) figures.push(fmt.secs(step.latency_ms));
    if (step.kind === "model") figures.push(fmt.tokens(step.input_tokens, step.output_tokens));
    if (step.cost_usd) figures.push(fmt.usd(step.cost_usd));
    header.append(badge(step.kind === "model" ? "warning" : step.kind === "library" ? "muted-badge" : "ok", step.kind === "model" ? "model" : step.kind),
      el("span", "small muted", figures.join(" · ")));
    card.append(header);
    for (const [title, value] of [["Request", step.request], ["Raw response", step.response], ["Result", step.result]]) {
      if (value === undefined) continue;
      const details = el("details");
      details.append(el("summary", "small", title), el("pre", "code", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
      card.append(details);
    }
    return card;
  }

  // ------------------------------------------------------------------ Cost
  async function renderCost(box) {
    const heading = el("h1", "heading", "Cost");
    box.append(heading);
    try {
      const data = await api("/api/admin/cost");
      if (!heading.isConnected) return;
      const priceRows = data.prices.map((p) => [p.item, el("span", "code", p.id),
        p.input == null ? "–" : "$" + p.input.toFixed(2), p.output == null ? "–" : "$" + p.output.toFixed(2), p.unit, p.used_for]);
      const link = el("a", "", "mistral.ai/pricing/api"); link.href = data.source; link.target = "_blank"; link.rel = "noopener";
      const source = el("p", "small muted", "List prices in US dollars, read on " + data.checked_on + " from "); source.append(link);
      box.append(section("List prices", table(["Item", "Id", "#Input", "#Output", "Unit", "Used for"], priceRows), source));
      box.append(section("Cost per use case, visitors", useCaseTable(data.live.per_use_case)));
      box.append(section("Cost per use case, eval runs", useCaseTable(data.eval.per_use_case)));
      box.append(lastRequest(data.last_trace));
      const totals = el("div", "grid-tiles");
      totals.append(tile("Visitors since start", fmt.usd(data.live.cost_usd.total, 3), data.live.turns + " turns"),
        tile("Eval runs since start", fmt.usd(data.eval.cost_usd.total, 3), data.eval.turns + " turns"),
        tile("Last golden-set run", fmt.usd(data.last_run && data.last_run.cost_usd, 3), data.last_run ? data.last_run.id : "no run"),
        tile("Hosting", "$0", "Render free plan, Frankfurt"));
      box.append(section("Totals", totals));
      box.append(section("What drives the cost", el("p", "muted", "Each model call reads about 2,200 input tokens, mostly the instructions and the tool definitions: about $0.004 at Medium 3.5 prices. Each library search costs $0.01, so a general question costs more than a balance check. Moderation is free. In production: cache frequent library answers, trim the instructions, and route simple questions to Mistral Small.")));
    } catch (error) {
      if (heading.isConnected) box.append(errorBox("Could not load the cost data: " + error.message));
    }
  }

  function useCaseTable(rows) {
    if (!rows || !rows.length) return el("p", "muted", "No turns yet.");
    return table(["Use case", "#Turns", "#Model calls", "#Library searches", "#Mean cost per turn", "#Mean latency"],
      rows.map((r) => [r.use_case, r.turns, r.model_calls, r.library_calls, fmt.usd(r.cost_usd), fmt.secs(r.latency_ms)]));
  }

  function lastRequest(trace) {
    if (!trace) return section("Last request", el("p", "muted", "No request yet."));
    const rows = (trace.steps || []).filter((s) => s.cost_usd != null || s.kind === "model")
      .map((s) => [s.name, s.handler || "", s.kind === "model" ? fmt.tokens(s.input_tokens, s.output_tokens) : "", fmt.usd(s.cost_usd || 0)]);
    rows.push(["Total", "", fmt.tokens(trace.totals.input_tokens, trace.totals.output_tokens), fmt.usd(trace.totals.cost_usd)]);
    return section("Last request: " + (trace.question || ""), table(["Step", "Handler", "Tokens", "#Cost"], rows));
  }

  // ------------------------------------------------------------------ Guardrails
  const RULES = [
    ["Card number, PIN or password", "input", "code: patterns, Luhn check", "blocks", (g) => g.blocked_before_agent.secret_in_message],
    ["Fake session note", "input", "code: pattern", "blocks", (g) => g.blocked_before_agent.spoofed_session],
    ["Jailbreak attempt", "input", "Mistral moderation", "score ≥ 0.3 blocks", (g) => g.blocked_before_agent.jailbreaking],
    ["Personal data", "input", "Mistral moderation", "score ≥ 0.5 is flagged, not blocked", (g) => g.flagged_not_blocked.pii],
    ["Guest block", "tool", "code: demo_bank.check", "refuses banking tools", (g) => g.tool_refusals.SIGN_IN_REQUIRED],
    ["Customer = signed-in customer", "tool", "code: demo_bank.check", "refuses another ID", (g) => g.tool_refusals.CUSTOMER_MISMATCH],
    ["Known card or account", "tool", "code: demo_bank.check", "refuses", (g) => (g.tool_refusals.CARD_NOT_FOUND || 0) + (g.tool_refusals.ACCOUNT_NOT_FOUND || 0)],
    ["Confirmation hold", "tool", "code: lea._tool_loop", "a lock waits for the tap", (g) => Object.values(g.confirmations).reduce((a, b) => a + b, 0)],
    ["Amount equals the tool result", "output", "code: check_output", "replaces the answer", (g) => g.corrected_after_agent.amount_not_from_tool],
    ["'Locked' only after a lock", "output", "code: check_output", "replaces the answer", (g) => g.corrected_after_agent.lock_claim_without_tool],
  ];

  async function renderGuardrails(box, state = { src: "live" }) {
    const heading = el("h1", "heading", "Guardrails");
    box.append(heading, el("p", "muted", "Every check around the model, where it runs and how often it acted. The model proposes; these checks decide."));
    const seg = el("div", "chips");
    const body = el("div", "stack");
    box.append(seg, body);
    try {
      const [summary, traces] = await Promise.all([api("/api/admin/summary"), api("/api/admin/traces")]);
      if (!heading.isConnected) return;
      const draw = () => {
        seg.replaceChildren(...[["live", "Visitors"], ["eval", "Eval runs"]].map(([key, text]) => {
          const b = el("button", key === state.src ? "button-primary" : "button-secondary", text); b.type = "button";
          b.addEventListener("click", () => { state.src = key; draw(); }); return b;
        }));
        const g = summary[state.src].guardrails;
        const rows = RULES.map(([rule, stage, how, action, count]) => [rule, stage, how, action, count(g) || 0]);
        const conf = g.confirmations;
        body.replaceChildren(
          table(["Rule", "Stage", "How", "Action", "#Times it acted"], rows),
          el("p", "small muted", g.passed_input_check + " of " + summary[state.src].turns + " turns passed the input check. Card locks: " +
            (conf.allowed || 0) + " allowed, " + (conf.denied || 0) + " denied, " + (conf.not_confirmed || 0) + " left unconfirmed."),
          section("Recent checks that acted", recentEvents(traces.filter((t) => (t.source === "eval") === (state.src === "eval")))));
      };
      draw();
    } catch (error) {
      if (heading.isConnected) box.append(errorBox("Could not load the guardrails: " + error.message));
    }
  }

  function recentEvents(traces) {
    const rows = [];
    for (const t of traces) for (const g of t.guardrails || []) if (g.outcome !== "pass") rows.push([fmt.time(t.ts), g.rule, g.stage, badge(OUTCOME_BADGE[g.outcome] || "muted-badge", g.outcome), g.detail || "", (t.question || "").slice(0, 60)]);
    if (!rows.length) return el("p", "muted", "None in the kept traces.");
    const box = el("div", "scroll-box"); box.append(table(["Time", "Rule", "Stage", "Outcome", "Detail", "Question"], rows.slice(0, 40))); return box;
  }

  // ------------------------------------------------------------------ Agent
  async function renderAgent(box) {
    const heading = el("h1", "heading", "Agent");
    box.append(heading, el("p", "muted", "The agent as Mistral has it right now, read live from the Agents API with the server's key, and the health of every dependency."));
    const healthBlock = el("div");
    box.append(healthBlock);
    const health = await loadHealth();
    if (health) healthBlock.append(section("Health", healthList(health)));
    try {
      const data = await api("/api/admin/agent");
      if (!heading.isConnected) return;
      const a = data.agent, args = a.completion_args || {};
      const facts = el("div", "grid-tiles");
      facts.append(tile("Name", a.name), tile("Version", String(a.version), "earlier versions restorable in Studio"),
        tile("Model", a.model), tile("Temperature", String(args.temperature), "max tokens " + args.max_tokens));
      const studio = el("a", "", "Open in Mistral Studio"); studio.href = "https://console.mistral.ai/build/agents/" + a.id; studio.target = "_blank"; studio.rel = "noopener";
      box.append(section("Studio agent", facts, el("p", "code muted", a.id), studio));
      const tools = (a.tools || []).map((t) => {
        if (t.type === "function") {
          const f = t.function || {};
          return [el("span", "code", f.name), "function", "our server (demo_bank)", f.description || "", el("span", "code", ((f.parameters || {}).required || []).join(", "))];
        }
        return [el("span", "code", t.type), "built-in", "Mistral", t.type === "document_library" ? "searches the library: " + (t.library_ids || []).join(", ") : "", ""];
      });
      box.append(section("Tools the model may ask for", table(["Name", "Type", "Runs on", "Description", "Required fields"], tools)));
      const instructions = el("details"); instructions.append(el("summary", "small", "Instructions, " + (a.instructions || "").length + " characters"), el("pre", "code", a.instructions || ""));
      box.append(section("Instructions", instructions));
      for (const lib of data.libraries || []) {
        const docs = table(["Document", "#Size", "Status", "Processed"], (lib.documents || []).map((d) => [d.name, (d.size || 0).toLocaleString("en-GB") + " B", badge(d.process_status === "done" ? "ok" : "warning", d.process_status), fmt.time(d.last_processed_at)]));
        box.append(section("Library: " + lib.name, el("p", "small muted", "Id " + lib.id + (lib.sharing_scope ? " · sharing " + lib.sharing_scope : "")), docs));
      }
    } catch (error) {
      if (heading.isConnected) box.append(errorBox(error.message));
    }
  }

  // ------------------------------------------------------------------ EU AI Act
  const EU = [
    ["What this system is", [
      "An AI system in the sense of Article 3(1): software that infers from a customer's message which tools to ask for and how to phrase an answer. It is built on Mistral Medium 3.5, a general-purpose model, reached through Mistral's Agents API.",
      "In a BNP Paribas deployment the bank is the deployer, and the party that builds and markets the assistant is its provider. Mistral is the provider of the general-purpose model."]],
    ["Risk category", [
      "Not prohibited: none of the practices of Article 5 apply to a support assistant.",
      "Not high-risk: it does not assess creditworthiness or set credit terms (Annex III, point 5b), and it takes no decision on the customer. It informs, runs simple requested actions after the customer's confirmation, and hands over to a human.",
      "Transparency duty: Article 50(1) requires that people be told they interact with an AI system. Léa says so in the header, the welcome message and whenever asked."]],
    ["Practices this demo follows beyond its risk level", [
      "Traceability: every request is traced end to end on the Trace page, and every turn is logged with a trace ID. This mirrors the record-keeping idea of Article 12.",
      "Human oversight: a card lock runs only after the customer's own tap and in-app approval; an advisor is always one tap away and gets a summary.",
      "Accuracy and robustness: a golden set of sixteen conversations, checked by code on four layers, gates every change. This is the kind of evidence Article 15 asks of high-risk systems."]],
    ["Personal data (GDPR)", [
      "Card numbers, PINs and passwords are masked in the page and blocked by the server; they never reach the model. The data in the demo is synthetic.",
      "Turn records hold no message text. Traces with the question and answer are kept in memory only, the last fifty, for this console. In production: retention agreed with the DPO, and EU or on-premises hosting per data class."]],
    ["Timeline that applies", [
      "Prohibited practices and AI literacy since 2 February 2025; general-purpose model obligations since 2 August 2025; the Article 50 transparency obligations since 2 August 2026, so they apply today.",
      "High-risk obligations phase in later and do not apply to this use. An engineering reading of Regulation (EU) 2024/1689, not legal advice; to confirm with BNP's compliance team."]],
  ];

  function renderEuAiAct(box) {
    box.append(el("h1", "heading", "EU AI Act"), el("p", "muted", "A working assessment of Léa under Regulation (EU) 2024/1689, written on 28 September 2026. Not legal advice."));
    for (const [title, lines] of EU) {
      const card = el("div", "card stack"); card.append(el("p", "label", title));
      for (const line of lines) card.append(el("p", "", line));
      box.append(card);
    }
  }

  // ------------------------------------------------------------------ router
  const PAGES = { overview: renderOverview, eval: renderEval, trace: renderTrace, cost: renderCost,
    guardrails: renderGuardrails, agent: renderAgent, "eu-ai-act": renderEuAiAct };

  function route() {
    const name = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
    for (const item of menu.querySelectorAll(".menu-item")) {
      const active = item.dataset.page === name;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    }
    const box = el("div", "page"); // a fresh container: a page that is left notices it is disconnected
    pageBox.replaceChildren(box);
    PAGES[name](box);
  }

  window.addEventListener("hashchange", route);
  route();
  loadHealth();
  setInterval(loadHealth, 60000);
})();
