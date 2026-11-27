"""Energy build:  python -m energy build --raw RAW_DIR      (RAW_DIR = a checkout of the `raw` branch's raw/)
                python -m energy check

Writes web/data/plants.json, grid.json, energy_lenses.json, energy_infra.json, and adds the energy sources to
sources.json so the Almanac cites them. `check` prints the milestone gates (PASS / NOTE / WARN / FAIL; FAIL exits 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import WEB_DATA, World, manifest, write_web

SOURCES = {
    "eia_860m_full": ("Preliminary Monthly Electric Generator Inventory (EIA-860M), all sheets",
                      "U.S. Energy Information Administration", "https://www.eia.gov/electricity/data/eia860m/",
                      "Public domain (U.S. Government work)", ["eia860m.xlsx"],
                      ["Every power plant (operating, planned, retired since 2015)", "Capacity lenses",
                       "Grid-operator territories"]),
    "eia_930": ("Hourly Electric Grid Monitor (Form EIA-930), six-month files", "U.S. Energy Information Administration",
                "https://www.eia.gov/electricity/gridmonitor/", "Public domain (U.S. Government work)",
                ["EIA930_BALANCE_", "EIA930_INTERCHANGE_", "EIA930_Reference_Tables.xlsx"],
                ["Grid replay: hourly demand, generation by fuel, flows between grid operators"]),
    "lbnl_queue": ("Queued Up: interconnection queue data (2026 edition, through 2025)",
                   "Lawrence Berkeley National Laboratory and GridTracker / Interconnection.fyi",
                   "https://emp.lbl.gov/queues", "CC BY 4.0 (credit Lawrence Berkeley National Laboratory)",
                   ["lbnl_queue.xlsx"], ["Interconnection queue lenses"]),
    "eia_gas_pipelines": ("Natural gas interstate and intrastate pipelines", "U.S. Energy Information Administration",
                          "https://atlas.eia.gov/", "Public domain (U.S. Government work)", ["gas_pipelines.geojson"],
                          ["Gas pipelines on the map"]),
    "eia_crude_pipelines": ("Crude oil pipelines (EIA data via an ArcGIS Online re-host)", "U.S. Energy Information Administration", "https://atlas.eia.gov/",
                            "Public domain (U.S. Government work)", ["crude_pipelines.geojson"], ["Crude pipelines"]),
    "eia_refineries": ("Petroleum refineries (EIA-820, via an ArcGIS Online re-host)", "U.S. Energy Information Administration",
                       "https://atlas.eia.gov/", "Public domain (U.S. Government work)", ["refineries.geojson"],
                       ["Refinery sites"]),
    "eia_gas_storage": ("Natural gas underground storage (EIA-191, via an ArcGIS Online re-host)", "U.S. Energy Information Administration",
                        "https://atlas.eia.gov/", "Public domain (U.S. Government work)", ["gas_storage.geojson"],
                        ["Gas storage sites"]),
    "eia_lng": ("LNG import and export terminals", "U.S. Energy Information Administration", "https://atlas.eia.gov/",
                "Public domain (U.S. Government work)", ["lng.geojson"], ["LNG terminals"]),
    "osm_data_centers": ("Data centers in OpenStreetMap", "OpenStreetMap contributors (via the Overpass API)",
                         "https://www.openstreetmap.org/", "ODbL 1.0: © OpenStreetMap contributors",
                         ["osm_data_centers.json"], ["Data-center lens and sites"]),
}


def merge_sources(man: dict, used: set, vintages: dict):
    path = WEB_DATA / "sources.json"
    cat = json.loads(path.read_text())
    cat = [s for s in cat if s["id"] not in SOURCES]
    for sid, (title, pub, home, lic, files, used_for) in SOURCES.items():
        if sid not in used:
            continue
        entries = [{"file": f, "url": e.get("url"), "retrieved": e.get("retrieved"), "sha256": e.get("sha256"),
                    "bytes": e.get("bytes")} for f, e in man.get("files", {}).items()
                   if any(f.startswith(p) for p in files)]
        cat.append({"id": sid, "title": title, "publisher": pub, "homepage": home, "license": lic,
                    "description": title, "used_for": used_for, "vintage": vintages.get(sid, ""), "files": entries})
    path.write_text(json.dumps(cat, indent=1, ensure_ascii=False))
    print(f"  sources.json: {len(cat)} sources")


def build(raw: Path):
    from . import grid as gridmod, infra, lenses, plants

    man = manifest(raw)
    world = World(raw)
    used, vint = set(), {}
    print("== plants (M1)")
    pl = plants.build(world, raw)
    write_web("plants.json", pl["plants"])
    used.add("eia_860m_full")
    vint["eia_860m_full"] = pl["plants"]["vintage"]
    energy_lenses = plants_l = lenses.plant_lenses(world, pl["per_cell"], pl["retiring_cell"])

    print("== grid (M2)")
    g = gridmod.build(world, raw, pl["ba_cap_cell"])
    write_web("grid.json", g["grid"])
    used.add("eia_930")
    vint["eia_930"] = g["grid"]["vintage"]
    energy_lenses = energy_lenses + [lenses.ba_lens(world, g["cell_ba"], g["codes"], g["grid"]["bas"])]

    print("== capital and constraints (M3)")
    q = lenses.queue(world, raw)
    queue_summary = None
    if q:
        energy_lenses += q[0]
        queue_summary = q[1]
        used.add("lbnl_queue")
        vint["lbnl_queue"] = "Queued Up 2026 edition (data through 2025)"

    print("== oil, gas and sites (M4)")
    pipes, psrc = infra.pipelines(world, raw)
    site_list, ssrc = infra.sites(world, raw)
    used |= set(psrc) | set(ssrc)
    if any(s["kind"] == "datacenter" for s in site_list):
        energy_lenses.append(lenses.datacenter_lens(world, site_list))
        vint["osm_data_centers"] = f"OpenStreetMap, retrieved {man.get('files', {}).get('osm_data_centers.json', {}).get('retrieved', '')[:10]}"
    write_web("energy_infra.json", {"version": 1, "sources": sorted(psrc + ssrc), "pipes": pipes, "sites": site_list,
                                    "rules": {"pipelines": "EIA pipeline layers rasterised onto hex links like roads "
                                              "(schematic, not survey-grade).",
                                              "datacenters": "OpenStreetMap sites tagged telecom=data_center or "
                                              "building=data_center, one marker per hex with a count."}})

    groups = [{"id": "energy", "name": "Energy"}, {"id": "capital", "name": "Capital & constraints"}]
    write_web("energy_lenses.json", {"vintage": pl["plants"]["vintage"], "groups": groups, "lenses": energy_lenses,
                                     "queue": queue_summary})
    merge_sources(man, used, vint)


def check() -> int:
    from .check import run
    return run()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m energy")
    ap.add_argument("command", choices=["build", "check"])
    ap.add_argument("--raw", default="raw", help="directory holding the raw files (the raw branch's raw/)")
    a = ap.parse_args(argv)
    if a.command == "build":
        build(Path(a.raw))
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
