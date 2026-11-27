"""Offline checks for app/formats.py: exact extraction from draw.io, ARM and Bicep (no AI)."""
import base64
import re
import urllib.parse
import zlib
from pathlib import Path

import pytest

from app import formats

DIR = Path(__file__).parent / "fixtures" / "formats"


def read(name: str) -> str:
    return (DIR / name).read_text(encoding="utf-8")


def test_detects_each_format():
    assert formats.detect(read("webapp.json")) == "arm"
    assert formats.detect(read("api.bicep")) == "bicep"
    assert formats.detect(read("shop.drawio")) == "drawio"
    assert formats.detect("Web API on AKS with 3 nodes") is None
    assert formats.detect("flowchart LR\n a --> b") is None  # Mermaid is handled by the Parser itself


def test_arm_keeps_exact_skus_and_settings():
    s = formats.summarize(read("webapp.json"), "arm")
    assert "Microsoft.Web/serverfarms" in s and "capacity=2" in s and "tier=PremiumV3" in s
    assert "Standard_D2ds_v5" in s and "ZoneRedundant" in s and '"storageSizeGB": 128' in s
    assert "planSku=P1v3" in s and "location=westeurope" in s          # parameter defaults resolved by the Parser
    assert "Microsoft.Cache/redis" in s and "properties.sku name=Standard, family=C, capacity=1" in s


def test_bicep_extracts_resources_skus_and_node_pools():
    s = formats.summarize(read("api.bicep"), "bicep")
    assert "Microsoft.ContainerService/managedClusters (aks)" in s and "vmSize='Standard_D4s_v5'" in s
    assert "count=nodeCount" in s and "nodeCount=3" in s
    assert "GP_Gen5_4" in s and "Standard_LRS" in s and "accessTier='Hot'" in s


def test_drawio_labels_icons_groups_and_connections():
    s = formats.summarize(read("shop.drawio"), "drawio")
    assert "- Shop web App Service P2v3 x2 | App Services | Azure - West Europe" in s
    assert "Orders DB Azure SQL S3 | SQL Database" in s
    assert "Front Door -> Shop web App Service P2v3 x2" in s and "(orders)" in s


def test_drawio_compressed_pages_are_inflated():
    inner = re.search(r"<mxGraphModel>.*</mxGraphModel>", read("shop.drawio"), re.S)[0]
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = base64.b64encode(co.compress(urllib.parse.quote(inner).encode()) + co.flush()).decode()
    compressed = f'<mxfile><diagram name="Shop">{packed}</diagram></mxfile>'
    assert "Shop web App Service P2v3 x2" in formats.summarize(compressed, "drawio")


def test_drawio_rejects_entity_expansion():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><mxfile><diagram>&a;</diagram></mxfile>'
    with pytest.raises(ValueError):
        formats.summarize(evil, "drawio")
