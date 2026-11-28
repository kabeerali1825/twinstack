// ArchTwin frontend: drives the agent pipeline one step per request and keeps the run in localStorage,
// so a refresh or a failed step resumes where it stopped. All server/LLM text is inserted as text, never HTML.
"use strict";

const STORE = "twinstack.run";
const STAGES = [
  ["parse", "Read the input"], ["profile", "Workload Profiler"], ["confirm", "You confirm"], ["price", "Pricer"], ["scout", "Region Scout"],
  ["swap", "Service Swap"], ["skeptic", "Skeptic"], ["judge", "Judge"], ["effort", "Total Cost"],
  ["commit", "Pricing Model"], ["plain", "Plain chat check"],
];
const WORKING = {
  parse: "Reading your architecture", price: "Matching each part to Azure price meters",
  scout: "Pricing every allowed region", swap: "Looking for better-fitting services",
  skeptic: "Checking alternatives against service limits", judge: "Building the three plans",
  commit: "Checking reservations and savings plans", profile: "Working out what to ask you",
  effort: "Estimating ops and migration effort", plain: "Asking a plain AI chat, for comparison",
};
const DEMO_IMAGES = { "contoso-bookings": "/demos/contoso-bookings.png" };
// One color per agent (see [data-agent] in styles.css).
const AGENT_KEY = {
  Parser: "reader", "Diagram Reader": "reader", You: "you", Pricer: "pricer", "Region Scout": "scout",
  "Service Swap": "swap", Skeptic: "skeptic", Judge: "judge", "Cost check": "cost", System: "system",
  "Pricing Model": "model", "Workload Profiler": "profiler", "Total Cost": "cost", "Plain Chat": "system",
};
const STAGE_AGENT = { parse: "reader", profile: "profiler", confirm: "you", price: "pricer", scout: "scout", swap: "swap", skeptic: "skeptic",
  judge: "judge", effort: "cost", commit: "model", plain: "system" };
const REGION_NAMES = {
  westeurope: "West Europe", northeurope: "North Europe", swedencentral: "Sweden Central", francecentral: "France Central",
  germanywestcentral: "Germany West Central", italynorth: "Italy North", polandcentral: "Poland Central",
  spaincentral: "Spain Central", uksouth: "UK South", ukwest: "UK West", switzerlandnorth: "Switzerland North",
  norwayeast: "Norway East", eastus: "East US", eastus2: "East US 2", centralus: "Central US", northcentralus: "North Central US",
  southcentralus: "South Central US", westus: "West US", westus2: "West US 2", westus3: "West US 3",
  westcentralus: "West Central US", canadacentral: "Canada Central", canadaeast: "Canada East",
  southeastasia: "Southeast Asia", eastasia: "East Asia", japaneast: "Japan East", japanwest: "Japan West",
  koreacentral: "Korea Central", centralindia: "Central India", southindia: "South India",
  australiaeast: "Australia East", australiasoutheast: "Australia Southeast",
};
const region = (r) => REGION_NAMES[r] || r;
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const $ = (id) => document.getElementById(id);

let S = null;          // the RunState, as the server returns it
let meta = { services: [], region_groups: {} };
let image = "";        // attached diagram, resized, as a data URI
let planIdx = 0;

// ---------- helpers ----------
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("aria-") && v != null) { el.setAttribute(k, String(v)); continue; }
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const svg = (d) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 12 12"); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", d); p.setAttribute("fill", "none"); p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "1.5"); p.setAttribute("stroke-linecap", "round"); p.setAttribute("stroke-linejoin", "round");
  s.append(p); return s;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const show = (id, on = true) => { $(id).hidden = !on; };
const money = (n) => usd.format(n);
const signed = (n) => (Math.abs(n) < 0.005 ? "No change" : `${n < 0 ? "−" : "+"}${usd.format(Math.abs(n))}`);
const unitPrice = (p) => (p >= 0.01 || p === 0 ? usd.format(p) : `$${p.toPrecision(3)}`);
// Quantities are stored in base units (5,000,000 requests); show them in the meter's unit ('1M' -> 5).
const unitSize = (unit) => { const m = /^(\d+)([KM]?)/.exec(unit) || [0, 1, ""]; return +m[1] * ({ K: 1e3, M: 1e6 }[m[2]] || 1); };
const inUnits = (qty, unit) => +(qty / unitSize(unit)).toPrecision(4);

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg.replace(/^Value error, /, "")).join("; ") : d || `Request failed (${r.status})`);
  }
  return data;
}

function save() {
  if (S.saved) return;
  try { localStorage.setItem(STORE, JSON.stringify(S.step === "parse" ? S : { ...S, input_image: "" })); }
  catch { /* storage full or blocked: the run still works, it just won't resume */ }
}
function clearRun() {
  try { localStorage.removeItem(STORE); } catch {}
  S = null; planIdx = 0;
  if (location.search) history.replaceState(null, "", "/");  // drop ?demo= so a refresh stays on the start screen
  document.body.dataset.view = "start";
  show("workspace", false); show("start"); show("run-error", false); show("new-analysis", false);
  markDemo("");
  window.scrollTo(0, 0);
  $("text").focus();
}
function markDemo(name) {
  for (const b of document.querySelectorAll("[data-demo]")) b.setAttribute("aria-pressed", String(b.dataset.demo === name));
}

// ---------- input ----------
async function readImage(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) throw new Error("Attach a PNG, JPG or WebP image.");
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = url; });
    const scale = Math.min(1, 1500 / Math.max(img.width, img.height));  // ~1,500 px keeps vision tokens low
    const c = h("canvas", { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}

const TEXT_FILE = /\.(drawio|xml|json|bicep|mmd|md|txt)$/i;
const MAX_TEXT = 200_000;

async function attach(file) {
  show("input-error", false);
  if (TEXT_FILE.test(file.name)) {  // draw.io / ARM / Bicep / Mermaid: read as text into the box; the server extracts it
    const text = await file.text();
    if (text.length > MAX_TEXT) {
      $("input-error").textContent = `${file.name} is too large (${text.length.toLocaleString()} characters; the limit is ${MAX_TEXT.toLocaleString()}).`;
      show("input-error"); return;
    }
    detach(); $("text").value = text; $("text").focus();
    return;
  }
  try {
    image = await readImage(file);
    $("preview").src = image; show("attachment");
  } catch (e) { $("input-error").textContent = e.message || "That image couldn't be read."; show("input-error"); }
}
function detach() { image = ""; $("file").value = ""; show("attachment", false); }

async function analyze() {
  const text = $("text").value.trim();
  show("input-error", false);
  if (text.length > MAX_TEXT) {
    $("input-error").textContent = `That's too long (${text.length.toLocaleString()} characters; the limit is ${MAX_TEXT.toLocaleString()}).`;
    show("input-error"); return;
  }
  if (!image && text.length < 10) {
    $("input-error").textContent = "Describe the architecture or attach a diagram first.";
    show("input-error"); $("text").focus(); return;
  }
  $("analyze").disabled = true;
  try {
    S = await api("/api/run/start", { text, image });
    save();
    await drive();
  } catch (e) { $("input-error").textContent = e.message; show("input-error"); }
  finally { $("analyze").disabled = false; }
}

// ---------- the loop ----------
async function drive() {
  let retries = 0;
  const waiting = () => !S || ["done", "failed"].includes(S.step) || (S.step === "confirm" && !S.confirmed);
  render();
  while (!waiting()) {
    try { S = await api("/api/run/step", S); }
    catch (e) { runError(e.message); return; }
    save();
    if (S.error) {
      if (retries++ < 1) { render(); await sleep(1500); continue; }
      runError(S.error); return;
    }
    retries = 0;
    render();
  }
  render();
}
function runError(msg) { $("run-error-text").textContent = msg; show("run-error"); render(); }

// ---------- rendering ----------
function render() {
  if (!S) return;
  const done = S.step === "done" && S.plans.length > 0;
  document.body.dataset.view = done ? "result" : "run";
  show("start", false); show("workspace"); show("new-analysis");
  markDemo(S.demo_name || "");
  renderActivity();
  show("confirm", S.step === "confirm");
  if (S.step === "confirm") renderConfirm();
  const working = !done && S.step !== "confirm" && S.step !== "failed" && $("run-error").hidden;
  show("waiting", working);
  if (working) {
    $("waiting-title").textContent = WORKING[S.step] || "Working";
    $("waiting-text").textContent = S.timeline.length ? S.timeline[S.timeline.length - 1].message : "";
  }
  show("results", done);
  if (done) renderResults();
}

function renderActivity() {
  const order = STAGES.map((s) => s[0]);
  let reached = S.step === "done" ? order.length : order.indexOf(S.step);
  if (S.swap_round > 0) reached = Math.max(reached, order.indexOf("swap"));
  if (S.options.some((o) => o.reviewed)) reached = Math.max(reached, order.indexOf("skeptic"));
  $("stepper").replaceChildren(...STAGES.map(([key, label], i) => {
    const now = key === S.step;
    const cls = now ? (key === "confirm" ? "wait" : "now") : i < reached || S.step === "done" ? "done" : "";
    return h("li", { class: cls, "data-agent": STAGE_AGENT[key], "aria-current": now ? "step" : null }, h("span", { class: "dot" }), label);
  }));
  $("log").replaceChildren(...S.timeline.map((e) => {
    const msg = e.data == null ? h("span", { class: "msg" }, e.message)
      : h("details", { class: "msg" }, h("summary", {}, e.message), h("pre", {}, JSON.stringify(e.data, null, 2)));
    return h("li", { class: `k-${e.kind}`, "data-agent": AGENT_KEY[e.agent] || "system" }, h("span", { class: "agent" }, e.agent), msg);
  }));
  const log = $("log");
  if (S.step !== "done") log.scrollTop = log.scrollHeight;
  $("activity-box").open = S.step !== "done";
  $("ai-cost").textContent = S.llm_calls
    ? `${S.llm_calls} AI call${S.llm_calls === 1 ? "" : "s"}, $${S.ai_cost_usd.toFixed(3)}` : "";
}

function compRow(c) {
  const cell = (field, label, attrs = {}) => h("td", {}, h("input", { value: c[field] ?? "", "data-f": field, "aria-label": label, ...attrs }));
  const tr = h("tr", { class: c.confidence === "low" ? "low" : null, "data-id": c.id, title: c.confidence === "low" ? "The agent wasn't sure about this one" : null },
    cell("service", "Service", { list: "services" }), cell("sku", "Size"),
    cell("quantity", "Count", { type: "number", min: "0", step: "any" }), cell("region", "Region"), cell("details", "Details"),
    h("td", {}, h("button", { type: "button", class: "btn ghost small", onclick: () => tr.remove(), "aria-label": `Remove ${c.service || "this row"}` }, "Remove")));
  return tr;
}

function renderConfirm() {
  if ($("comp-rows").dataset.run === S.run_id) return;  // keep the user's edits
  $("comp-rows").dataset.run = S.run_id;
  $("comp-rows").replaceChildren(...S.components.map(compRow));
  $("confirm-image").src = image || "";
  show("confirm-image", Boolean(image) && S.components.some((c) => c.source === "diagram"));
  const current = [...S.constraints.allowed_regions].sort().join();
  const opts = Object.entries(meta.region_groups).map(([g, list]) =>
    h("option", { value: g, selected: [...list].sort().join() === current }, `${g} (${list.length} regions)`));
  const home = S.components[0]?.region || "westeurope";
  opts.push(h("option", { value: "__home", selected: !opts.some((o) => o.selected) }, `Only ${home}`));
  $("region-group").replaceChildren(...opts);
  $("notes").value = S.constraints.notes || "";

  // Workload Profiler questions: one radio group each, "Not sure" by default.
  const qs = S.questions || [];
  show("questions", qs.length > 0);
  $("question-list").replaceChildren(...qs.map((q) => h("fieldset", { class: "question", "data-q": q.id },
    h("legend", {}, q.question, q.why ? h("small", {}, ` (${q.why})`) : null),
    [...q.options, "Not sure"].map((choice) => h("label", { class: "choice" },
      h("input", { type: "radio", name: q.id, value: choice, checked: (q.answer || "Not sure") === choice }), choice)))));
}

async function confirmComponents() {
  let n = Math.max(0, ...S.components.map((c) => +c.id.slice(1) || 0));
  S.components = [...$("comp-rows").querySelectorAll("tr")].map((tr) => {
    const get = (f) => tr.querySelector(`[data-f="${f}"]`).value.trim();
    const old = S.components.find((c) => c.id === tr.dataset.id);
    return {
      ...(old || { id: `c${++n}`, confidence: "high", source: "user" }),
      service: get("service"), sku: get("sku") || null, quantity: parseFloat(get("quantity")) || 1,
      region: get("region") || "westeurope", details: get("details"),
    };
  }).filter((c) => c.service);
  const g = $("region-group").value;
  S.constraints = {
    allowed_regions: g === "__home" ? [S.components[0]?.region || "westeurope"] : meta.region_groups[g],
    notes: $("notes").value.trim(),
  };
  for (const q of S.questions || []) {
    q.answer = document.querySelector(`input[name="${q.id}"]:checked`)?.value || "";
  }
  S.confirmed = true;
  show("confirm-error", false);
  $("confirm-btn").disabled = true;
  try { await drive(); }
  catch (e) { $("confirm-error").textContent = e.message; show("confirm-error"); }
  finally { $("confirm-btn").disabled = false; }
}

// ---------- results ----------
const opt = (id) => S.options.find((o) => o.id === id);
const comp = (id) => S.components.find((c) => c.id === id);
const cand = (oid, region) => S.candidates.find((c) => c.option_id === oid && c.region === region);
const label = (o) => `${o.service}${o.sku ? " " + o.sku : ""}`;

function meterList(title, c) {
  return h("div", {}, h("h3", {}, title),
    c ? c.lines.map((l) => h("div", { class: "meter" },
      h("span", {}, `${l.meter}: ${inUnits(l.qty, l.unit)} × ${unitPrice(l.unit_price)} per ${l.unit}`, h("code", { title: "Azure meterId" }, l.meter_id)),
      h("span", { class: "amount" }, money(l.monthly))))
      : h("div", { class: "meter muted" }, "Not needed in this plan"));
}

function compareRow(it) {
  const c = comp(it.component_id), orig = opt(`${c.id}.orig`), today = cand(orig.id, c.region);
  const todayCost = today ? today.monthly_cost : 0;
  const o = opt(it.option_id), twin = it.replaced_by ? null : cand(it.option_id, it.region);
  const covers = o.also_replaces.map((id) => comp(id)).filter(Boolean).map((x) => x.service).join(", ");
  const planSub = it.replaced_by ? `Covered by ${label(o)}`
    : !o.is_original ? `${o.is_fix ? "Fix: " : ""}${label(o)} in ${region(it.region)}${covers ? `, also replaces ${covers}` : ""}`
    : it.region !== c.region ? `Same, moved to ${region(it.region)}` : "Same";
  const delta = it.monthly_cost - todayCost;
  const detail = h("tr", { class: "detail", hidden: true },
    h("td", { colspan: 4 },
      h("div", { class: "meters" }, meterList(`Today, ${region(c.region)}`, today), meterList(it.replaced_by ? "Plan" : `Plan, ${region(it.region)}`, twin)),
      !o.is_original && !it.replaced_by && o.rationale ? h("p", { class: "why" }, o.rationale) : null,
      !o.is_original && !it.replaced_by && o.tradeoffs.length ? h("p", { class: "why" }, "Trade-offs: " + o.tradeoffs.join("; ")) : null));
  const toggle = h("button", { type: "button", class: "toggle", "aria-expanded": false, "aria-label": `Show price meters for ${label(orig)}` }, svg("M4.5 2.5 8 6l-3.5 3.5"));
  const flip = () => { const open = detail.hidden; detail.hidden = !open; toggle.setAttribute("aria-expanded", String(open)); };
  toggle.addEventListener("click", (e) => { e.stopPropagation(); flip(); });
  const row = h("tr", { class: "item", onclick: flip },
    h("td", {}, toggle, h("span", { class: "what" }, label(orig)), h("span", { class: "sub" }, region(c.region))),
    h("td", { class: "r amount" }, money(todayCost)),
    h("td", { class: "r" }, h("span", { class: `amount${it.replaced_by ? " gone" : ""}` }, money(it.monthly_cost)),
      h("span", { class: `sub${!o.is_original && !it.replaced_by ? " new" : ""}` }, planSub)),
    h("td", { class: `r amount delta${delta < -0.005 ? " save" : ""}` }, signed(delta)));
  return [row, detail];
}

function renderResults() {
  const plans = S.plans, plan = plans[Math.min(planIdx, plans.length - 1)], orig = S.original_total;
  const ruleIssues = (S.issues || []).filter((i) => i.kind === "rule");
  // Lean: even the Cheapest plan saves nothing and nothing needed fixing, so say so plainly.
  const lean = plans[0].savings <= 0.005 && !ruleIssues.length;
  const tried = S.options.filter((o) => !o.is_original).length;
  const headline = lean ? "Your design is already lean"
    : plan.savings > 0.005 ? h("span", { class: "save" }, `Save ${money(plan.savings)} a month`)
    : plan.savings < -0.005 ? `Meeting your requirements adds ${money(-plan.savings)} a month` : "No saving in this plan";
  const sub = lean
    ? `No cheaper option passed the checks: ${tried} ${tried === 1 ? "alternative was" : "alternatives were"} priced `
      + `in ${S.constraints.allowed_regions.length} regions. Each one, and why it was turned down, is listed below.`
    : plan.savings > 0.005
    ? `${plan.name} plan: ${plan.savings_pct}% less than today, about ${money(plan.savings * 12)} a year`
      + (ruleIssues.length ? `, with today's ${ruleIssues.length === 1 ? "problem" : "problems"} fixed.` : ".")
    : plan.savings < -0.005 ? `Today's design doesn't meet its requirements; this ${plan.name.toLowerCase()} plan fixes it.`
    : `${plan.name} plan keeps today's setup.`;
  const summary = h("div", { class: "card summary" },
    h("div", {},
      h("h1", { id: "results-h" }, headline),
      h("p", {}, sub),
      S.fixed_total ? h("p", {}, (() => {
        const d = S.fixed_total - plan.monthly_total;
        return `Compared with just fixing today's design (${money(S.fixed_total)} a month), this plan `
          + (Math.abs(d) < 0.005 ? "costs the same." : d > 0 ? `saves ${money(d)} a month.` : `costs ${money(-d)} more a month.`);
      })()) : null,
      S.saved ? h("p", {}, `Saved example, recorded ${new Date(S.timeline[0].t * 1000).toLocaleDateString()} with live prices.`) : null),
    h("dl", { class: "figs" },
      h("div", {}, h("dt", {}, "Today"), h("dd", {}, money(orig))),
      h("div", {}, h("dt", {}, plan.name), h("dd", { class: "save" }, money(plan.monthly_total)))));

  const seg = h("div", { class: "segmented", role: "tablist", "aria-label": "Plans" }, plans.map((p, i) =>
    h("button", { type: "button", role: "tab", "aria-selected": i === planIdx, onclick: () => { planIdx = i; renderResults(); } },
      p.name, h("small", {}, `${money(p.monthly_total)}/mo`))));
  const where = plan.region === "original regions" ? "Each part stays in its region" : `Everything in ${region(plan.region)}`;

  const table = h("table", { class: "compare" },
    h("thead", {}, h("tr", {}, h("th", {}, "Component"), h("th", { class: "r" }, "Today"), h("th", { class: "r" }, plan.name), h("th", { class: "r" }, "Change"))),
    h("tbody", {}, plan.items.flatMap(compareRow)),
    h("tfoot", {}, h("tr", {}, h("td", {}, "Per month"), h("td", { class: "r amount" }, money(orig)),
      h("td", { class: "r amount" }, money(plan.monthly_total)), h("td", { class: `r amount delta${plan.savings > 0 ? " save" : ""}` }, signed(-plan.savings)))));

  // Pricing Model: the same plan with 1- or 3-year commitments (reservations / savings plans), computed in code.
  const cm = plan.commitments;
  const pct = (x) => (cm.payg ? Math.round(100 * (1 - x / cm.payg)) : 0);
  const commit = cm && cm.three_year < cm.payg - 0.005 ? h("div", { class: "commit" },
    h("h3", {}, "Commit to save more"),
    h("div", { class: "commit-figs" },
      h("div", {}, h("span", {}, "Pay as you go"), h("strong", {}, money(cm.payg))),
      h("div", {}, h("span", {}, "1-year commitments"), h("strong", {}, money(cm.one_year)), h("small", {}, `${pct(cm.one_year)}% less`)),
      h("div", {}, h("span", {}, "3-year commitments"), h("strong", {}, money(cm.three_year)), h("small", {}, `${pct(cm.three_year)}% less`))),
    h("details", { class: "commit-lines" }, h("summary", {}, "What to commit to"),
      h("ul", {}, cm.lines.map((l) => h("li", {},
        h("span", {}, `${label(opt(plan.items.find((i) => i.component_id === l.component_id)?.option_id) || comp(l.component_id))}: ${l.meter.split(" / ").pop()}`),
        h("span", { class: "muted" }, ` ${money(l.payg)} → ${money(l.one_year)} (${l.how_one_year}) or ${money(l.three_year)} (${l.how_three_year})`))))),
    cm.hybrid_benefit > 0 ? h("p", { class: "muted" },
      `Azure Hybrid Benefit: if you already own SQL Server licences with Software Assurance, about ${money(cm.hybrid_benefit)} `
      + "a month of licence cost goes away on top of this.") : null,
    h("p", { class: "muted small" }, "Commitments lock in a size and region for the term; pay-as-you-go is the default above."))
    : null;

  // Total Cost: Azure bill + people effort, against today. Estimates from the Total Cost agent, math in code.
  const effort = S.ops_rate > 0 ? (() => {
    const ops = plan.ops_hours * S.ops_rate, todayOps = S.today_ops_hours * S.ops_rate;
    const total = plan.monthly_total + ops, todayTotal = orig + todayOps;
    return h("div", { class: "effort" },
      h("h3", {}, "Total cost with people effort"),
      h("div", { class: "commit-figs" },
        h("div", {}, h("span", {}, "Azure bill"), h("strong", {}, money(plan.monthly_total))),
        h("div", {}, h("span", {}, `Ops effort, ${plan.ops_hours} h a month`), h("strong", {}, money(ops)),
          h("small", { class: "muted" }, `today ${S.today_ops_hours} h`)),
        h("div", {}, h("span", {}, "Total a month"), h("strong", {}, money(total)),
          h("small", { class: total < todayTotal ? "" : "risk" }, `today ${money(todayTotal)}`)),
        h("div", {}, h("span", {}, "One-off migration"), h("strong", {}, `${plan.migration_hours} h`),
          h("small", { class: "muted" }, `about ${money(plan.migration_hours * S.ops_rate)}`))),
      h("p", { class: "muted small" }, `Effort is an AI estimate at ${money(S.ops_rate)} an hour, based on the team skills `
        + "you gave. Use it to compare plans, not as a quote."));
  })() : null;

  const planCard = h("div", { class: "card" },
    h("div", { class: "plan-head" }, seg, h("span", { class: "muted" }, where)),
    h("div", { class: "plan-text" }, h("p", {}, plan.summary || "The lowest total that passed every check, in one region."),
      plan.tradeoffs.length ? h("ul", {}, plan.tradeoffs.map((t) => h("li", {}, t))) : null,
      plan.note ? h("p", { class: "plan-note" }, plan.note.replace(/\b[a-z]+(?:central|europe|us\d?|asia|east|west|north|south)\w*\b/g, region)) : null),
    h("div", { class: "table-scroll" }, table),
    commit, effort);

  const rej = S.rejections.map((r) => {
    const o = opt(r.option_id);
    return h("li", {}, h("span", { class: "who" }, `${r.by} rejected ${o ? label(o) : r.option_id}. `), r.reason,
      r.source_url ? [" ", h("a", { href: r.source_url, target: "_blank", rel: "noopener" }, "Rule source")] : null);
  });
  const rejCard = h("div", { class: "card section" }, h("h2", {}, "Rejected alternatives"),
    rej.length ? h("ul", { class: "rejections" }, rej) : h("p", { class: "muted" }, "None. Every alternative passed the checks."));

  const actions = h("div", { class: "result-actions" },
    h("span", { class: "muted" }, `This analysis made ${S.llm_calls} AI calls and cost ${S.ai_cost_usd < 0.01 ? "under 1 cent" : money(S.ai_cost_usd)}.`),
    h("span", { class: "result-buttons" },
      h("button", { type: "button", class: "btn", onclick: downloadReport }, "Download report (.md)"),
      h("button", { type: "button", class: "btn", onclick: (e) => downloadPdf(e.currentTarget) }, "Download PDF"),
      meta.sharing && !S.saved ? h("button", {
        type: "button", class: "btn", disabled: !!S.shared, onclick: (e) => shareRun(e.currentTarget),
        title: "Adds this result to the samples on the home page, for anyone to open. Your diagram image is not shared.",
      }, S.shared ? "Shared as a sample" : "Share as a sample") : null,
      h("button", { type: "button", class: "btn primary", onclick: clearRun }, "Analyze another")));

  const imgCard = S.demo_image ? h("div", { class: "card section" }, h("h2", {}, "Diagram analyzed"),
    h("img", { class: "figure", src: S.demo_image, alt: "The diagram that was analyzed" })) : null;

  // Problems in TODAY's design, found by the Skeptic. Rule issues are fixed in every plan; advice is shown only.
  // No rule issues + a completed check = a visible pass, not silence.
  const issues = S.issues || [];
  const passed = !ruleIssues.length && S.fit_rules_checked > 0;
  const fixCard = passed ? h("div", { class: "card section fit-pass" },
    h("h2", {}, "Fit check passed"),
    h("p", {}, `Today's design meets the stated requirements: ${S.fit_rules_checked} `
      + `${S.fit_rules_checked === 1 ? "rule" : "rules"} checked, no problems found.`),
    issues.length ? [h("p", { class: "muted" }, "A few things worth a look, not required:"),
      h("ul", { class: "issues" }, issues.map((i) => {
        const c = comp(i.component_id);
        return h("li", { class: "advice" }, h("span", { class: "who" }, c ? label(c) : i.component_id), " ", i.reason);
      }))] : null)
  : issues.length ? h("div", { class: "card section fix-first" },
    h("h2", {}, ruleIssues.length
      ? `Today's design has ${ruleIssues.length} ${ruleIssues.length === 1 ? "problem" : "problems"} to fix first`
      : "Things to check in today's design"),
    ruleIssues.length && S.fixed_total ? h("p", { class: "muted" },
      `Fixing ${ruleIssues.length === 1 ? "it" : "them"} takes today's ${money(orig)} to ${money(S.fixed_total)} a month `
      + `(${signed(S.fixed_total - orig).replace("−", "-")}). Every plan below includes the ${ruleIssues.length === 1 ? "fix" : "fixes"}.`) : null,
    h("ul", { class: "issues" }, issues.map((i) => {
      const c = comp(i.component_id);
      return h("li", { class: i.kind },
        h("span", { class: "who" }, c ? label(opt(`${c.id}.orig`) || c) : i.component_id), " ", i.reason, " ",
        i.kind === "rule" ? h("a", { href: i.source_url, target: "_blank", rel: "noopener" }, "Rule source")
          : h("span", { class: "tag" }, "Advice, not enforced"));
    }))) : null;
  // Plain Chat: the same question asked of a no-tools chat. Its numbers are shown, never used.
  const b = S.baseline;
  const meters = S.candidates.filter((c) => c.option_id.endsWith(".orig") && c.region === comp(c.component_id)?.region)
    .reduce((n, c) => n + c.lines.length, 0);
  const off = b && orig ? Math.round(100 * (b.today - orig) / orig) : 0;
  const chatCard = b ? h("div", { class: "card section baseline" },
    h("h2", {}, "A plain AI chat vs ArchTwin"),
    h("p", { class: "muted" }, "We asked an AI chat the same question with no tools and no live prices."),
    h("div", { class: "table-scroll" }, h("table", { class: "compare" },
      h("thead", {}, h("tr", {}, h("th", {}), h("th", { class: "r" }, "Plain chat"), h("th", { class: "r" }, "ArchTwin"))),
      h("tbody", {},
        h("tr", {}, h("td", {}, "Today's bill"), h("td", { class: "r amount" }, money(b.today)), h("td", { class: "r amount" }, money(orig))),
        h("tr", {}, h("td", {}, "Cheapest option"), h("td", { class: "r amount" }, money(b.cheaper)), h("td", { class: "r amount" }, money(plans[0].monthly_total))),
        h("tr", {}, h("td", {}, "Backed by"), h("td", { class: "r" }, "Memory"),
          h("td", { class: "r" }, `${meters} live price meters, ${S.fit_rules_checked} rules, ${S.rejections.length} rejected options`))))),
    h("p", {}, Math.abs(off) < 5 ? "The chat's guess for today's bill was close this time, but it can't show where any number comes from."
      : `The chat's guess for today's bill was ${Math.abs(off)}% ${off > 0 ? "too high" : "too low"}.`),
    b.suggestions.length ? h("details", { class: "commit-lines" }, h("summary", {}, "What the chat suggested"),
      h("ul", {}, b.suggestions.map((s) => h("li", {}, s)))) : null) : null;
  $("results").replaceChildren(...[summary, fixCard, planCard, archDiagrams(plan), chatCard, rejCard, imgCard, actions].filter(Boolean));
}

// ---------- architecture diagrams: today vs the selected plan, drawn by code with Mermaid ----------
let mermaidLib = null;
async function mermaidApi() {
  if (!mermaidLib) {
    mermaidLib = (await import("https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs")).default;
    mermaidLib.initialize({
      startOnLoad: false, securityLevel: "strict", theme: "base",  // strict: labels are sanitized, no scripts
      themeVariables: { fontFamily: "Geologica, sans-serif", fontSize: "13px", lineColor: "#868BA0", primaryTextColor: "#33364A" },
    });
  }
  return mermaidLib;
}
const mmd = (s) => String(s).replace(/"/g, "#quot;").replace(/</g, "#lt;").replace(/>/g, "#gt;");

function archCode(nodes, edges) {
  const usesUsers = edges.some(([a, b]) => a === "users" || b === "users");
  return ["flowchart LR",
    usesUsers ? 'users(["Users"]):::users' : "",
    ...nodes.map((n) => `${n.id}["${n.lines.map(mmd).join("<br/>")}"]:::${n.cls}`),
    ...edges.map(([a, b]) => `${a} --> ${b}`),
    "classDef same fill:#FFFFFF,stroke:#C9D0E0,color:#33364A",
    "classDef chg fill:#EFE8FB,stroke:#8458B3,stroke-width:2px,color:#33364A",
    "classDef users fill:#E2F1F9,stroke:#3E8FBF,color:#33364A",
  ].filter(Boolean).join("\n");
}

function archDiagrams(plan) {
  const edges = (S.connections || []).filter(([a, b]) => [a, b].every((x) => x === "users" || comp(x)));
  const todayNodes = S.components.map((c) => {
    const cd = cand(`${c.id}.orig`, c.region);
    return { id: c.id, cls: "same", lines: [label(c), `${region(c.region)}${cd ? `, ${money(cd.monthly_cost)}/mo` : ""}`] };
  });
  const covered = Object.fromEntries(plan.items.filter((i) => i.replaced_by).map((i) => [i.component_id, opt(i.replaced_by).component_id]));
  const planNodes = plan.items.filter((i) => !i.replaced_by).map((i) => {
    const o = opt(i.option_id), c = comp(i.component_id);
    const merged = Object.keys(covered).filter((k) => covered[k] === i.component_id).map((k) => comp(k).service);
    return { id: i.component_id, cls: !o.is_original || i.region !== c.region ? "chg" : "same",
      lines: [label(o), `${region(i.region)}, ${money(i.monthly_cost)}/mo`, ...(merged.length ? [`replaces ${merged.join(", ")}`] : [])] };
  });
  const to = (x) => covered[x] || x;
  const planEdges = [...new Set(edges.map(([a, b]) => `${to(a)} ${to(b)}`))].map((e) => e.split(" ")).filter(([a, b]) => a !== b);
  const box = (title, code) => {
    const el = h("div", { class: "arch-box" }, h("h3", {}, title), h("div", { class: "arch-svg muted" }, "Drawing…"));
    mermaidApi().then((m) => m.render(`arch-${Math.random().toString(36).slice(2)}`, code))
      .then(({ svg }) => { el.lastChild.innerHTML = svg; el.lastChild.classList.remove("muted"); })  // Mermaid output, strict mode
      .catch(() => { el.lastChild.textContent = "The diagram couldn't be drawn (no connection to the diagram library)."; });
    return el;
  };
  return h("div", { class: "card section arch" },
    h("h2", {}, "Architecture"),
    h("p", { class: "muted" }, edges.length ? "Highlighted boxes change in this plan." : "Highlighted boxes change in this plan. No connections were read from the input, so only the parts are shown."),
    h("div", { class: "arch-grid" }, box("Today", archCode(todayNodes, edges)), box(`${plan.name} plan`, archCode(planNodes, planEdges))));
}

// ---------- report export (built from the run itself: no AI call, nothing sent anywhere) ----------
function reportMarkdown() {
  const md = [], cell = (s) => String(s ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ");
  const today = (c) => cand(`${c.id}.orig`, c.region);
  md.push(`# ArchTwin report`, "",
    `Generated ${new Date().toLocaleString()} from a run on ${new Date(S.timeline[0].t * 1000).toLocaleString()}. `
    + `Prices from the Azure Retail Prices API (pay-as-you-go, USD). AI cost of the analysis: $${S.ai_cost_usd.toFixed(3)} `
    + `(${S.llm_calls} calls).`, "");
  md.push(`## Summary`, "", `| | Region | Per month | Saving vs today |`, `|---|---|---|---|`,
    `| Today | ${[...new Set(S.components.map((c) => region(c.region)))].join(", ")} | ${money(S.original_total)} | |`,
    ...S.plans.map((p) => `| ${p.name} | ${p.region === "original regions" ? "Original regions" : region(p.region)} | ${money(p.monthly_total)} | ${money(p.savings)} (${p.savings_pct}%) |`), "");
  if (S.constraints.notes) md.push(`**Requirements:** ${S.constraints.notes}`, "");

  const rule = (S.issues || []).filter((i) => i.kind === "rule");
  md.push(`## Fit check of today's design`, "");
  if (!rule.length && S.fit_rules_checked) md.push(`Passed: ${S.fit_rules_checked} rules checked, no problems found.`, "");
  if (!S.fit_rules_checked && !(S.issues || []).length) md.push("Not available for this run (recorded before the fit check existed).", "");
  for (const i of S.issues || []) {
    const c = comp(i.component_id);
    md.push(`- **${c ? label(c) : i.component_id}** (${i.kind === "rule" ? "must fix" : "advice"}): ${i.reason}`
      + (i.source_url ? ` [Rule source](${i.source_url})` : ""));
  }
  if (S.fixed_total) md.push("", `Fixing today's design: ${money(S.original_total)} → ${money(S.fixed_total)} a month.`);
  md.push("");

  for (const p of S.plans) {
    md.push(`## ${p.name} plan: ${money(p.monthly_total)} a month`, "");
    if (p.summary) md.push(p.summary, "");
    if (p.note) md.push(`> ${p.note}`, "");
    md.push(`| Component | Today | ${p.name} | Per month |`, `|---|---|---|---|`);
    for (const it of p.items) {
      const c = comp(it.component_id), o = opt(it.option_id), t = today(c);
      md.push(`| ${cell(label(opt(`${c.id}.orig`)))} | ${t ? money(t.monthly_cost) : "-"} | `
        + `${cell(it.replaced_by ? `Covered by ${label(o)}` : `${label(o)}, ${region(it.region)}`)} | ${money(it.monthly_cost)} |`);
    }
    if (p.tradeoffs.length) md.push("", "**Trade-offs:**", ...p.tradeoffs.map((t) => `- ${t}`));
    const cm = p.commitments;
    if (cm && cm.three_year < cm.payg - 0.005) {
      md.push("", `**With commitments:** ${money(cm.payg)} pay-as-you-go, ${money(cm.one_year)} with 1-year, `
        + `${money(cm.three_year)} with 3-year.`, ...cm.lines.map((l) => `- ${l.meter}: ${l.how_one_year} → ${money(l.one_year)}; ${l.how_three_year} → ${money(l.three_year)}`));
      if (cm.hybrid_benefit) md.push(`- Azure Hybrid Benefit could remove ${money(cm.hybrid_benefit)} of SQL licence cost.`);
    }
    if (S.ops_rate) md.push("", `**People effort:** ${p.ops_hours} ops hours a month (today ${S.today_ops_hours}) at `
      + `${money(S.ops_rate)}/h; one-off migration ${p.migration_hours} hours. AI estimate, for comparison only.`);
    md.push("", `**Price lines (Azure meter IDs):**`);
    for (const it of p.items.filter((i) => !i.replaced_by)) {
      for (const l of cand(it.option_id, it.region)?.lines || []) {
        md.push(`- ${l.meter}: ${inUnits(l.qty, l.unit)} × ${unitPrice(l.unit_price)} per ${l.unit} = ${money(l.monthly)} (meterId \`${l.meter_id}\`)`);
      }
    }
    md.push("");
  }

  if (S.baseline) md.push(`## A plain AI chat vs ArchTwin`, "", "The same question asked of an AI chat with no tools or live prices.", "",
    `| | Plain chat | ArchTwin |`, `|---|---|---|`,
    `| Today's bill | ${money(S.baseline.today)} | ${money(S.original_total)} |`,
    `| Cheapest option | ${money(S.baseline.cheaper)} | ${money(S.plans[0].monthly_total)} |`, "",
    ...S.baseline.suggestions.map((s) => `- Chat suggested: ${cell(s)}`), "");
  md.push(`## Rejected alternatives`, "");
  if (!S.rejections.length) md.push("None.");
  for (const r of S.rejections) {
    const o = opt(r.option_id);
    md.push(`- **${o ? label(o) : r.option_id}**, rejected by ${r.by}: ${r.reason}` + (r.source_url ? ` [Rule source](${r.source_url})` : ""));
  }
  md.push("", `## Agent activity`, "", ...S.timeline.map((e) => `- **${e.agent}**: ${e.message}`), "",
    "---", "Read-only analysis: ArchTwin never connects to an Azure account. Plans are estimates; check them before you buy.");
  return md.join("\n");
}

// Download PDF: html2pdf (lazy-loaded) renders a light copy of the results to an A4 PDF in the browser. Nothing is uploaded.
let pdfLib = null;
async function downloadPdf(btn) {
  const text = btn.textContent;
  btn.disabled = true; btn.textContent = "Making PDF…";
  try {
    pdfLib ||= await new Promise((ok, fail) => document.head.append(h("script", {
      src: "https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js",
      onload: () => ok(window.html2pdf), onerror: () => fail(new Error("couldn't load the PDF library")) })));
    const copy = $("results").cloneNode(true);
    copy.removeAttribute("id");
    copy.classList.add("pdf");
    for (const d of copy.querySelectorAll("details")) d.open = true;
    // The renderer draws an SVG at its natural size (cropped), so give each diagram a fixed size that fits A4.
    for (const s of copy.querySelectorAll(".arch-svg svg")) {
      const vb = s.viewBox.baseVal, w = Math.min(vb.width, 640);
      s.setAttribute("width", w); s.setAttribute("height", w * vb.height / vb.width); s.style.maxWidth = "none";
    }
    await pdfLib().set({
      margin: 10, filename: `archtwin-report-${new Date().toISOString().slice(0, 10)}.pdf`,
      image: { type: "jpeg", quality: 0.95 }, html2canvas: { scale: 2, backgroundColor: "#ffffff", scrollX: 0, scrollY: 0 },  // scroll: else a scrolled page gives a blank first page
      jsPDF: { unit: "mm", format: "a4" }, pagebreak: { mode: ["css", "legacy"], avoid: ["tr", ".commit-figs", ".arch-box", "li"] },
    }).from(copy).save();
  } catch (e) {
    alert(`The PDF couldn't be made (${e.message}). Download the report (.md) instead.`);
  } finally {
    btn.disabled = false; btn.textContent = text;
  }
}

function downloadReport() {
  const blob = new Blob([reportMarkdown()], { type: "text/markdown" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `archtwin-report-${new Date().toISOString().slice(0, 10)}.md` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function openDemo(name) {
  try {
    S = { ...(await api(`/demos/${name}.json`)), saved: true, demo_name: name, demo_image: DEMO_IMAGES[name] || "" };
    planIdx = 0; render(); window.scrollTo(0, 0);
  } catch { $("input-error").textContent = "That example isn't available right now."; show("input-error"); }
}

async function shareRun(btn) {
  if (!confirm("Share this result as a sample? Anyone visiting ArchTwin can open it, including your text description. Your diagram image is not shared.")) return;
  btn.disabled = true;
  try {
    await api(`/api/share/${S.run_id}`, {});
    S.shared = true; save();
    btn.textContent = "Shared as a sample";
  } catch (e) { btn.disabled = false; alert(e.message); }
}

async function openShared(id) {
  try {
    S = { ...(await api(`/api/shared/${id}`)), saved: true };
    planIdx = 0; render(); window.scrollTo(0, 0);
  } catch (e) { $("input-error").textContent = e.message; show("input-error"); }
}

// ---------- wiring ----------
$("file").onchange = (e) => e.target.files[0] && attach(e.target.files[0]);
$("remove-image").onclick = detach;
const composer = $("composer");
composer.ondragover = (e) => { e.preventDefault(); composer.classList.add("over"); };
composer.ondragleave = () => composer.classList.remove("over");
composer.ondrop = (e) => { e.preventDefault(); composer.classList.remove("over"); e.dataTransfer.files[0] && attach(e.dataTransfer.files[0]); };
$("text").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) analyze(); };
// Paste a copied screenshot or image anywhere on the start screen to attach it. Text pastes normally.
document.addEventListener("paste", (e) => {
  if (document.body.dataset.view !== "start") return;
  const file = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
  if (!file) return;
  e.preventDefault();
  attach(file);
});
$("analyze").onclick = analyze;
$("use-example").onclick = () => {
  $("text").value = "Web API on AKS with 3 Standard_D4s_v5 nodes, Azure SQL Database General Purpose 4 vCore provisioned, "
    + "Redis Premium P1 cache, all in West Europe. About 2,000 users, busy in business hours, quiet at night. Data must stay in the EU.";
  $("text").focus();
};
$("use-sample").onclick = async () => {
  const blob = await (await fetch(DEMO_IMAGES["contoso-bookings"])).blob();
  await attach(new File([blob], "contoso-bookings.png", { type: "image/png" }));
};
for (const b of document.querySelectorAll("[data-demo]")) b.onclick = () => openDemo(b.dataset.demo);
$("add-row").onclick = () => $("comp-rows").append(compRow({ id: "", service: "", quantity: 1, region: S.components[0]?.region || "westeurope" }));
$("confirm-btn").onclick = confirmComponents;
$("retry").onclick = () => { show("run-error", false); drive(); };
for (const id of ["restart-1", "restart-2", "new-analysis"]) $(id).onclick = clearRun;
$("brand").onclick = (e) => { e.preventDefault(); clearRun(); };

// Fill the sample cards from the saved runs themselves, so the numbers never drift from the files.
async function fillSamples() {
  for (const el of document.querySelectorAll("[data-figs]")) {
    try {
      const run = await api(`/demos/${el.dataset.figs}.json`);
      const best = run.plans[0];
      el.replaceChildren(
        h("span", { class: "was" }, money(run.original_total)), " to ",
        h("strong", {}, money(best.monthly_total)), " a month",
        h("span", { class: "pct" }, `saves ${best.savings_pct}%`));
    } catch { el.remove(); }
  }
}

// Visitors' shared results: up to 5, newest first, from /api/recent. Hidden when there are none.
async function fillRecent() {
  let runs = [];
  try { runs = await api("/api/recent"); } catch { return; }
  $("recent-grid").replaceChildren(...runs.map((r) => h("button", { type: "button", class: "sample", onclick: () => openShared(r.id) },
    h("span", { class: "sample-kind" }, `${r.kind}, shared ${new Date(r.t * 1000).toLocaleDateString()}`),
    h("span", { class: "sample-name" }, r.name),
    h("span", { class: "sample-desc" }, r.desc),
    h("span", { class: "sample-figs" }, h("span", { class: "was" }, money(r.was)), " to ",
      h("strong", {}, money(r.now)), " a month", h("span", { class: "pct" }, `saves ${r.pct}%`)))));
  show("recent", runs.length > 0);
}

(async function init() {
  fillSamples();
  fillRecent();
  try {
    meta = await api("/api/meta");
    $("services").replaceChildren(...meta.services.map((s) => h("option", { value: s })));
    if (meta.live_runs_per_hour) $("run-limit").textContent = meta.live_runs_per_hour;
  } catch { /* the confirm step still works with free text */ }
  const demo = new URLSearchParams(location.search).get("demo");  // ?demo=contoso-bookings: link straight to a sample
  if (/^[a-z0-9-]+$/.test(demo || "")) { openDemo(demo); markDemo(demo); return; }
  try { S = JSON.parse(localStorage.getItem(STORE)); } catch { S = null; }
  if (!S) return;
  if (S.step === "parse" && !S.input_image && !S.input_text) { clearRun(); return; }
  if (["confirm", "done", "failed"].includes(S.step)) render(); else drive();  // resume an unfinished run
})();
