"""M1: every power plant in EIA-860M (operating, planned, retired since 2015), placed on the hex map.

Unlike usaciv's resources.json, nothing is filtered by size: the browser thins plants by zoom instead, so state and
national totals add up to the whole inventory. Generators are summed into plants per status; a plant's fuel is
its largest fuel category by net summer capacity (nameplate where that is blank).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from pipeline.sources._resource_util import num, xlsx_records
from pipeline.sources.eia_860m import fuel_category

FUEL_IDS = ["gas", "coal", "nuclear", "hydro", "wind", "solar", "battery", "pumped_storage", "geothermal", "oil",
            "bio", "other"]
FUEL_NAMES = {"gas": "Natural gas", "coal": "Coal", "nuclear": "Nuclear", "hydro": "Hydro", "wind": "Wind",
              "solar": "Solar", "battery": "Battery storage", "pumped_storage": "Pumped storage",
              "geothermal": "Geothermal", "oil": "Oil", "bio": "Biomass & waste", "other": "Other"}
FUEL_COLORS = {"gas": "#f08a24", "coal": "#5a5a5a", "nuclear": "#e8d44d", "hydro": "#3fa7d6", "wind": "#7fd1c0",
               "solar": "#ffc93c", "battery": "#7bc96f", "pumped_storage": "#2f7fb8", "geothermal": "#d9534f",
               "oil": "#8b5a2b", "bio": "#7a9a3a", "other": "#a0a0a0"}
STORAGE = {"battery", "pumped_storage"}
ZERO_CARBON = {"nuclear", "hydro", "wind", "solar", "geothermal"}
STATUSES = ["operating", "planned", "retired"]
IN_SERVICE = {"OP", "SB"}
RETIRED_SINCE = 2015


def _status_code(text: str) -> str:
    return (text or "")[1:3] if (text or "").startswith("(") else (text or "")[:2].upper()


def read_sheet(path: Path, sheet: str, status: str) -> tuple[dict, dict]:
    """Plants of one sheet: {plant id: plant}, plus raw totals {fuel: mw} over every row counted."""
    plants: dict[str, dict] = {}
    raw_tot: Counter = Counter()
    for r in xlsx_records(path, "Plant ID", sheet=sheet):
        pid = r.get("Plant ID")
        mw = num(r.get("Net Summer Capacity (MW)"))
        if mw is None:
            mw = num(r.get("Nameplate Capacity (MW)"))
        if not pid or mw is None:
            continue
        if status == "operating" and _status_code(r.get("Status", "")) not in IN_SERVICE:
            continue
        if status == "retired":
            yr = num(r.get("Retirement Year"))
            if yr is None or yr < RETIRED_SINCE:
                continue
        fuel = fuel_category(r.get("Energy Source Code", ""), r.get("Prime Mover Code", ""))
        raw_tot[fuel] += mw
        p = plants.setdefault(pid, {
            "id": pid, "name": r.get("Plant Name", ""), "state": r.get("Plant State", ""),
            "lat": num(r.get("Latitude")), "lon": num(r.get("Longitude")), "fuels": Counter(), "ba": Counter(),
            "years": [], "retire": Counter(), "offshore": False,
        })
        p["fuels"][fuel] += mw
        if r.get("Balancing Authority Code"):
            p["ba"][r["Balancing Authority Code"]] += mw
        if r.get("Prime Mover Code") == "WS":
            p["offshore"] = True
        yk = {"operating": "Operating Year", "planned": "Planned Operation Year", "retired": "Retirement Year"}[status]
        if (yr := num(r.get(yk))) is not None:
            p["years"].append(int(yr))
        if status == "operating" and (ry := num(r.get("Planned Retirement Year"))) is not None:
            p["retire"][int(ry)] += mw
    return plants, raw_tot


def build(world, raw: Path) -> dict:
    path = raw / "eia860m.xlsx"
    month = None
    import json
    man = json.loads((raw / "MANIFEST.json").read_text()) if (raw / "MANIFEST.json").exists() else {}
    month = man.get("eia860m_month")
    rows, totals, per_cell = [], {}, defaultdict(lambda: defaultdict(Counter))
    retiring_cell: Counter = Counter()
    retiring_by_year: Counter = Counter()
    ba_cap_cell: dict[int, Counter] = defaultdict(Counter)
    raw_totals = {}
    for status, sheet in (("operating", "Operating"), ("planned", "Planned"), ("retired", "Retired")):
        plants, raw_tot = read_sheet(path, sheet, status)
        raw_totals[status] = {k: round(v, 1) for k, v in raw_tot.items()}
        tot = {"all_mw": 0.0, "mapped_mw": 0.0, "plants": 0, "mapped_plants": 0,
               "by_fuel": Counter(), "mapped_by_fuel": Counter()}
        for p in plants.values():
            mw = sum(p["fuels"].values())
            fuel = p["fuels"].most_common(1)[0][0]
            tot["all_mw"] += mw
            tot["plants"] += 1
            for f, v in p["fuels"].items():
                tot["by_fuel"][f] += v
            if p["lat"] is None or p["lon"] is None:
                continue
            x, y, cell = world.place(p["lon"], p["lat"], rings=3 if p["offshore"] else 1)
            if cell < 0:
                continue  # Alaska, Hawaii, Puerto Rico, or far offshore: counted in the totals, not on the map
            tot["mapped_mw"] += mw
            tot["mapped_plants"] += 1
            for f, v in p["fuels"].items():
                tot["mapped_by_fuel"][f] += v
                per_cell[status][cell][f] += v
            ba = p["ba"].most_common(1)[0][0] if p["ba"] else ""
            if status == "operating" and ba:
                ba_cap_cell[cell][ba] += mw
            yrs = p["years"]
            year = (min(yrs) if status != "retired" else max(yrs)) if yrs else None
            if status == "operating":
                for ry, v in p["retire"].items():
                    retiring_by_year[ry] += v
                    retiring_cell[cell] += v
            rows.append([p["name"], p["state"], FUEL_IDS.index(fuel) if fuel in FUEL_IDS else FUEL_IDS.index("other"),
                         round(mw, 1), STATUSES.index(status), year, x, y, cell, ba])
        totals[status] = {
            "all_mw": round(tot["all_mw"]), "mapped_mw": round(tot["mapped_mw"]),
            "plants": tot["plants"], "mapped_plants": tot["mapped_plants"],
            "by_fuel": {k: round(v) for k, v in tot["by_fuel"].most_common()},
            "mapped_by_fuel": {k: round(v) for k, v in tot["mapped_by_fuel"].most_common()},
        }
    rows.sort(key=lambda r: -r[3])
    label = month or "latest"
    out = {
        "version": 1, "vintage": f"EIA-860M, {label}", "as_of": month, "sources": ["eia_860m_full"],
        "fuels": [{"id": f, "name": FUEL_NAMES[f], "color": FUEL_COLORS[f], "zero_carbon": f in ZERO_CARBON,
                   "storage": f in STORAGE} for f in FUEL_IDS],
        "statuses": STATUSES,
        "columns": ["name", "state", "fuel", "mw", "status", "year", "x", "y", "cell", "ba"],
        "rows": rows,
        "totals": totals,
        "retiring_by_year": {str(k): round(v) for k, v in sorted(retiring_by_year.items())},
        "rule": ("Every generator in EIA-860M: Operating sheet units in service (status OP or SB), every Planned sheet "
                 f"unit, and Retired sheet units retired in {RETIRED_SINCE} or later, summed into plants by status. "
                 "Capacity is net summer capacity (nameplate where blank). A plant's fuel is its largest fuel by "
                 "capacity. Plants outside the lower 48 count in the national totals but are not drawn."),
    }
    return {"plants": out, "per_cell": per_cell, "retiring_cell": retiring_cell, "ba_cap_cell": ba_cap_cell,
            "raw_totals": raw_totals}
