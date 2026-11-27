"""M4 (and the data-center points of M3): pipelines on hex links, energy sites as points.

Pipelines reuse usaciv's road rasteriser (pipeline/build/infrastructure.py), so they snap to the hex grid the same
way roads do. Sites: petroleum refineries (EIA-820), natural gas underground storage (EIA-191), LNG export and
import terminals (EIA) when the layer is available, and OpenStreetMap data centers aggregated per hex.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from pipeline.build.infrastructure import links_of, sample_cells

from .common import find_raw, lines_from_geojson, read_json

PIPE_CLASSES = [
    {"id": "gas_interstate", "name": "Interstate gas pipeline", "color": "#4f86c6", "width_px": 1.8},
    {"id": "gas_intrastate", "name": "Intrastate gas pipeline", "color": "#7fa9d6", "width_px": 1.1, "dash": [3, 2]},
    {"id": "crude", "name": "Crude oil pipeline", "color": "#8b5a2b", "width_px": 2.0},
]


def _pipes(world, path: Path, cls_of) -> dict:
    out = {}
    feats = read_json(path)["features"]
    owner = world.owner
    for f in feats:
        cls = cls_of(f.get("properties") or {})
        if cls is None:
            continue
        for line in lines_from_geojson(f.get("geometry")):
            pts = [world.proj(x, y) for x, y in line]
            for run in sample_cells(world.grid, pts):
                for a, b in links_of(world.grid, run):
                    if owner[a] == -2 or owner[b] == -2 or (owner[a] < 0 and owner[b] < 0):
                        continue
                    out[(a, b)] = min(cls, out.get((a, b), 9))
    return out


def pipelines(world, raw: Path):
    links, sources, counts = {}, [], Counter()
    gas = find_raw(raw, "gas_pipelines.geojson")
    if gas:
        def gas_cls(p):
            if (p.get("Status") or "").lower().startswith("aband"):
                return None
            return 0 if (p.get("TYPEPIPE") or "").lower().startswith("inter") else 1
        for k, v in _pipes(world, gas, gas_cls).items():
            links[k] = min(v, links.get(k, 9))
        sources.append("eia_gas_pipelines")
    crude = find_raw(raw, "crude_pipelines.geojson")
    if crude:
        feats = read_json(crude)["features"]
        if len(feats) >= 50:  # a national layer, not a one-state extract
            for k, v in _pipes(world, crude, lambda p: 2).items():
                links[k] = min(v, links.get(k, 9))
            sources.append("eia_crude_pipelines")
        else:
            print(f"    crude pipelines: only {len(feats)} features (a regional extract), left out")
    for v in links.values():
        counts[v] += 1
    used = sorted(set(links.values()))
    remap = {c: i for i, c in enumerate(used)}
    rows = [[a, b, remap[c]] for (a, b), c in sorted(links.items())]
    print(f"    pipelines: {len(rows)} hex links " + ", ".join(f"{PIPE_CLASSES[c]['id']} {n}" for c, n in sorted(counts.items())))
    return {"classes": [PIPE_CLASSES[c] for c in used], "links": rows}, sources


def _pt(f):
    g = f.get("geometry") or {}
    if g.get("type") == "Point":
        return g["coordinates"][0], g["coordinates"][1]
    p = f.get("properties") or {}
    lon, lat = p.get("Longitude") or p.get("lon"), p.get("Latitude") or p.get("lat")
    return (lon, lat) if lon is not None and lat is not None else (None, None)


def sites(world, raw: Path):
    out, sources = [], []
    ref = find_raw(raw, "refineries.geojson")
    if ref:
        for f in read_json(ref)["features"]:
            p = f.get("properties") or {}
            lon, lat = _pt(f)
            if lon is None:
                continue
            x, y, cell = world.place(lon, lat)
            if cell < 0:
                continue
            name = " ".join(w for w in [(p.get("Company") or "").title(), (p.get("Site") or "").title()] if w)
            out.append({"kind": "refinery", "name": name or "Refinery", "x": x, "y": y, "cell": cell,
                        "detail": f"{p.get('Corp', '') or ''} · {p.get('Source', 'EIA-820')}, {p.get('Period', '')}".strip(" ·,"),
                        "source": "EIA Energy Atlas (EIA-820)"})
        sources.append("eia_refineries")
    sto = find_raw(raw, "gas_storage.geojson")
    if sto:
        for f in read_json(sto)["features"]:
            p = f.get("properties") or {}
            if (p.get("Status") or "Active").lower() != "active":
                continue
            lon, lat = _pt(f)
            if lon is None:
                continue
            x, y, cell = world.place(lon, lat)
            if cell < 0:
                continue
            wc = p.get("work_cap")
            detail = f"{(p.get('Field_Type') or '').strip()}; {(p.get('Company') or '').title()}"
            if wc:
                detail += f"; working gas capacity {wc / 1000:,.0f} MMcf"
            out.append({"kind": "gas_storage", "name": (p.get("Field") or "Gas storage").title(), "x": x, "y": y,
                        "cell": cell, "detail": detail, "source": "EIA Energy Atlas (EIA-191)"})
        sources.append("eia_gas_storage")
    lng = find_raw(raw, "lng.geojson")
    if lng:
        feats = read_json(lng)["features"]
        terminals = [f for f in feats if "station_na" not in (f.get("properties") or {})]
        if terminals:
            for f in terminals:
                p = f.get("properties") or {}
                lon, lat = _pt(f)
                if lon is None:
                    continue
                x, y, cell = world.place(lon, lat, rings=2)
                if cell < 0:
                    continue
                name = p.get("Name") or p.get("Terminal") or p.get("Company") or "LNG terminal"
                out.append({"kind": "lng", "name": str(name).title(), "x": x, "y": y, "cell": cell,
                            "detail": p.get("Type") or p.get("Status") or "", "source": "EIA Energy Atlas"})
            sources.append("eia_lng")
        else:
            print("    LNG layer is refuelling stations, not terminals: left out")
    dc = find_raw(raw, "osm_data_centers.json")
    n_dc = 0
    if dc:
        by_cell = defaultdict(list)
        for e in read_json(dc).get("elements", []):
            lat = e.get("lat") or (e.get("center") or {}).get("lat")
            lon = e.get("lon") or (e.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            x, y, cell = world.place(lon, lat)
            if cell < 0:
                continue
            tags = e.get("tags") or {}
            by_cell[cell].append((tags.get("name") or tags.get("operator") or "", x, y))
            n_dc += 1
        for cell, items in by_cell.items():
            cx, cy = world.center(cell)
            names = sorted({n for n, _, _ in items if n})
            out.append({"kind": "datacenter", "name": "Data centers", "x": round(cx, 1), "y": round(cy, 1),
                        "cell": cell, "count": len(items), "names": names[:6],
                        "source": "© OpenStreetMap contributors (ODbL)"})
        sources.append("osm_data_centers")
    print(f"    sites: " + ", ".join(f"{k} {n}" for k, n in Counter(s['kind'] for s in out).items()) + f" ({n_dc} OSM data centers)")
    return out, sources
