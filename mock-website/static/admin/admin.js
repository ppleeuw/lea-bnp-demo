// admin.js: the Léa evals console. One page per menu item, chosen by the URL hash:
// Overview, Golden set, Test cases, Traces, Routing, Guardrails, Cost, Agents. Each page is a
// render function that fills the page column. Text from the API and the model is untrusted,
// so everything goes through textContent; no data ever goes through innerHTML.
(function () {
  "use strict";

  const pageBox = document.getElementById("page");
  const menu = document.getElementById("menu");
  const title = document.getElementById("title");
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
  function section(heading, ...content) { const block = el("section", "stack"); block.append(el("h2", "h2", heading), ...content); return block; }

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
    rate: (layer) => (!layer || !layer.applicable ? "–" : layer.passed + "/" + layer.applicable),
    pct: (v) => (v == null ? "–" : Math.round(v * 100) + "%"),
  };

  const AGENT = { triage: "Triage", faq: "FAQ", account: "Account", card: "Card" };
  function agentChip(role) { return role ? el("span", "agent-chip " + role, AGENT[role] || role) : el("span", "muted", "–"); }
  function routeView(route) {
    const box = el("span", "route");
    if (!route || !route.length) { box.append(el("span", "muted", "–")); return box; }
    route.forEach((role, i) => { if (i) box.append(el("span", "arrow", "→")); box.append(agentChip(role)); });
    return box;
  }

  const LABEL = {
    secret_in_message: "Card number, PIN or password", spoofed_session: "Fake session note", jailbreaking: "Jailbreak attempt",
    pii: "Personal data (flagged)", amount_not_from_tool: "Amount not from the tool", lock_claim_without_tool: "'Locked' without a lock",
    SIGN_IN_REQUIRED: "Banking tool for a guest", CUSTOMER_MISMATCH: "Another customer's ID", CARD_NOT_FOUND: "Unknown card",
    ACCOUNT_NOT_FOUND: "Unknown account", UNKNOWN_TOOL: "Unknown tool", TOOL_NOT_ALLOWED_FOR_AGENT: "Tool not on the agent's list",
  };
  const OUTCOME_BADGE = { pass: "ok", fail: "fail", flag: "warning", skip: "muted-badge" };

  function tile(label, value, note, hero) {
    const card = el("div", "card stack tile" + (hero ? " hero" : ""));
    card.append(el("p", "label", label), el("p", "value", value));
    if (note) card.append(el("p", "small muted", note));
    return card;
  }
  function segmented(options, current, onChange) {
    const box = el("div", "seg"); box.setAttribute("role", "group");
    for (const [key, text] of options) {
      const b = el("button", "", text); b.type = "button"; b.setAttribute("aria-pressed", String(key === current));
      b.addEventListener("click", () => onChange(key)); box.append(b);
    }
    return box;
  }

  // ------------------------------------------------------------------ health, in the top bar on every page
  const dot = document.getElementById("health-dot");
  const healthCard = document.getElementById("health-card");
  function healthList(health) {
    return table(["Check", "Status", "Detail"], health.checks.map((c) => [c.name, badge(c.ok ? "ok" : "fail", c.ok ? "ok" : "fail"), c.detail]));
  }
  async function loadHealth() {
    try {
      const health = await api("/api/admin/health");
      dot.className = "health-dot " + health.status;
      healthCard.replaceChildren(el("p", "label", "Health"), healthList(health));
      return health;
    } catch (error) {
      dot.className = "health-dot down";
      healthCard.replaceChildren(errorBox("Could not check the health: " + error.message));
      return null;
    }
  }
  dot.addEventListener("click", () => { healthCard.hidden = !healthCard.hidden; dot.setAttribute("aria-expanded", String(!healthCard.hidden)); });
  document.addEventListener("click", (e) => {
    if (!healthCard.hidden && !healthCard.contains(e.target) && !dot.contains(e.target)) { healthCard.hidden = true; dot.setAttribute("aria-expanded", "false"); }
  });

  // ------------------------------------------------------------------ Overview
  async function renderOverview(box) {
    const tiles = el("div", "grid-tiles"), chartBlock = el("div"), healthBlock = el("div");
    box.append(tiles, chartBlock, healthBlock);
    try {
      const [summary, health] = await Promise.all([api("/api/admin/summary"), loadHealth()]);
      if (!box.isConnected) return;
      const last = (summary.runs || []).find((r) => r.status === "done");
      const live = summary.live;
      const layers = (last && last.layers) || {};
      tiles.append(
        tile("Golden set", last ? last.passed + " / " + last.total : "–", last ? fmt.time(last.finished_at || last.started_at) : "no run yet", true),
        tile("Routing", fmt.rate(layers.routing), "right agent answered"),
        tile("Guardrail cases", last && last.guardrail_cases ? last.guardrail_cases.passed + " / " + last.guardrail_cases.total : "–", "stopped as expected"),
        tile("Cost of that run", fmt.usd(last && last.cost_usd, 3), last ? last.total + " cases" : ""),
        tile("Visitor turns", String(live.turns), live.conversations + " conversations"),
        tile("Latency p50 / p95", fmt.secs(live.latency_ms.p50) + " / " + fmt.secs(live.latency_ms.p95), "per visitor turn"),
        tile("Cost per visitor turn", fmt.usd(live.cost_usd.per_turn), "per conversation " + fmt.usd(live.cost_usd.per_conversation)));
      chartBlock.append(section("Latency per turn", legend(), latencyChart(summary.recent || [])));
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
    const box = el("div", "card chart");
    const data = recent.slice(0, 60).reverse();
    if (!data.length) { box.append(el("p", "muted", "No turns yet.")); return box; }
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
        const route = (d.route || []).map((r) => AGENT[r] || r).join(" → ") || "no agent";
        const guard = d.guardrail_in || d.guardrail_out;
        tip.replaceChildren(el("div", "", fmt.time(d.ts) + " · " + (d.source === "eval" ? "eval" : "visitor")),
          el("div", "", fmt.secs(d.latency_ms) + " · " + fmt.usd(d.cost_usd) + " · " + d.model_calls + " model calls"),
          el("div", "muted", route + (guard ? " · stopped: " + (LABEL[guard] || guard) : "")));
        tip.style.left = (20 + (L + i * step + step / 2) * k) + "px";
        tip.style.top = (20 + yt * k) + "px";
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

  // ------------------------------------------------------------------ Golden set
  const LAYERS = [["routing", "Routing"], ["tools", "Tools"], ["confirmation", "Confirmation"], ["guardrails", "Guardrails"], ["answer", "Answer"]];

  async function renderRuns(box, state = { selected: null, timer: null }) {
    const run = el("button", "button-primary", "Run the golden set"); run.type = "button";
    const progress = el("p", "small muted");
    const matrix = el("div"), history = el("div"), detail = el("div");
    const toolbar = el("div", "chips"); toolbar.append(run, progress);
    box.append(toolbar, matrix, detail, history);

    async function refresh() {
      if (!toolbar.isConnected) return; // the page was left: stop polling
      try {
        const summary = await api("/api/admin/summary");
        const runs = summary.runs || [];
        const running = runs.find((r) => r.status === "running");
        run.disabled = !!running;
        progress.textContent = running ? "Running: " + running.done + " of " + running.total + " cases done" : "";
        const done = runs.filter((r) => r.status === "done");
        matrix.replaceChildren(section("Latest runs by layer", layerMatrix(done.slice(0, 4))));
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
        detail.replaceChildren(section("Cases · run " + data.id, caseTable(data.cases || [])));
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

  function layerMatrix(runs) {
    if (!runs.length) return el("p", "muted", "No run yet.");
    const headers = [""].concat(runs.map((r, i) => (i === 0 ? "Latest · " : "") + fmt.time(r.finished_at || r.started_at)));
    const row = (name, cell) => [name].concat(runs.map(cell));
    const rows = LAYERS.map(([key, name]) => row(name, (r) => (r.layers ? fmt.rate(r.layers[key]) : "–")));
    rows.push(row("Cases passed", (r) => badge(r.passed === r.total ? "ok" : "fail", r.passed + "/" + r.total)));
    rows.push(row("Mean time per case", (r) => fmt.secs(r.latency_ms_mean)));
    rows.push(row("Cost of the run", (r) => fmt.usd(r.cost_usd, 3)));
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
    return table(["Case", "Result", "Route", "#Time", "#Cost", "Tools", "Guardrail"], cases.map((c) => {
      const name = el("div", "stack"); name.style.gap = "2px"; name.append(el("span", "", c.name));
      if (!c.passed) { name.append(el("span", "small fail-text", (c.problems || []).join("; ")), el("span", "small muted", "Answer: " + (c.answer || ""))); }
      const routes = el("div", "stack"); routes.style.gap = "4px";
      const turns = (c.route || []).map((r) => (r ? r.split(" → ") : []));
      if (turns.length) turns.forEach((r) => routes.append(routeView(r))); else routes.append(el("span", "muted", "–"));
      return [name, badge(c.passed ? "ok" : "fail", c.passed ? "pass" : "fail"), routes, fmt.secs(c.latency_ms), fmt.usd(c.cost_usd),
        el("span", "code", (c.tools || []).join(", ") || "–"), LABEL[c.guardrail] || c.guardrail || "–"];
    }));
  }

  // ------------------------------------------------------------------ Test cases
  async function renderCases(box) {
    try {
      const cases = await api("/api/admin/cases");
      if (!box.isConnected) return;
      const list = (xs) => el("span", "code", (xs || []).join(", ") || "–");
      const answer = (c) => {
        const parts = [];
        if (c.must_contain) parts.push("has " + c.must_contain.join(", "));
        if (c.must_contain_any) parts.push("has one of " + c.must_contain_any.join(" | "));
        if (c.must_not_contain) parts.push("not " + c.must_not_contain.join(", "));
        return el("span", "small", parts.join(" · ") || "–");
      };
      const turns = (c) => {
        const box = el("div", "stack"); box.style.gap = "2px";
        for (const t of c.turns) box.append(el("span", typeof t === "string" ? "" : "muted", typeof t === "string" ? "“" + t + "”" : "(tap: " + (t.confirm === "allow" ? "Lock card" : "Keep card active") + ")"));
        return box;
      };
      box.append(el("div", "grid-tiles"));
      box.lastChild.append(
        tile("Cases", String(cases.length), null, true),
        tile("Routing checks", String(cases.filter((c) => c.expect_agent).length)),
        tile("Guardrail cases", String(cases.filter((c) => c.expect_guardrail).length)),
        tile("Signed in", String(cases.filter((c) => c.auth).length), (cases.length - cases.filter((c) => c.auth).length) + " as a guest"));
      box.append(table(["Case", "Customer says", "Agent", "Tools that must run", "Tools that must not", "Guardrail", "Answer"], cases.map((c) => [
        el("span", "", c.name), turns(c), agentChip(c.expect_agent), list((c.expect_tools || []).concat(c.expect_pending ? [c.expect_pending + " (waits)"] : [])),
        list(c.forbid_tools), c.expect_guardrail ? badge("warning", LABEL[c.expect_guardrail] || c.expect_guardrail) : el("span", "muted", "–"), answer(c)])));
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the cases: " + error.message));
    }
  }

  // ------------------------------------------------------------------ Traces
  async function renderTraces(box, state = { id: "latest" }) {
    const list = el("div"), body = el("div", "stack");
    box.append(list, body);
    try {
      const traces = await api("/api/admin/traces");
      if (!box.isConnected) return;
      if (!traces.length) { const empty = el("div", "empty"); empty.append(el("h2", "h2", "No traces yet")); box.replaceChildren(empty); return; }
      const show = async (id) => {
        state.id = id;
        list.replaceChildren(section("Recent requests", traceList(traces, state, show)));
        try { body.replaceChildren(...traceView(await api("/api/admin/traces/" + encodeURIComponent(id)))); }
        catch (error) { body.replaceChildren(errorBox("Could not load the trace: " + error.message)); }
      };
      show(state.id === "latest" ? traces[0].id : state.id);
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the traces: " + error.message));
    }
  }

  function outcomeBadge(outcome) {
    return badge(outcome === "blocked" ? "fail" : outcome === "corrected" || outcome === "waiting for confirmation" ? "warning" : "ok", outcome);
  }

  function traceList(traces, state, show) {
    const box = el("div", "scroll-box");
    box.append(table(["Time", "Source", "Question", "Route", "Outcome", "#Latency", "#Cost"], traces.map((t) => ({
      selected: t.id === state.id, onClick: () => show(t.id),
      cells: [fmt.time(t.ts), t.source === "eval" ? "eval" : "visitor", (t.question || "").slice(0, 70), routeView(t.route),
        outcomeBadge(t.outcome), fmt.secs(t.totals.latency_ms), fmt.usd(t.totals.cost_usd)],
    }))));
    return box;
  }

  function traceView(t) {
    const summary = el("div", "card stack");
    const head = el("div", "row"); head.append(el("span", "label", (t.signed_in ? "Signed-in customer" : "Guest") + " · " + (t.source === "eval" ? "eval" : "visitor")), outcomeBadge(t.outcome), routeView(t.route));
    summary.append(head, el("p", "", t.question), el("p", "muted", t.answer || "(no text: the page shows a card)"));
    const totals = t.totals;
    summary.append(el("p", "small muted", [fmt.secs(totals.latency_ms), fmt.usd(totals.cost_usd), fmt.tokens(totals.input_tokens, totals.output_tokens),
      totals.model_calls + " model calls", totals.library_calls + " library searches", "trace " + t.id, fmt.time(t.ts)].join(" · ")));
    if (t.sources && t.sources.length) summary.append(el("p", "small muted", "Sources: " + t.sources.join(", ")));
    const events = section("Checks", table(["Rule", "Stage", "Outcome", "Detail"],
      (t.guardrails || []).map((g) => [g.rule, g.stage, badge(OUTCOME_BADGE[g.outcome] || "muted-badge", g.outcome), g.detail || ""])));
    return [summary, events, section("Steps", ...(t.steps || []).map(stepCard))];
  }

  const KIND_BADGE = { model: "warning", handoff: "ok", library: "muted-badge", tool: "ok", check: "muted-badge" };
  function stepCard(step) {
    const card = el("div", "card stack");
    const header = el("div", "row");
    header.append(badge(KIND_BADGE[step.kind] || "muted-badge", step.kind), el("span", "", step.name), el("span", "small muted", step.handler || ""));
    if (step.agents && step.agents.length) header.append(routeView(step.agents));
    const figures = [];
    if (step.latency_ms != null) figures.push(fmt.secs(step.latency_ms));
    if (step.kind === "model") figures.push(fmt.tokens(step.input_tokens, step.output_tokens));
    if (step.priced_as) figures.push("priced as " + step.priced_as);
    if (step.cost_usd) figures.push(fmt.usd(step.cost_usd));
    header.append(el("span", "small muted", figures.join(" · ")));
    card.append(header);
    for (const [name, value] of [["Request", step.request], ["Raw response", step.response], ["Result", step.result]]) {
      if (value === undefined) continue;
      const details = el("details");
      details.append(el("summary", "small", name), el("pre", "code", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
      card.append(details);
    }
    return card;
  }

  // ------------------------------------------------------------------ Routing
  async function renderRouting(box, state = { src: "eval" }) {
    const bar = el("div", "chips"), body = el("div", "stack");
    body.style.gap = "24px";
    box.append(bar, body);
    try {
      const [summary, cases] = await Promise.all([api("/api/admin/summary"), api("/api/admin/cases")]);
      const last = (summary.runs || []).find((r) => r.status === "done");
      const run = last ? await api("/api/admin/runs/" + encodeURIComponent(last.id)) : null;
      if (!box.isConnected) return;
      const models = Object.fromEntries((summary.agents || []).map((a) => [a.role, a.model]));
      const draw = () => {
        bar.replaceChildren(segmented([["eval", "Eval runs"], ["live", "Visitors"]], state.src, (k) => { state.src = k; draw(); }));
        const r = summary[state.src].routing;
        const tiles = el("div", "grid-tiles");
        tiles.append(
          tile("Routing in latest run", last && last.layers ? fmt.rate(last.layers.routing) : "–", last ? fmt.time(last.finished_at) : "no run yet", true),
          tile("Turns routed", String(r.turns)),
          tile("Handed off", r.turns ? fmt.pct(r.handed_off / r.turns) : "–", r.handed_off + " turns"),
          tile("Handoffs", String(r.handoffs), r.turns ? (r.handoffs / r.turns).toFixed(1) + " per turn" : ""));
        const total = r.answered_by.reduce((a, x) => a + x.turns, 0) || 1;
        const share = (n) => { const b = el("div", "bar"); const i = el("i"); i.style.width = Math.round((n / total) * 100) + "%"; b.append(i); return b; };
        const byAgent = r.answered_by.length
          ? table(["Answered by", "Model", "#Turns", "Share", "#Mean cost", "#Mean latency"], r.answered_by.map((a) =>
            [agentChip(a.agent), el("span", "code nowrap", models[a.agent] || "–"), a.turns, share(a.turns), fmt.usd(a.cost_usd), fmt.secs(a.latency_ms)]))
          : el("p", "muted", "No turns yet.");
        const routes = Object.entries(r.routes);
        const routeTable = routes.length ? table(["Route", "#Turns"], routes.map(([k, n]) => [routeView(k.split(" → ")), n])) : el("p", "muted", "No turns yet.");
        body.replaceChildren(tiles, section("Answered by", byAgent), section("Routes", routeTable), section("Expected agent, latest run", expectedTable(cases, run)));
      };
      draw();
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the routing: " + error.message));
    }
  }

  function expectedTable(cases, run) {
    if (!run) return el("p", "muted", "No run yet.");
    const byName = Object.fromEntries((run.cases || []).map((c) => [c.name, c]));
    const rows = cases.filter((c) => c.expect_agent).map((c) => {
      const got = byName[c.name];
      const ok = got && got.agent === c.expect_agent;
      const routes = el("div", "stack"); routes.style.gap = "4px";
      ((got && got.route) || []).forEach((r) => routes.append(routeView(r ? r.split(" → ") : [])));
      return [c.name, agentChip(c.expect_agent), got ? agentChip(got.agent) : el("span", "muted", "not in this run"), routes,
        got ? badge(ok ? "ok" : "fail", ok ? "pass" : "fail") : badge("muted-badge", "–")];
    });
    return table(["Case", "Expected", "Answered by", "Route", "Result"], rows);
  }

  // ------------------------------------------------------------------ Guardrails
  const RULES = [
    ["Card number, PIN or password", "input", "code: patterns, Luhn check", "blocks", (g) => g.blocked_before_agent.secret_in_message],
    ["Fake session note", "input", "code: pattern", "blocks", (g) => g.blocked_before_agent.spoofed_session],
    ["Jailbreak attempt", "input", "Mistral moderation", "score ≥ 0.3 blocks", (g) => g.blocked_before_agent.jailbreaking],
    ["Personal data", "input", "Mistral moderation", "score ≥ 0.5 is flagged", (g) => g.flagged_not_blocked.pii],
    ["Tool on the agent's allow-list", "tool", "code: lea._not_allowed", "refuses", (g) => g.tool_refusals.TOOL_NOT_ALLOWED_FOR_AGENT],
    ["Guest block", "tool", "code: demo_bank.check", "refuses banking tools", (g) => g.tool_refusals.SIGN_IN_REQUIRED],
    ["Customer = signed-in customer", "tool", "code: demo_bank.check", "refuses another ID", (g) => g.tool_refusals.CUSTOMER_MISMATCH],
    ["Known card or account", "tool", "code: demo_bank.check", "refuses", (g) => (g.tool_refusals.CARD_NOT_FOUND || 0) + (g.tool_refusals.ACCOUNT_NOT_FOUND || 0)],
    ["Confirmation hold", "tool", "code: lea._tool_loop", "a lock waits for the tap", (g) => Object.values(g.confirmations).reduce((a, b) => a + b, 0)],
    ["Amount equals the tool result", "output", "code: check_output", "replaces the answer", (g) => g.corrected_after_agent.amount_not_from_tool],
    ["'Locked' only after a lock", "output", "code: check_output", "replaces the answer", (g) => g.corrected_after_agent.lock_claim_without_tool],
  ];

  async function renderGuardrails(box, state = { src: "eval" }) {
    const bar = el("div", "chips"), body = el("div", "stack");
    body.style.gap = "24px";
    box.append(bar, body);
    try {
      const [summary, traces] = await Promise.all([api("/api/admin/summary"), api("/api/admin/traces")]);
      if (!box.isConnected) return;
      const draw = () => {
        bar.replaceChildren(segmented([["eval", "Eval runs"], ["live", "Visitors"]], state.src, (k) => { state.src = k; draw(); }));
        const s = summary[state.src], g = s.guardrails, conf = g.confirmations;
        const tiles = el("div", "grid-tiles");
        tiles.append(
          tile("Passed the input check", g.passed_input_check + " / " + s.turns, "turns", true),
          tile("Blocked before the agents", String(Object.values(g.blocked_before_agent).reduce((a, b) => a + b, 0))),
          tile("Tool calls refused", String(Object.values(g.tool_refusals).reduce((a, b) => a + b, 0))),
          tile("Card locks", String((conf.allowed || 0)), (conf.denied || 0) + " kept active · " + (conf.not_confirmed || 0) + " unconfirmed"));
        body.replaceChildren(tiles,
          section("Rules", table(["Rule", "Stage", "Where", "Action", "#Times it acted"], RULES.map(([rule, stage, how, action, count]) => [rule, stage, el("span", "code", how), action, count(g) || 0]))),
          section("Recent checks that acted", recentEvents(traces.filter((t) => (t.source === "eval") === (state.src === "eval")))));
      };
      draw();
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the guardrails: " + error.message));
    }
  }

  function recentEvents(traces) {
    const rows = [];
    for (const t of traces) for (const g of t.guardrails || []) if (g.outcome !== "pass") rows.push([fmt.time(t.ts), g.rule, g.stage, badge(OUTCOME_BADGE[g.outcome] || "muted-badge", g.outcome), g.detail || "", (t.question || "").slice(0, 60)]);
    if (!rows.length) return el("p", "muted", "None in the kept traces.");
    const box = el("div", "scroll-box"); box.append(table(["Time", "Rule", "Stage", "Outcome", "Detail", "Question"], rows.slice(0, 40))); return box;
  }

  // ------------------------------------------------------------------ Cost
  async function renderCost(box) {
    try {
      const data = await api("/api/admin/cost");
      if (!box.isConnected) return;
      const totals = el("div", "grid-tiles");
      totals.append(tile("Last golden-set run", fmt.usd(data.last_run && data.last_run.cost_usd, 3), data.last_run ? data.last_run.total + " cases" : "no run", true),
        tile("Per visitor turn", fmt.usd(data.live.cost_usd.per_turn), data.live.turns + " turns"),
        tile("Visitors since start", fmt.usd(data.live.cost_usd.total, 3)),
        tile("Eval runs since start", fmt.usd(data.eval.cost_usd.total, 3), data.eval.turns + " turns"),
        tile("Hosting", "$0", "Render free plan, Frankfurt"));
      box.append(totals);
      const priceRows = data.prices.map((p) => [p.item, el("span", "code", p.id),
        p.input == null ? "–" : "$" + p.input.toFixed(2), p.output == null ? "–" : "$" + p.output.toFixed(2), p.unit, p.used_for]);
      const link = el("a", "", "mistral.ai/pricing/api"); link.href = data.source; link.target = "_blank"; link.rel = "noopener";
      const source = el("p", "small muted", "Read on " + data.checked_on + " from "); source.append(link);
      box.append(section("List prices (USD)", table(["Item", "Id", "#Input", "#Output", "Unit", "Used for"], priceRows), source));
      box.append(section("Per use case · eval runs", useCaseTable(data.eval.per_use_case)));
      box.append(section("Per use case · visitors", useCaseTable(data.live.per_use_case)));
      box.append(lastRequest(data.last_trace));
    } catch (error) {
      if (box.isConnected) box.append(errorBox("Could not load the cost data: " + error.message));
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
      .map((s) => [s.name, s.agents ? routeView(s.agents) : el("span", "muted", "–"), s.priced_as ? el("span", "code", s.priced_as) : "–",
        s.kind === "model" ? fmt.tokens(s.input_tokens, s.output_tokens) : "", fmt.usd(s.cost_usd || 0)]);
    rows.push([el("b", "", "Total"), routeView(trace.route), "", fmt.tokens(trace.totals.input_tokens, trace.totals.output_tokens), fmt.usd(trace.totals.cost_usd)]);
    return section("Last request · " + (trace.question || ""), table(["Step", "Agents", "Priced as", "Tokens", "#Cost"], rows),
      el("p", "small muted", "Mistral reports one usage per call; after a handoff all its tokens are priced at the dearer model (upper bound)."));
  }

  // ------------------------------------------------------------------ Agents
  async function renderAgents(box) {
    const healthBlock = el("div");
    box.append(healthBlock);
    const health = await loadHealth();
    if (health && box.isConnected) healthBlock.append(section("Health", healthList(health)));
    try {
      const data = await api("/api/admin/agents");
      if (!box.isConnected) return;
      const tools = (a) => el("span", "code", (a.tools || []).map((t) => (t.type === "function" ? (t.function || {}).name : t.type)).join(", ") || "–");
      const handoffs = (a) => { const s = el("span", "route"); (a.hands_off_to || []).forEach((r) => s.append(agentChip(r))); return s; };
      const studio = (a) => { const l = el("a", "", "Studio"); l.href = "https://console.mistral.ai/build/agents/" + a.id; l.target = "_blank"; l.rel = "noopener"; return l; };
      box.append(section("Agents", table(["Agent", "Name", "Model", "#Version", "#Temperature", "Tools", "Hands off to", ""],
        data.agents.map((a) => [agentChip(a.role), a.name, el("span", "code nowrap", a.model), a.version, (a.completion_args || {}).temperature, tools(a), handoffs(a), studio(a)]))));
      for (const a of data.agents) {
        const card = el("div", "card stack");
        const head = el("div", "row"); head.append(agentChip(a.role), el("span", "", a.name), el("span", "small muted code", a.id));
        const fn = (a.tools || []).map((t) => {
          if (t.type === "function") { const f = t.function || {}; return [el("span", "code", f.name), "function", "our server", f.description || ""]; }
          return [el("span", "code", t.type), "built-in", "Mistral", t.type === "document_library" ? "library " + (t.library_ids || []).join(", ") : ""];
        });
        const instructions = el("details"); instructions.append(el("summary", "small", "Instructions · " + (a.instructions || "").length + " characters"), el("pre", "code", a.instructions || ""));
        card.append(head, table(["Tool", "Type", "Runs on", "Description"], fn), instructions);
        box.append(card);
      }
      for (const lib of data.libraries || []) {
        const docs = table(["Document", "#Size", "Status", "Processed"], (lib.documents || []).map((d) => [d.name, (d.size || 0).toLocaleString("en-GB") + " B", badge(d.process_status === "done" ? "ok" : "warning", d.process_status), fmt.time(d.last_processed_at)]));
        box.append(section("Library · " + lib.name, el("p", "small muted code", lib.id + (lib.sharing_scope ? " · sharing " + lib.sharing_scope : "")), docs));
      }
    } catch (error) {
      if (box.isConnected) box.append(errorBox(error.message));
    }
  }

  // ------------------------------------------------------------------ router
  const PAGES = {
    overview: ["Overview", renderOverview], runs: ["Golden set", renderRuns], cases: ["Test cases", renderCases],
    traces: ["Traces", renderTraces], routing: ["Routing", renderRouting], guardrails: ["Guardrails", renderGuardrails],
    cost: ["Cost", renderCost], agents: ["Agents", renderAgents],
  };

  function route() {
    const name = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
    for (const item of menu.querySelectorAll(".menu-item")) {
      const active = item.dataset.page === name;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    }
    title.textContent = PAGES[name][0];
    document.title = PAGES[name][0] + " · Léa Evals";
    const box = el("div", "page"); // a fresh container: a page that is left notices it is disconnected
    box.style.padding = "0";
    pageBox.replaceChildren(box);
    PAGES[name][1](box);
  }

  window.addEventListener("hashchange", route);
  route();
  loadHealth();
  setInterval(loadHealth, 60000);
})();
