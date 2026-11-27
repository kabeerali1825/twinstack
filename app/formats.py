"""Exact extraction from structured inputs: draw.io diagrams, ARM templates, Bicep files.

Code pulls out what is written (resource types, SKUs, counts, locations, labels, icons, connections) into a short
plain-text summary; the Parser then maps that to billing services. No AI reads the raw file, so SKUs stay exact and
a 100 KB template becomes ~1 KB of input.
"""
import base64
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

MAX_SUMMARY = 8000


def detect(text: str) -> str | None:
    t = text.lstrip()
    if t.startswith("<") and ("<mxfile" in t[:500] or "<mxGraphModel" in t[:500]):
        return "drawio"
    if t.startswith("{") and '"resources"' in t:
        return "arm"
    if re.search(r"^\s*resource\s+\w+\s+'[\w.]+/[\w./]+@[\w-]+'", text, re.M):
        return "bicep"
    return None


def summarize(text: str, fmt: str) -> str:
    out = {"drawio": _drawio, "arm": _arm, "bicep": _bicep}[fmt](text)
    return out[:MAX_SUMMARY]


# ---------- draw.io ----------
def _strip(label: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", label or ""))).strip()


MAX_XML = 2_000_000  # decompressed bytes per diagram page


def _safe_xml(text: str) -> ET.Element:
    if re.search(r"<!(?:DOCTYPE|ENTITY)", text, re.I):  # no entity expansion ("billion laughs") from uploads
        raise ValueError("draw.io files with DOCTYPE or ENTITY declarations aren't accepted.")
    return ET.fromstring(text)


def _inflate(data: str) -> str:
    d = zlib.decompressobj(-15)
    raw = d.decompress(base64.b64decode(data), MAX_XML)
    if d.unconsumed_tail:
        raise ValueError("This draw.io page is too large to read.")
    return urllib.parse.unquote(raw.decode("utf-8"))


def _drawio_models(text: str) -> list[ET.Element]:
    root = _safe_xml(text)
    if root.tag == "mxGraphModel":
        return [root]
    models = []
    for d in root.iter("diagram"):
        inner = d.find("mxGraphModel")
        if inner is not None:
            models.append(inner)
        elif (d.text or "").strip():  # compressed: base64 -> raw deflate -> URL-encoded XML
            models.append(_safe_xml(_inflate(d.text.strip())))
    return models


def _icon(style: str) -> str:
    """'image=img/lib/azure2/compute/App_Services.svg' -> 'App Services'; 'mxgraph.azure.sql_database' -> 'sql database'."""
    m = re.search(r"img/lib/\w+/(?:[\w-]+/)*([\w-]+)\.svg", style or "") or re.search(r"mxgraph\.azure\w*\.([\w.]+)", style or "")
    return m[1].replace("_", " ").replace(".", " ") if m else ""


def _drawio(text: str) -> str:
    lines = ["draw.io diagram. Shapes (label | Azure icon | inside group):"]
    edges = []
    for model in _drawio_models(text):
        cells = {c.get("id"): c for c in model.iter("mxCell")}
        for c in cells.values():
            label, icon = _strip(c.get("value")), _icon(c.get("style"))
            if c.get("edge") == "1":
                s, t = cells.get(c.get("source")), cells.get(c.get("target"))
                if s is not None and t is not None:
                    edges.append(f"{_strip(s.get('value')) or _icon(s.get('style'))} -> "
                                 f"{_strip(t.get('value')) or _icon(t.get('style'))}" + (f" ({label})" if label else ""))
            elif c.get("vertex") == "1" and (label or icon):
                parent = cells.get(c.get("parent"))
                group = _strip(parent.get("value")) if parent is not None and parent.get("vertex") == "1" else ""
                lines.append(f"- {label or '(no label)'} | {icon or '-'} | {group or '-'}")
    if edges:
        lines += ["Connections:", *[f"- {e}" for e in edges]]
    return "\n".join(lines)


# ---------- ARM ----------
def _resources(items: list, parent: str = "") -> list[dict]:
    out = []
    for r in items or []:
        if isinstance(r, dict) and "type" in r:
            r = {**r, "type": f"{parent}/{r['type']}" if parent and "/" not in r["type"].split("/", 1)[0] else r["type"]}
            out.append(r)
            out += _resources(r.get("resources"), r["type"])
    return out


def _arm_line(r: dict) -> str:
    sku = r.get("sku") or {}
    props = r.get("properties") or {}
    bits = [r["type"], f"name {r.get('name')}"]
    if r.get("location"):
        bits.append(f"location {r['location']}")
    if r.get("kind"):
        bits.append(f"kind {r['kind']}")
    for where, s in (("sku", sku), ("properties.sku", props.get("sku"))):  # Redis and others nest it under properties
        if isinstance(s, dict) and s:
            bits.append(f"{where} " + ", ".join(f"{k}={v}" for k, v in s.items() if isinstance(v, (str, int, float))))
    hw = props.get("hardwareProfile", {}).get("vmSize") if isinstance(props.get("hardwareProfile"), dict) else None
    if hw:
        bits.append(f"vmSize {hw}")
    for pool in props.get("agentPoolProfiles") or []:
        bits.append(f"node pool {pool.get('name')}: {pool.get('count')} x {pool.get('vmSize')}")
    for key in ("highAvailability", "zoneRedundant", "storage", "capacity", "storageProfile", "accessTier"):
        if key in props and isinstance(props[key], (str, int, float, dict)):
            bits.append(f"{key} {json.dumps(props[key])[:120]}")
    if r.get("copy"):
        bits.append(f"copies {r['copy'].get('count')}")
    return "- " + "; ".join(str(b) for b in bits)


def _arm(text: str) -> str:
    doc = json.loads(text)
    params = {k: v.get("defaultValue") for k, v in (doc.get("parameters") or {}).items() if isinstance(v, dict)}
    lines = ["ARM template. Resources (type; name; location; sku; key settings):"]
    lines += [_arm_line(r) for r in _resources(doc.get("resources"))]
    if params:
        lines.append("Parameter defaults (used where a value is [parameters('x')]): "
                     + "; ".join(f"{k}={v}" for k, v in params.items() if isinstance(v, (str, int, float))))
    return "\n".join(lines)


# ---------- Bicep ----------
_RES = re.compile(r"^\s*resource\s+(\w+)\s+'([\w.]+/[\w./]+)@[\w-]+'\s*=\s*(?:if\s*\([^)]*\)\s*)?(\[?\s*for[^:]*:)?\s*\{", re.M)
_KEYS = re.compile(r"^\s*(name|location|kind|tier|family|capacity|vmSize|count|accessTier|mode|"
                   r"highAvailability|zoneRedundant|storageSizeGB)\s*:\s*(.+?)\s*$", re.M)


def _block(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
    return text[start:]


def _bicep(text: str) -> str:
    params = dict(re.findall(r"^\s*param\s+(\w+)\s+\w+\s*=\s*(.+?)\s*$", text, re.M))
    lines = ["Bicep file. Resources (type; key settings as written):"]
    for m in _RES.finditer(text):
        body = _block(text, m.end() - 1)
        sku = re.search(r"\bsku\s*:\s*\{(.*?)\}", body, re.S)
        kv = [f"{k}={v}" for k, v in _KEYS.findall(body)[:12]]
        lines.append(f"- {m[2]} ({m[1]})" + (" [loop]" if m[3] else "") + (": " + "; ".join(kv) if kv else "")
                     + (f"; sku {{{' '.join(sku[1].split())}}}" if sku else ""))
    if params:
        lines.append("Parameter defaults: " + "; ".join(f"{k}={v}" for k, v in params.items()))
    return "\n".join(lines)
