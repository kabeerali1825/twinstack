"""Region Scout (plain code, no LLM): prices every picked meter in every allowed region.

One price query per (service, sku), covering all regions at once. A region counts only if every
meter of the option is sold there.
"""
from concurrent.futures import ThreadPoolExecutor

from app.models import Candidate, PriceLine, RunState
from app.pricing import meter_region, monthly_cost, non_regional, odata, query

MAX_REGIONS_PER_QUERY = 18  # + serviceName + skuName = the API's 20-condition limit


def _rows(service: str, sku: str, regions: list[str]) -> list[dict]:
    rows = []
    regions = [*regions, *non_regional(regions)]  # licences in 'Global'; Front Door etc. with no region or per zone
    for i in range(0, len(regions), MAX_REGIONS_PER_QUERY):
        chunk = regions[i:i + MAX_REGIONS_PER_QUERY]
        regions_or = " or ".join(odata(armRegionName=r) for r in chunk)
        rows += query(f"{odata(serviceName=service, skuName=sku)} and ({regions_or})")
    return rows


def run(state: RunState) -> None:
    todo = [o for o in state.options if o.priced and not o.scouted]
    comps = {c.id: c for c in state.components}
    regions = sorted(set(state.constraints.allowed_regions) | {c.region for c in state.components})
    keys = sorted({(m.service, m.sku) for o in todo for m in o.meters})
    state.log("Region Scout", "start", f"Checking {len(keys)} SKUs across {len(regions)} regions")
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip(keys, pool.map(lambda k: _rows(*k, regions), keys)))

    for o in todo:
        o.scouted = True
        if not o.meters:
            continue
        home = comps[o.component_id].region
        found = []
        for region in regions:
            if region not in state.constraints.allowed_regions and not (o.is_original and region == home):
                continue
            lines = []
            for m in o.meters:
                where = meter_region(m, region)
                tiers = [r for r in fetched[(m.service, m.sku)]
                         if r["armRegionName"] == where and r["type"] == "Consumption"
                         and r["productName"] == m.product and r["meterName"] == m.meter]
                if not tiers:
                    break
                tiers = list({r["tierMinimumUnits"]: r for r in tiers}.values())  # same meter under 2 skuIds
                base = min(tiers, key=lambda r: r["tierMinimumUnits"])
                lines.append(PriceLine(meter=f"{m.product} / {m.meter}", meter_id=base["meterId"],
                                       unit_price=base["unitPrice"], unit=m.unit, qty=m.qty,
                                       monthly=round(monthly_cost(tiers, m.qty), 2)))
            else:  # every meter sold in this region
                found.append(Candidate(option_id=o.id, component_id=o.component_id, region=region,
                                       monthly_cost=round(sum(l.monthly for l in lines), 2), lines=lines))
        state.candidates += found
        if o.is_original and any(c.region == home and c.monthly_cost == 0 for c in found):
            state.log("Region Scout", "warn", f"{o.id}: {o.service} prices at $0.00. Its real cost is probably billed "
                                              f"elsewhere (e.g. compute as Virtual Machines); add that as a component.")
        if found:
            best = min(found, key=lambda c: c.monthly_cost)
            state.log("Region Scout", "output", f"{o.id}: sold in {len(found)} regions, cheapest {best.region} "
                                                f"${best.monthly_cost:,.2f}/mo",
                      {c.region: c.monthly_cost for c in found})
        else:
            state.log("Region Scout", "warn", f"{o.id}: not sold in any allowed region")

    # Baseline: each original option in its own region.
    state.original_total = round(sum(c.monthly_cost for c in state.candidates
                                     if (o := state.option(c.option_id)) and o.is_original
                                     and c.region == comps[c.component_id].region), 2)
