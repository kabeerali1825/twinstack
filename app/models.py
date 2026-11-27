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


class PlanItem(BaseModel):
    component_id: str
    option_id: str
    region: str
    monthly_cost: float
    replaced_by: str | None = None    # set when another item's option made this component unnecessary


class Plan(BaseModel):
    name: str
    region: str                       # one region for the whole plan, or "original regions"
    items: list[PlanItem]
    monthly_total: float
    savings: float
    savings_pct: float
    summary: str = ""
    tradeoffs: list[str] = []


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
    options: list[Option] = []
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    plans: list[Plan] = []
    original_total: float = 0
    swap_round: int = 0
    timeline: list[Event] = []
    llm_calls: int = 0
    ai_cost_usd: float = 0
    error: str = ""

    def log(self, agent: str, kind: str, message: str, data: Any = None) -> None:
        self.timeline.append(Event(agent=agent, kind=kind, message=message, data=data))

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)
