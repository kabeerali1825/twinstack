"""Builds data/azure_services.json: every exact serviceName in the Azure Retail Prices API.

Run from the repo root:  .venv/Scripts/python -m scripts.build_service_catalog

The API has no "distinct" query, a 20-condition filter limit and a 1M-row skip cap, so a full
crawl can't work. Instead, per serviceFamily we keep asking for rows whose name we haven't seen
(first page only). A group with too many names is split by name prefix, and finished prefixes are
excluded with `startswith(...) eq false`. We stop only when a query returns nothing, so the list
is complete; if a filter can't fit in 20 conditions we fail loudly instead of missing names.
"""
import datetime
import json
import urllib.parse

from app.pricing import API, API_VERSION, SERVICES_FILE, _get, odata

MAX_CONDITIONS = 20  # API returns 400 above this
requests = 0


def q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def values(field: str, clauses: list[str]) -> set[str]:
    global requests
    if len(clauses) > MAX_CONDITIONS:
        raise RuntimeError(f"Filter needs {len(clauses)} conditions (max {MAX_CONDITIONS}): {clauses[:3]}...")
    params = {"api-version": API_VERSION}
    if clauses:
        params["$filter"] = " and ".join(clauses)
    requests += 1
    return {r[field] for r in _get(API + "?" + urllib.parse.urlencode(params))["Items"]}


def distinct(field: str, base: list[str], prefix: str = "") -> set[str]:
    """Every value of `field` starting with `prefix` among rows matching `base`."""
    scope = base + ([f"startswith({field}, {q(prefix)})"] if prefix else [])
    found: set[str] = set()
    while True:
        new = values(field, scope + [f"{field} ne {q(v)}" for v in found]) - found
        if not new:
            return found
        if len(scope) + len(found) + len(new) > MAX_CONDITIONS:
            return split(field, base, prefix)
        found |= new


def split(field: str, base: list[str], prefix: str) -> set[str]:
    """Like distinct(), but one branch per next character, each excluded with one condition."""
    scope = base + ([f"startswith({field}, {q(prefix)})"] if prefix else [])
    found: set[str] = set()
    excluded: list[str] = []
    while True:
        vals = values(field, scope + excluded)
        if not vals:
            return found
        for child in sorted({v[:len(prefix) + 1] for v in vals}):
            if child == prefix:  # a value equal to the prefix itself
                found.add(prefix)
                excluded.append(f"{field} ne {q(prefix)}")
            else:
                found |= distinct(field, base, child)
                excluded.append(f"startswith({field}, {q(child)}) eq false")


def main():
    families = sorted(distinct("serviceFamily", []))
    print(f"{len(families)} service families ({requests} requests)")
    services: dict[str, set[str]] = {}
    for fam in families:
        names = distinct("serviceName", [odata(serviceFamily=fam)])
        print(f"  {fam:35} {len(names):4} services  ({requests} requests so far)")
        for n in names:
            services.setdefault(n, set()).add(fam)
    SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERVICES_FILE.write_text(json.dumps({
        "generated": datetime.date.today().isoformat(),
        "source": f"{API}?api-version={API_VERSION}",
        "count": len(services),
        "services": [{"serviceName": n, "serviceFamily": sorted(f)}
                     for n, f in sorted(services.items(), key=lambda kv: kv[0].lower())],
    }, indent=1))
    print(f"wrote {len(services)} services to {SERVICES_FILE} using {requests} requests")


if __name__ == "__main__":
    main()
