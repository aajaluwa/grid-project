# Energy data contracts (grid-project)

The energy build (`python -m energy build --raw RAW`) adds four files to `web/data/` and appends its sources to
`sources.json`. Coordinates, cells and the null rule are exactly as in `DATA_CONTRACTS.md` (map km, odd-r hex cells,
`null` = no data, never 0). Raw inputs come from the `raw` branch, filled by `.github/workflows/fetch-raw.yml`.

## plants.json (lazy, ~1.2 MB)
Every EIA-860M plant: Operating units in service (status OP or SB), every Planned unit, Retired units since 2015,
summed into plants per status. `columns` = `name, state, fuel, mw, status, year, x, y, cell, ba`; `fuel` indexes
`fuels`, `status` indexes `statuses` (`operating`, `planned`, `retired`). `mw` = net summer capacity (nameplate where
blank). `ba` = the plant's main balancing-authority code. Rows sorted by `mw`, descending. `totals[status]` =
`{all_mw, mapped_mw, plants, mapped_plants, by_fuel, mapped_by_fuel}` (all = national incl. AK, HI, PR).

## grid.json (lazy, ~0.8 MB; loaded when the replay opens)
EIA-930 newest six-month files. `bas[]` = `{id, name, region, tz, gen_only, color, cell, x, y, hexes, reports}`;
`cell_ba[cell]` = the operator owning each hex (approximate, from plant registrations) or null.
`hours` = `{start_utc, n, step_s}`: the last 14 days, hourly. `hourly.demand[ba][k]` and `hourly.gen[ba][fuel][k]`
in MW (EIA "Adjusted" values), `hourly.interchange[] = {a, b, v[k]}` (positive = `a` exports to `b`; both sides'
reports averaged). `daily` = whole-period daily means. `check` = share of operator-hours whose fuel mix sums to net
generation within 2%.

## energy_lenses.json (at start; merged into the lens dock by main.js)
Same shape as `lenses.json`. Groups `energy`, `capital`. Lenses: `cap_mw`, `fuel_main` (category), `clean_share`,
`planned_mw`, `retiring_mw`, `retired_mw`, `grid_operator` (category), `queue_gw`, `queue_type` (category),
`datacenters`. `queue` = summary of the LBNL active queue (MW by type, unplaced MW).

## energy_infra.json (after the first frame, < 300 kB)
`pipes = {classes[], links: [[cell a, cell b, class index]]}` (gas interstate, gas intrastate, crude), rasterised
like usaciv's roads. `sites[] = {kind: refinery|gas_storage|lng|datacenter, name, x, y, cell, detail?, count?,
names?, source}`; data centers are one item per hex with a `count`.
