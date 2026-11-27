# TwinStack

**Same architecture. Cheaper twin.** Upload an Azure architecture diagram, describe it in plain text, or paste a
Mermaid diagram, draw.io file, ARM template or Bicep file. A team of
agents finds cheaper ways to run the same architecture that still meet its requirements, using **live Azure prices**
in every allowed region, and returns three plans: **Cheapest, Balanced, Safest**. Every price is traced to its Azure
`meterId`, and every rejection cites a Microsoft Learn page.

Built for SSI's Agent-A-Thon. Read-only by design: it never connects to an Azure account.

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
| Plain Chat | Sonnet, no tools | The same question asked of a plain AI chat, shown next to TwinStack's numbers for comparison (never used in a plan) |

Results also show today's architecture and each plan as diagrams (changed parts highlighted) and can be downloaded
as a Markdown report or printed to PDF.

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

## Budget guards

At most 18 AI calls per run (a typical run makes 9-13); live runs limited per visitor; live runs stop when the key's total spend (read from the
gateway) reaches `SPEND_STOP_USD`; saved examples cost nothing to view.

## Data

Azure Retail Prices API (public, no login), a catalog of all 192 billing service names
(`scripts/build_service_catalog.py`), and hand-checked service limits with Microsoft Learn links. Example
architectures are made up.
