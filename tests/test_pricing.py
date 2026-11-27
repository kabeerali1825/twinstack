"""Offline checks for app/pricing.py, using rows shaped like real API responses (westeurope, Nov 2026)."""
import urllib.parse

import pytest

from app import pricing
from app.pricing import monthly_cost, odata, parse_uom, resolve_service, services


def row(price, uom="1 Hour", type="Consumption", term=None, tier=0.0):
    return {"unitPrice": price, "unitOfMeasure": uom, "type": type, "reservationTerm": term,
            "tierMinimumUnits": tier}


def test_vm_pay_as_you_go_and_reservations():
    assert monthly_cost([row(0.115)]) == pytest.approx(83.95)                       # D2s v5 Linux, 730 h
    assert monthly_cost([row(0.115)], qty=2) == pytest.approx(167.90)
    assert monthly_cost([row(622.0, type="Reservation", term="1 Year")]) == pytest.approx(51.8333, abs=1e-3)
    assert monthly_cost([row(1194.0, type="Reservation", term="3 Years")]) == pytest.approx(33.1667, abs=1e-3)


def test_per_second_units():
    # Container Apps vCPU active: $0.000034/s, one vCPU busy all month
    assert monthly_cost([row(3.4e-05, "1 Second")]) == pytest.approx(3.4e-05 * 730 * 3600)


def test_tiers_with_free_grant():
    # Functions executions, unit '10': first 25,000 units (250k executions) free
    tiers = [row(0.0, "10"), row(4e-06, "10", tier=25000.0)]
    assert monthly_cost(tiers, qty=200_000) == 0.0
    assert monthly_cost(tiers, qty=1_250_000) == pytest.approx(100_000 * 4e-06)


def test_volume_tiers():
    tiers = [row(0.05, "1 GB/Month"), row(0.04, "1 GB/Month", tier=100.0)]
    assert monthly_cost(tiers, qty=150) == pytest.approx(100 * 0.05 + 50 * 0.04)


@pytest.mark.parametrize("uom,expected", [
    ("1 Hour", (1, "Hour")), ("1/Month", (1, "Month")), ("1 GB/Month", (1, "Month")), ("10K", (10_000, None)),
    ("1M", (1_000_000, None)), ("1 GiB Second", (1, "Second")), ("10K/Month", (10_000, "Month")),
    ("1 GiB/Hour", (1, "Hour")), ("100", (100, None)), ("1 MB", (1, None)), ("1/Year", (1, "Year")),
])
def test_parse_uom(uom, expected):
    assert parse_uom(uom) == expected


def test_odata_escapes_quotes():
    assert odata(serviceName="Virtual Machines", skuName="it's") == \
        "serviceName eq 'Virtual Machines' and skuName eq 'it''s'"


def test_non_regional_prices_map_to_the_right_rows():
    from app.models import MeterPick
    from app.pricing import billing_zone, meter_region, row_scope
    assert billing_zone("westeurope") == billing_zone("eastus") == "Zone 1"
    assert billing_zone("japaneast") == "Zone 2" and billing_zone("australiaeast") == "Zone 4"
    assert [row_scope(r) for r in ("westeurope", "Global", "", "Zone 1")] == ["regional", "global", "none", "zone"]
    pick = lambda **kw: MeterPick(service="s", product="p", sku="k", meter="m", unit="1 Hour", qty=1, **kw)
    assert meter_region(pick(scope="zone"), "northeurope") == "Zone 1"
    assert meter_region(pick(scope="none"), "northeurope") == ""
    assert meter_region(pick(global_meter=True), "northeurope") == "Global"   # runs saved before `scope` existed
    assert meter_region(pick(), "northeurope") == "northeurope"


def test_service_catalog():
    assert len(services()) > 150
    assert resolve_service("virtual machines") == "Virtual Machines"
    assert resolve_service(" Redis Cache ") == "Redis Cache"


def test_resolve_by_product_name(monkeypatch):
    by_product = {
        "Azure OpenAI": [{"serviceName": "Foundry Models"}] * 3,
        "Shared": [{"serviceName": "A"}, {"serviceName": "B"}],
    }

    def fake_get(url):
        product = next((p for p in by_product if urllib.parse.quote_plus(f"'{p}'") in url), None)
        return {"Items": by_product.get(product, [])}

    monkeypatch.setattr(pricing, "_get", fake_get)
    pricing._service_for_product.cache_clear()
    assert resolve_service("Azure OpenAI") == "Foundry Models"
    assert resolve_service("Shared") is None          # billed by two services: ambiguous
    assert resolve_service("Azure DevOpz") is None    # no fuzzy matching
    pricing._service_for_product.cache_clear()
