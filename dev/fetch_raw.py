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
        try:
            d = json.loads(get(url))
        except RuntimeError:
            simple = urllib.parse.urlencode({"where": "1=1", "outFields": fields, "outSR": 4326, "f": "geojson",
                                             "resultOffset": offset_n})
            d = json.loads(get(f"{service_url}/{layer}/query?{simple}"))
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
EIA_ORG = "https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services"
# EIA Energy Atlas layers that its public service listing hides but that answer anonymous queries. Balancing-authority
# boundaries are no longer public (token required): territories are derived from EIA-860M plant registrations instead.
CRUDE_CANDIDATES = [
    f"{EIA_ORG}/CrudeOil_Pipelines_US_EIA/FeatureServer",
    "https://services6.arcgis.com/LUfLUpH7tPVKQsyI/arcgis/rest/services/Crude_Oil_Pipelines_US_/FeatureServer",
    "https://services5.arcgis.com/vNzamREXvX2WcX6d/arcgis/rest/services/EIA_Crude_Oil_Pipeline/FeatureServer",
    "https://services1.arcgis.com/LjEDDFoKcMteM7QH/arcgis/rest/services/Crude_Oil_Pipelines/FeatureServer",
]
KNOWN = {
    # EIA's own copies answer HTTP 400 to anonymous queries; these ArcGIS Online re-hosts of the same EIA-820 and
    # EIA-191 layers answer. A failed download keeps the last good file (the workflow restores raw/ first).
    "refineries": ("https://services.arcgis.com/8df8p0NlLFEShl0r/arcgis/rest/services/"
                   "Petroleum_Refineries_US_EIA__1544220956852457142/FeatureServer", 0),
    "gas_storage": ("https://services1.arcgis.com/ajkPMiuiqxF9xEgP/arcgis/rest/services/"
                    "Natural_Gas_Underground_Storage_US_EIA/FeatureServer", 0),
}
SEARCH = {
    "refineries": "Petroleum Refineries",
    "gas_storage": "Natural Gas Underground Storage",
    "lng": "LNG Import Export Terminals",
}
WANT = {
    "gas_pipelines": re.compile(r"NaturalGas.*(Inter|Pipeline)", re.I),
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
        return (("FGr1D95XCGALKXqM" in (i.get("url") or "")) * 20 + ("eia" in owner) * 4 + ("bts" in owner or "ntad" in t) * 2 + (title.lower() in t) * 3
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
                if layer is None:
                    meta = json.loads(get(svc + "?f=json"))
                    layer = (meta.get("layers") or [{"id": 0}])[0]["id"]
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


@step("crude_pipelines")
def crude_pipelines():
    for svc in CRUDE_CANDIDATES:
        try:
            meta = json.loads(get(svc + "?f=json"))
            layer = (meta.get("layers") or [{"id": 0}])[0]["id"]
            arcgis_geojson(svc, layer, "crude_try", offset=0.01)
            d = json.loads((OUT / "crude_try.geojson").read_text())
            if len(d["features"]) >= 50:
                (OUT / "crude_try.geojson").rename(OUT / "crude_pipelines.geojson")
                MANIFEST["files"]["crude_pipelines.geojson"] = MANIFEST["files"].pop("crude_try.geojson")
                MANIFEST.setdefault("atlas", {})["crude_pipelines"] = {"service": svc, "layer": layer}
                return
            print(f"  {svc}: only {len(d['features'])} features")
        except Exception as exc:  # noqa: BLE001
            print(f"  {svc}: {exc}")
    (OUT / "crude_try.geojson").unlink(missing_ok=True)
    MANIFEST["files"].pop("crude_try.geojson", None)
    raise RuntimeError("no national crude pipeline layer answered")


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
    last = None
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"):
        try:
            body = get(url, data=urllib.parse.urlencode({"data": query}).encode(), timeout=400)
            if body.lstrip().startswith(b"{") and b'"elements"' in body[:2000]:
                save("osm_data_centers.json", body, url)
                return
            last = f"{url}: not JSON"
        except RuntimeError as exc:
            last = exc
    raise RuntimeError(str(last))


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
    prev = OUT / "MANIFEST.json"
    if prev.exists():
        try:
            MANIFEST["files"].update(json.loads(prev.read_text()).get("files", {}))
            for k in ("eia860m_month", "eia930_period", "atlas"):
                MANIFEST.setdefault(k, json.loads(prev.read_text()).get(k))
        except ValueError:
            pass
    steps = {"eia_860m": eia_860m, "eia_930": eia_930, "eia_atlas": eia_atlas, "crude_pipelines": crude_pipelines, "lbnl_queue": lbnl_queue,
             "osm_data_centers": osm_data_centers, "world_interim": world_interim}
    for name in (argv or list(steps)):
        steps[name]()
    MANIFEST["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "MANIFEST.json").write_text(json.dumps(MANIFEST, indent=1))
    print(json.dumps(MANIFEST["errors"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
