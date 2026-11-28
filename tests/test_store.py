"""Shared samples (app/store.py) against a small in-memory stand-in for Upstash. No network."""
import json
from pathlib import Path

import pytest

from app import store
from app.models import RunState

DEMO = Path(__file__).resolve().parent.parent / "public" / "demos" / "aks-sql-redis.json"


@pytest.fixture
def redis(monkeypatch):
    db: dict = {}

    def pipe(*cmds):
        out = []
        for op, key, *args in cmds:
            if op == "SET":
                nx = "NX" in args
                out.append(None if nx and key in db else db.setdefault(key, args[0]) if nx else db.update({key: args[0]}))
            elif op == "GET":
                out.append(db.get(key))
            elif op == "LPUSH":
                db[key] = [args[0], *db.get(key, [])]
            elif op == "LTRIM":
                db[key] = db.get(key, [])[args[0]: args[1] + 1]
            elif op == "LRANGE":
                out.append(db.get(key, [])[args[0]: args[1] + 1])
            if op in ("LPUSH", "LTRIM", "EXPIRE"):
                out.append(1)
        return out

    monkeypatch.setenv("KV_REST_API_URL", "https://fake")
    monkeypatch.setenv("KV_REST_API_TOKEN", "t")
    monkeypatch.setattr(store, "_pipe", pipe)
    return db


def run(run_id: str) -> RunState:
    return RunState.model_validate({**json.loads(DEMO.read_text()), "run_id": run_id, "input_image": "data:x"})


def test_share_keeps_newest_five(redis):
    assert store.share("aaaaaaaaaaaa") is False  # never finished here
    for i in range(7):
        rid = f"{i:012x}"
        store.keep_finished(run(rid))
        assert store.share(rid) and store.share(rid)  # second share is a no-op
    ids = [r["id"] for r in store.recent()]
    assert ids == [f"{i:012x}" for i in (6, 5, 4, 3, 2)]
    assert store.get_shared(f"{1:012x}") is None  # dropped off the list
    saved = json.loads(store.get_shared(f"{6:012x}"))
    assert "input_image" not in saved and saved["plans"]  # image never stored


def test_finished_run_is_never_overwritten(redis):
    store.keep_finished(run("bbbbbbbbbbbb"))
    fake = run("bbbbbbbbbbbb")
    fake.input_text = "something else"
    store.keep_finished(fake)
    assert "something else" not in redis["run:bbbbbbbbbbbb"]
