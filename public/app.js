// TwinStack frontend: drives the agent pipeline one step per request and keeps the run in localStorage,
// so a refresh or a failed step resumes where it stopped. All server/LLM text is inserted as text, never HTML.
"use strict";

const STORE = "twinstack.run";
const STAGES = [
  ["parse", "Read"], ["confirm", "You confirm"], ["price", "Pricer"], ["scout", "Region Scout"],
  ["swap", "Service Swap"], ["skeptic", "Skeptic"], ["judge", "Judge"],
];
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const $ = (id) => document.getElementById(id);

let S = null;          // the RunState, exactly as the server returns it
let meta = null;       // { services, region_groups }
let image = "";        // resized diagram as a data URI
let planIdx = 0;       // selected plan tab

// ---------- small helpers ----------
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("aria-") && v != null) { el.setAttribute(k, String(v)); continue; }  // "true"/"false"
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const show = (id, on = true) => { $(id).hidden = !on; };

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
  try {
    // Keep the image only until it has been read; it is large.
    localStorage.setItem(STORE, JSON.stringify(S.step === "parse" ? S : { ...S, input_image: "" }));
  } catch { /* storage full or blocked: the run still works, it just won't resume */ }
}
function clearRun() {
  try { localStorage.removeItem(STORE); } catch {}
  S = null; planIdx = 0;
  for (const id of ["progress", "confirm", "results", "log-section", "run-error"]) show(id, false);
  show("input"); show("intro");
}

// ---------- input ----------
function selectTab(which) {
  const text = which === "text";
  $("tab-text").setAttribute("aria-selected", text);
  $("tab-image").setAttribute("aria-selected", !text);
  show("pane-text", text); show("pane-image", !text);
}

async function readImage(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) throw new Error("Use a PNG, JPG or WebP image.");
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = url; });
    const scale = Math.min(1, 1500 / Math.max(img.width, img.height));  // ~1,500 px: cheaper vision tokens
    const c = h("canvas", { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}

async function takeFile(file) {
  const err = $("input-error");
  try {
    image = await readImage(file);
    $("preview").src = image; show("preview"); show("drop-text", false); show("input-error", false);
  } catch (e) { err.textContent = e.message || "Couldn't read that image."; show("input-error"); }
}

async function analyze() {
  const useImage = $("tab-image").getAttribute("aria-selected") === "true";
  const body = useImage ? { image, text: $("image-notes").value } : { text: $("text").value };
  const btn = $("analyze"), err = $("input-error");
  show("input-error", false);
  if (useImage && !image) { err.textContent = "Choose a diagram image first."; show("input-error"); return; }
  btn.disabled = true;
  try {
    S = await api("/api/run/start", body);
    save();
    show("intro", false); show("input", false);
    await drive();
  } catch (e) { err.textContent = e.message; show("input-error"); }
  finally { btn.disabled = false; }
}

// ---------- the loop ----------
async function drive() {
  let retries = 0;
  render();
  const waiting = () => !S || ["done", "failed"].includes(S.step) || (S.step === "confirm" && !S.confirmed);
  while (!waiting()) {
    try {
      S = await api("/api/run/step", S);
    } catch (e) { runError(e.message); return; }
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

function runError(msg) {
  $("run-error-text").textContent = msg;
  show("run-error"); render();
}

// ---------- rendering ----------
function render() {
  if (!S) return;
  show("progress"); show("log-section");
  renderPipeline(); renderLog();
  show("confirm", S.step === "confirm");
  if (S.step === "confirm") renderConfirm();
  show("results", S.step === "done" && S.plans.length > 0);
  if (S.step === "done") renderResults();
}

function renderPipeline() {
  const order = STAGES.map((s) => s[0]);
  let reached = S.step === "done" ? order.length : order.indexOf(S.step);
  if (S.swap_round > 0) reached = Math.max(reached, order.indexOf("swap"));
  if (S.options.some((o) => o.reviewed)) reached = Math.max(reached, order.indexOf("skeptic"));
  $("pipeline").replaceChildren(...STAGES.map(([key, label], i) => {
    const now = key === S.step;
    const cls = now ? (key === "confirm" ? "wait" : "now") : i < reached || S.step === "done" ? "done" : "";
    return h("li", { class: cls, "aria-current": now ? "step" : null }, label);
  }));
}

function renderLog() {
  $("log").replaceChildren(...S.timeline.map((e) => {
    const msg = e.data == null ? h("div", { class: "msg" }, e.message)
      : h("details", { class: "msg" }, h("summary", {}, e.message), h("pre", {}, JSON.stringify(e.data, null, 2)));
    return h("li", { class: e.kind }, h("span", { class: "agent" }, e.agent), msg);
  }));
  const calls = `${S.llm_calls} AI call${S.llm_calls === 1 ? "" : "s"}`;
  $("ai-cost").textContent = S.llm_calls ? `${calls}, $${S.ai_cost_usd.toFixed(4)} so far` : "";
}

function compRow(c) {
  const cell = (field, attrs = {}) => h("td", {}, h("input", {
    value: c[field] ?? "", "data-f": field, "aria-label": field, ...attrs,
  }));
  const tr = h("tr", { class: c.confidence === "low" ? "low" : null, "data-id": c.id },
    cell("service", { list: "services" }), cell("sku"), cell("quantity", { type: "number", min: "0", step: "any" }),
    cell("region"), cell("details"),
    h("td", {}, h("button", { type: "button", class: "link", onclick: () => tr.remove(), "aria-label": "Remove this component" }, "Remove")));
  if (c.confidence === "low") tr.title = "The agent was unsure about this one";
  return tr;
}

function renderConfirm() {
  if ($("comp-rows").dataset.run === S.run_id) return;  // keep the user's edits
  $("comp-rows").dataset.run = S.run_id;
  $("comp-rows").replaceChildren(...S.components.map(compRow));
  $("confirm-image").src = image || "";
  show("confirm-image", Boolean(image) && S.components.some((c) => c.source === "diagram"));
  const groups = meta?.region_groups || {};
  const current = [...S.constraints.allowed_regions].sort().join();
  const opts = Object.entries(groups).map(([g, list]) => h("option", { value: g, selected: [...list].sort().join() === current }, `${g} (${list.length} regions)`));
  const home = S.components[0]?.region || "westeurope";
  opts.push(h("option", { value: "__home", selected: !opts.some((o) => o.selected) }, `Only ${home}`));
  $("region-group").replaceChildren(...opts);
  $("notes").value = S.constraints.notes || "";
}

async function confirmComponents() {
  const rows = [...$("comp-rows").querySelectorAll("tr")];
  let n = Math.max(0, ...S.components.map((c) => +c.id.slice(1) || 0));
  S.components = rows.map((tr) => {
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
  S.confirmed = true;
  show("confirm-error", false);
  $("confirm-btn").disabled = true;
  try {
    await drive();
  } catch (e) { $("confirm-error").textContent = e.message; show("confirm-error"); }
  finally { $("confirm-btn").disabled = false; }
}

// ---------- results: two itemized bills ----------
const opt = (id) => S.options.find((o) => o.id === id);
const comp = (id) => S.components.find((c) => c.id === id);
const cand = (oid, region) => S.candidates.find((c) => c.option_id === oid && c.region === region);

const unitPrice = (p) => (p >= 0.01 || p === 0 ? usd.format(p) : `$${p.toPrecision(3)}`);

function meterLines(c) {
  if (!c) return h("div", { class: "meters" }, "No price lines.");
  return h("div", { class: "meters" }, c.lines.map((l) => h("div", {},
    h("span", {}, `${l.meter}: ${+l.qty.toPrecision(4)} × ${unitPrice(l.unit_price)} per ${l.unit}`,
      h("br"), h("code", { title: "Azure meterId" }, l.meter_id)),
    h("span", {}, usd.format(l.monthly)))));
}

function billLine(o, c, region, cost, { changed = false, gone = null } = {}) {
  const label = `${o.service}${o.sku ? " " + o.sku : ""}`;
  const sub = gone ? `Not needed: covered by ${gone}` : `${region}${o.is_original ? "" : ": " + o.rationale}`;
  const body = gone ? h("div", { class: "meters" }, "Its work moves to the option above.")
    : h("div", {}, meterLines(c), o.tradeoffs.length ? h("div", { class: "meters" }, "Trade-offs: " + o.tradeoffs.join("; ")) : null);
  return h("details", { class: `line${changed ? " changed" : ""}${gone ? " gone" : ""}` },
    h("summary", {}, h("span", { class: "what" }, label, h("small", {}, sub)), h("span", { class: "cost" }, usd.format(cost))),
    body);
}

function renderResults() {
  const plans = S.plans;
  const plan = plans[Math.min(planIdx, plans.length - 1)];
  const cheapest = plans[0];
  const orig = S.original_total;
  const yearly = usd.format(cheapest.savings * 12);

  const verdict = h("p", { class: "verdict", id: "verdict" },
    `Your design costs ${usd.format(orig)} a month. `,
    h("span", { class: "twin" }, `Its cheaper twin costs ${usd.format(cheapest.monthly_total)}.`));
  const sub = h("p", { class: "verdict-sub" },
    `That's ${usd.format(cheapest.savings)} less a month (${cheapest.savings_pct}%), about ${yearly} a year, `
    + `with every price from live Azure meters. Pick a plan to see what changes.`);

  const tabs = h("div", { class: "plan-tabs", role: "tablist", "aria-label": "Plans" }, plans.map((p, i) =>
    h("button", { type: "button", role: "tab", "aria-selected": i === planIdx, onclick: () => { planIdx = i; renderResults(); } },
      p.name, h("small", {}, `${usd.format(p.monthly_total)} a month, saves ${p.savings_pct}%`))));

  const summary = h("div", { class: "plan-summary" },
    h("p", {}, plan.summary || "The lowest total that passed every check, all in one region."),
    plan.tradeoffs.length ? h("ul", {}, plan.tradeoffs.map((t) => h("li", {}, t))) : null);

  const origBill = h("div", { class: "bill" },
    h("h3", {}, "Today", h("span", {}, usd.format(orig))),
    h("div", { class: "where" }, "As described, in its current regions"),
    plan.items.map((it) => {
      const c = comp(it.component_id), o = opt(`${c.id}.orig`), cd = cand(o.id, c.region);
      return billLine(o, cd, c.region, cd ? cd.monthly_cost : 0);
    }),
    h("div", { class: "bill-total" }, h("span", {}, "Per month"), h("span", {}, usd.format(orig))));

  const twinBill = h("div", { class: "bill twin" },
    h("h3", {}, `${plan.name} twin`, h("span", {}, usd.format(plan.monthly_total))),
    h("div", { class: "where" }, plan.region === "original regions" ? "Each component stays in its region" : `Everything in ${plan.region}`),
    plan.items.map((it) => {
      const o = opt(it.option_id), c = comp(it.component_id);
      if (it.replaced_by) return billLine(opt(`${c.id}.orig`), null, it.region, 0, { gone: `${o.service} ${o.sku || ""}`.trim() });
      return billLine(o, cand(it.option_id, it.region), it.region, it.monthly_cost,
        { changed: !o.is_original || it.region !== c.region });
    }),
    h("div", { class: "bill-total" }, h("span", {}, "Per month"), h("span", {}, usd.format(plan.monthly_total))));

  const rej = S.rejections.map((r) => {
    const o = opt(r.option_id);
    return h("li", {}, h("span", { class: "who" }, `${r.by} rejected ${o ? `${o.service} ${o.sku || ""}` : r.option_id}`), ": ", r.reason,
      r.source_url ? [" ", h("a", { href: r.source_url, target: "_blank", rel: "noopener" }, "Read the rule")] : null);
  });

  $("results").replaceChildren(...[
    S.saved ? h("p", { class: "hint" }, `Saved example, recorded ${new Date(S.timeline[0].t * 1000).toLocaleDateString()} with live Azure prices. Viewing it costs nothing.`) : null,
    S.demo_image ? h("img", { class: "result-image", src: S.demo_image, alt: "The diagram the agents read" }) : null,
    verdict, sub, tabs, summary, h("div", { class: "bills" }, origBill, twinBill),
    h("div", { class: "subsection" }, h("h2", {}, "What the skeptic turned down"),
      rej.length ? h("ul", { class: "rejections" }, rej) : h("p", {}, "Nothing: every alternative passed the checks.")),
    h("div", { class: "actions subsection" },
      h("button", { type: "button", class: "primary", onclick: () => { clearRun(); window.scrollTo(0, 0); } }, "Analyze another architecture"),
      h("span", { class: "hint" }, `This analysis used ${S.llm_calls} AI calls and cost ${S.ai_cost_usd < 0.01 ? "under 1 cent" : usd.format(S.ai_cost_usd)} in AI.`)),
  ].filter(Boolean));
}

const DEMO_IMAGES = { "contoso-bookings": "/demos/contoso-bookings.png" };

async function openDemo(name) {
  try {
    S = { ...(await api(`/demos/${name}.json`)), saved: true, demo_image: DEMO_IMAGES[name] || "" };
    planIdx = 0;
    show("intro", false); show("input", false);
    render(); window.scrollTo(0, 0);
  } catch (e) { $("input-error").textContent = "That saved example isn't available right now."; show("input-error"); }
}

async function useSample() {
  const blob = await (await fetch(DEMO_IMAGES["contoso-bookings"])).blob();
  await takeFile(new File([blob], "contoso-bookings.png", { type: "image/png" }));
}

// ---------- wiring ----------
$("tab-text").onclick = () => selectTab("text");
$("tab-image").onclick = () => selectTab("image");
$("use-example").onclick = () => { $("text").value = $("text").placeholder; $("text").focus(); };
$("file").onchange = (e) => e.target.files[0] && takeFile(e.target.files[0]);
const drop = $("drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && takeFile(e.dataTransfer.files[0]); };
$("analyze").onclick = analyze;
for (const b of document.querySelectorAll("[data-demo]")) b.onclick = () => openDemo(b.dataset.demo);
$("use-sample").onclick = useSample;
$("add-row").onclick = () => $("comp-rows").append(compRow({ id: "", service: "", quantity: 1, region: S.components[0]?.region || "westeurope" }));
$("confirm-btn").onclick = confirmComponents;
$("retry").onclick = () => { show("run-error", false); drive(); };
for (const id of ["restart-1", "restart-2"]) $(id).onclick = clearRun;

(async function init() {
  try {
    meta = await api("/api/meta");
    $("services").replaceChildren(...meta.services.map((s) => h("option", { value: s })));
  } catch { meta = { services: [], region_groups: {} }; }
  try { S = JSON.parse(localStorage.getItem(STORE)); } catch { S = null; }
  if (!S) return;
  if (S.step === "parse" && !S.input_image && !S.input_text) { clearRun(); return; }
  show("intro", false); show("input", false);
  if (["confirm", "done", "failed"].includes(S.step)) render(); else drive();  // resume an unfinished run
})();
