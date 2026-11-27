"""Vercel entry point: exposes the FastAPI `app`."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # local dev only; on Vercel env vars come from project settings

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="TwinStack")


@app.get("/api/health")
def health():
    return {"ok": True}


# Local dev: serve the frontend from the same process. On Vercel, /public is served statically.
PUBLIC = Path(__file__).resolve().parent.parent / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
