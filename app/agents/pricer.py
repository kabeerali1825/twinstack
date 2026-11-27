"""Pricer agent (smart model): picks which real price meters an option uses, and how much of each.
(Haiku mixed up Linux/Windows rows and SKU sizes too often; a wrong meter is a wrong price.)

Code builds a numbered menu of real meters from the Retail Prices API; the LLM only picks menu numbers
and quantities, so it can't invent a SKU and never does price math.
"""
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.models import MeterPick, Option, RunState
from app.pricing import PER_MONTH, odata, parse_uom, query

MENU_MAX = 40


class _Pick(BaseModel):
    index: int
    count: float = 1
    count_means: Literal["instances", "vcores", "units"] = "instances"
    active_fraction: float = 1
    monthly_usage: float | None = None
    why: str = ""


class _OptionPicks(BaseModel):
    option_id: str
    meters: list[_Pick] = []
    problem: str = ""


class _Out(BaseModel):
    options: list[_OptionPicks]


SYSTEM = """You map Azure resources to their billing meters. For each option you get a numbered menu of real
price meters (product | sku | meter | unit | USD per unit). Pick the meters this resource is billed on.

Reply with JSON only:
{"options": [{"option_id": str, "meters": [{"index": int, "count": number, "count_means": "instances"|"vcores"|"units",
  "active_fraction": number, "monthly_usage": number|null, "why": str}], "problem": str}]}

For each picked meter give:
- count: how many run at the same time: instances, nodes, databases, or vCPUs / GiB for per-vCPU or per-GiB meters.
- count_means: what you counted: "instances" (servers, nodes, plans, databases, including an HA standby),
  "vcores" (vCores), or "units" (GiB, RU blocks, anything else).
- active_fraction: share of the month they run, 0 to 1. Provisioned resources (VMs, App Service plans, provisioned
  databases, caches, cluster fees) bill all month: use 1 even when traffic is low. Use less than 1 only for meters that
  bill actual use (serverless, consumption, "Active Usage").
- monthly_usage: for meters billed by amount used: the TOTAL for the month as a plain count of the thing itself,
  never in the meter's unit. Examples: 50 million executions -> 50000000 (even if the unit is "10" or "1M");
  300,000 operations -> 300000 (unit "10K"); 50 GB stored -> 50 (unit "1 GB/Month"); 1 million GB-seconds of
  execution time -> 1000000 (unit "1 GB Second"). Code converts it. Leave null for meters priced by time running
  (instances per hour), where count and active_fraction apply.

Rules:
- Pick only from that option's menu. Prefer Linux and regular (not Spot / Low Priority / Windows) meters unless the details say otherwise.
- A row whose sku names a size is priced for that whole size: sku "4 vCore" already costs 4 vCores, so one database is
  count 1. A generic sku "vCore" is per single vCore: count = number of vCores.
- If a SKU has both a whole-resource meter ("P1 Cache") and a per-node meter ("P1 Cache Instance"), pick the whole one.
- ORIGINAL options are priced exactly as described: the stated count, always on. Don't optimize them.
- Alternatives replace today's setup for the SAME workload. Under each alternative you see how today's setup was
  priced: keep those assumptions (operating system, data volumes, request counts, storage size, users) unless the
  alternative's details change them. Never switch Windows/Linux silently.
- High availability: an ORIGINAL is priced as it is today, so include an HA standby only if its details say HA or a
  standby is configured or enabled today ("HA required" or "needs HA" is a requirement, not a setup: don't price it
  for the original). For alternatives, follow the requirements: if they ask for zone-redundant or high
  availability for a database that bills a standby server (PostgreSQL or MySQL Flexible Server), the standby is a
  second server: count 2, count_means "instances". For SQL Database use its "Zone Redundancy" meter when listed.
- Some services bill the software license as a separate meter (e.g. product "... - SQL License", per vCore): include it
  (count = vCores) unless the details mention Azure Hybrid Benefit or bring-your-own license. Serverless prices
  already include the license.
- List only meters you are using. Never list a meter to say it is "not chosen" or as an alternative.
- Pick each billable thing once. The same meter name can appear under several products (e.g. "Blob Storage" and
  "General Block Blob v2"): choose the one product that matches and don't add it twice.
- Include storage meters only when the details give a size. Skip meters for features not mentioned.
- Use workload facts from the requirements (users, requests or events a month, data size, busy hours) before
  assuming anything. When a number is still not given, make a reasonable assumption and say it in "why" (short).
- If nothing in the menu fits, return no meters and explain in "problem"."""

TIME_UNITS = {"Second", "Minute", "Hour", "Day"}
MAX_COUNT = 1000  # more concurrent units than this is a mistake (e.g. seconds given as a count)
SIZED = re.compile(r"^(\d+) vCore", re.I)  # '4 vCore' rows are already priced for all 4 vCores


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def menu(option: Option, region: str) -> list[dict]:
    """Distinct consumption meters for the option's service in `region`, most relevant first."""
    # Some meters (e.g. SQL licenses) are priced once in a 'Global' region, not per region.
    flt = f"{odata(serviceName=option.service)} and ({odata(armRegionName=region)} or armRegionName eq 'Global')"
    if option.service == "Virtual Machines" and option.sku:  # VM rows per region are too many to scan
        sku = option.sku.strip().replace(" ", "_")
        flt += " and " + odata(armSkuName=sku if sku.lower().startswith("standard_") else f"Standard_{sku}")
    rows = {}
    for r in query(flt):
        if r["type"] == "Consumption" and not r["tierMinimumUnits"]:
            rows.setdefault((r["productName"], r["skuName"], r["meterName"]), r)
    # Hide per-node meters ("C2 Cache Instance") when the whole-resource one ("C2 Cache") exists.
    rows = {k: r for k, r in rows.items()
            if not (k[2].endswith(" Instance") and (k[0], k[1], k[2].removesuffix(" Instance")) in rows)}
    want = _words(f"{option.sku or ''} {option.details}")
    sku = (option.sku or "").lower()

    def score(r: dict) -> float:
        s = len(want & _words(f"{r['productName']} {r['skuName']} {r['meterName']}"))
        if sku and sku in (r["skuName"].lower(), r["armSkuName"].lower()):
            s += 10
        return s

    return sorted(rows.values(), key=score, reverse=True)[:MENU_MAX]


def _qty(m: _Pick, row: dict, option: Option, stated_count: float) -> float | None:
    """The quantity monthly_cost() expects, from the LLM's count / active_fraction / monthly_usage.
    None means the numbers are implausible."""
    n, period = parse_uom(row["unitOfMeasure"])
    if period in TIME_UNITS and m.monthly_usage is not None:
        # A monthly total of a per-second/per-hour meter (1,000,000 GB-seconds) -> average amount in use at once,
        # which is what monthly_cost() multiplies back up.
        return m.monthly_usage / PER_MONTH[period]
    if period in TIME_UNITS:
        count = m.count
        size = SIZED.match(row["skuName"])
        if size and m.count_means == "vcores":  # vCores on a row priced per whole size: 8 x '4 vCore' -> 2
            count /= int(size[1])
        sku = (option.sku or "").lower()
        if option.is_original and sku and sku in (row["skuName"].lower(), row["armSkuName"].lower()):
            # As described: the stated count, always on; up to 2x when the requirements add an HA standby.
            return count if stated_count <= count <= 2 * stated_count else stated_count
        if not 0 < count <= MAX_COUNT:
            return None
        return count * min(max(m.active_fraction, 0), 1)
    # monthly_usage is already a plain count (5,000,000 requests), which is what monthly_cost() wants.
    return m.monthly_usage if m.monthly_usage is not None else m.count * n


def _as_priced(o: Option | None) -> str:
    """How an option was priced, in meter units, so alternatives can keep the same workload assumptions."""
    if not o or not o.meters:
        return "not priced"
    def amount(m: MeterPick) -> str:
        n, period = parse_uom(m.unit)
        if period in TIME_UNITS:
            return f"{m.qty:g} running ({m.qty * PER_MONTH[period] / n:,.0f} x '{m.unit}' a month)"
        return f"{m.qty:,.0f} a month (unit '{m.unit}')"
    return "; ".join(f"{m.product} / {m.meter}: {amount(m)} ({m.why})" for m in o.meters)


def _license_for(pick: MeterPick, option: Option) -> MeterPick | None:
    """Provisioned SQL compute ('... - Compute Gen5') is billed without the SQL license, which is a separate
    per-vCore meter in the 'Global' region ('... - SQL License'). Serverless already includes it."""
    if " - Compute" not in pick.product or "Serverless" in pick.product or "hybrid" in option.details.lower():
        return None
    product = pick.product.split(" - Compute")[0] + " - SQL License"
    rows = [r for r in query(odata(serviceName=pick.service, armRegionName="Global", productName=product))
            if r["type"] == "Consumption"]
    if not rows:
        return None
    size = pick.sku.split()[0]  # '4 vCore' -> 4 vCores per instance; generic 'vCore' -> qty is already vCores
    vcores = pick.qty * (int(size) if size.isdigit() else 1)
    r = rows[0]
    return MeterPick(service=pick.service, product=product, sku=r["skuName"], meter=r["meterName"],
                     unit=r["unitOfMeasure"], qty=vcores, why=f"SQL license for {vcores:g} vCores (added by code)",
                     global_meter=True)


def run(state: RunState) -> None:
    todo = [o for o in state.options if not o.priced]
    if not todo:
        return
    comps = {c.id: c for c in state.components}
    menus = {o.id: menu(o, comps[o.component_id].region) for o in todo}
    state.log("Pricer", "tool", f"Fetched live price meters for {len(todo)} options",
              {o.id: len(menus[o.id]) for o in todo})

    blocks = []
    for o in todo:
        c = comps[o.component_id]
        lines = "\n".join(f"  {i} | {r['productName']} | {r['skuName']} | {r['meterName']} | {r['unitOfMeasure']} | {r['unitPrice']}"
                          for i, r in enumerate(menus[o.id]))
        kind = "ORIGINAL" if o.is_original else "Alternative"
        today = "" if o.is_original else "\nToday's setup was priced as:\n" + "\n".join(
            f"  {cid}: {_as_priced(state.option(f'{cid}.orig'))}" for cid in [o.component_id, *o.also_replaces])
        blocks.append(f"{kind} option {o.id}: {o.service}, sku {o.sku or '?'}, count {c.quantity:g}. "
                      f"Details: {o.details or '-'}{today}\n"
                      f"Menu:\n{lines or '  (empty: no meters found)'}")
    out = llm.chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": f"Requirements: {state.constraints.notes or 'none stated'}\n\n" + "\n\n".join(blocks)}],
        _Out, state, smart=True, max_tokens=500 + 350 * len(todo))  # meter choice is accuracy-critical

    picks = {p.option_id: p for p in out.options}
    for o in todo:
        o.priced = True
        p = picks.get(o.id)
        for m in (p.meters if p else []):
            if not 0 <= m.index < len(menus[o.id]):
                continue
            r = menus[o.id][m.index]
            qty = _qty(m, r, o, comps[o.component_id].quantity)
            if qty is None:
                state.log("Pricer", "warn", f"{o.id}: ignored implausible amount for {r['meterName']} ({m.why})")
            elif qty > 0:
                o.meters.append(MeterPick(service=o.service, product=r["productName"], sku=r["skuName"],
                                          meter=r["meterName"], unit=r["unitOfMeasure"], qty=qty, why=m.why,
                                          global_meter=r["armRegionName"] == "Global"))
        # Same sku+meter picked twice: within one product it's a schedule (add up); across products it's a
        # duplicate (e.g. Gen5 and DC-Series compute, or the same storage meter under two products): keep the first.
        merged: dict[tuple, MeterPick] = {}
        for m in o.meters:
            k = (m.sku, m.meter, m.unit)
            if k not in merged:
                merged[k] = m
            elif merged[k].product == m.product:
                merged[k].qty += m.qty
            else:
                state.log("Pricer", "warn", f"{o.id}: dropped duplicate {m.meter} from '{m.product}'")
        o.meters = list(merged.values())
        if o.meters and not any("License" in m.product for m in o.meters):
            licenses = {}
            for m in o.meters:
                if (lic := _license_for(m, o)) and lic.qty > licenses.get(lic.product, lic).qty - 1e-9:
                    licenses[lic.product] = lic
            o.meters += licenses.values()
        if o.meters:
            state.log("Pricer", "output", f"{o.id}: billed on " + ", ".join(
                f"{m.meter}{' (global price)' if m.global_meter else ''} x{m.qty:g}" for m in o.meters),
                      [m.model_dump() for m in o.meters])
        else:
            state.log("Pricer", "warn", f"{o.id}: no matching price meters ({p.problem if p else 'not answered'})")
