"""Offline checks for the code-side pricing and plan rules (no LLM, no network)."""
from app.agents.judge import _worth_moving
from app.agents.pricer import _Pick, _qty
from app.models import Option, PlanItem

ROW_4VCORE = {"skuName": "4 vCore", "armSkuName": "", "unitOfMeasure": "1 Hour"}
ALT = Option(id="c1.r1a1", component_id="c1", service="Azure Database for PostgreSQL", sku="4 vCore")
ORIG = Option(id="c1.orig", component_id="c1", service="Azure Database for PostgreSQL", sku="4 vCore", is_original=True)


def test_sized_sku_counted_as_vcores_is_divided():
    # '4 vCore' is priced for the whole server; 8 "vcores" = 2 servers (primary + HA standby)
    assert _qty(_Pick(index=0, count=8, count_means="vcores"), ROW_4VCORE, ALT, 1) == 2
    assert _qty(_Pick(index=0, count=2, count_means="instances"), ROW_4VCORE, ALT, 1) == 2


def test_original_keeps_stated_count_but_allows_ha_standby():
    assert _qty(_Pick(index=0, count=2), ROW_4VCORE, ORIG, 1) == 2      # HA standby from the requirements
    assert _qty(_Pick(index=0, count=0.5), ROW_4VCORE, ORIG, 1) == 1    # never "optimizes" the original
    assert _qty(_Pick(index=0, count=5), ROW_4VCORE, ORIG, 1) == 1      # more than 2x is a mistake


def test_usage_amounts_are_plain_counts():
    executions = {"skuName": "Standard", "armSkuName": "", "unitOfMeasure": "10"}
    gb_seconds = {"skuName": "Standard", "armSkuName": "", "unitOfMeasure": "1 GB Second"}
    fn = Option(id="c1.orig", component_id="c1", service="Functions", is_original=True)
    assert _qty(_Pick(index=0, monthly_usage=50_000_000), executions, fn, 1) == 50_000_000
    # 1M GB-seconds a month = ~0.38 GB in use on average; monthly_cost() multiplies it back to 1M
    from app.pricing import monthly_cost
    q = _qty(_Pick(index=0, monthly_usage=1_000_000), gb_seconds, fn, 1)
    assert monthly_cost([{**gb_seconds, "unitPrice": 1.0, "type": "Consumption", "tierMinimumUnits": 0}], q) \
        == __import__("pytest").approx(1_000_000)


def test_fit_review_fixes_todays_design_even_if_it_costs_more(monkeypatch):
    """Today: PostgreSQL Burstable ($30) while the requirements need HA. The only fix ($300) costs more:
    it must survive the cost check, and every plan must use it instead of the broken original."""
    from app import llm
    from app.agents import judge, skeptic
    from app.models import Candidate, Component, Constraints, RunState

    s = RunState(step="skeptic", constraints=Constraints(allowed_regions=["westeurope"], notes="Zone-redundant HA required"))
    s.components = [Component(id="c1", service="Azure Database for PostgreSQL", sku="B2s", region="westeurope")]
    s.options = [
        Option(id="c1.orig", component_id="c1", service="Azure Database for PostgreSQL", sku="B2s", is_original=True,
               priced=True, scouted=True),
        Option(id="c1.r1a1", component_id="c1", service="Azure Database for PostgreSQL", sku="2 vCore", priced=True,
               scouted=True, round=1),
    ]
    s.candidates = [Candidate(option_id="c1.orig", component_id="c1", region="westeurope", monthly_cost=30, lines=[]),
                    Candidate(option_id="c1.r1a1", component_id="c1", region="westeurope", monthly_cost=300, lines=[])]
    replies = iter([
        skeptic._Out(findings=[skeptic._Finding(option_id="c1.orig", rule_id="postgres-burstable-no-ha",
                                                reason="Burstable has no HA, which the requirements need.")]),
        judge._Out(plans=[]),
    ])
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: next(replies))

    skeptic.run(s)
    assert [i.rule for i in s.issues] == ["postgres-burstable-no-ha"]
    assert not s.rejections                          # the pricier fix is NOT rejected as "not cheaper"
    assert not skeptic.needs_rework(s)               # a fix exists, so no rework round is needed

    judge.run(s)
    assert s.fixed_total == 300
    assert all(i.option_id == "c1.r1a1" for p in s.plans for i in p.items)   # broken original never kept
    assert all(p.savings == -270 for p in s.plans)


def test_commitments_pick_cheapest_and_skip_licences(monkeypatch):
    from app.agents import pricing_model as pm
    from app.models import MeterPick, PriceLine

    vm_rows = [
        {"type": "Consumption", "meterId": "vm", "armRegionName": "westeurope", "unitPrice": 0.1,
         "savingsPlan": [{"term": "1 Year", "unitPrice": 0.08}, {"term": "3 Years", "unitPrice": 0.06}]},
        {"type": "Reservation", "meterId": "vm", "armRegionName": "westeurope", "reservationTerm": "1 Year", "unitPrice": 600.0},
        {"type": "Reservation", "meterId": "vm", "armRegionName": "westeurope", "reservationTerm": "3 Years", "unitPrice": 1080.0},
    ]
    vm = MeterPick(service="Virtual Machines", product="VM", sku="Standard_D2s_v5", meter="D2s v5", unit="1 Hour", qty=2.5)
    line = PriceLine(meter="VM / D2s v5", meter_id="vm", unit_price=0.1, unit="1 Hour", qty=2.5, monthly=182.5)
    out = pm._line(vm, line, vm_rows, "westeurope", "c1")
    # 1y: reservation 2 x 50 + 0.5 x 73 = 136.5 beats savings plan 0.08 x 730 x 2.5 = 146
    assert out.one_year == 136.5 and "reservation (2 units)" in out.how_one_year
    # 3y: reservation 2 x 30 + 0.5 x 73 = 96.5 beats savings plan 0.06 x 730 x 2.5 = 109.5
    assert out.three_year == 96.5 and "3-year reservation" in out.how_three_year

    lic = PriceLine(meter="SQL - SQL License / vCore", meter_id="lic", unit_price=0.1, unit="1 Hour", qty=4, monthly=292)
    assert pm._line(vm, lic, vm_rows, "westeurope", "c2").three_year == 292      # licences aren't discounted

    # A '4 vCore' row whose savings-plan rate is per vCore (0.134 vs 0.67) is scaled x4, not taken as 80% off
    assert pm._savings_plan({"savingsPlan": [{"term": "1 Year", "unitPrice": 0.134}]}, 0.67, "4 vCore") == {1: 0.536}
    assert pm._savings_plan({"savingsPlan": [{"term": "1 Year", "unitPrice": 0.01}]}, 0.67, "P1 v3") == {}


def test_mermaid_is_detected():
    from app.agents.parser import MERMAID
    assert MERMAID.match("flowchart LR\n a --> b") and MERMAID.match("```mermaid\ngraph TD\n a-->b")
    assert not MERMAID.match("Web API on AKS with 3 nodes")


def test_workload_answers_go_into_requirements_once():
    from app.agents import profiler
    from app.models import Constraints, Question, RunState
    s = RunState(constraints=Constraints(allowed_regions=["westeurope"], notes="EU only."))
    s.questions = [Question(id="q1", question="Traffic pattern?", options=["Steady", "Business hours"], answer="Business hours"),
                   Question(id="q2", question="Data size?", options=["Small", "Large"], answer="Not sure")]
    profiler.apply_answers(s)
    profiler.apply_answers(s)  # confirming twice must not repeat
    assert s.constraints.notes == "EU only. Workload answers: Traffic pattern? Business hours."


def items(*costs):
    return [PlanItem(component_id=f"c{i}", option_id=f"c{i}.orig", region="x", monthly_cost=c) for i, c in enumerate(costs)]


def test_region_move_needs_material_saving():
    region, note = _worth_moving(items(700, 71), items(680, 51), "westeurope", "northeurope")  # 40/771 = 5.2%
    assert region == "northeurope" and "saves $40.00" in note
    region, note = _worth_moving(items(700, 71), items(690, 61), "westeurope", "northeurope")  # 20/771 = 2.6%
    assert region == "westeurope" and "stays in westeurope" in note
