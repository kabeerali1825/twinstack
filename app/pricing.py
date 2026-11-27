"""Azure Retail Prices API client + monthly cost math. Plain code only: the LLM never does price math."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

API = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"  # needed for savingsPlan prices
MAX_PAGES = 20  # pages are 1,000 rows; a filter needing more is too broad, so we raise instead of truncating
HOURS_PER_MONTH = 730  # Azure pricing calculator convention
PER_MONTH = {"Second": HOURS_PER_MONTH * 3600, "Minute": HOURS_PER_MONTH * 60, "Hour": HOURS_PER_MONTH,
             "Day": HOURS_PER_MONTH / 24, "Month": 1, "Year": 1 / 12}
DATA = Path(__file__).resolve().parent.parent / "data"
SNAPSHOTS = DATA / "price_snapshots"
SERVICES_FILE = DATA / "azure_services.json"  # built by scripts/build_service_catalog.py

# unitOfMeasure seen in the API: '1 Hour', '1/Month', '1 GB/Month', '10K', '1M', '1 GiB Second', '100', ...
_UOM = re.compile(r"^(\d+)([KM]?)\s*(?:[KMGT]i?B)?\s*/?\s*(Second|Minute|Hour|Day|Month|Year)?$")


def odata(**eq) -> str:
    """odata(serviceName='Virtual Machines', armRegionName='westeurope') -> $filter string.
    Values are case-sensitive in the API; single quotes are escaped."""
    return " and ".join(f"{k} eq '{str(v).replace(chr(39), chr(39) * 2)}'" for k, v in eq.items())


def _get(url: str) -> dict:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            retryable = not isinstance(e, urllib.error.HTTPError) or e.code == 429 or e.code >= 500
            if not retryable or attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _fetch_all(flt: str) -> list[dict]:
    url = API + "?" + urllib.parse.urlencode(
        {"api-version": API_VERSION, "meterRegion": "'primary'", "$filter": flt})
    items = []
    for _ in range(MAX_PAGES):
        page = _get(url)
        items += page["Items"]
        url = page.get("NextPageLink")
        if not url:
            break
    else:
        raise ValueError(f"Price filter matches more than {len(items)} rows; narrow it: {flt}")
    # The API returns duplicate rows; keep the newest per meter/SKU/price type/term/tier.
    # (Sizes share a meterId: SQL '2 vCore' and '40 vCore' are both meter 'vCore'.)
    latest = {}
    for it in sorted(items, key=lambda i: i.get("effectiveStartDate", "")):
        latest[(it["meterId"], it["skuId"], it["type"], it.get("reservationTerm"), it["tierMinimumUnits"])] = it
    return list(latest.values())


@lru_cache(maxsize=512)
def query(flt: str) -> tuple[dict, ...]:
    """Live price rows for an OData filter. Cached per instance; falls back to a committed
    snapshot in data/price_snapshots/ if the API is unreachable. Set PRICE_SNAPSHOT_WRITE=1
    locally to save snapshots for demos. An empty result means the SKU isn't sold there."""
    snap = SNAPSHOTS / f"{hashlib.sha1(flt.encode()).hexdigest()[:16]}.json"
    try:
        items = _fetch_all(flt)
    except (urllib.error.URLError, TimeoutError):
        if not snap.exists():
            raise
        items = json.loads(snap.read_text())["items"]
    else:
        if os.environ.get("PRICE_SNAPSHOT_WRITE") == "1":
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({"filter": flt, "items": items}, indent=1))
    return tuple(items)


@lru_cache(maxsize=1)
def services() -> dict[str, list[str]]:
    """Every exact API serviceName -> its serviceFamily list."""
    return {s["serviceName"]: s["serviceFamily"] for s in json.loads(SERVICES_FILE.read_text())["services"]}


@lru_cache(maxsize=256)
def _service_for_product(product: str) -> str | None:
    # First page is enough: we only need which service bills this product.
    url = API + "?" + urllib.parse.urlencode({"api-version": API_VERSION, "$filter": odata(productName=product)})
    names = {r["serviceName"] for r in _get(url)["Items"]}
    return names.pop() if len(names) == 1 else None  # unknown or ambiguous -> None


# Common names that are neither a service name nor an exact product name in the price API.
ALIASES = {
    "azure cache for redis": "Redis Cache", "azure redis cache": "Redis Cache",
    "azure sql": "SQL Database", "azure sql database": "SQL Database",
    "azure sql managed instance": "SQL Managed Instance", "azure functions": "Functions",
    "aks": "Azure Kubernetes Service", "azure virtual machines": "Virtual Machines", "vm": "Virtual Machines",
    "azure blob storage": "Storage", "blob storage": "Storage", "web app": "Azure App Service",
    "app service": "Azure App Service", "container apps": "Azure Container Apps",
}


def resolve_service(name: str) -> str | None:
    """Exact API serviceName for `name`, else None. Never fuzzy-matches.

    1. A service name or a known alias, case-insensitive ('redis cache', 'Azure Cache for Redis' -> 'Redis Cache').
    2. Else an exact, case-sensitive product name, looked up live, since marketing names
       differ from billing names ('Azure OpenAI' -> 'Foundry Models', 'Static Web Apps' -> 'Azure App Service').
    """
    name = name.strip()
    key = name.lower()
    known = {n.lower(): n for n in services()}.get(key) or ALIASES.get(key)
    if known:
        return known
    try:
        return _service_for_product(name)
    except (urllib.error.URLError, TimeoutError):
        return None  # can't check right now: treat as unknown; the confirm step flags it for the user


def billing_zone(region: str) -> str:
    """Azure bills some services (Front Door, bandwidth) by zone, not region. Europe and the Americas' main
    regions are Zone 1. ponytail: name-based approximation; extend if a run needs the exact zone table."""
    r = region.lower()
    if "australia" in r:
        return "Zone 4"
    if "india" in r:
        return "Zone 5"
    if any(k in r for k in ("asia", "japan", "korea")):
        return "Zone 2"
    if "brazil" in r:
        return "Zone 3"
    return "Zone 1"


def row_scope(arm_region: str) -> str:
    """Where a price row applies: 'regional', or one price for everywhere ('global' / 'none') or per zone."""
    if arm_region == "Global":
        return "global"
    if arm_region == "":
        return "none"
    if arm_region.startswith("Zone "):
        return "zone"
    return "regional"


def price_region(scope: str, region: str) -> str:
    """The armRegionName to look up for a meter of this scope, when deploying in `region`."""
    return {"global": "Global", "none": "", "zone": billing_zone(region)}.get(scope, region)


def meter_region(m, region: str) -> str:
    """armRegionName to price meter pick `m` at when deploying in `region` (older runs only set global_meter)."""
    scope = m.scope if (m.scope != "regional" or not m.global_meter) else "global"
    return price_region(scope, region)


def non_regional(regions: list[str]) -> list[str]:
    """Extra armRegionNames to query so global, region-less and zone-billed rows come back too."""
    return ["Global", "", *sorted({billing_zone(r) for r in regions})]


def parse_uom(uom: str) -> tuple[int, str | None]:
    """'10K/Month' -> (10000, 'Month'); '1 GB' -> (1, None)."""
    m = _UOM.match(uom.strip())
    if not m:
        raise ValueError(f"Unknown unitOfMeasure: {uom!r}")
    return int(m[1]) * {"": 1, "K": 1_000, "M": 1_000_000}[m[2]], m[3]


def monthly_cost(tiers: list[dict], qty: float = 1.0) -> float:
    """Monthly USD for one meter.

    tiers: that meter's price rows (a single row unless the meter is tiered).
    qty:   for time-based units, how much runs all month (2 VMs -> 2, 4 GiB memory -> 4;
           part-time -> a fraction, e.g. business hours ~0.36). Otherwise the monthly usage
           in the unit's base (GB stored, executions, ...).
    """
    first = tiers[0]
    if first["type"] == "Reservation":  # unitPrice is the whole term, despite the '1 Hour' unit
        return first["unitPrice"] / (12 * int(first["reservationTerm"].split()[0])) * qty
    n, period = parse_uom(first["unitOfMeasure"])
    units = qty * PER_MONTH.get(period, 1) / n  # billable units of unitOfMeasure
    tiers = sorted(tiers, key=lambda t: t["tierMinimumUnits"])
    cost = 0.0
    for t, nxt in zip(tiers, tiers[1:] + [None]):
        hi = nxt["tierMinimumUnits"] if nxt else float("inf")
        cost += max(0.0, min(units, hi) - t["tierMinimumUnits"]) * t["unitPrice"]
    return cost
