"""M5 browser tests. Two things are checked in a real (headless) browser against the built site:

1. The voice switch: Analyst mode is the default, Story mode relabels the map, the replay panel, the advisor and
   the tooltips, and the voice survives a shared link (#voice=story).
2. The human test (docs: "M5 test plan"): for each of its six questions, the page shows the answer in
   energy/m5.py's key, reached the way a tester would reach it (a replay moment, an operator link, an advisor
   section, a lens tooltip). If a test here fails, a tester cannot pass that question either.

Needs Playwright, which is not a pipeline dependency:
    uv run --with playwright python -m playwright install chromium     # once
    uv run --with playwright python -m unittest discover -s tests/e2e -v
CHROME_PATH=/path/to/chrome uses an installed Chrome instead of Playwright's Chromium. Skipped without Playwright.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import socketserver
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("REQUIRE_E2E"):  # CI: a missing browser must fail the build, not skip the tests
        raise
    sync_playwright = None

from energy import m5

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
BOOT_MS = 45000
IGNORED_CONSOLE = (
    "favicon",  # http.server has no favicon
)


def num(text: str) -> float:
    """First number in a string ("Natural gas 49%" -> 49.0, "1,865 GW" -> 1865.0)."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    return float(m.group(0).replace(",", "")) if m else float("nan")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipUnless((WEB / "data" / "grid.json").exists(), "web/data is not built (python -m energy build)")
class M5Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = m5.answer_key(WEB / "data")
        handler = functools.partial(_Quiet, directory=str(WEB))
        cls.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        cls.httpd.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        args = ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"]
        exe = os.environ.get("CHROME_PATH") or None
        cls.browser = cls.pw.chromium.launch(executable_path=exe, args=args)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 900})
        self.page.set_default_timeout(15000)
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", self._console)

    def _console(self, msg):
        if msg.type == "error" and not any(s in msg.text for s in IGNORED_CONSOLE):
            self.errors.append(f"console.error: {msg.text}")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [], "the page logged errors")

    # ---- helpers ------------------------------------------------------------------------------------------------
    def open(self, hash_: str = ""):
        self.page.goto(self.base + hash_, wait_until="domcontentloaded")
        self.page.wait_for_function("() => window.__app?.energy?.model?.plants?.length > 0", timeout=BOOT_MS)

    def js(self, expr: str, arg=None):
        return self.page.evaluate(expr, arg)

    def toggle_voice(self):
        was_story = self.js("() => document.body.classList.contains('voice-story')")
        self.page.click("#btn-voice")
        self.page.wait_for_function("s => document.body.classList.contains('voice-story') === s", arg=not was_story)

    def yield_labels(self) -> list[str]:
        return self.js("() => window.__app.data.nation.yields.map((y) => y.label)")

    def layers_off(self, *ids):
        for i in ids:
            self.assertTrue(self.js("i => window.__app.ui.setMapToggle(i, false)", i), f"no map toggle {i}")

    def open_replay(self):
        self.open("#replay=0")
        self.page.wait_for_selector(".en-head h2", timeout=BOOT_MS)

    def lower48(self):
        return self.page.locator(".en-sec", has=self.page.locator("h4", has_text=re.compile("Lower 48|whole realm"))).first

    def open_replay_at_peak(self):
        self.open_replay()
        self.page.locator("button.en-moment", has_text=re.compile("Peak demand|hungriest", re.I)).click()
        self.page.wait_for_function("t => document.querySelector('.en-time')?.innerText.includes(t)",
                                    arg=self.key["peak"]["utc"])

    def open_capital(self):
        self.open("#advisor=capital")
        self.page.wait_for_selector(".adv-main .adv-tiles", timeout=BOOT_MS)
        return self.page.locator(".adv-main")

    def adv_section(self, title: str):
        return self.page.locator(".adv-main section.adv-sec", has=self.page.locator("h3", has_text=title)).first

    def fly(self, x: float, y: float, zoom: float = 3.0):
        self.js("([x, y, z]) => window.__app.flyTo(x, y, window.__app.cam.fitScale * z, 60)", [x, y, zoom])
        self.page.wait_for_function("() => !window.__app.cam.flight", timeout=5000)
        self.page.wait_for_timeout(250)

    def hover_world(self, x: float, y: float) -> str:
        """Point the mouse at map-km x/y and return the tooltip's text."""
        sx, sy = self.js("""([x, y]) => { const r = document.getElementById('map').getBoundingClientRect();
            const [a, b] = window.__app.cam.toScreen(x, y); return [a + r.left, b + r.top]; }""", [x, y])
        self.page.mouse.move(sx + 20, sy + 20)
        self.page.mouse.move(sx, sy)
        self.page.wait_for_selector("#tooltip:not([hidden])", timeout=5000)
        return self.page.inner_text("#tooltip")

    # ---- the two voices -----------------------------------------------------------------------------------------
    # Labels are compared with textContent: CSS upper-cases some headings, and innerText returns what is painted.
    def test_default_is_analyst_mode(self):
        self.open()
        self.assertTrue(self.js("() => document.body.classList.contains('voice-analyst')"))
        self.assertEqual(self.page.inner_text("#btn-voice .lbl"), "Analyst mode")
        self.assertEqual(self.page.get_attribute("#btn-voice", "aria-pressed"), "false")
        labels = self.yield_labels()
        self.assertIn("GDP", labels)
        self.assertNotIn("Gold", labels)
        self.assertIn("Analyst mode", self.page.inner_text("#turn"))
        self.assertNotIn("voice=", self.page.url)

    def test_story_mode_relabels_and_is_kept_in_the_url(self):
        self.open()
        self.toggle_voice()
        self.assertIn("Gold", self.yield_labels())
        self.assertEqual(self.page.inner_text("#btn-voice .lbl"), "Story mode")
        self.assertEqual(self.page.get_attribute("#btn-voice", "aria-pressed"), "true")
        self.assertIn("Story mode", self.page.inner_text("#turn"))
        self.page.wait_for_function("() => location.hash.includes('voice=story')", timeout=5000)
        shared = self.page.url[len(self.base):]
        self.page.goto("about:blank")
        self.open(shared)  # a shared link opens in the same voice
        self.assertTrue(self.js("() => document.body.classList.contains('voice-story')"))
        self.assertIn("Gold", self.yield_labels())
        self.toggle_voice()  # and switching back drops it from the URL
        self.page.wait_for_function("() => !location.hash.includes('voice=')", timeout=5000)

    def test_replay_panel_follows_the_voice(self):
        self.open_replay()
        self.assertEqual(self.page.inner_text(".en-head h2"), "Grid replay")
        self.assertIn("Demand", self.lower48().text_content())
        self.assertIn("Moments in this window", self.page.text_content(".en-panel"))
        self.toggle_voice()
        self.page.wait_for_function("() => document.querySelector('.en-head h2')?.innerText === 'The Power Council'")
        self.assertIn("Hunger", self.lower48().text_content())
        self.assertIn("Legendary hours", self.page.text_content(".en-panel"))

    def test_capital_advisor_follows_the_voice(self):
        adv = self.open_capital()
        self.assertIn("Operating capacity", adv.locator(".adv-tiles").text_content())
        self.assertIn("Interconnection queue", adv.text_content())
        self.toggle_voice()
        self.page.wait_for_function(
            "() => document.querySelector('.adv-main .adv-tiles')?.textContent.includes('Power of the realm')")
        self.assertIn("The waiting line", adv.text_content())

    def test_plant_tooltip_follows_the_voice(self):
        self.open()
        self.layers_off("sites", "datacenters", "pipelines")
        # the largest operating plant away from any city marker, so the city tooltip cannot win the hover
        p = self.js("""() => { const a = window.__app, g = a.w.grid, near = new Set();
            for (const c of a.w.cityByCell.keys()) { near.add(c); for (let d = 0; d < 6; d++) near.add(g.neighbour(c, d)); }
            return a.energy.model.plants.filter((p) => p.status === 'operating' && !near.has(p.cell))
              .sort((x, y) => y.mw - x.mw)[0]; }""")
        self.fly(p["x"], p["y"], 4)
        tip = self.hover_world(p["x"], p["y"])
        self.assertIn(p["name"], tip)
        self.assertIn("Operating", tip)
        self.toggle_voice()
        self.page.mouse.move(2, 450)
        self.assertIn("Working", self.hover_world(p["x"], p["y"]))

    # ---- the human test: each answer is on screen where a tester looks -----------------------------------------
    def test_A1_peak_demand_moment(self):
        k = self.key["peak"]
        self.open_replay_at_peak()
        clock = self.page.inner_text(".en-time")
        self.assertIn(f"{k['utc']} UTC", clock)
        self.assertIn(f"{k['eastern']} Eastern", clock)
        self.assertIn(k["demand_text"], self.lower48().locator(".en-kpis").inner_text())
        self.assertEqual(self.js("() => window.__app.energy.replay.hour"), k["hour"])
        # the moment is shareable: the URL now carries its hour
        self.page.wait_for_function("p => location.hash.includes(p)", arg=f"replay={k['hour']}", timeout=5000)

    def test_N1_largest_fuel_at_peak(self):
        k = self.key["peak"]
        self.open_replay_at_peak()
        first = self.lower48().locator(".en-legend li").first.inner_text()
        self.assertIn(k["top_fuel_name"], first)
        self.assertLessEqual(abs(num(first) - k["top_fuel_pct"]), 1)

    def test_N3_texas_operator_and_its_fuel(self):
        k = self.key["ercot"]
        self.open_replay_at_peak()
        self.page.click(f"button.en-link[data-ba='{k['id']}']")
        self.page.wait_for_function("id => document.querySelector('.en-sec h3')?.innerText.startsWith(id)", arg=k["id"])
        detail = self.page.locator(".en-sec", has=self.page.locator("h3")).first
        self.assertIn(k["name"], detail.locator("h3").inner_text())
        self.assertIn(k["demand_text"], detail.locator(".en-kpis").inner_text())
        first = detail.locator(".en-legend li").first.inner_text()
        self.assertIn(k["top_fuel_name"], first)
        self.assertLessEqual(abs(num(first) - k["top_fuel_pct"]), 1)
        # clicking the middle of Texas on the map selects the same operator
        cell = self.js("""() => { const a = window.__app, tx = a.data.states.find((s) => s.name === 'Texas');
            return a.w.grid.cellAt(tx.label.x, tx.label.y); }""")
        grid = json.loads((WEB / "data" / "grid.json").read_text())
        self.assertEqual(grid["cell_ba"][cell], k["id"], "central Texas is mapped to ERCOT")

    def test_A2_interconnection_queue(self):
        k = self.key["queue"]
        self.open_capital()
        sec = self.adv_section("Interconnection queue")
        self.assertIn(f"{k['active_text']} of generation and storage was waiting", sec.inner_text())
        top = sec.locator(".adv-bar").first
        self.assertEqual(top.locator(".lb").inner_text().strip(), k["top_type"])
        self.assertEqual(top.locator(".vl").inner_text().strip(), k["top_type_text"])

    def test_N2_solar_versus_coal(self):
        c = self.key["capacity"]
        self.open_capital()
        sec = self.adv_section("Operating capacity by fuel")
        rows = {}
        for b in sec.locator(".adv-bar").all():
            name = b.locator(".lb").inner_text().split("\n")[0].strip()
            rows[name.split()[0].lower()] = b.locator(".vl").inner_text().strip()
        self.assertEqual(rows.get("solar"), c["solar_text"])
        self.assertEqual(rows.get("coal"), c["coal_text"])
        bigger = "solar" if num(rows["solar"]) > num(rows["coal"]) else "coal"
        self.assertEqual(bigger, c["more"])

    def test_A3_highest_household_price(self):
        k = self.key["price"]
        self.open("#lens=price_res")
        self.page.wait_for_selector("#legend:not([hidden])")
        self.layers_off("plants", "sites", "datacenters", "pipelines")
        # a hex of the same state, well inside it and away from city markers
        cell = self.js("""hc => { const a = window.__app, own = a.w.raw.owner, g = a.w.grid;
            const cities = [...a.w.cityByCell.keys()].map((c) => [g.cx(c), g.cy(c)]);
            let best = hc, bestD = -1;
            for (let i = 0; i < own.length; i++) {
              if (own[i] !== own[hc]) continue;
              let inside = true; for (let d = 0; d < 6; d++) { const j = g.neighbour(i, d); if (j < 0 || own[j] !== own[hc]) inside = false; }
              if (!inside) continue;
              const x = g.cx(i), y = g.cy(i); let dmin = Infinity;
              for (const [cx, cy] of cities) dmin = Math.min(dmin, Math.hypot(cx - x, cy - y));
              if (dmin > bestD) { bestD = dmin; best = i; } }
            return best; }""", k["high_cell"])
        x, y = self.js("c => [window.__app.w.grid.cx(c), window.__app.w.grid.cy(c)]", cell)
        self.fly(x, y, 3)
        tip = self.hover_world(x, y)
        self.assertIn(k["high_state"], tip)
        self.assertIn("Household power price:", tip)
        shown = num(tip.split("Household power price:")[1])
        self.assertAlmostEqual(shown, k["high"], delta=0.005, msg="the tooltip shows the price to the cent")
        # and the precision separates the top state from the runner-up (a tester compares hovers)
        lens = self.js("() => { const l = window.__app.lensById.get('price_res'); return [l.decimals, l.description]; }")
        self.assertEqual(lens[0], 2)
        self.assertIn(k["month"], lens[1])

    # ---- sources and robustness ---------------------------------------------------------------------------------
    def test_panels_cite_their_sources(self):
        self.open_replay()
        self.assertIn("EIA-930", self.page.inner_text(".en-head"))
        self.assertTrue(self.page.inner_text(".en-foot").strip())
        adv = self.open_capital()
        self.page.wait_for_selector(".adv-main .adv-sources")
        src = adv.locator(".adv-sources").inner_text()
        self.assertIn("860M", src)
        self.assertIn("Queued Up", src)

    def test_views_load_without_errors_in_both_voices(self):
        views = ["", "#lens=fuel_main", "#lens=grid_operator", "#lens=queue_gw", "#lens=datacenters", "#lens=price_res",
                 "#replay=100", "#advisor=capital", "#advisor=energy", "#advisor=council"]
        for v in views:
            for voice in ("", "voice=story"):
                h = v + (("&" if v else "#") + voice if voice else "")
                with self.subTest(view=h or "(home)"):
                    self.open(h)
                    self.page.wait_for_timeout(700)
                    self.assertEqual(self.errors, [], h)


if __name__ == "__main__":
    unittest.main()
