"""Database storage added by code when the input states a size (app/agents/pricer.py). No network."""
import pytest

from app.agents import pricer
from app.models import MeterPick, Option


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    # Any storage meter asked for exists, priced per GB a month.
    monkeypatch.setattr(pricer, "query", lambda flt: ({"type": "Consumption", "unitOfMeasure": "1 GB/Month"},))


def compute(service: str, product: str) -> MeterPick:
    return MeterPick(service=service, product=product, sku="8 vCore", meter="vCore", unit="1 Hour", qty=1)


def test_sql_business_critical_250_gb():  # Contoso: the calculator charges 250 GB at $0.275
    o = Option(id="c4.orig", component_id="c4", service="SQL Database", details="Business Critical, 8 vCore, zone redundant, 250 GB")
    s = pricer._storage_for(compute("SQL Database", "SQL Database Single/Elastic Pool Business Critical - Compute Gen5"),
                            o, pricer._stated_gb(o.details), 1, "northeurope")
    assert (s.product, s.meter, s.qty) == ("SQL Database Single/Elastic Pool Business Critical - Storage",
                                           "Business Critical Data Stored", 250)


def test_general_purpose_zone_redundant_and_postgres_ha():
    gp = Option(id="a", component_id="c", service="SQL Database", details="General Purpose, zone redundant")
    s = pricer._storage_for(compute("SQL Database", "SQL Database Single/Elastic Pool General Purpose - Compute Gen5"),
                            gp, 100, 2, "westeurope")
    assert s.sku == "General Purpose Zone Redundancy" and s.qty == 200  # 2 databases

    pg = Option(id="b", component_id="c", service="Azure Database for PostgreSQL", details="zone-redundant HA, 256 GB")
    s = pricer._storage_for(compute("Azure Database for PostgreSQL", "Azure Database for PostgreSQL Flexible Server General Purpose Dsv5 Series Compute"),
                            pg, 256, 1, "northeurope")
    assert s.product == "Azure Database for PostgreSQL Flex Server Storage" and s.qty == 512  # standby too
    no_ha = Option(id="c", component_id="c", service="Azure Database for PostgreSQL", details="single server, no HA")
    assert pricer._storage_for(compute("Azure Database for PostgreSQL", "Azure Database for PostgreSQL Flexible Server General Purpose Dsv5 Series Compute"),
                               no_ha, 128, 1, "northeurope").qty == 128


def test_size_parsing_and_unknown_services():
    assert pricer._stated_gb("General Purpose, 4 vCore") is None
    # Seen in a recording: the memory size was read as the data size.
    assert pricer._stated_gb("General Purpose, 4 vCore 16 GiB, zone-redundant HA, 256 GB storage") == 256
    assert pricer._stated_gb("2 vCPU, 16 GB RAM, 100 GB data") == 100
    assert pricer._stated_gb("", "Business Critical, 1 TB") == 1024  # falls back to the component's details
    redis = MeterPick(service="Redis Cache", product="Azure Redis Cache Premium", sku="P1", meter="P1 Cache", unit="1 Hour", qty=1)
    assert pricer._storage_for(redis, Option(id="r", component_id="r", service="Redis Cache"), 50, 1, "westeurope") is None
