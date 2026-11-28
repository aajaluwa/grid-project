"""M5 answer key: the rules match the site's, the formats match the browser's, and the key agrees with the data.

Standard library only. Runs against the built web/data (python -m energy build first).
"""

import json
import unittest
from pathlib import Path

from energy import m5

WEB = Path(__file__).resolve().parents[1] / "web" / "data"


def tiny_grid(demand, gen, start="2026-11-01T00:00Z"):
    n = len(next(iter(demand.values())))
    return {"fuels": ["nuclear", "coal", "gas", "solar"], "hours": {"start_utc": start, "n": n, "step_s": 3600},
            "hourly": {"demand": demand, "gen": gen, "interchange": []}, "bas": []}


class FormatParity(unittest.TestCase):
    """energy/m5.py formats numbers exactly as fuels.js fmtMW() and capital.js gw() do."""

    def test_fmt_mw(self):
        self.assertEqual(m5.fmt_mw(521_212), "521.2 GW")
        self.assertEqual(m5.fmt_mw(58_800), "58.8 GW")
        self.assertEqual(m5.fmt_mw(60_000), "60 GW")
        self.assertEqual(m5.fmt_mw(9_876.4), "9,876 MW")
        self.assertEqual(m5.fmt_mw(150), "150 MW")

    def test_gw(self):
        self.assertEqual(m5.gw(1_865_000), "1,865 GW")
        self.assertEqual(m5.gw(174_400), "174 GW")
        self.assertEqual(m5.gw(55_900), "55.9 GW")
        self.assertEqual(m5.gw(22_000), "22 GW")


class MomentRules(unittest.TestCase):
    """peak_hour() follows replay.js computeMoments(): skip hours with < 85% of operators reporting; first max wins."""

    def test_partial_hours_are_skipped(self):
        demand = {f"B{i}": [100, 100, 100] for i in range(10)}
        demand["B0"] = [100, 100, 5000]
        for i in range(1, 3):
            demand[f"B{i}"][2] = None  # hour 2: 8 of 10 report (80%), so its spike cannot be the peak
        g = tiny_grid(demand, {})
        self.assertEqual(m5.peak_hour(g)[0], 0)

    def test_first_of_equal_peaks_wins(self):
        g = tiny_grid({"A": [10, 30, 30], "B": [10, 10, 10]}, {})
        self.assertEqual(m5.peak_hour(g), (1, 40))

    def test_mix_ignores_charging_and_sorts(self):
        g = tiny_grid({"A": [100]}, {"A": {"gas": [60], "solar": [-5], "nuclear": [40]}})
        self.assertEqual(m5.us_at(g, 0)["mix"], [("gas", 60), ("nuclear", 40)])

    def test_hour_label(self):
        g = tiny_grid({"A": [1, 1]}, {}, start="2026-11-25T00:00Z")
        lab = m5.hour_label(g, 1)
        self.assertEqual(lab["utc"], "Wed, Nov 25, 01:00")
        if lab["eastern"] is not None:
            self.assertEqual(lab["eastern"], "8 PM")


@unittest.skipUnless((WEB / "grid.json").exists() and (WEB / "plants.json").exists(), "energy data not built")
class KeyAgreesWithData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = m5.answer_key(WEB)
        cls.grid = json.loads((WEB / "grid.json").read_text())
        cls.plants = json.loads((WEB / "plants.json").read_text())
        cls.world = json.loads((WEB / "world.json").read_text())

    def test_peak_is_the_maximum_of_eligible_hours(self):
        n_ba = len(self.grid["hourly"]["demand"])
        eligible = [m5.us_at(self.grid, k) for k in range(self.grid["hours"]["n"])]
        eligible = [u["demand"] for u in eligible if u["reported"] >= 0.85 * n_ba]
        self.assertAlmostEqual(self.key["peak"]["demand_mw"], round(max(eligible)), delta=1)

    def test_peak_mix_is_consistent(self):
        p = self.key["peak"]
        self.assertEqual(p["top_fuel"], max(p["mix_pct"], key=p["mix_pct"].get))
        self.assertTrue(95 <= sum(p["mix_pct"].values()) <= 105)  # rounded shares

    def test_capacity_comparison_follows_totals(self):
        c, by = self.key["capacity"], self.plants["totals"]["operating"]["by_fuel"]
        self.assertEqual(c["more"], "solar" if by["solar"] > by["coal"] else "coal")
        self.assertEqual(c["top_fuel"], max(by, key=by.get))

    def test_price_extremes_are_on_the_map(self):
        pr = self.key["price"]
        self.assertGreaterEqual(pr["high"], pr["low"])
        names = [s["name"] for s in self.world["states"]]
        self.assertEqual(names[self.world["owner"][pr["high_cell"]]], pr["high_state"])
        self.assertEqual(names[self.world["owner"][pr["low_cell"]]], pr["low_state"])

    def test_ercot_is_a_reporting_operator(self):
        self.assertIn("ERCO", self.grid["hourly"]["demand"])
        self.assertIsNotNone(self.key["ercot"]["top_fuel"])

    def test_markdown_has_six_questions(self):
        md = m5.to_markdown(self.key)
        self.assertEqual(sum(1 for line in md.splitlines() if line.startswith(("| A", "| N"))), 6)


if __name__ == "__main__":
    unittest.main()
