"""Step machine: each call runs exactly one agent step on a RunState and moves it to the next step.

parse -> profile -> confirm (human, answers questions) -> price -> scout -> swap -> price -> scout -> skeptic
      -> (swap, max 2 reworks) -> judge -> effort -> commit -> done
The browser drives the loop (one short HTTP request per step) and keeps the state, so runs can resume.
"""
from app.agents import diagram, judge, parser, pricer, pricing_model, profiler, scout, skeptic, swap, total_cost
from app.llm import BudgetExceeded
from app.models import Option, RunState
from app.pricing import resolve_service

MAX_SWAP_ROUNDS = 3  # first proposal + 2 reworks


def _parse(state: RunState) -> None:
    if state.input_image:
        diagram.run(state)
        state.input_image = ""  # read once; don't ship it back and forth on every step
    else:
        parser.run(state)


AGENTS = {"parse": _parse, "profile": profiler.run, "price": pricer.run, "scout": scout.run, "swap": swap.run,
          "skeptic": skeptic.run, "judge": judge.run, "effort": total_cost.run, "commit": pricing_model.run}


def _start_pricing(state: RunState) -> None:
    """Human confirmed the component list: validate it and create the original options."""
    if not state.components:
        raise ValueError("No components to analyse.")
    for c in state.components:
        service = resolve_service(c.service)
        if not service:
            raise ValueError(f"{c.id}: '{c.service}' is not an Azure billing service name.")
        c.service = service
    profiler.apply_answers(state)
    state.options = [Option(id=f"{c.id}.orig", component_id=c.id, service=c.service, sku=c.sku,
                            details=f"{c.details}; count {c.quantity:g}".strip("; "), is_original=True)
                     for c in state.components]
    state.candidates, state.rejections, state.plans, state.swap_round = [], [], [], 0
    state.log("You", "done", f"Confirmed {len(state.components)} components",
              [c.model_dump() for c in state.components])


def _next(state: RunState) -> str:
    match state.step:
        case "parse":
            return "profile"
        case "profile":
            return "confirm"
        case "price":
            return "scout"
        case "scout":
            return "swap" if state.swap_round == 0 else "skeptic"
        case "swap":
            return "price"
        case "skeptic":
            rework = skeptic.needs_rework(state) and state.swap_round < MAX_SWAP_ROUNDS and state.swap_had_new
            return "swap" if rework else "judge"
        case "judge":
            return "effort"
        case "effort":
            return "commit"
        case "commit":
            return "done"
    raise ValueError(f"Unknown step {state.step}")


def step(state: RunState) -> RunState:
    if state.step in ("done", "failed"):
        return state
    if state.step == "confirm":
        if not state.confirmed:
            raise ValueError("Confirm the component list first.")
        _start_pricing(state)
        state.step = "price"
    state.error = ""
    try:
        AGENTS[state.step](state)
    except BudgetExceeded as e:
        state.error, state.step = str(e), "failed"
        state.log("System", "error", str(e))
        return state
    except Exception as e:  # leave the step as is so the browser can retry it
        state.error = f"{state.step} failed: {e}"
        state.log("System", "error", state.error)
        return state
    state.step = _next(state)
    return state
