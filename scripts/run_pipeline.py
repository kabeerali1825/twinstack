"""Runs the whole pipeline from the command line, auto-confirming the parsed components.

  .venv/Scripts/python -m scripts.run_pipeline "Web API on AKS ..."      # live (spends budget)
  LLM_FIXTURES=record MODEL_SMART=claude-haiku ...                       # cheap dev run, saves replies
  LLM_FIXTURES=replay ...                                                 # free, from saved replies
  IMAGE=public/demos/contoso-bookings.png ...                             # diagram input
Prints the timeline and plans; writes the final state to the path in OUT (optional).
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from app import orchestrator  # noqa: E402
from app.models import RunState  # noqa: E402

DEMO = ("Web API on AKS with 3 Standard_D4s_v5 nodes, Azure SQL Database General Purpose 4 vCore provisioned, "
        "Redis Premium P1 cache, all in West Europe. About 2,000 users, busy in business hours, quiet at night. "
        "Data must stay in the EU.")


def run(text: str, max_steps: int = 20, image: str = "") -> RunState:
    state = RunState(input_text=text, input_image=image)
    shown = 0
    for _ in range(max_steps):
        if state.step == "confirm":
            state.confirmed = True
        state = orchestrator.step(state)
        for e in state.timeline[shown:]:
            print(f"[{e.agent:>12}] {e.kind:6} {e.message}")
        shown = len(state.timeline)
        if state.step in ("done", "failed") or state.error:
            break
    return state


if __name__ == "__main__":
    image = ""
    if os.environ.get("IMAGE"):  # IMAGE=path/to/diagram.png -> Diagram Reader instead of Parser
        import base64
        kind = "png" if os.environ["IMAGE"].lower().endswith(".png") else "jpeg"
        image = f"data:image/{kind};base64," + base64.b64encode(open(os.environ["IMAGE"], "rb").read()).decode()
    s = run(sys.argv[1] if len(sys.argv) > 1 else ("" if image else DEMO), image=image)
    print(f"\nstep={s.step} error={s.error!r} llm_calls={s.llm_calls} ai_cost=${s.ai_cost_usd:.4f}")
    print(f"original: ${s.original_total:,.2f}/mo")
    for p in s.plans:
        print(f"{p.name:9} ${p.monthly_total:,.2f}/mo  saves ${p.savings:,.2f} ({p.savings_pct}%)  {p.summary}")
        for i in p.items:
            print(f"    {i.component_id}: {i.option_id} @ {i.region} ${i.monthly_cost:,.2f}")
    if os.environ.get("OUT"):
        open(os.environ["OUT"], "w").write(s.model_dump_json(indent=1))
