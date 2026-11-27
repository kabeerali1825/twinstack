"""Pricing-Model agent (plain code, no LLM): what each plan costs with 1- or 3-year commitments.

For every priced line it compares pay-as-you-go with the line's reservation and savings-plan prices from the
Retail Prices API and keeps the cheapest per term. Reservations cover whole always-on units only; the rest stays
pay-as-you-go. Azure Hybrid Benefit (bring your own SQL Server licences) is reported, not applied.
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor

from app.agents.scout import _rows
from app.models import Commitments, CommitLine, RunState
from app.pricing import PER_MONTH, meter_region, odata, parse_uom, query

TERMS = {"1 Year": 1, "3 Years": 3}
TIME_UNITS = {"Second", "Minute", "Hour", "Day"}
SIZED = re.compile(r"^(\d+) vCore", re.I)
# A commitment price is trusted only if it's 2-75% below pay-as-you-go (Azure's deepest real discounts are ~72%);
# outside that the units don't match, e.g. a per-vCore savings-plan rate on a '4 vCore' row looks like 80% off.
MIN_RATIO, MAX_RATIO = 0.25, 0.98


def _plausible(commit: float, payg: float) -> bool:
    return payg > 0 and MIN_RATIO <= commit / payg <= MAX_RATIO


def _savings_plan(cons: dict, payg_unit: float, sku: str) -> dict[int, float]:
    """Hourly-style savings-plan unit price per term, unit-corrected for sized SKUs ('4 vCore' rows can carry a
    per-vCore rate)."""
    size = SIZED.match(sku)
    out = {}
    for sp in cons.get("savingsPlan") or []:
        years, price = TERMS.get(sp.get("term")), sp.get("unitPrice")
        if not years or price is None:
            continue
        if not _plausible(price, payg_unit) and size and _plausible(price * int(size[1]), payg_unit):
            price *= int(size[1])
        if _plausible(price, payg_unit):
            out[years] = price
    return out


def _reservations(rows: list[dict], line, m, region: str) -> dict[int, float]:
    """Reservation price per unit per MONTH, by term. Sized rows ('2 vCore') use the product's per-vCore reservation."""
    res = [r for r in rows if r["type"] == "Reservation" and r["meterId"] == line.meter_id and r["armRegionName"] == region]
    per = 1
    size = SIZED.match(m.sku)
    if not res and size:
        generic = query(odata(serviceName=m.service, armRegionName=region, productName=m.product, skuName="vCore"))
        res = [r for r in generic if r["type"] == "Reservation"]
        per = int(size[1])
    return {TERMS[r["reservationTerm"]]: r["unitPrice"] / (12 * TERMS[r["reservationTerm"]]) * per
            for r in res if r.get("reservationTerm") in TERMS}


def _line(m, line, rows: list[dict], region: str, component_id: str) -> CommitLine:
    out = CommitLine(component_id=component_id, meter=line.meter, payg=line.monthly,
                     one_year=line.monthly, three_year=line.monthly)
    n, period = parse_uom(line.unit)
    # Commitments discount compute, not software licences (those are what Azure Hybrid Benefit covers).
    if period not in TIME_UNITS or line.qty <= 0 or line.monthly <= 0 or "License" in line.meter:
        return out
    cons = next((r for r in rows if r["type"] == "Consumption" and r["meterId"] == line.meter_id
                 and r["armRegionName"] == region), None)
    if not cons:
        return out
    hours = PER_MONTH[period] / n                         # billable units per "1 running" per month
    payg_unit_month = line.unit_price * hours             # one unit running all month, pay-as-you-go
    sp = _savings_plan(cons, line.unit_price, m.sku)
    ri = {y: p for y, p in _reservations(rows, line, m, region).items() if _plausible(p, payg_unit_month)}
    whole = math.floor(line.qty + 1e-9)                   # reservations cover whole always-on units only
    for years, field in ((1, "one_year"), (3, "three_year")):
        best, how = line.monthly, "pay-as-you-go"
        if years in sp:
            cost = sp[years] * hours * line.qty
            if cost < best:
                best, how = cost, f"{years}-year savings plan"
        if years in ri and whole >= 1:
            cost = ri[years] * whole + payg_unit_month * (line.qty - whole)
            if cost < best:
                best, how = cost, f"{years}-year reservation ({whole} {'unit' if whole == 1 else 'units'})"
        setattr(out, field, round(best, 2))
        setattr(out, f"how_{field}", how)
    return out


def run(state: RunState) -> None:
    regions = sorted(set(state.constraints.allowed_regions) | {c.region for c in state.components})
    work = []  # (plan, item, meter pick, price line, region)
    for plan in state.plans:
        for item in plan.items:
            if item.replaced_by:
                continue
            o = state.option(item.option_id)
            cand = next((c for c in state.candidates if c.option_id == o.id and c.region == item.region), None)
            if cand:
                work += [(plan, item, m, line) for m, line in zip(o.meters, cand.lines)]
    keys = sorted({(m.service, m.sku) for _, _, m, _ in work})
    state.log("Pricing Model", "start", f"Checking reservations and savings plans for {len(work)} price lines")
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip(keys, pool.map(lambda k: _rows(*k, regions), keys)))

    for plan in state.plans:
        lines = [_line(m, line, fetched[(m.service, m.sku)], meter_region(m, item.region),
                       item.component_id)
                 for p, item, m, line in work if p is plan]
        hybrid = sum(l.payg for l in lines if "SQL License" in l.meter)
        plan.commitments = Commitments(
            payg=round(sum(l.payg for l in lines), 2), one_year=round(sum(l.one_year for l in lines), 2),
            three_year=round(sum(l.three_year for l in lines), 2), hybrid_benefit=round(hybrid, 2),
            lines=[l for l in lines if l.three_year < l.payg - 0.005])
        c = plan.commitments
        state.log("Pricing Model", "output",
                  f"{plan.name}: ${c.payg:,.2f}/mo pay-as-you-go, ${c.one_year:,.2f} with 1-year commitments, "
                  f"${c.three_year:,.2f} with 3-year" + (f"; Azure Hybrid Benefit could remove ${hybrid:,.2f} of SQL "
                                                         f"licences" if hybrid else ""),
                  [l.model_dump() for l in c.lines])
