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


def items(*costs):
    return [PlanItem(component_id=f"c{i}", option_id=f"c{i}.orig", region="x", monthly_cost=c) for i, c in enumerate(costs)]


def test_region_move_needs_material_saving():
    region, note = _worth_moving(items(700, 71), items(680, 51), "westeurope", "northeurope")  # 40/771 = 5.2%
    assert region == "northeurope" and "saves $40.00" in note
    region, note = _worth_moving(items(700, 71), items(690, 61), "westeurope", "northeurope")  # 20/771 = 2.6%
    assert region == "westeurope" and "stays in westeurope" in note
