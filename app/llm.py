"""LiteLLM gateway wrapper: model routing, per-run call cap, spend tracking, Pydantic-validated JSON.

The API key is read from env only and never logged or returned.
"""
import os
from dataclasses import dataclass
from functools import cache
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

MAX_CALLS_PER_RUN = 15
T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cost_usd: float = 0.0


@cache
def _client() -> OpenAI:
    # The SDK retries 429 / 5xx / timeouts with exponential backoff.
    return OpenAI(
        api_key=os.environ["LITELLM_API_KEY"],
        base_url=os.environ["LITELLM_BASE_URL"],
        max_retries=4,
        timeout=90,
    )


def _create(**kwargs) -> tuple[str, float]:
    raw = _client().chat.completions.with_raw_response.create(**kwargs)
    resp = raw.parse()
    # LiteLLM sends the per-call USD cost in this header (verified for both models).
    cost = float(raw.headers.get("x-litellm-response-cost") or 0)
    return resp.choices[0].message.content or "", cost


def chat(messages: list[dict], usage: Usage, *, smart: bool = False, max_tokens: int = 800) -> str:
    """One LLM call. smart=True -> MODEL_SMART (Sonnet), else MODEL_FAST (Haiku)."""
    if usage.calls >= MAX_CALLS_PER_RUN:
        raise BudgetExceeded(f"This run hit its limit of {MAX_CALLS_PER_RUN} AI calls.")
    usage.calls += 1  # counted before the call, so failed calls count too
    model = os.environ["MODEL_SMART" if smart else "MODEL_FAST"]
    text, cost = _create(model=model, messages=messages, max_tokens=max_tokens)
    usage.cost_usd += cost
    return text


def _json_part(text: str) -> str:
    # Drops ```json fences or any preamble around the object.
    return text[text.find("{"): text.rfind("}") + 1]


def chat_json(messages: list[dict], schema: type[T], usage: Usage, *, smart: bool = False,
              max_tokens: int = 1500) -> T:
    """LLM call whose reply must validate as `schema`. Retries once with the error, then raises."""
    text = chat(messages, usage, smart=smart, max_tokens=max_tokens)
    try:
        return schema.model_validate_json(_json_part(text))
    except ValidationError as e:
        retry = [*messages,
                 {"role": "assistant", "content": text},
                 {"role": "user", "content": f"That JSON failed validation:\n{e}\nReply with only the corrected JSON."}]
        text = chat(retry, usage, smart=smart, max_tokens=max_tokens)
        return schema.model_validate_json(_json_part(text))
