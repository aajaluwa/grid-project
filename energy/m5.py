"""M5 validation: the answer key for the human test, computed from the built data with the same rules the site uses.

    python -m energy m5-key            # markdown answer key for the current build
    python -m energy m5-key --json     # the same as JSON (the browser tests read this)

The data refreshes monthly, so the answers change: regenerate the key before each test session.
The rules mirror web/js/ext/energy/replay.js (usAt, computeMoments), web/js/ext/energy/fuels.js (fmtMW) and
web/js/ext/advisors/views/capital.js (gw), so a mismatch between key and site is a real bug.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import WEB_DATA

FUEL_NAMES = {"gas": "Natural gas", "coal": "Coal", "nuclear": "Nuclear", "hydro": "Hydro", "wind": "Wind",
              "solar": "Solar", "battery": "Battery storage", "pumped_storage": "Pumped storage",
              "geothermal": "Geothermal", "oil": "Oil", "bio": "Biomass & waste", "other": "Other"}


def _load(name: str, web: Path):
    return json.loads((web / name).read_text())


def us_at(grid: dict, k: int) -> dict:
    """replay.js usAt(): lower-48 demand, reporting count and fuel mix (positive values only) for hour k."""
    demand, reported, mix = 0.0, 0, {}
    for ba, arr in grid["hourly"]["demand"].items():
        d = arr[k]
        if d is not None:
            demand += d
            reported += 1
        gen = grid["hourly"]["gen"].get(ba, {})
        for f in grid["fuels"]:
            v = gen.get(f, [None] * (k + 1))[k] if f in gen else None
            if v is not None and v > 0:
                mix[f] = mix.get(f, 0) + v
    return {"demand": demand, "reported": reported, "mix": sorted(mix.items(), key=lambda x: -x[1])}


def ba_mix(grid: dict, ba: str, k: int) -> list:
    gen = grid["hourly"]["gen"].get(ba, {})
    mix = [(f, gen[f][k]) for f in grid["fuels"] if f in gen and gen[f][k] is not None and gen[f][k] > 0]
    return sorted(mix, key=lambda x: -x[1])


def net_flow(grid: dict, ba: str, k: int) -> float:
    net = 0.0
    for f in grid["hourly"]["interchange"]:
        if ba not in (f["a"], f["b"]) or f["v"][k] is None:
            continue
        net += f["v"][k] if f["a"] == ba else -f["v"][k]
    return net


def peak_hour(grid: dict) -> tuple[int, float]:
    """replay.js computeMoments(): first hour with the highest demand among hours where >= 85% of operators report."""
    n_ba = len(grid["hourly"]["demand"])
    best = (-1, float("-inf"))
    for k in range(grid["hours"]["n"]):
        u = us_at(grid, k)
        if u["reported"] < 0.85 * n_ba:
            continue
        if u["demand"] > best[1]:
            best = (k, u["demand"])
    return best


def hour_label(grid: dict, k: int) -> dict:
    t = datetime.strptime(grid["hours"]["start_utc"], "%Y-%m-%dT%H:00Z").replace(tzinfo=timezone.utc) + timedelta(hours=k)
    try:
        from zoneinfo import ZoneInfo
        et = t.astimezone(ZoneInfo("America/New_York"))
        eastern = f"{et.hour % 12 or 12} {'AM' if et.hour < 12 else 'PM'}"
    except Exception:  # noqa: BLE001 - no tz database: leave the Eastern label out
        eastern = None
    return {"utc_iso": t.strftime("%Y-%m-%dT%H:00Z"), "utc": f"{t:%a, %b} {t.day}, {t:%H:%M}", "eastern": eastern}


def fmt_mw(mw: float) -> str:
    """fuels.js fmtMW()."""
    if abs(mw) >= 10000:
        return f"{mw / 1000:,.1f}".rstrip("0").rstrip(".") + " GW"
    return f"{round(mw):,} MW"


def gw(mw: float) -> str:
    """capital.js gw()."""
    digits = 0 if mw >= 100000 else 1
    return f"{mw / 1000:,.{digits}f}".rstrip("0").rstrip(".") + " GW" if digits else f"{mw / 1000:,.0f} GW"


def answer_key(web: Path = WEB_DATA) -> dict:
    grid, plants, lenses, world = (_load(n, web) for n in ("grid.json", "plants.json", "energy_lenses.json", "world.json"))
    k, peak = peak_hour(grid)
    when = hour_label(grid, k)
    us = us_at(grid, k)
    tot = sum(v for _, v in us["mix"])
    top_fuel, top_v = us["mix"][0]
    top_ba = max(((ba, arr[k]) for ba, arr in grid["hourly"]["demand"].items() if arr[k] is not None), key=lambda x: x[1])
    erco = ba_mix(grid, "ERCO", k)
    erco_tot = sum(v for _, v in erco)
    erco_name = next((b["name"] for b in grid["bas"] if b["id"] == "ERCO"), "ERCO")
    queue = lenses["queue"]
    q_top = next(iter(queue["by_type_mw"].items()))
    by_fuel = plants["totals"]["operating"]["by_fuel"]
    price = next(l for l in lenses["lenses"] if l["id"] == "price_res")
    month = price["description"].split(" in ", 1)[1].split(",", 1)[0]
    by_state = {}
    for cell, v in enumerate(price["values"]):
        o = world["owner"][cell]
        if v is not None and o >= 0:
            by_state[o] = v
    hi_state = max(by_state, key=by_state.get)
    lo_state = min(by_state, key=by_state.get)
    sample_cell = {o: next(c for c, own in enumerate(world["owner"]) if own == o and price["values"][c] is not None)
                   for o in (hi_state, lo_state)}
    pct = lambda v, t: round(100 * v / t) if t else 0
    return {
        "as_of": {"grid": grid["vintage"], "plants": plants["vintage"], "queue": "LBNL Queued Up 2026 edition",
                  "price": month},
        "peak": {"hour": k, **when, "demand_mw": round(peak), "demand_text": fmt_mw(peak),
                 "top_operator": top_ba[0], "top_operator_mw": top_ba[1], "top_operator_text": fmt_mw(top_ba[1]),
                 "top_fuel": top_fuel, "top_fuel_name": FUEL_NAMES[top_fuel], "top_fuel_pct": pct(top_v, tot),
                 "mix_pct": {f: pct(v, tot) for f, v in us["mix"]}},
        "queue": {"active_mw": queue["active_mw"], "active_text": gw(queue["active_mw"]), "top_type": q_top[0],
                  "top_type_mw": q_top[1], "top_type_text": gw(q_top[1]),
                  "top_type_pct": pct(q_top[1], queue["active_mw"])},
        "price": {"month": month, "high_state": world["states"][hi_state]["name"], "high": by_state[hi_state],
                  "high_cell": sample_cell[hi_state], "low_state": world["states"][lo_state]["name"],
                  "low": by_state[lo_state], "low_cell": sample_cell[lo_state]},
        "capacity": {"top_fuel": next(iter(by_fuel)), "top_fuel_text": gw(next(iter(by_fuel.values()))),
                     "solar_mw": by_fuel.get("solar", 0), "coal_mw": by_fuel.get("coal", 0),
                     "solar_text": gw(by_fuel.get("solar", 0)), "coal_text": gw(by_fuel.get("coal", 0)),
                     "more": "solar" if by_fuel.get("solar", 0) > by_fuel.get("coal", 0) else "coal"},
        "ercot": {"id": "ERCO", "name": erco_name, "top_fuel": erco[0][0] if erco else None,
                  "top_fuel_name": FUEL_NAMES.get(erco[0][0], "") if erco else "",
                  "top_fuel_pct": pct(erco[0][1], erco_tot) if erco else None,
                  "demand_text": fmt_mw(grid["hourly"]["demand"]["ERCO"][k] or 0),
                  "net_flow_mw": round(net_flow(grid, "ERCO", k))},
    }


def to_markdown(key: dict) -> str:
    p, q, pr, c, e = key["peak"], key["queue"], key["price"], key["capacity"], key["ercot"]
    eastern = f" ({p['eastern']} Eastern)" if p["eastern"] else ""
    rows = [
        ("A1", "Analyst", "When did lower-48 demand peak in the replay window, and how high?",
         f"{p['utc']} UTC{eastern}; {p['demand_text']}", "time exact; demand within 1%"),
        ("A2", "Analyst", "How much capacity was waiting in interconnection queues at the end of 2025, and which technology was largest?",
         f"{q['active_text']}; {q['top_type']} ({q['top_type_text']}, {q['top_type_pct']}%)", "total within 2%; technology exact"),
        ("A3", "Analyst", f"Which lower-48 state had the highest household electricity price in {pr['month']}, and what was it?",
         f"{pr['high_state']}, {pr['high']:.2f} cents/kWh (lowest: {pr['low_state']}, {pr['low']:.2f})", "state exact; price within 0.1 cent"),
        ("N1", "Non-expert", "At the hour of peak demand, which fuel made the most of the country's electricity, and about what share?",
         f"{p['top_fuel_name']}, about {p['top_fuel_pct']}%", "fuel exact; share within 3 points"),
        ("N2", "Non-expert", "Does the country have more solar or more coal power-plant capacity?",
         f"{c['more'].title()} ({c['solar_text']} solar vs {c['coal_text']} coal)", "the right one of the two"),
        ("N3", "Non-expert", "Which grid operator runs most of Texas, and what was its biggest fuel at the peak-demand hour?",
         f"{e['id']} ({e['name']}); {e['top_fuel_name']}, about {e['top_fuel_pct']}%", "operator exact; fuel exact"),
    ]
    out = ["| # | Tester | Question | Expected answer | Counts as correct |", "| --- | --- | --- | --- | --- |"]
    out += [f"| {a} | {b} | {c_} | {d} | {e_} |" for a, b, c_, d, e_ in rows]
    src = key["as_of"]
    return "\n".join(out) + f"\n\nData: {src['grid']}; {src['plants']}; {src['queue']}; prices {src['price']}."
