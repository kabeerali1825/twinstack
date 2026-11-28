"""Vercel entry point: exposes the FastAPI `app`."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # local dev only; on Vercel env vars come from project settings

import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import Path as PathParam
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app import llm, orchestrator, store
from app.agents.parser import REGION_GROUPS
from app.models import RunState
from app.pricing import services

app = FastAPI(title="ArchTwin")
IMAGE_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
MAX_IMAGE_CHARS = 3_500_000  # ~2.6 MB image; Vercel's request body limit is 4.5 MB
MAX_COMPONENTS = 25


class StartRequest(BaseModel):
    text: str = Field(default="", max_length=200_000)  # pasted text, Mermaid, or a draw.io / ARM / Bicep file
    image: str = Field(default="", max_length=MAX_IMAGE_CHARS)

    @model_validator(mode="after")
    def check(self):
        if self.image and not self.image.startswith(IMAGE_PREFIXES):
            raise ValueError("image must be a PNG, JPEG or WebP data URI")
        if not self.image and len(self.text.strip()) < 10:
            raise ValueError("describe the architecture (10+ characters) or upload a diagram")
        return self


# ---- Budget guards --------------------------------------------------------------------------------
# ponytail: in-memory per server instance; Vercel may run several, so treat these as soft limits.
# The hard stop is the key's total spend, read from the gateway.
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
LIMITS = {"start": int(os.environ.get("LIVE_RUNS_PER_HOUR", "3")), "step": 80}


def _rate_limit(request: Request, kind: str) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0]
    hits, now = _hits[(ip, kind)], time.time()
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= LIMITS[kind]:
        raise HTTPException(429, f"Limit reached: {LIMITS['start']} live analyses per hour per visitor. "
                                 "Try again later, or open a sample result.")
    hits.append(now)


def _budget_ok() -> None:
    if os.environ.get("LIVE_RUNS_ENABLED", "true").lower() != "true":
        raise HTTPException(503, "Live runs are paused to protect the AI budget. Open a saved demo instead.")
    if os.environ.get("LLM_FIXTURES"):
        return  # replaying recorded replies: no spend
    try:
        spent = llm.key_spend()
    except Exception:
        return  # can't check right now; per-run and per-visitor caps still apply
    if spent >= float(os.environ.get("SPEND_STOP_USD", "23")):
        raise HTTPException(503, "The AI budget for this demo is used up. Open a saved demo instead.")


# ---- Routes ---------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    """Lists for the confirm step (service names, region groups) and the live-run limit to show."""
    return {"services": sorted(services()), "region_groups": REGION_GROUPS, "live_runs_per_hour": LIMITS["start"],
            "sharing": store.enabled()}


@app.post("/api/run/start")
def start(req: StartRequest, request: Request) -> RunState:
    _budget_ok()
    _rate_limit(request, "start")
    return RunState(input_text=req.text.strip(), input_image=req.image)


@app.post("/api/run/step")
def run_step(state: RunState, request: Request) -> RunState:
    _budget_ok()
    _rate_limit(request, "step")
    if state.step != "parse":
        state.input_image = ""
    if len(state.components) > MAX_COMPONENTS:
        raise HTTPException(400, f"At most {MAX_COMPONENTS} components per analysis.")
    try:
        state = orchestrator.step(state)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if state.step == "done":
        try:
            store.keep_finished(state)
        except Exception as e:  # storage is optional: the run itself has finished fine
            print(f"store.keep_finished failed: {e}")
    return state


# ---- Shared samples (Upstash Redis; no AI spend) ---------------------------------------------------
@app.get("/api/recent")
def recent():
    """Card summaries of the newest runs people chose to share."""
    if not store.enabled():
        return []
    try:
        return store.recent()
    except Exception as e:
        print(f"store.recent failed: {e}")
        return []


@app.post("/api/share/{run_id}")
def share(run_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")):
    if not store.enabled():
        raise HTTPException(503, "Sharing isn't set up on this server.")
    if not store.share(run_id):
        raise HTTPException(404, "This result can no longer be shared (results can be shared for an hour after they finish).")
    return {"ok": True}


@app.get("/api/shared/{run_id}")
def shared(run_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")):
    raw = store.get_shared(run_id) if store.enabled() else None
    if raw is None:
        raise HTTPException(404, "That shared result isn't available any more.")
    return Response(raw, media_type="application/json")


# Local dev: serve the frontend from the same process. On Vercel, /public is served statically.
PUBLIC = Path(__file__).resolve().parent.parent / "public"
if PUBLIC.is_dir():
    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        # Browsers otherwise reuse a stale app.js after an edit; Vercel already sends max-age=0 in production.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
