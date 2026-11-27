"""Offline checks for app/llm.py (no network, no spend)."""
import pytest
from pydantic import BaseModel

from app import llm


class Thing(BaseModel):
    name: str


@pytest.fixture
def fake(monkeypatch):
    replies = []
    monkeypatch.setenv("MODEL_FAST", "fast-model")
    monkeypatch.setattr(llm, "_create", lambda **kw: (replies.pop(0), 0.01))
    return replies


def test_json_with_fences_and_spend(fake):
    fake.append('Sure:\n```json\n{"name": "aks"}\n```')
    u = llm.Usage()
    assert llm.chat_json([], Thing, u).name == "aks"
    assert u.llm_calls == 1 and u.ai_cost_usd == pytest.approx(0.01)


def test_retries_once_then_succeeds(fake):
    fake += ['{"nope": 1}', '{"name": "sql"}']
    u = llm.Usage()
    assert llm.chat_json([], Thing, u).name == "sql"
    assert u.llm_calls == 2


def test_call_cap(fake):
    u = llm.Usage(llm_calls=llm.MAX_CALLS_PER_RUN)
    with pytest.raises(llm.BudgetExceeded):
        llm.chat([], u)
