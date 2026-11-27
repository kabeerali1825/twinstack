"""End-to-end pipeline from recorded LLM replies (tests/fixtures/llm) and price snapshots (data/price_snapshots).
No network, no spend. Re-record after changing prompts:
  LLM_FIXTURES=record PRICE_SNAPSHOT_WRITE=1 MODEL_SMART=claude-haiku python -m scripts.run_pipeline
"""
import urllib.error

import pytest

from app import pricing
from scripts.run_pipeline import DEMO, run


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("LLM_FIXTURES", "replay")
    monkeypatch.setenv("MODEL_FAST", "claude-haiku")
    monkeypatch.setenv("MODEL_SMART", "claude-haiku")  # fixtures were recorded with Haiku in every role

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
    assert s.llm_calls <= 15

    # Original: AKS uptime SLA + 3 x D4s v5 + SQL GP 4 vCore + its SQL license + Redis P1, West Europe.
    assert s.original_total == pytest.approx(73.00 + 503.70 + 488.92 + 291.90 + 405.15, abs=0.02)

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
    # Rejections carry who rejected and why; Skeptic ones cite a Learn page.
    assert all(r.reason and (r.by != "Skeptic" or r.source_url.startswith("https://learn.microsoft.com"))
               for r in s.rejections)


def test_api_round_trip(offline):
    """The browser's loop: start, then POST the whole state back for each step, confirming in between."""
    from fastapi.testclient import TestClient

    from api.index import app

    client = TestClient(app)
    assert client.post("/api/run/start", json={"text": "hi"}).status_code == 422  # too short
    state = client.post("/api/run/start", json={"text": DEMO}).json()
    for _ in range(20):
        if state["step"] == "confirm":
            assert client.post("/api/run/step", json=state).status_code == 400  # must confirm first
            state["confirmed"] = True
        r = client.post("/api/run/step", json=state)
        assert r.status_code == 200, r.text
        state = r.json()
        if state["step"] == "done":
            break
    assert state["step"] == "done" and len(state["plans"]) == 3
