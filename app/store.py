"""Shared sample runs in Upstash Redis (REST API, stdlib only).

Every finished run is kept for an hour under run:<id> so its owner can choose to share it. Sharing puts a small
card summary on the `recent` list (newest 5) and keeps the run for 90 days. Only the server writes run:<id>,
from the state it just produced, so a shared sample can't be swapped for other content later.

ponytail: the run state itself still comes from the browser between steps, so a determined visitor could shape
the last step's input. Sign RunState between steps if shared samples ever need to be tamper-proof.
"""
import json
import os
import urllib.request

from app.models import RunState

KEEP = 5
PENDING_TTL = 3600          # seconds a finished run waits for its owner to share it
SHARED_TTL = 90 * 24 * 3600


def _conf() -> tuple[str, str] | None:
    # Vercel's Upstash integration names them KV_REST_API_*; Upstash's own docs use UPSTASH_REDIS_REST_*.
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return (url.rstrip("/"), token) if url and token else None


def enabled() -> bool:
    return _conf() is not None


def _pipe(*cmds: list) -> list:
    """Runs Redis commands in one request; returns each command's result."""
    url, token = _conf()
    req = urllib.request.Request(f"{url}/pipeline", data=json.dumps(cmds).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.load(r)
    if errors := [x["error"] for x in out if "error" in x]:
        raise RuntimeError(f"Redis: {errors[0]}")
    return [x["result"] for x in out]


def keep_finished(state: RunState) -> None:
    """Called when a run finishes. NX: a run already kept (or shared) is never overwritten."""
    if enabled() and state.plans:
        _pipe(["SET", f"run:{state.run_id}", state.model_dump_json(exclude={"input_image"}),
               "EX", PENDING_TTL, "NX"])


def _summary(s: RunState) -> dict:
    services = list(dict.fromkeys(c.service for c in s.components))
    from_diagram = any(c.source == "diagram" for c in s.components)
    return {
        "id": s.run_id,
        "kind": "Diagram input" if from_diagram else "Text input",
        "name": ", ".join(services[:3]) + (f" +{len(services) - 3}" if len(services) > 3 else ""),
        "desc": (f"{len(s.components)} components read from a diagram." if from_diagram
                 else s.input_text[:110] + ("…" if len(s.input_text) > 110 else "")),
        "was": s.original_total,
        "now": s.plans[0].monthly_total,
        "pct": s.plans[0].savings_pct,
        "t": s.timeline[0].t if s.timeline else 0,
    }


def recent() -> list[dict]:
    return [json.loads(x) for x in _pipe(["LRANGE", "recent", 0, KEEP - 1])[0]]


def share(run_id: str) -> bool:
    """Adds a finished run to the recent list. False if it's unknown or its hour to share has passed."""
    if any(r["id"] == run_id for r in recent()):
        return True
    raw = _pipe(["GET", f"run:{run_id}"])[0]
    if raw is None:
        return False
    _pipe(["EXPIRE", f"run:{run_id}", SHARED_TTL],
          ["LPUSH", "recent", json.dumps(_summary(RunState.model_validate_json(raw)))],
          ["LTRIM", "recent", 0, KEEP - 1])
    return True


def get_shared(run_id: str) -> str | None:
    """The stored run JSON, only if it's on the recent list."""
    if not any(r["id"] == run_id for r in recent()):
        return None
    return _pipe(["GET", f"run:{run_id}"])[0]
