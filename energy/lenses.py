"""Energy lenses (M1 and M3): per-hex values the lens dock colours the map by. Written to energy_lenses.json and
merged into the lens list by main.js, so they work exactly like usaciv's own lenses (legend, brushing, URL).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.sources._resource_util import num, xlsx_rows

from .plants import FUEL_COLORS, FUEL_IDS, FUEL_NAMES, STORAGE, ZERO_CARBON

LOG_RAMP = ["#14102a", "#3b1f6b", "#7a2f8a", "#c44e6b", "#f08a4b", "#fde29a"]
GREEN_RAMP = ["#2b2b2b", "#4a5a3a", "#5f8a3f", "#7fbf4f", "#b7e07a", "#eaf7c8"]
BLUE_RAMP = ["#101c2c", "#183a5a", "#1f5f8a", "#2f8fbf", "#7cc4e4", "#d7f0fa"]
RED_RAMP = ["#1c1414", "#4a1f1f", "#7f2a26", "#b8432f", "#e8774a", "#ffd0a0"]
QUEUE_TYPES = {"Solar": "solar", "Wind": "wind", "Offshore Wind": "wind", "Battery": "battery", "Storage": "battery",
               "Gas": "gas", "Nuclear": "nuclear", "Hydro": "hydro", "Pumped Storage": "pumped_storage",
               "Geothermal": "geothermal", "Coal": "coal", "Oil": "oil", "Biomass": "bio"}


def pct_domain(values, log=False):
    vals = sorted(v for v in values if v is not None and (v > 0 or not log))
    if not vals:
        return [0, 1]
    lo, hi = vals[int(0.02 * (len(vals) - 1))], vals[int(0.98 * (len(vals) - 1))]
    def nice(v, up):
        if v == 0:
            return 0
        mag = 10 ** (math.floor(math.log10(abs(v))) - 1)
        return round((math.ceil if up else math.floor)(v / mag) * mag, 10)
    lo, hi = nice(lo, False), nice(hi, True)
    return [lo, hi] if hi > lo else [lo, nice(vals[-1], True) or 1]


def lens(id, name, unit, values, *, group="energy", fmt="float", scale="linear", ramp=None, higher="neutral",
         source="eia_860m_full", description="", note=None, emoji="⚡", domain=None, categories=None):
    out = {"id": id, "group": group, "name": name, "icon": f"l-{id}", "emoji": emoji, "unit": unit, "format": fmt,
           "scale": scale, "ramp": ramp or LOG_RAMP, "higher_is": higher, "source": source,
           "description": description, "values": values}
    if categories is not None:
        out["categories"] = categories
        out["ramp"] = [c["color"] for c in categories]
        out["domain"] = [0, len(categories) - 1]
        out["scale"] = "linear"
    else:
        out["domain"] = domain or pct_domain(values, scale == "log")
    if note:
        out["note"] = note
    return out


def county_weights(world) -> dict[int, list[tuple[int, float]]]:
    """county index -> [(cell, share of the county's sample points in that cell)]; small counties that no sample
    point hits go whole to their representative cell from counties.json."""
    hits = defaultdict(Counter)
    for cell, smp in enumerate(world.county_samples):
        for k in smp:
            if k >= 0:
                hits[k][cell] += 1
    out = {k: [(c, n / sum(cs.values())) for c, n in cs.items()] for k, cs in hits.items()}
    counties = json.loads((world_web_dir() / "counties.json").read_text())
    ci = counties["columns"].index("cell")
    for k, row in enumerate(counties["rows"]):
        if k not in out and row[ci] is not None:
            out[k] = [(row[ci], 1.0)]
    return out


def world_web_dir() -> Path:
    from .common import WEB_DATA
    return WEB_DATA


def queue(world, raw: Path):
    p = raw / "lbnl_queue.xlsx"
    if not p.exists():
        return None
    geo_idx = {g: k for k, g in enumerate(world.geoids)}
    weights = county_weights(world)
    gw = [0.0] * world.size
    by_type = defaultdict(lambda: [0.0] * world.size)
    stats = Counter()
    unplaced = 0.0
    total = 0.0
    hdr = None
    for row in xlsx_rows(p, "03. Complete Queue Data"):
        vals = list(row.values())
        if hdr is None:
            if "q_status" in vals:
                hdr = row
            continue
        r = {hdr[k]: v for k, v in row.items() if k in hdr}
        if (r.get("q_status") or "").lower() != "active":
            continue
        mw = sum(v for v in (num(r.get("mw_1")), num(r.get("mw_2")), num(r.get("mw_3"))) if v)
        if not mw:
            continue
        total += mw
        stats[r.get("type_clean") or "Other"] += mw
        fips = (r.get("fips_code") or "").split(".")[0]
        k = geo_idx.get(fips.zfill(5)) if fips.isdigit() else None
        if k is None or k not in weights:
            unplaced += mw
            continue
        t = QUEUE_TYPES.get(r.get("type_1") or "", "other")
        for cell, share in weights[k]:
            gw[cell] += mw * share / 1000
            by_type[t][cell] += mw * share / 1000
    values = [round(v, 3) if world.owner[i] >= 0 else None for i, v in enumerate(gw)]
    cats = [c for c in FUEL_IDS if any(by_type[c])]
    dom = []
    for i in range(world.size):
        if world.owner[i] < 0 or gw[i] <= 0:
            dom.append(None)
            continue
        best = max(cats, key=lambda c: by_type[c][i])
        dom.append(cats.index(best))
    summary = {"active_mw": round(total), "mapped_mw": round(total - unplaced), "unplaced_mw": round(unplaced),
               "by_type_mw": {k: round(v) for k, v in stats.most_common()}}
    print(f"    queue: {total / 1000:,.0f} GW active, {unplaced / 1000:,.1f} GW without a mapped county")
    note = ("Most queued capacity is never built: projects withdraw. A queue entry is a request to connect, "
            "not a plant. Data through the end of 2025.")
    return [
        lens("queue_gw", "Interconnection queue", "GW waiting to connect", values, group="capital", scale="log",
             ramp=BLUE_RAMP, higher="neutral", source="lbnl_queue", emoji="⏳",
             description=("Active interconnection-queue requests (generation and storage, all components) by the "
                          "county of their point of interconnection, spread over the county's hexes by area "
                          "(LBNL Queued Up 2026 edition, CC BY 4.0)."), note=note),
        lens("queue_type", "Queued technology", "category", dom, group="capital", source="lbnl_queue", emoji="🧩",
             fmt="category", description="The technology with the most active queued capacity in each hex (primary type).",
             note=note, categories=[{"id": c, "name": FUEL_NAMES[c], "color": FUEL_COLORS[c]} for c in cats]),
    ], summary


def plant_lenses(world, per_cell, retiring_cell):
    op, pl, rt = per_cell["operating"], per_cell["planned"], per_cell["retired"]
    land = lambda i: world.owner[i] >= 0
    cap = [round(sum(op[i].values()), 1) if land(i) else None for i in range(world.size)]
    cats = FUEL_IDS
    dom = []
    clean = []
    for i in range(world.size):
        if not land(i) or not op[i]:
            dom.append(None)
            clean.append(None)
            continue
        dom.append(cats.index(op[i].most_common(1)[0][0]))
        gen = {f: v for f, v in op[i].items() if f not in STORAGE}
        tot = sum(gen.values())
        clean.append(round(sum(v for f, v in gen.items() if f in ZERO_CARBON) / tot, 3) if tot else None)
    planned = [round(sum(pl[i].values()), 1) if land(i) else None for i in range(world.size)]
    retired = [round(sum(rt[i].values()), 1) if land(i) else None for i in range(world.size)]
    retiring = [round(retiring_cell.get(i, 0.0), 1) if land(i) else None for i in range(world.size)]
    src = ("EIA-860M monthly generator inventory: net summer capacity of every generator in service, summed per hex.")
    return [
        lens("cap_mw", "Generating capacity", "MW", cap, scale="log", ramp=LOG_RAMP, emoji="⚡",
             description=src + " Zero means no registered plant in the hex."),
        lens("fuel_main", "Main fuel", "category", dom, fmt="category", emoji="🔥",
             description="The fuel with the most operating capacity in each hex (EIA-860M).",
             categories=[{"id": c, "name": FUEL_NAMES[c], "color": FUEL_COLORS[c]} for c in cats]),
        lens("clean_share", "Zero-carbon share", "share of capacity", clean, fmt="pct", ramp=GREEN_RAMP,
             higher="good", emoji="🌱", domain=[0, 1],
             description=("Nuclear, hydro, wind, solar and geothermal as a share of operating generating capacity in "
                          "the hex (storage excluded from both sides). Capacity, not energy produced."),
             note="Capacity share, not generation: a solar MW produces far fewer MWh a year than a nuclear MW."),
        lens("planned_mw", "Planned additions", "MW", planned, group="capital", scale="log", ramp=GREEN_RAMP,
             emoji="🏗️", description="Capacity in EIA-860M's Planned sheet (all planning stages), by hex.",
             note="Planned units include early-stage projects that may slip or be cancelled."),
        lens("retiring_mw", "Planned retirements", "MW", retiring, group="capital", scale="log", ramp=RED_RAMP,
             emoji="🪦", higher="neutral",
             description="Operating capacity with a planned retirement year in EIA-860M, by hex."),
        lens("retired_mw", "Retired since 2015", "MW", retired, group="capital", scale="log", ramp=RED_RAMP,
             emoji="🏚️", description="Capacity in EIA-860M's Retired sheet with a retirement year of 2015 or later."),
    ]


def ba_lens(world, cell_ba, codes, bas):
    color = {b["id"]: b["color"] for b in bas}
    name = {b["id"]: b["name"] for b in bas}
    cats = [{"id": c, "name": f"{c}: {name.get(c, c)}", "color": color.get(c, "#888888")} for c in codes]
    vals = [v if (v >= 0 and world.owner[i] >= 0) else None for i, v in enumerate(cell_ba)]
    return lens("grid_operator", "Grid operator", "category", vals, fmt="category", emoji="🗺️", source="eia_860m_full",
                description=("Approximate territory of each balancing authority: the operator with the most registered "
                             "operating capacity in the hex (EIA-860M), with empty hexes taking the nearest operator."),
                note="Rough service areas derived from plant registrations, not legal boundaries.", categories=cats)


def datacenter_lens(world, sites):
    counts = Counter()
    for s in sites:
        if s["kind"] == "datacenter":
            counts[s["cell"]] += s.get("count", 1)
    vals = [counts.get(i, 0) if world.owner[i] >= 0 else None for i in range(world.size)]
    return lens("datacenters", "Data centers", "sites mapped in OpenStreetMap", vals, group="capital", fmt="int",
                scale="linear", ramp=LOG_RAMP, emoji="🖥️", source="osm_data_centers", domain=[0, 10],
                description=("Data center sites tagged in OpenStreetMap (telecom=data_center or building=data_center), "
                             "counted per hex. © OpenStreetMap contributors, ODbL."),
                note="Crowd-mapped and incomplete; counts sites, not power demand.")


def retail_prices(world, raw: Path):
    """State residential electricity price (EIA Electric Power Monthly table 5.6.A), on every hex of the state."""
    import re as _re
    p = raw / "eia_retail_prices.xlsx"
    if not p.exists():
        return None
    by_name = {s["name"]: i for i, s in enumerate(world.states)}
    cur, chg, month = {}, {}, ""
    for row in xlsx_rows(p):
        vals = list(row.values())
        if not vals:
            continue
        if not month and vals[0] and "by State," in vals[0]:
            m = _re.search(r"by State, (\w+ \d{4})", vals[0])
            month = m.group(1) if m else ""
        k = by_name.get((vals[0] or "").strip())
        if k is None or len(vals) < 3:
            continue
        now, before = num(vals[1]), num(vals[2])
        if now is not None:
            cur[k] = round(now, 2)
            if before:
                chg[k] = round(now / before - 1, 4)
    price = [cur.get(world.owner[i]) if world.owner[i] >= 0 else None for i in range(world.size)]
    change = [chg.get(world.owner[i]) if world.owner[i] >= 0 else None for i in range(world.size)]
    desc = (f"Average residential price of electricity in {month}, by state (EIA Electric Power Monthly, table 5.6.A). "
            "Every hex of a state shows the state's figure.")
    print(f"    retail prices: {len(cur)} states, {month}")
    return [
        lens("price_res", "Household power price", "cents per kWh", price, group="capital", fmt="float", ramp=RED_RAMP,
             higher="bad", source="eia_retail_prices", emoji="💡", description=desc,
             note=f"{month}; one value per state, so a utility's own rate can differ a lot."),
        lens("price_res_change", "Price change in a year", "change vs a year earlier", change, group="capital",
             fmt="pct", ramp=["#1f5f8a", "#7cc4e4", "#f2f2f2", "#e8774a", "#7f2a26"], higher="bad",
             source="eia_retail_prices", emoji="📈", domain=[-0.15, 0.15],
             description=f"Residential price in {month} against the same month a year earlier (EIA table 5.6.A).",
             note="Single-month comparison: seasonal and billing effects move it."),
    ], month
