# ArchTwin — Project Context for Claude Code

> Save this file as `CLAUDE.md` in the repo root so Claude Code loads it automatically.
> Name: **ArchTwin** (renamed from TwinStack in Nov 2026; repo and folder keep the old name). Tagline: *"Same architecture. Cheaper twin."*
> Repo: https://github.com/kabeerali1825/twinstack (public) · local folder: `D:\Agent-A-Thon\twinstack` (Windows dev machine)

---

## 1. Who / what / why

- **Developer:** Kabeer Ali — backend engineer (.NET, Azure, AI). Building this solo for SSI's internal **Agent-A-Thon** (multi-agent hackathon).
- **Product:** a multi-agent web app. The user uploads an **Azure architecture diagram image** (or pastes a text description). Agents find cheaper ways to run the same architecture that still meet every requirement, backed by **live Azure prices** and **Microsoft documentation**, and output three plans: **Cheapest / Balanced / Safest**.
- **Why it's not "just ask Claude":** a chat gives rough, possibly stale guesses. ArchTwin searches live prices across all regions, pricing models and alternative services, checks every option against real service limits, and cites a meter ID and doc link for every number.

---

## 2. Hackathon rules (hard requirements)

**Every entry must:**
1. Be an agentic AI solution: agents plan, call tools/APIs and act on the results, **live through the organizer-provided LiteLLM key**. Multi-agent workflows (distinct roles handing work to each other) score higher.
2. Use **public or made-up data only**. No client or internal company data; entries are public URLs.
3. Be **live on Vercel**, with a working public URL evaluators can open and use. JavaScript (Next.js) or **Python (FastAPI/Flask)**. Streamlit and Gradio can't run on Vercel.
4. Get **one agent flow working end to end first**, then add agents, tools and polish. **Show each agent's steps in the app**, because evaluators score the orchestration.

**Other rules:**
- Individual work: one entry per person, own repo, deployed from own **Vercel Hobby (free)** account.
- API key must be stored as a Vercel env var and **never committed**. An API key in code gets the entry filtered out.
- Build tool rule: **use Claude Code in "Accept Edits" mode, not full auto.**
- Agents can be plain code; LangGraph / Vercel AI SDK are optional.

**Judging rubric (no weights published):**
1. **Completeness, including multi-agent orchestration.** Does it work end to end?
2. **Creativity & uniqueness.** Is the idea fresh and specific?
3. **Technical execution.** Clean build, sensible agents, API key kept out of the code.
4. **Presentation & live product.** Live on Vercel and easy to demo.
5. **Business value.** Would someone at SSI or a client actually use it?

---

## 3. LLM access (LiteLLM gateway)

- **Base URL:** `https://apissidevbuddy-qa.ssidecisions.com/litellm/v1` (OpenAI-compatible)
- **Key:** personal; stored ONLY in env var `LITELLM_API_KEY` (Vercel env vars + local `.env`). **Never in code, never in the repo, never sent to the browser.**
- **Models available (only these):**
  - Sonnet (named `Claude-sonnet-5-5` in the organizer email): main model for planning, reasoning, tool use and vision.
  - `claude-haiku` (Haiku 5.5 on the backend): fast and cheap, for simple steps and sub-agents.
  - **First task:** call `GET {base_url}/models` once to confirm the exact model ID strings (including case).
- Both models accept images (send OpenAI-style `image_url` content with a base64 data URI).
- **Limits:**
  - **$25 total, no refill.** This also covers **judges using the live app**, so keep a reserve.
  - **60 requests/minute.**
  - Key valid until **about 29 December 2026**. Finish and deploy well before then.

```python
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["LITELLM_API_KEY"],
                base_url=os.environ["LITELLM_BASE_URL"])

resp = client.chat.completions.create(
    model=os.environ.get("MODEL_FAST", "claude-haiku"),
    messages=[{"role": "user", "content": "..."}],
    max_tokens=800,
)
```

---

## 3a. Which model does what (decided)

| Role | Model | Notes |
|---|---|---|
| **Writing the app's code** (this Claude Code session) | **Claude Opus 5.5** via Claude Code | Allowed by the rules ("Build: Cursor or Claude Code"). Run in **Accept Edits mode, not full auto**. Opus never ships inside the app. |
| **The app's agents at runtime** | **Only** the LiteLLM models: Sonnet + `claude-haiku` | All agent calls go through `LITELLM_BASE_URL` with `LITELLM_API_KEY`. No other provider, key or model in the app. |

Rules for Claude Code when writing the app:
- **Never hardcode an Opus (or any non-LiteLLM) model name** in the app code. Agent model names come from env vars `MODEL_SMART` (Sonnet) and `MODEL_FAST` (Haiku).
- Write prompts that work on **Haiku and Sonnet**, the models the app actually ships with. Don't rely on Opus-level reasoning inside the app.
- During development, test agents against the real LiteLLM models (Haiku by default) or recorded fixtures, not another provider.

---

## 4. Developer constraints & preferences

- **Python only** (FastAPI). Hosted on **Vercel free tier**.
- **Free only:** no paid services. **No Azure or AWS subscription.** The app reads public data only.
- **Read-only by design:** the app never deploys, changes or accesses any Azure account. (Kabeer explicitly doesn't want AI agents acting on production systems.)
- Make-believe architectures and public reference architectures only. No real client data.

---

## 5. What the user does (UX flow)

1. **Input** either:
   - **upload a diagram image** (PNG/JPG, e.g. drawn by Claude, draw.io export, whiteboard photo), or
   - **paste a text description** ("Web API on AKS, Azure SQL, Redis Premium, Blob Storage, West Europe, ~2k users").
2. **Confirm what the AI read:** an editable component list beside the image; low-confidence items highlighted. (Human-in-the-loop checkpoint.)
3. **Answer a few questions** (only what's missing): traffic pattern, always-on vs spiky, data size, residency rules (e.g. EU only), uptime/HA needs, team skills.
4. **Watch agents work live** in a step-by-step timeline (each agent's inputs, tool calls, outputs, rejections).
5. **Get three plans** (Cheapest / Balanced / Safest), each compared against the original.

---

## 6. Agents

| # | Agent | Job | Model |
|---|---|---|---|
| 1 | **Diagram Reader** (vision) | Extract components (from Azure icons and labels), connections and visible details (region, tier, counts, zone redundancy), with a **confidence per component** | Sonnet |
| 2 | **Parser** | Turn text input into a component list; normalize both inputs into one schema | Haiku |
| 3 | **Workload Profiler** | Ask only for missing info; capture traffic shape, constraints, team skills | Haiku |
| 4 | **Service Swap Agent** | Propose better-fitting Azure services per component (AKS → Container Apps, Azure SQL → SQL serverless / PostgreSQL Flexible, nightly-job VM → Functions / Container Apps job, Redis Premium → Standard …) | Sonnet |
| 4b | **Pricer** (added in build) | Pick the real price meters (and amounts) each option is billed on, from a code-built menu of live rows; code adds SQL licenses and does unit math | Sonnet (Haiku mixed up Linux/Windows rows and sizes) |
| 5 | **Region Scout** | Query live prices for each candidate in every allowed region; only count SKUs actually sold there | **No LLM: code** |
| 6 | **Pricing-Model Agent** | Compare pay-as-you-go, 1y/3y reservations, savings plans, spot (only where safe), Dev/Test, Azure Hybrid Benefit | Code (math) + Haiku (explanations) |
| 7 | **Fit Checker / Skeptic** | Reject options that break limits or rules (timeouts, missing features, SLA, zone support, residency, spot for prod…); send back for rework, **max 2 loops** | Sonnet |
| 8 | **Total Cost Agent** | Add people/ops effort cost next to the Azure bill (e.g. AKS needs Kubernetes skills) | Haiku |
| 9 | **Judge** | Build the Cheapest / Balanced / Safest plans with savings, trade-offs and reasons | Sonnet |
| 10 | **Plain Chat** (added in M3) | Ask a no-tools chat the same question; its guess is shown next to ArchTwin's numbers, never used in a plan | Sonnet |

**Pipeline:**
`diagram|text → Diagram Reader / Parser → HUMAN CONFIRM → Workload Profiler → Service Swap → Region Scout → Pricing-Model → Fit Checker/Skeptic (↺ max 2) → Total Cost → Judge → 3 plans`

**Core principle: "the LLM decides, the code calculates."** All price fetching, filtering, unit conversion, monthly math, ranking and savings calculations are plain Python. LLMs only read diagrams, propose swaps, judge fit and write explanations. Never let an LLM do arithmetic on prices.

---

## 7. Outputs

- Original monthly cost vs each plan's cost and savings ($ and %).
- **Side-by-side price comparison per component and its alternatives.**
- Plain-English trade-offs (e.g. "cold start after idle", "lower max request timeout").
- **Every price traced to its Azure `meterId`**; every rule linked to its Microsoft Learn page.
- A list of **rejected options with the Skeptic's reasons** (shows agents checking each other).
- "This analysis cost $0.0X in AI" (spend display).
- Downloadable report (stretch).

**Demo moment:** "Upload the diagram Claude drew. ~60 s later: your design costs ~$3,240/mo; its cheaper twin costs ~$1,980/mo, same specs, still in the EU, proof for every number." (Illustrative numbers; real ones come from the API.)

---

## 8. Data sources (all free, no login)

### Azure Retail Prices API (live prices)
- Endpoint: `https://prices.azure.com/api/retail/prices`
- Unauthenticated. Microsoft docs: https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
- Use `api-version=2023-01-01-preview`, which is needed for **savings plan** prices.
- OData `$filter`. **Filter values are case-sensitive**, e.g.
  `$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'westeurope' and armSkuName eq 'Standard_D2s_v5'`
- Useful fields: `serviceName`, `productName`, `skuName`, `armSkuName`, `armRegionName`, `meterName`, `meterId`, `unitPrice`, `retailPrice`, `unitOfMeasure`, `priceType` (`Consumption`, `Reservation`, `DevTestConsumption`), `reservationTerm`, `savingsPlan`, `isPrimaryMeterRegion`, `currencyCode`.
- Paginated (100 items per page) via `NextPageLink`. Use `meterRegion='primary'` to reduce duplicates.
- A missing SKU in a region = not sold there. That's how Region Scout checks availability.
- **Cache aggressively** (in-memory per instance, plus pre-fetched JSON snapshots for demo services/regions committed in `data/`).
- Note: Claude's sandbox could not reach this host during planning, so verify from a local machine/browser first.

### Service limits & rules knowledge file
- `data/service_limits.json`: hand-curated (LLM-assisted at build time, then human-checked) facts per service/tier: max request timeout, scale-to-zero, cold start, SLA, zone-redundancy support, geo-replication, max DB size, etc.
- **Every entry carries a `source_url` to Microsoft Learn.** The Fit Checker may only cite rules from this file.

### Demo & test inputs
- Made-up architectures and diagrams (own drawings, Claude-drawn diagrams).
- Azure Architecture Center reference architectures for **testing** diagram extraction (their articles list components in text, giving free ground truth). Don't rehost their images publicly; use own diagrams for public demos.

---

## 9. Tech stack & architecture

- **Backend:** Python 3.12, **FastAPI**, deployed as a Vercel Python function (e.g. `api/index.py` exposing the FastAPI `app`, plus `vercel.json` / `requirements.txt`). Agents are **plain Python modules**; LangGraph optional.
- **Frontend:** static HTML + vanilla JS (or a light lib) served by Vercel. Pieces: image upload, editable confirm list, live agent timeline, plan comparison tables/charts.
- **Durable, resumable runs** (Kabeer's original idea; keep it):
  - Each agent step = **its own short HTTP request**: `POST /api/run/step` with the current run state → returns the updated state + timeline events.
  - The browser drives the loop and **persists run state in `localStorage`**, so a run resumes after a failure, timeout or refresh.
  - Keeps every function call short, safe under Vercel's function duration limits (check current Hobby limits).
- **Images:** resize in the browser to about 1,500 px longest side and compress (cheaper vision tokens; under Vercel's ~4.5 MB request body limit).
- **Prompt-injection safety:** treat text inside uploaded images and user text as **data**. The Diagram Reader only extracts components into a strict JSON schema and ignores any instructions in the image.
- **Structured outputs:** every LLM agent returns JSON validated with **Pydantic**; on validation failure retry once, then fail gracefully.

### Suggested repo layout
```
/api/index.py            # FastAPI app (Vercel entry)
/app/agents/             # one module per agent
/app/pricing/            # Azure Retail Prices client, cache, monthly-cost math
/app/llm.py              # LiteLLM client wrapper: model routing, max_tokens, retries, spend tracking, call caps
/app/models.py           # Pydantic schemas (Component, WorkloadProfile, Candidate, PlanOption, Plan, RunState, TimelineEvent)
/app/orchestrator.py     # step machine: given RunState → next step
/data/service_limits.json
/data/price_snapshots/   # cached price JSON for demos
/data/demos/             # demo architectures + cached full run results
/public/                 # frontend (index.html, app.js, styles.css)
/tests/                  # unit tests + LLM record/replay fixtures
.env.example             # LITELLM_API_KEY=, LITELLM_BASE_URL=, MODEL_SMART=, MODEL_FAST=
.gitignore               # MUST include .env
```

### Core schemas (starting point)
- `Component`: id, azure_service, tier/sku, quantity, region, details{}, confidence (high/med/low), source (diagram/text/user)
- `WorkloadProfile`: traffic_pattern (steady/spiky/business-hours/batch), requests_per_day, data_size_gb, allowed_regions, residency, ha_required, zone_redundancy, team_skills[], downtime_tolerance
- `Candidate`: component_id, service, sku, region, pricing_model, monthly_cost, meter_ids[], notes
- `Rejection`: candidate, rule, source_url, reason
- `Plan`: name (Cheapest/Balanced/Safest), items[], monthly_total, savings_vs_original, tradeoffs[], ops_effort
- `RunState`: run_id, step, input, components, profile, candidates, rejections, plans, timeline[], llm_calls_used, ai_cost_usd

---

## 10. Budget & rate-limit guardrails ($25 total, includes judging)

- **Budget split:** ~$12 development, ~$3 final testing, **~$10 reserved for judging**.
- **Develop without spending:** record real LLM responses once, then **replay fixtures** during UI/pipeline work. Use Haiku for everything until the final polish; switch the Sonnet agents on at the end.
- **Per-call:** always set `max_tokens`; keep prompts tight; send only needed context.
- **Per-run caps:** max 18 LLM calls per run (`MAX_CALLS_PER_RUN`; typical run 9-13); Skeptic max 2 retry loops; hard stop with a clear message if exceeded. No unbounded loops anywhere.
- **Concurrency:** at most ~5 parallel LLM calls (semaphore); retry HTTP 429 with exponential backoff (60 RPM limit).
- **Cached demos:** one-click demo architectures show **saved full results** (cost $0, still work if the key expires or runs out). Judges can still run their own input live.
- **Live-run limits:** e.g. 3 live runs per visitor per hour, plus a global kill switch env var (e.g. `LIVE_RUNS_ENABLED=false`) when spend nears the reserve.
- **Spend tracking:** log the cost per call (LiteLLM usually returns response cost; otherwise estimate from token usage) and show it in the UI.
- Optional: prompt caching for the large static system prompt / service-limits context if the gateway supports it.

---

## 11. Key safety checklist

- [ ] `.env` in `.gitignore` **before the first commit**
- [ ] `.env.example` with empty values only
- [ ] Vercel env vars: `LITELLM_API_KEY`, `LITELLM_BASE_URL`, `MODEL_SMART`, `MODEL_FAST`
- [ ] All LLM calls server-side; the key never reaches frontend JS or responses
- [ ] Optional: gitleaks / secret-scan pre-commit hook
- [ ] Never log the key

---

## 12. Scope & milestones

**M1 — Core (must work end to end first; this alone meets the must-haves)**
- Text input **and** diagram-image input → component list → **human confirm** step
- Compute + database components only
- Service Swap → Region Scout (live prices) → Skeptic → Judge
- One working plan comparison with meter IDs, live agent timeline, deployed on Vercel

**M2 — Next**
- Pricing-Model Agent (reservations, savings plans, spot, Dev/Test, Hybrid Benefit)
- Workload Profiler questions
- Three plans (Cheapest / Balanced / Safest)
- Total Cost Agent (ops effort)
- Mermaid diagram input (Claude often produces Mermaid)

**M3 — Stretch**
- Caching, storage, networking components (Redis, Blob, egress, NAT, IPs, Log Analytics)
- draw.io (`.drawio` XML) and Bicep/ARM template input (deterministic parsing)
- Side-by-side "plain Claude chat estimate vs ArchTwin" comparison
- Report export (Markdown/PDF), one-click demos with cached results, polish

**Status (Nov 2026):** M1, M2 and M3 built. M3 = networking/storage pricing (no code change needed), draw.io / ARM /
Bicep input (`app/formats.py`, exact extraction in code), Markdown report + print-to-PDF, before/after architecture
diagrams (Mermaid, from parsed `connections`), Plain Chat comparison, re-recorded demos and fixtures.

**Out of scope:** deploying/changing anything in Azure; accessing any Azure account; other clouds (AWS/GCP).

---

## 13. How we prove it works (show in README / demo)

- **Diagram reading accuracy:** run ~10 Azure Architecture Center diagrams; compare extracted components to the article's component list → "extracted 46/50 correctly; the confirm step caught the rest."
- **Price accuracy:** plans for 3 reference architectures match the official Azure Pricing Calculator.
- **Self-checking:** every rejected option is shown with its rule + source link.
- **AI cost transparency:** per-run AI cost displayed.

---

## 14. Rubric fit (keep in mind when prioritizing)

| Criterion | How ArchTwin earns it |
|---|---|
| Completeness / multi-agent | 9 role-based agents, live tool calls, a Skeptic that sends work back, a human confirm step, every step visible |
| Creativity | Diagram in → verified cheaper twin out; pre-deployment optimization with live prices and a source for every number |
| Technical execution | FastAPI on Vercel, resumable step design, key in env vars, caching, Pydantic-validated outputs, safe image handling, budget guardrails |
| Presentation | Upload a diagram → watch the agents → before/after costs; cached one-click demos |
| Business value | SSI can use it in every client proposal and architecture review |

---

## 15. Decisions already made (don't re-open)

- Ideas considered and **rejected**: dependency-upgrade impact analysis (Claude Code already does it); disaster-recovery planner (nobody would pay ~$4k/mo more for a second region); plain "price my architecture" calculator (a Claude chat can roughly do it). Also explored but not chosen: CVE range verifier, app privacy-label auditor, cloud bill detective, requirements-ambiguity tool, log root-cause detective.
- **Chosen:** Azure architecture **cost optimization** including **alternative, better-fitting Azure services** with price comparisons, plus **diagram image input**.

## 16. Open items to verify early

1. Exact model ID strings via `GET /models` (Sonnet name casing).
2. Whether LiteLLM returns per-request cost (header/usage field) for spend tracking.
3. Current Vercel Hobby Python function duration and body-size limits.
4. Azure Retail Prices API reachable from Vercel; confirm filter syntax for the target services.
5. ~~Final product name~~ Decided: **ArchTwin** (`archtwin.vercel.app` was free in Nov 2026).

## 17. Suggested first steps for Claude Code

1. Scaffold the repo (layout above), `.gitignore` with `.env`, `.env.example`, FastAPI hello-world deployed to Vercel.
2. Build `app/llm.py` (routing, max_tokens, retries, call caps, spend log) and verify `/models`.
3. Build `app/pricing/` client + cache; unit-test monthly cost for a few VM / App Service / SQL SKUs.
4. Implement M1 agents with Pydantic schemas and record/replay fixtures.
5. Frontend: input (text + image upload with resize) → confirm list → timeline → plan table.
6. Deploy, test end to end, then move to M2.
