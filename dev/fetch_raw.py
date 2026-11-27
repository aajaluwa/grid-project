"""Download the raw energy datasets into raw/ (run in GitHub Actions, where every host is reachable).

Standard library only. Each step is independent: a failure is logged to raw/MANIFEST.json and the
others still run. The workflow commits raw/ to the orphan branch `raw`, so the build can run anywhere
that can reach github.com.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

OUT = Path("raw")
UA = "energyciv/0.1 (open data pipeline; github.com/aajaluwa/energyciv)"
MANIFEST: dict = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": {}, "errors": {}}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def get(url: str, data: bytes | None = None, timeout: int = 600, headers: dict | None = None) -> bytes:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - logged and retried
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def save(name: str, body: bytes, url: str, compress: bool = False) -> None:
    path = OUT / (name + (".gz" if compress else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, 6) if compress else body)
    MANIFEST["files"][path.name if path.parent == OUT else str(path.relative_to(OUT))] = {
        "url": url, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
        "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"  saved {path} ({len(body) / 1e6:.1f} MB)")


def step(name):
    def wrap(fn):
        def run():
            print(f"== {name}")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                MANIFEST["errors"][name] = str(exc)[:500]
                print(f"  FAILED: {exc}")
        return run
    return wrap


@step("eia_860m")
def eia_860m():
    base = "https://www.eia.gov/electricity/data/eia860m/"
    found = {}
    try:
        page = get(base).decode("utf-8", "replace")
        for rel, month, year in re.findall(r"((?:archive/)?xls/([a-z]+)_generator(\d{4})\.xlsx)", page, re.I):
            if month.lower() in MONTHS:
                key = (int(year), MONTHS.index(month.lower()))
                if key not in found or found[key].startswith("archive/"):
                    found[key] = rel
    except RuntimeError as exc:
        print(f"  landing page: {exc}")
    y, m = date.today().year, date.today().month - 1
    for _ in range(14):
        found.setdefault((y, m), f"xls/{MONTHS[m]}_generator{y}.xlsx")
        y, m = (y, m - 1) if m > 0 else (y - 1, 11)
    for key in sorted(found, reverse=True):
        rel = found[key]
        for url in (base + rel, base + "archive/" + rel.split("/")[-1] if not rel.startswith("archive/") else None):
            if not url:
                continue
            try:
                body = get(url)
            except RuntimeError:
                continue
            if body[:2] == b"PK":
                save("eia860m.xlsx", body, url)
                MANIFEST["eia860m_month"] = f"{key[0]}-{key[1] + 1:02d}"
                return
            print(f"  {url}: not a workbook")
    raise RuntimeError("no EIA-860M workbook found")


@step("eia_930")
def eia_930():
    base = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/"
    today = date.today()
    halves = []
    y = today.year
    for yr in (y, y - 1):
        halves += [(yr, "Jul_Dec"), (yr, "Jan_Jun")]
    got = 0
    for yr, half in halves:
        if got >= 1:
            break
        for kind in ("BALANCE", "INTERCHANGE"):
            name = f"EIA930_{kind}_{yr}_{half}.csv"
            try:
                body = get(base + name)
            except RuntimeError as exc:
                print(f"  {name}: {exc}")
                break
            if body[:200].lower().lstrip().startswith(b"<"):
                print(f"  {name}: HTML, not CSV")
                break
            save(name, body, base + name, compress=True)
        else:
            got += 1
            MANIFEST["eia930_period"] = f"{yr} {half}"
    if not got:
        raise RuntimeError("no EIA-930 six-month files found")
    try:
        url = "https://www.eia.gov/electricity/930-content/EIA930_Reference_Tables.xlsx"
        save("EIA930_Reference_Tables.xlsx", get(url), url)
    except Exception as exc:  # noqa: BLE001
        MANIFEST["errors"]["eia_930_reference"] = str(exc)[:300]


def arcgis_geojson(service_url: str, layer: int, name: str, offset: float = 0.01, fields: str = "*") -> None:
    q = urllib.parse.urlencode({"where": "1=1", "outFields": fields, "outSR": 4326, "f": "geojson",
                                "maxAllowableOffset": offset, "geometryPrecision": 4,
                                "resultRecordCount": 2000, "resultOffset": 0})
    feats, offset_n = [], 0
    while True:
        params = q.replace("resultOffset=0", f"resultOffset={offset_n}")
        url = f"{service_url}/{layer}/query?{params}"
        d = json.loads(get(url))
        if "error" in d:
            raise RuntimeError(f"{name}: {d['error']}")
        batch = d.get("features", [])
        feats += batch
        if len(batch) < 2000 and not d.get("properties", {}).get("exceededTransferLimit") \
                and not d.get("exceededTransferLimit"):
            break
        offset_n += len(batch)
        if not batch or offset_n > 200000:
            break
    body = json.dumps({"type": "FeatureCollection", "features": feats}).encode()
    save(f"{name}.geojson", body, f"{service_url}/{layer}/query", compress=len(body) > 20e6)


ORGS = ["https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services",
        "https://services2.arcgis.com/vHeD2bHG1G3sW5YZ/arcgis/rest/services",
        "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services"]
KNOWN = {"balancing_authorities": ("https://services7.arcgis.com/FGr1D95XCGALKXqM/ArcGIS/rest/services/"
                                   "Balancing_Authorities/FeatureServer", 255)}
SEARCH = {
    "crude_pipelines": "Crude Oil Pipelines",
    "refineries": "Petroleum Refineries",
    "gas_storage": "Natural Gas Underground Storage",
    "lng": "Liquefied Natural Gas Import Export Terminals",
}
WANT = {
    "balancing_authorities": re.compile(r"^Balancing_Authorit", re.I),
    "gas_pipelines": re.compile(r"NaturalGas.*(Inter|Pipeline)", re.I),
    "crude_pipelines": re.compile(r"Crude.*Pipeline", re.I),
    "refineries": re.compile(r"Refiner", re.I),
    "gas_storage": re.compile(r"(Underground.*Storage|Storage_Fields|NaturalGas_Storage)", re.I),
    "lng": re.compile(r"LNG", re.I),
}


def arcgis_search(title: str) -> str | None:
    """Find a Feature Service by title on ArcGIS Online, preferring EIA- or BTS-owned items."""
    q = urllib.parse.urlencode({"q": f'title:"{title}" AND type:"Feature Service"', "f": "json", "num": 25})
    d = json.loads(get("https://www.arcgis.com/sharing/rest/search?" + q))
    items = d.get("results", [])
    MANIFEST.setdefault("search", {})[title] = [(i.get("title"), i.get("owner"), i.get("url")) for i in items[:8]]
    def score(i):
        owner = (i.get("owner") or "").lower()
        t = (i.get("title") or "").lower()
        return (("eia" in owner) * 4 + ("bts" in owner or "ntad" in t) * 2 + (title.lower() in t) * 3
                + ("eia" in t) + bool(i.get("url")))
    items = [i for i in items if i.get("url")]
    if not items:
        return None
    best = max(items, key=score)
    return best["url"].rstrip("/")


@step("eia_atlas")
def eia_atlas():
    catalog = {}
    for org in ORGS:
        try:
            d = json.loads(get(org + "?f=json"))
            catalog[org] = [s["name"] for s in d.get("services", []) if s.get("type") == "FeatureServer"]
        except Exception as exc:  # noqa: BLE001
            catalog[org] = f"error {exc}"
    save("atlas_catalog.json", json.dumps(catalog, indent=1).encode(), "; ".join(ORGS))
    for key, pat in WANT.items():
        if key in KNOWN:
            svc, layer = KNOWN[key]
            try:
                MANIFEST.setdefault("atlas", {})[key] = {"service": svc, "layer": layer}
                arcgis_geojson(svc, layer, key, offset=0.02)
                continue
            except Exception as exc:  # noqa: BLE001
                MANIFEST["errors"][f"atlas_{key}_known"] = str(exc)[:300]
        hit = None
        for org, names in catalog.items():
            if isinstance(names, list):
                for n in names:
                    if pat.search(n):
                        hit = (org, n)
                        break
            if hit:
                break
        svc = None
        if hit:
            svc = f"{hit[0]}/{hit[1]}/FeatureServer"
        elif key in SEARCH:
            svc = arcgis_search(SEARCH[key])
        if not svc:
            MANIFEST["errors"][f"atlas_{key}"] = "no matching service"
            continue
        n = svc.rstrip("/").split("/")[-2]
        try:
            meta = json.loads(get(svc + "?f=json"))
            layers = meta.get("layers") or []
            layer = layers[0]["id"] if layers else 0
            MANIFEST.setdefault("atlas", {})[key] = {"service": svc, "layer": layer, "name": n}
            arcgis_geojson(svc, layer, key, offset=0.01 if "pipeline" in key else 0.02)
        except Exception as exc:  # noqa: BLE001
            MANIFEST["errors"][f"atlas_{key}"] = str(exc)[:300]


@step("lbnl_queue")
def lbnl_queue():
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    links = []
    for page_url in ("https://emp.lbl.gov/queues",
                     "https://eta-publications.lbl.gov/publications/us-interconnection-queue-data-0"):
        try:
            page = get(page_url, headers=hdr).decode("utf-8", "replace")
        except RuntimeError as exc:
            print(f"  {page_url}: {exc}")
            continue
        links += [urllib.parse.urljoin(page_url, l) for l in re.findall(r'href="([^"]+\.xlsx[^"]*)"', page, re.I)]
    MANIFEST["lbnl_links"] = links[:10]
    if not links:
        raise RuntimeError("no .xlsx link on emp.lbl.gov/queues")
    for link in links:
        body = get(link, headers=hdr)
        if body[:2] == b"PK":
            save("lbnl_queue.xlsx", body, link)
            return
    raise RuntimeError(f"no workbook among {links[:5]}")


@step("osm_data_centers")
def osm_data_centers():
    query = """[out:json][timeout:300];
area["ISO3166-1"="US"][admin_level=2]->.us;
(nwr["telecom"="data_center"](area.us);nwr["building"="data_center"](area.us););
out center tags;"""
    url = "https://overpass-api.de/api/interpreter"
    body = get(url, data=urllib.parse.urlencode({"data": query}).encode(), timeout=400)
    save("osm_data_centers.json", body, url)


@step("world_interim")
def world_interim():
    src = Path("data/interim/_world.json")
    if src.exists():
        shutil.copy(src, OUT / "_world.json")
        MANIFEST["files"]["_world.json"] = {"url": "built by pipeline world", "bytes": src.stat().st_size}
    else:
        raise RuntimeError("data/interim/_world.json missing: the world build failed")


def main(argv):
    OUT.mkdir(exist_ok=True)
    steps = {"eia_860m": eia_860m, "eia_930": eia_930, "eia_atlas": eia_atlas, "lbnl_queue": lbnl_queue,
             "osm_data_centers": osm_data_centers, "world_interim": world_interim}
    for name in (argv or list(steps)):
        steps[name]()
    MANIFEST["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "MANIFEST.json").write_text(json.dumps(MANIFEST, indent=1))
    print(json.dumps(MANIFEST["errors"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
