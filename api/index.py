"""Vercel entry point: exposes the FastAPI `app`."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # local dev only; on Vercel env vars come from project settings

import os

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import orchestrator
from app.models import RunState

app = FastAPI(title="TwinStack")


class StartRequest(BaseModel):
    text: str = Field(min_length=10, max_length=4000)


def _live_enabled() -> None:
    if os.environ.get("LIVE_RUNS_ENABLED", "true").lower() != "true":
        raise HTTPException(503, "Live runs are paused to protect the AI budget. Try a saved demo.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/run/start")
def start(req: StartRequest) -> RunState:
    _live_enabled()
    return RunState(input_text=req.text)


@app.post("/api/run/step")
def run_step(state: RunState) -> RunState:
    _live_enabled()
    try:
        return orchestrator.step(state)
    except ValueError as e:
        raise HTTPException(400, str(e))


# Local dev: serve the frontend from the same process. On Vercel, /public is served statically.
PUBLIC = Path(__file__).resolve().parent.parent / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
