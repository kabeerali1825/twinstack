# ArchTwin

**Same architecture. Cheaper twin.** Upload an Azure architecture diagram, describe it in plain text, or paste a
Mermaid diagram, draw.io file, ARM template or Bicep file. A team of
agents finds cheaper ways to run the same architecture that still meet its requirements, using **live Azure prices**
in every allowed region, and returns three plans: **Cheapest, Balanced, Safest**. Every price is traced to its Azure
`meterId`, and every rejection cites a Microsoft Learn page.

Built for SSI's Agent-A-Thon. Read-only by design: it never connects to an Azure account.

**Live app: https://twinstack-lilac.vercel.app**

| Try it | Cost |
|---|---|
| Open a recorded result: [Web API on AKS (text input)](https://twinstack-lilac.vercel.app/?demo=aks-sql-redis) · [Contoso Bookings (diagram input)](https://twinstack-lilac.vercel.app/?demo=contoso-bookings) | Free, instant |
| Run your own: paste a description or upload a diagram, then watch each agent work. Click "the example text" or "a sample diagram" to start. | ~1 minute, 3 live runs per hour per visitor |
| Open a result other visitors chose to share (home page, "Recently shared by visitors") | Free, instant |

![Results for the Contoso Bookings diagram: today's $4,925/month vs a $905/month cheapest plan, with per-component prices](docs/results.png)

**Reading the savings.** The Cheapest plan takes the lowest-priced option for every component, which can mean a
migration (above, most of the saving is SQL Database to PostgreSQL). Balanced and Safest keep more of today's setup;
each plan lists its trade-offs, and every rejected alternative is shown with the rule and Microsoft Learn link that
ruled it out.

## How it works

```
diagram | text -> Diagram Reader / Parser -> Workload Profiler -> YOU CONFIRM (+ answer its questions)
  -> Pricer -> Region Scout -> Service Swap -> Pricer -> Region Scout -> Skeptic (sends work back, max 2 rounds)
  -> Judge -> Total Cost -> Pricing Model -> Plain Chat -> 3 plans
```

| Agent | Model | Job |
|---|---|---|
| Diagram Reader | Sonnet (vision) | Reads Azure icons and labels into a component list, with a confidence per item |
| Parser | Haiku | Same, from text or Mermaid; draw.io, ARM and Bicep files are first extracted exactly by code (`app/formats.py`) |
| Workload Profiler | Haiku | Asks only what's missing (traffic shape, uptime, data size, team skills) |
| You | | Check and edit the component list and answer the questions before anything is priced |
| Pricer | Sonnet | Picks which real price meters each option is billed on, from a menu of live rows (it cannot invent a SKU) |
| Region Scout | code | Prices every meter in every allowed region; a region counts only if every meter is sold there |
| Service Swap | Sonnet | Proposes better-fitting services and SKUs (e.g. AKS + nodes -> Container Apps) |
| Skeptic | Sonnet + code | Rejects options that break a documented limit (only rules in `data/service_limits.json`), plus code checks for options that can't be priced or aren't cheaper |
| Judge | Sonnet + code | Code computes the cheapest single-region plan; the Judge picks Balanced and Safest and explains all three |
| Total Cost | Haiku + code | Estimates ops and migration hours per option; code prices them at `OPS_RATE_USD` (default 60) |
| Pricing Model | code | Each plan with 1- and 3-year reservations or savings plans, and Azure Hybrid Benefit for SQL licences |
| Plain Chat | Sonnet, no tools | The same question asked of a plain AI chat, shown next to ArchTwin's numbers for comparison (never used in a plan) |

Results also show today's architecture and each plan as diagrams (changed parts highlighted), can be downloaded
as a Markdown report or PDF, and can be shared as a sample on the home page (opt-in; the 5 newest are listed).

**The LLM decides, the code calculates.** All price lookups, unit conversions, monthly math, totals and savings are
plain Python. Each step is its own short HTTP request; the browser keeps the run state, so a run survives a refresh.

## Run locally

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest uvicorn
copy .env.example .env        # then put your LiteLLM key in .env
.venv\Scripts\python -m uvicorn api.index:app --reload --port 8000
```

Tests run offline from recorded LLM replies and price snapshots: `.venv\Scripts\python -m pytest`.

## Deploy (Vercel)

Import the repo in Vercel, then set these environment variables: `LITELLM_API_KEY`, `LITELLM_BASE_URL`,
`MODEL_SMART`, `MODEL_FAST`, and optionally `LIVE_RUNS_ENABLED` (set `false` to pause live runs),
`SPEND_STOP_USD` (default 23), `LIVE_RUNS_PER_HOUR` (default 3) and `OPS_RATE_USD` (default 60).

Shared samples need a free [Upstash Redis](https://upstash.com) database: set `UPSTASH_REDIS_REST_URL` and
`UPSTASH_REDIS_REST_TOKEN` (or connect it from Vercel's Storage tab, which sets `KV_REST_API_URL` / `KV_REST_API_TOKEN`).
Without them the Share button is hidden and everything else works. Sharing makes no AI calls.

## Budget guards

At most 18 AI calls per run (a typical run makes 9-13); live runs limited per visitor; live runs stop when the key's total spend (read from the
gateway) reaches `SPEND_STOP_USD`; saved examples cost nothing to view.

## Data

Azure Retail Prices API (public, no login), a catalog of all 192 billing service names
(`scripts/build_service_catalog.py`), and hand-checked service limits with Microsoft Learn links. Example
architectures are made up.
