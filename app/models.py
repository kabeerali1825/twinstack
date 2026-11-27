"""Pydantic schemas shared by agents, orchestrator and API."""
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class Component(BaseModel):
    id: str
    service: str                      # exact Retail Prices serviceName, e.g. 'Virtual Machines'
    sku: str | None = None            # e.g. 'Standard_D4s_v5', 'P1 v3', '2 vCore', 'C1'
    quantity: float = 1
    region: str = "westeurope"        # armRegionName
    details: str = ""                 # tier, OS, storage, usage... in plain words
    confidence: Confidence = "high"
    source: Literal["text", "diagram", "user"] = "text"


class Question(BaseModel):
    """Asked by the Workload Profiler on the confirm screen."""
    id: str
    question: str
    why: str = ""
    options: list[str]
    components: list[str] = []
    answer: str = ""


class Constraints(BaseModel):
    allowed_regions: list[str]
    notes: str = ""                   # residency, HA, uptime, traffic shape, from the input


class MeterPick(BaseModel):
    """A price meter, identified the same way in every region, and how much of it is used."""
    service: str
    product: str
    sku: str
    meter: str
    unit: str
    qty: float
    why: str = ""
    global_meter: bool = False        # priced in the 'Global' region, same price everywhere (e.g. SQL licenses)


class Option(BaseModel):
    """One way to run a component: the original or an alternative from Service Swap."""
    id: str
    component_id: str
    service: str
    sku: str | None = None
    details: str = ""
    is_original: bool = False
    rationale: str = ""
    tradeoffs: list[str] = []
    also_replaces: list[str] = []     # other component ids this option makes unnecessary (AKS nodes -> Container Apps)
    is_fix: bool = False              # proposed to fix an issue in today's design (may cost more)
    ops_hours: float = 0              # Total Cost agent: people hours a month to run it
    migration_hours: float = 0        # one-off hours to move from today's setup to it
    effort_reason: str = ""
    meters: list[MeterPick] = []      # chosen by the Pricer
    priced: bool = False              # Pricer has run on it (meters may still be empty)
    scouted: bool = False             # Region Scout has run on it
    reviewed: bool = False            # Skeptic has run on it
    round: int = 0                    # Service Swap round that proposed it (0 = original)


class PriceLine(BaseModel):
    meter: str
    meter_id: str
    unit_price: float
    unit: str
    qty: float
    monthly: float


class Candidate(BaseModel):
    """An option priced in one region (plain code, no LLM)."""
    option_id: str
    component_id: str
    region: str
    monthly_cost: float
    lines: list[PriceLine]


class Rejection(BaseModel):
    option_id: str
    rule: str
    reason: str
    source_url: str = ""
    by: str = "Skeptic"


class Issue(BaseModel):
    """Something in TODAY's design that doesn't fit the requirements."""
    component_id: str
    kind: Literal["rule", "advice"]   # rule: breaks a cited rule, must be fixed; advice: a judgement call, shown only
    rule: str = ""                    # rule id from data/service_limits.json (empty for advice)
    reason: str
    source_url: str = ""


class PlanItem(BaseModel):
    component_id: str
    option_id: str
    region: str
    monthly_cost: float
    replaced_by: str | None = None    # set when another item's option made this component unnecessary


class CommitLine(BaseModel):
    """One price line under pay-as-you-go and the cheapest 1- and 3-year commitment."""
    component_id: str
    meter: str
    payg: float
    one_year: float
    three_year: float
    how_one_year: str = "pay-as-you-go"
    how_three_year: str = "pay-as-you-go"


class Commitments(BaseModel):
    payg: float
    one_year: float
    three_year: float
    hybrid_benefit: float = 0         # SQL licence cost Azure Hybrid Benefit could remove (reported, not applied)
    lines: list[CommitLine] = []      # only lines a commitment makes cheaper


class Plan(BaseModel):
    name: str
    region: str                       # one region for the whole plan, or "original regions"
    items: list[PlanItem]
    monthly_total: float
    savings: float
    savings_pct: float
    summary: str = ""
    tradeoffs: list[str] = []
    note: str = ""                    # code-written note, e.g. why the plan did or didn't move region
    commitments: Commitments | None = None  # set by the Pricing-Model step
    ops_hours: float = 0              # Total Cost step: people hours a month to run this plan
    migration_hours: float = 0        # one-off hours to get from today's setup to this plan


class Event(BaseModel):
    agent: str
    kind: Literal["start", "tool", "output", "reject", "warn", "error", "done"]
    message: str
    data: Any = None
    t: float = Field(default_factory=time.time)


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    step: str = "parse"
    input_text: str = ""
    input_image: str = ""             # data: URI; cleared once the Diagram Reader has read it
    confirmed: bool = False
    components: list[Component] = []
    constraints: Constraints = Constraints(allowed_regions=[])
    questions: list[Question] = []    # from the Workload Profiler, answered on the confirm screen
    options: list[Option] = []
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    issues: list[Issue] = []          # problems the Skeptic found in today's design
    plans: list[Plan] = []
    original_total: float = 0
    fixed_total: float = 0            # today's design with the cheapest fix for each rule issue (0 = no fixes)
    fit_rules_checked: int = 0        # rules today's design was checked against (0 = not checked yet)
    ops_rate: float = 0               # USD per ops hour used by the Total Cost step (0 = not run)
    today_ops_hours: float = 0
    swap_round: int = 0
    swap_had_new: bool = True         # last Service Swap round added something; if not, stop reworking
    timeline: list[Event] = []
    llm_calls: int = 0
    ai_cost_usd: float = 0
    error: str = ""

    def log(self, agent: str, kind: str, message: str, data: Any = None) -> None:
        self.timeline.append(Event(agent=agent, kind=kind, message=message, data=data))

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)
