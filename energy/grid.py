"""M2: grid operators (balancing authorities) on the hex map, and their hourly operation from EIA-930.

Territories: EIA's balancing-authority boundary layer is no longer public, so each US land hex goes to the
operator that registered the most operating capacity in it (EIA-860M "Balancing Authority Code"), and hexes
without plants take the nearest assigned hex's operator. That is a rough service area, stated as such in the UI.

Hourly data: the newest EIA-930 six-month balance and interchange files. The last 14 days are kept hourly
(the replay); the whole period is kept as daily means for context.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from pipeline.sources._resource_util import xlsx_records

HOURLY_DAYS = 14
FUEL_ORDER = ["nuclear", "coal", "gas", "oil", "hydro", "pumped_storage", "wind", "solar", "battery", "geothermal",
              "other"]
PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f",
           "#bab0ac", "#6b8fc9", "#d37295", "#8cd17d", "#b6992d", "#499894", "#e8a33d", "#86bcb6", "#f1ce63",
           "#a0cbe8", "#ffbe7d"]


def classify(header: str) -> str | None:
    h = re.sub(r"\s+", " ", header)
    if "Net Generation (MW) from" not in h or "(Adjusted)" not in h:
        return None
    for key, fuel in (("Coal", "coal"), ("Natural Gas", "gas"), ("Nuclear", "nuclear"), ("Petroleum", "oil"),
                      ("Hydropower", "hydro"), ("Pumped", "pumped_storage"), ("Solar", "solar"), ("Wind", "wind"),
                      ("Battery", "battery"), ("Other Energy Storage", "battery"), ("Unknown Energy Storage", "battery"),
                      ("Geothermal", "geothermal"), ("Other Fuel", "other"), ("Unknown Fuel", "other")):
        if key in h:
            return fuel
    return None


def _num(s: str):
    if s is None or s == "":
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _utc(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M:%S %p")


def read_balance(path: Path):
    text = io.TextIOWrapper(gzip.open(path), encoding="utf-8-sig") if path.suffix == ".gz" else open(path, encoding="utf-8-sig")
    reader = csv.DictReader(text)
    fuel_cols = {h: classify(h) for h in reader.fieldnames if classify(h)}
    dem_col = next(h for h in reader.fieldnames if re.sub(r"\s+", " ", h) == "Demand (MW) (Adjusted)")
    gen_col = next(h for h in reader.fieldnames if re.sub(r"\s+", " ", h) == "Net Generation (MW) (Adjusted)")
    data = defaultdict(dict)  # ba -> utc -> {demand, net, fuel: mw}
    region = {}
    for row in reader:
        ba = row["Balancing Authority"]
        t = _utc(row["UTC Time at End of Hour"])
        rec = {"demand": _num(row[dem_col]), "net": _num(row[gen_col]), "fuels": Counter()}
        for h, fuel in fuel_cols.items():
            v = _num(row[h])
            if v is not None:
                rec["fuels"][fuel] += v
        data[ba][t] = rec
        region[ba] = row.get("Region", "")
    return data, region


def read_interchange(path: Path):
    text = io.TextIOWrapper(gzip.open(path), encoding="utf-8-sig") if path.suffix == ".gz" else open(path, encoding="utf-8-sig")
    flows = defaultdict(lambda: defaultdict(list))  # (a, b) sorted -> utc -> [a->b estimates]
    for row in csv.DictReader(text):
        a, b = row["Balancing Authority"], row["Directly Interconnected Balancing Authority"]
        v = _num(row["Interchange (MW)"])
        if v is None or not b or a == b:
            continue
        t = _utc(row["UTC Time at End of Hour"])
        if a < b:
            flows[(a, b)][t].append(v)       # positive: a exports to b
        else:
            flows[(b, a)][t].append(-v)
    return flows


def ba_names(raw: Path) -> dict:
    p = raw / "EIA930_Reference_Tables.xlsx"
    out = {}
    if p.exists():
        for r in xlsx_records(p, "BA Code", sheet="BAs"):
            if r.get("BA Code"):
                out[r["BA Code"]] = {"name": r.get("BA Name", ""), "tz": r.get("Time Zone", ""),
                                     "region": r.get("Region/Country Name", ""),
                                     "gen_only": r.get("Generation Only BA", "") == "Yes"}
    return out


def territories(world, ba_cap_cell: dict) -> tuple[list[int], list[str]]:
    codes = sorted({ba for c in ba_cap_cell.values() for ba in c})
    idx = {c: i for i, c in enumerate(codes)}
    values = [-1] * world.size
    for cell, cap in ba_cap_cell.items():
        if world.owner[cell] >= 0 and cap:
            values[cell] = idx[cap.most_common(1)[0][0]]
    seeded = sum(1 for v in values if v >= 0)
    values = world.fill_nearest(values)
    print(f"    grid operators: {len(codes)} codes, {seeded} hexes seeded by plants, filled to {sum(1 for v in values if v >= 0)}")
    return values, codes


def build(world, raw: Path, ba_cap_cell: dict) -> dict:
    bal = sorted(raw.glob("EIA930_BALANCE_*.csv*"))
    itc = sorted(raw.glob("EIA930_INTERCHANGE_*.csv*"))
    if not bal:
        raise FileNotFoundError("no EIA-930 balance file in raw/")
    data, region = read_balance(bal[-1])
    flows = read_interchange(itc[-1]) if itc else {}
    names = ba_names(raw)
    cell_ba, codes = territories(world, ba_cap_cell)

    # The replay ends at the newest hour that most operators have reported.
    counts = Counter(t for ba in data for t, r in data[ba].items() if r["demand"] is not None)
    n_ba = len(data)
    end = max(t for t, n in counts.items() if n >= 0.85 * n_ba)
    start = end - timedelta(hours=24 * HOURLY_DAYS - 1)
    hours = [start + timedelta(hours=k) for k in range(24 * HOURLY_DAYS)]

    # Operators with a territory on the map (generation-only operators often have none: they still get flows).
    cells_of = Counter(codes[v] for v in cell_ba if v >= 0)
    anchors = {}
    for code in codes:
        cells = [i for i, v in enumerate(cell_ba) if v >= 0 and codes[v] == code]
        if not cells:
            continue
        mx = sum(world.center(c)[0] for c in cells) / len(cells)
        my = sum(world.center(c)[1] for c in cells) / len(cells)
        cell = min(cells, key=lambda c: (world.center(c)[0] - mx) ** 2 + (world.center(c)[1] - my) ** 2)
        anchors[code] = cell

    bas, hourly_dem, hourly_gen = [], {}, {}
    check_rows = check_ok = 0
    for k, code in enumerate(sorted(set(data) | set(anchors))):
        series = data.get(code, {})
        dem = [None if (r := series.get(t)) is None or r["demand"] is None else round(r["demand"]) for t in hours]
        gen = {}
        for f in FUEL_ORDER:
            vals = [None if (r := series.get(t)) is None else round(r["fuels"].get(f, 0)) for t in hours]
            if any(v for v in vals if v):
                gen[f] = vals
        for t in hours:
            r = series.get(t)
            if r and r["net"] not in (None, 0) and r["fuels"]:
                check_rows += 1
                if abs(sum(r["fuels"].values()) - r["net"]) <= max(2.0, 0.02 * abs(r["net"])):
                    check_ok += 1
        cell = anchors.get(code)
        meta = names.get(code, {})
        bas.append({
            "id": code, "name": meta.get("name") or code, "region": meta.get("region") or region.get(code, ""),
            "tz": meta.get("tz", ""), "gen_only": bool(meta.get("gen_only")),
            "color": PALETTE[k % len(PALETTE)], "cell": cell,
            "x": round(world.center(cell)[0], 1) if cell is not None else None,
            "y": round(world.center(cell)[1], 1) if cell is not None else None,
            "hexes": cells_of.get(code, 0), "reports": code in data,
        })
        if code in data:
            hourly_dem[code] = dem
            hourly_gen[code] = gen

    inter = []
    for (a, b), series in flows.items():
        vals = []
        for t in hours:
            est = series.get(t)
            vals.append(round(sum(est) / len(est)) if est else None)
        peak = max((abs(v) for v in vals if v is not None), default=0)
        if peak >= 50 and a in hourly_dem and b in hourly_dem:
            inter.append({"a": a, "b": b, "v": vals})
    inter.sort(key=lambda f: -max(abs(v) for v in f["v"] if v is not None))

    # Daily means over the whole six-month file (US lower 48 = the sum over operators).
    days = sorted({t.date() for ba in data for t in data[ba]})
    daily_dem, daily_us = defaultdict(list), {f: [] for f in FUEL_ORDER}
    by_day = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    us_day = defaultdict(Counter)
    us_hours = defaultdict(set)
    for ba, series in data.items():
        for t, r in series.items():
            d = t.date()
            if r["demand"] is not None:
                acc = by_day[ba][d]
                acc[0] += r["demand"]
                acc[1] += 1
            for f, v in r["fuels"].items():
                us_day[d][f] += v
            us_hours[d].add(t)
    for ba in hourly_dem:
        daily_dem[ba] = [round(by_day[ba][d][0] / by_day[ba][d][1]) if by_day[ba][d][1] else None for d in days]
    for f in FUEL_ORDER:
        daily_us[f] = [round(us_day[d][f] / max(1, len(us_hours[d]))) for d in days]

    period = f"{days[0].isoformat()} to {days[-1].isoformat()}" if days else ""
    grid = {
        "version": 1, "vintage": f"EIA-930 Hourly Electric Grid Monitor, {period}", "sources": ["eia_930", "eia_860m_full"],
        "fuels": FUEL_ORDER,
        "rule": ("Territories are approximate: each hex goes to the grid operator (balancing authority) that registered "
                 "the most operating capacity in it in EIA-860M; hexes without plants take the nearest operator. "
                 "EIA's own boundary layer is not public. Flows are EIA-930 interchange between directly connected "
                 "operators (positive = first operator exports to the second); both sides' reports are averaged."),
        "hours": {"start_utc": hours[0].strftime("%Y-%m-%dT%H:00Z"), "n": len(hours), "step_s": 3600},
        "bas": bas,
        "cell_ba": [codes[v] if v >= 0 else None for v in cell_ba],
        "hourly": {"demand": hourly_dem, "gen": hourly_gen, "interchange": inter},
        "daily": {"dates": [d.isoformat() for d in days], "demand": daily_dem, "us_gen": daily_us},
        "check": {"rows": check_rows, "fuel_sum_matches_net": check_ok,
                  "share": round(check_ok / check_rows, 4) if check_rows else None,
                  "rule": "Hours where the sum of generation by fuel is within 2% (or 2 MW) of reported net generation."},
    }
    return {"grid": grid, "cell_ba": cell_ba, "codes": codes}
