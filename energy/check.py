"""Milestone gates for the energy build. Each line is PASS, NOTE, WARN or FAIL; any FAIL exits 1."""

from __future__ import annotations

import json

from .common import WEB_DATA

EIA_END_2025_MW = 1_280_799  # EIA "Electricity in the U.S.", utility-scale capacity at the end of 2025
EAGER_BUDGET = 300 * 1024


def _load(name):
    p = WEB_DATA / name
    return (json.loads(p.read_text()), p.stat().st_size) if p.exists() else (None, 0)


def run() -> int:
    out = []
    add = lambda lvl, msg: out.append((lvl, msg))
    world, _ = _load("world.json")
    n_cells = world["grid"]["cols"] * world["grid"]["rows"]
    sources, _ = _load("sources.json")
    src_ids = {s["id"] for s in sources or []}

    plants, size = _load("plants.json")
    if not plants:
        add("FAIL", "M1 plants.json missing")
    else:
        t = plants["totals"]["operating"]
        rows = [r for r in plants["rows"] if r[4] == 0]
        drawn = sum(r[3] for r in rows)
        add("PASS" if abs(drawn - t["mapped_mw"]) <= 0.001 * t["mapped_mw"] + 1 else "FAIL",
            f"M1 drawn operating plants sum to the mapped total: {drawn:,.0f} vs {t['mapped_mw']:,} MW")
        share = t["mapped_mw"] / t["all_mw"]
        add("PASS" if share >= 0.95 else "WARN", f"M1 {share:.1%} of national operating capacity is on the lower-48 map "
            f"({t['mapped_plants']:,} of {t['plants']:,} plants; the rest is AK, HI, PR or unlocated)")
        ratio = t["all_mw"] / EIA_END_2025_MW
        add("PASS" if 0.98 <= ratio <= 1.12 else "WARN",
            f"M1 national operating capacity {t['all_mw']:,} MW ({plants['vintage']}) is {ratio - 1:+.1%} vs EIA's "
            f"published end-2025 total of {EIA_END_2025_MW:,} MW (later inventory: growth expected)")
        bf = t["by_fuel"]
        add("PASS" if bf.get("gas", 0) > bf.get("coal", 0) else "FAIL",
            f"M1 gas outranks coal as it does nationally: gas {bf.get('gas', 0):,} MW, coal {bf.get('coal', 0):,} MW")
        add("NOTE", f"M1 plants.json {size / 1024:.0f} kB, {len(plants['rows']):,} plants (lazy-loaded)")

    grid, gsize = _load("grid.json")
    if not grid:
        add("FAIL", "M2 grid.json missing")
    else:
        n = grid["hours"]["n"]
        bad = [ba for ba, v in grid["hourly"]["demand"].items() if len(v) != n]
        add("PASS" if not bad else "FAIL", f"M2 {len(grid['hourly']['demand'])} operators with {n} hourly values each")
        ch = grid["check"]
        add("PASS" if (ch["share"] or 0) >= 0.9 else "WARN",
            f"M2 fuel mix sums to net generation in {ch['share']:.1%} of {ch['rows']:,} operator-hours (EIA-930 consistency)")
        placed = sum(1 for b in grid["bas"] if b["cell"] is not None)
        add("PASS" if placed >= 30 else "WARN", f"M2 {placed} operators have a territory and an anchor on the map")
        add("PASS" if len(grid["hourly"]["interchange"]) >= 50 else "WARN",
            f"M2 {len(grid['hourly']['interchange'])} operator-to-operator flow series")
        add("NOTE", f"M2 grid.json {gsize / 1024:.0f} kB (lazy-loaded with the replay); {grid['vintage']}")

    lenses, lsize = _load("energy_lenses.json")
    if not lenses:
        add("FAIL", "M3 energy_lenses.json missing")
    else:
        for l in lenses["lenses"]:
            problems = []
            if len(l["values"]) != n_cells:
                problems.append("wrong length")
            if not l.get("unit") or not l.get("description"):
                problems.append("unit or description missing")
            if l.get("source") not in src_ids:
                problems.append(f"source {l.get('source')} not in sources.json")
            add("FAIL" if problems else "PASS", f"M3 lens {l['id']}: " + ("; ".join(problems) if problems else
                f"unit '{l['unit']}', source {l['source']}" + (", caveat shown" if l.get("note") else "")))
        add("NOTE", f"M3 energy_lenses.json {lsize / 1024:.0f} kB, {len(lenses['lenses'])} lenses")

    infra, isize = _load("energy_infra.json")
    if not infra:
        add("FAIL", "M4 energy_infra.json missing")
    else:
        add("PASS" if isize <= EAGER_BUDGET else "WARN",
            f"M4 energy_infra.json {isize / 1024:.0f} kB (eager budget {EAGER_BUDGET // 1024} kB)")
        add("PASS" if len(infra["pipes"]["links"]) > 500 else "WARN", f"M4 {len(infra['pipes']['links']):,} pipeline hex links")
        kinds = {}
        for s in infra["sites"]:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        add("NOTE", "M4 sites: " + ", ".join(f"{k} {v}" for k, v in kinds.items()))

    for lvl, msg in out:
        print(f"{lvl:5} {msg}")
    return 1 if any(l == "FAIL" for l, _ in out) else 0
