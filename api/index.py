"""Vercel entry point: exposes the FastAPI `app`."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # local dev only; on Vercel env vars come from project settings

import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app import llm, orchestrator
from app.agents.parser import REGION_GROUPS
from app.models import RunState
from app.pricing import services

app = FastAPI(title="TwinStack")
IMAGE_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
MAX_IMAGE_CHARS = 3_500_000  # ~2.6 MB image; Vercel's request body limit is 4.5 MB
MAX_COMPONENTS = 25


class StartRequest(BaseModel):
    text: str = Field(default="", max_length=4000)
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
    return {"services": sorted(services()), "region_groups": REGION_GROUPS, "live_runs_per_hour": LIMITS["start"]}


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
        return orchestrator.step(state)
    except ValueError as e:
        raise HTTPException(400, str(e))


# Local dev: serve the frontend from the same process. On Vercel, /public is served statically.
PUBLIC = Path(__file__).resolve().parent.parent / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
