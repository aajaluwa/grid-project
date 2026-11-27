"""Shared helpers for the energy build: the hex world, placing points on it, and writing web/data files.

The energy build reads the raw files that .github/workflows/fetch-raw.yml commits to the `raw` branch
(EIA, LBNL, OpenStreetMap, the EIA Energy Atlas, plus usaciv's `_world` interim) and writes extra files
into web/data. It reuses usaciv's projection and hex grid, so every point lands on the same hexes.
Standard library only, like the rest of the pipeline.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from pathlib import Path

from pipeline.core.geo import MapProjection, Shape, ShapeIndex
from pipeline.core.hexgrid import HexGrid

ROOT = Path(__file__).resolve().parents[1]
WEB_DATA = ROOT / "web" / "data"

# Same 7 sample points as pipeline/build/world.py: the centre plus six points 60% of the way to each corner.
OFFSETS = [(0.0, 0.0)] + [
    (0.6 * 25.0 * math.cos(math.radians(60 * k - 30)), 0.6 * 25.0 * math.sin(math.radians(60 * k - 30)))
    for k in range(6)
]


class World:
    """The hex map as the energy builders need it."""

    def __init__(self, raw: Path):
        d = json.loads((raw / "_world.json").read_text())
        d = d.get("data", d)
        self.grid = HexGrid(**d["grid"])
        self.proj = MapProjection(**d["projection"])
        self.owner: list[int] = d["owner"]
        self.county: list[int] = d["county"]
        self.county_samples: list[list[int]] = d["county_samples"]
        self.geoids: list[str] = d["counties"]
        self.state_fips: list[str] = d["states"]
        web_world = json.loads((WEB_DATA / "world.json").read_text())
        self.states = web_world["states"]  # [{fips, abbr, name}] in owner-index order
        self.abbr = [s["abbr"] for s in self.states]
        self.county_names = [c["name"] for c in web_world["counties"]]
        if web_world["grid"] != d["grid"] or web_world["owner"] != self.owner:
            raise RuntimeError("raw/_world.json and web/data/world.json disagree: rebuild both from the same run")
        self.size = self.grid.size

    def center(self, cell: int) -> tuple[float, float]:
        return self.grid.center(*self.grid.col_row(cell))

    def land_cell(self, x: float, y: float, rings: int = 1) -> int:
        """The US land cell at x/y, else the nearest US land cell within `rings` rings; -1 if none."""
        cell = self.grid.cell_at(x, y)
        if cell < 0:
            return -1
        if self.owner[cell] >= 0:
            return cell
        seen, frontier = {cell}, [cell]
        for _ in range(rings):
            ring = []
            for c in frontier:
                for n in self.grid.neighbours(c):
                    if n not in seen:
                        seen.add(n)
                        ring.append(n)
            land = [n for n in ring if self.owner[n] >= 0]
            if land:
                return min(land, key=lambda n: (self.center(n)[0] - x) ** 2 + (self.center(n)[1] - y) ** 2)
            frontier = ring
        return -1

    def place(self, lon: float, lat: float, rings: int = 1) -> tuple[float, float, int]:
        x, y = self.proj(lon, lat)
        return round(x, 1), round(y, 1), self.land_cell(x, y, rings)

    def assign_polygons(self, shapes: list[Shape]) -> list[int]:
        """Index of the shape holding the majority of each US land cell's 7 sample points (centre breaks ties);
        -1 where no point falls in any shape."""
        index = ShapeIndex(shapes, cell_km=100)
        out = [-1] * self.size
        for i in range(self.size):
            if self.owner[i] < 0:
                continue
            cx, cy = self.center(i)
            hits = [index.locate(cx + dx, cy + dy) for dx, dy in OFFSETS]
            valid = [h for h in hits if h >= 0]
            if not valid:
                continue
            counts = Counter(valid)
            top = max(counts.values())
            tied = [k for k, v in counts.items() if v == top]
            out[i] = hits[0] if hits[0] in tied else tied[0]
        return out

    def fill_nearest(self, values: list[int]) -> list[int]:
        """Give US land cells without a value the value of the nearest cell that has one (breadth-first)."""
        out = list(values)
        frontier = [i for i in range(self.size) if self.owner[i] >= 0 and out[i] >= 0]
        while frontier:
            nxt = []
            for c in frontier:
                for n in self.grid.neighbours(c):
                    if self.owner[n] >= 0 and out[n] < 0:
                        out[n] = out[c]
                        nxt.append(n)
            frontier = nxt
        return out


def shape_from_geojson(geom: dict, proj: MapProjection) -> Shape:
    t, c = geom["type"], geom["coordinates"]
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    return Shape([[proj(x, y) for x, y, *_ in ring] for poly in polys for ring in poly])


def lines_from_geojson(geom: dict) -> list[list[tuple[float, float]]]:
    if not geom:
        return []
    t, c = geom["type"], geom["coordinates"]
    lines = [c] if t == "LineString" else c if t == "MultiLineString" else []
    return [[(x, y) for x, y, *_ in line] for line in lines]


def read_json(path: Path):
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()))
    return json.loads(path.read_text())


def find_raw(raw: Path, name: str) -> Path | None:
    for p in (raw / name, raw / (name + ".gz")):
        if p.exists():
            return p
    return None


def write_web(name: str, obj) -> int:
    path = WEB_DATA / name
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text)
    print(f"  wrote web/data/{name} ({len(text) / 1024:.0f} kB)")
    return len(text)


def manifest(raw: Path) -> dict:
    p = raw / "MANIFEST.json"
    return json.loads(p.read_text()) if p.exists() else {"files": {}, "errors": {}}
