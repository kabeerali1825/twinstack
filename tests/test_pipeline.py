"""End-to-end pipeline from recorded LLM replies (tests/fixtures/llm) and price snapshots (data/price_snapshots).
No network, no spend. Re-record after changing prompts:
  LLM_FIXTURES=record PRICE_SNAPSHOT_WRITE=1 MODEL_SMART=claude-sonnet-5-5 python -m scripts.run_pipeline
(Pricer, Swap, Skeptic and Judge run on Sonnet as in production; Haiku as Pricer misprices AKS and SQL.)
"""
import urllib.error

import pytest

from app import llm, pricing
from scripts.run_pipeline import DEMO, run


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("LLM_FIXTURES", "replay")
    monkeypatch.setenv("MODEL_FAST", "claude-haiku")
    monkeypatch.setenv("MODEL_SMART", "claude-sonnet-5-5")  # as recorded
    monkeypatch.setitem(llm._thinking_off, "claude-sonnet-5-5", "between_tools")  # the key the recording used

    def no_network(*a, **kw):
        raise urllib.error.URLError("offline test")

    monkeypatch.setattr(pricing, "_fetch_all", no_network)  # query() falls back to snapshots
    monkeypatch.setattr(pricing, "_get", no_network)
    pricing.query.cache_clear()
    yield
    pricing.query.cache_clear()


def test_demo_run_end_to_end(offline):
    s = run(DEMO)
    assert s.step == "done" and not s.error
    assert s.llm_calls <= 16

    # Original: AKS uptime SLA + 3 x D4s v5 + SQL GP 4 vCore + its SQL license + Redis P1, West Europe
    # (the recorded Pricer may add a small storage line on top).
    core = 73.00 + 503.70 + 488.92 + 291.90 + 405.15
    assert core - 0.02 <= s.original_total <= core * 1.02

    assert [p.name for p in s.plans] == ["Cheapest", "Balanced", "Safest"]
    cheapest = s.plans[0]
    assert all(cheapest.monthly_total <= p.monthly_total + 0.01 for p in s.plans)
    for p in s.plans:
        assert p.monthly_total == pytest.approx(sum(i.monthly_cost for i in p.items))
        assert p.savings == pytest.approx(s.original_total - p.monthly_total, abs=0.02)
        if p.region != "original regions":
            assert {i.region for i in p.items} == {p.region}  # one region per plan

    # Every priced line is traceable to an Azure meter.
    assert all(line.meter_id for c in s.candidates for line in c.lines)
    # M2 steps ran: commitments never cost more than pay-as-you-go; ops effort was estimated.
    for p in s.plans:
        assert p.commitments and p.commitments.three_year <= p.commitments.one_year <= p.commitments.payg + 0.01
    assert s.ops_rate > 0 and s.today_ops_hours > 0
    # M3: the plain-chat baseline ran, and data flows were read for the architecture diagram.
    assert s.baseline and s.baseline.today > 0
    assert s.connections and all(a in ids or a == "users" for a, b in s.connections for ids in [{c.id for c in s.components}])
    # Rejections carry who rejected and why; Skeptic ones cite a Learn page.
    assert all(r.reason and (r.by != "Skeptic" or r.source_url.startswith("https://learn.microsoft.com"))
               for r in s.rejections)


def diagram_state():
    import base64
    from pathlib import Path

    from app.models import RunState
    png = (Path(__file__).resolve().parents[1] / "public" / "demos" / "contoso-bookings.png").read_bytes()
    return RunState(input_image="data:image/png;base64," + base64.b64encode(png).decode())


def test_diagram_reader(offline):
    from app import orchestrator
    s = orchestrator.step(diagram_state())
    assert s.step == "profile" and not s.error  # next: the Workload Profiler, then the confirm screen
    assert s.input_image == ""  # not shipped back and forth after reading
    services = {c.service for c in s.components}
    assert {"Azure App Service", "SQL Database", "Redis Cache"} <= services
    assert all(c.source == "diagram" for c in s.components)
    assert "northeurope" in s.constraints.allowed_regions


def test_live_run_limit_per_visitor(offline):
    from fastapi.testclient import TestClient

    from api import index
    index._hits.clear()
    client = TestClient(index.app)
    codes = [client.post("/api/run/start", json={"text": DEMO}).status_code for _ in range(index.LIMITS["start"] + 1)]
    assert codes[:-1] == [200] * index.LIMITS["start"] and codes[-1] == 429
    index._hits.clear()


def test_api_round_trip(offline):
    """The browser's loop: start, then POST the whole state back for each step, confirming in between."""
    from fastapi.testclient import TestClient

    from api.index import app

    from api import index
    index._hits.clear()
    client = TestClient(app)
    assert client.post("/api/run/start", json={"text": "hi"}).status_code == 422  # too short
    assert client.post("/api/run/start", json={"image": "data:text/html;base64,PGgxPg=="}).status_code == 422
    state = client.post("/api/run/start", json={"text": DEMO}).json()
    for _ in range(30):
        if state["step"] == "confirm":
            assert client.post("/api/run/step", json=state).status_code == 400  # must confirm first
            state["confirmed"] = True
        r = client.post("/api/run/step", json=state)
        assert r.status_code == 200, r.text
        state = r.json()
        if state["step"] == "done":
            break
    assert state["step"] == "done" and len(state["plans"]) == 3
