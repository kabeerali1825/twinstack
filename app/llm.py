"""LiteLLM gateway wrapper: model routing, per-run call cap, spend tracking, Pydantic-validated JSON,
and record/replay fixtures so development and tests don't spend budget.

The API key is read from env only and never logged or returned.

LLM_FIXTURES=record  -> real calls, each reply saved to tests/fixtures/llm/<hash>.json
LLM_FIXTURES=replay  -> no network; replies come from those files (missing file = error)
"""
import hashlib
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TypeVar

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ValidationError

MAX_CALLS_PER_RUN = 18  # a typical run makes 10-13 (incl. Profiler, Total Cost, Plain Chat); 3 rework rounds ~16
_thinking_off: dict[str, str] = {}  # model -> thinking type that turns thinking off
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "llm"
T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Usage:
    """Anything with these two fields works as usage, e.g. RunState."""
    llm_calls: int = 0
    ai_cost_usd: float = 0.0


@cache
def _client() -> OpenAI:
    # The SDK retries 429 / 5xx / timeouts with exponential backoff.
    return OpenAI(
        api_key=os.environ["LITELLM_API_KEY"],
        base_url=os.environ["LITELLM_BASE_URL"],
        max_retries=4,
        timeout=90,
    )


_spend_cache: tuple[float, float] = (0.0, 0.0)  # (checked_at, spend)


def key_spend(max_age: float = 60) -> float:
    """Total USD spent on the key so far, from the gateway (cached per instance for `max_age` seconds)."""
    global _spend_cache
    if time.time() - _spend_cache[0] > max_age:
        url = os.environ["LITELLM_BASE_URL"].rsplit("/v1", 1)[0] + "/key/info"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            _spend_cache = (time.time(), float(json.load(r)["info"]["spend"]))
    return _spend_cache[1]


def _call_api(**kwargs) -> tuple[str, float]:
    raw = _client().chat.completions.with_raw_response.create(**kwargs)
    resp = raw.parse()
    # LiteLLM sends the per-call USD cost in this header (verified for both models).
    cost = float(raw.headers.get("x-litellm-response-cost") or 0)
    return resp.choices[0].message.content or "", cost


def _create(**kwargs) -> tuple[str, float]:
    mode = os.environ.get("LLM_FIXTURES", "")
    if not mode:
        return _call_api(**kwargs)
    key = hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()[:20]
    path = FIXTURES / f"{key}.json"
    if mode == "replay":
        if not path.exists():
            raise RuntimeError(f"No recorded LLM reply {path.name}; run once with LLM_FIXTURES=record")
        saved = json.loads(path.read_text())
        return saved["text"], saved["cost"]
    text, cost = _call_api(**kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": kwargs["model"], "text": text, "cost": cost}, indent=1))
    return text, cost


def chat(messages: list[dict], usage, *, smart: bool = False, max_tokens: int = 800) -> str:
    """One LLM call. smart=True -> MODEL_SMART (Sonnet), else MODEL_FAST (Haiku)."""
    if usage.llm_calls >= MAX_CALLS_PER_RUN:
        raise BudgetExceeded(f"This run hit its limit of {MAX_CALLS_PER_RUN} AI calls.")
    usage.llm_calls += 1  # counted before the call, so failed calls count too
    model = os.environ["MODEL_SMART" if smart else "MODEL_FAST"]
    # The gateway enables extended thinking by default; on structured tasks it can spend the whole
    # max_tokens budget thinking and return empty content, so it's switched off. Models differ in how:
    # Haiku takes "disabled", Sonnet 5.5 asks for "between_tools" (and says so in a 400 error).
    try:
        text, cost = _create(model=model, messages=messages, max_tokens=max_tokens,
                             extra_body={"thinking": {"type": _thinking_off.get(model, "disabled")}})
    except BadRequestError as e:
        if "between_tools" not in str(e) or _thinking_off.get(model) == "between_tools":
            raise
        _thinking_off[model] = "between_tools"
        text, cost = _create(model=model, messages=messages, max_tokens=max_tokens,
                             extra_body={"thinking": {"type": "between_tools"}})
    usage.ai_cost_usd += cost
    return text


def _json_part(text: str) -> str:
    # Drops ```json fences or any preamble around the object.
    return text[text.find("{"): text.rfind("}") + 1]


# Haiku sometimes ends the object with a stray key fragment: `..."EU.", "}` or `..."EU.", "}"]}`.
_TRAILING_JUNK = re.compile(r',\s*"[\s"\]}:]*$')


def _validate(text: str, schema: type[T]) -> T:
    part = _json_part(text)
    try:
        return schema.model_validate_json(part)
    except ValidationError as e:
        fixed = _TRAILING_JUNK.sub("", part)
        if fixed != part:
            try:
                return schema.model_validate_json(fixed + "}")
            except ValidationError:
                pass
        raise e


def chat_json(messages: list[dict], schema: type[T], usage, *, smart: bool = False,
              max_tokens: int = 1500) -> T:
    """LLM call whose reply must validate as `schema`. Retries once with the error, then raises."""
    text = chat(messages, usage, smart=smart, max_tokens=max_tokens)
    try:
        return _validate(text, schema)
    except ValidationError as e:
        retry = [*messages,
                 {"role": "assistant", "content": text},
                 {"role": "user", "content": f"That JSON failed validation:\n{e}\nReply with only the corrected JSON."}]
        text = chat(retry, usage, smart=smart, max_tokens=max_tokens)
        return _validate(text, schema)
