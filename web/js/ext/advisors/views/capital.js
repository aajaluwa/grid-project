// Grid & Capital advisor (grid-project, M5): the whole fleet from EIA-860M (not just the big sites), what is being
// built and retired, and what is waiting in the interconnection queue (LBNL), each linked to its lens on the map.
import { esc } from "../core/util.js";
import { barList } from "../core/charts.js";
import { section, tiles, lensButton, sourcesHtml } from "../core/view.js";

const gw = (mw) => (mw == null ? "–" : `${(mw / 1000).toLocaleString("en-US", { maximumFractionDigits: mw >= 100000 ? 0 : 1 })} GW`);

export function mount(ctx) {
  const { app, data, el } = ctx;
  const story = () => !!app.energy?.voice?.story();
  el.innerHTML = `<p class="adv-empty pad">Loading the plant inventory…</p>`;
  let alive = true;
  const draw = () => Promise.all([app.loadData("plants"), app.loadData("energy_lenses")]).then(([plants, energyLenses]) => {
    if (!alive) return;
    el.innerHTML = "";
    if (!plants) {
      el.innerHTML = `<p class="adv-empty pad">plants.json is not built.</p>`;
      return;
    }
    const queue = energyLenses?.queue || null;
    const op = plants.totals.operating, pl = plants.totals.planned, rt = plants.totals.retired;
    const fuelById = new Map(plants.fuels.map((f) => [f.id, f]));
    const S = story();

    const head = el.appendChild(document.createElement("div"));
    head.innerHTML = tiles([
      { label: S ? "Power of the realm" : "Operating capacity", value: gw(op.all_mw), sub: `${op.plants.toLocaleString("en-US")} plants, ${plants.as_of}` },
      { label: S ? "Being built" : "Planned additions", value: gw(pl.all_mw), sub: `${pl.plants.toLocaleString("en-US")} plants` },
      { label: S ? "Abandoned since 2015" : "Retired since 2015", value: gw(rt.all_mw), sub: `${rt.plants.toLocaleString("en-US")} plants` },
    ]) + `<p class="adv-callout">${esc(plants.rule)}</p>`;

    const mix = section(el, {
      title: S ? "What powers the realm" : "Operating capacity by fuel",
      kicker: "EIA-860M, every generator in service",
      note: "Nameplate-style capacity (net summer MW), not energy produced: a solar or wind MW yields far fewer MWh a year than a nuclear or gas MW.",
    });
    barList(mix, {
      ariaLabel: "Operating capacity by fuel",
      rows: Object.entries(op.by_fuel).map(([f, v]) => ({
        key: f, label: fuelById.get(f)?.name || f, value: v, color: fuelById.get(f)?.color || "#999", text: gw(v),
        sub: `${((100 * v) / op.all_mw).toFixed(1)}%`,
      })),
    });
    const b1 = lensButton(app, "fuel_main", "Show the main fuel of every hex");
    if (b1) mix.appendChild(b1);

    const build = section(el, {
      title: S ? "Builders at work" : "Planned additions by fuel",
      kicker: "EIA-860M Planned sheet",
      note: "Every planning stage, from early proposals to units under construction; some will slip or be cancelled.",
    });
    barList(build, {
      ariaLabel: "Planned capacity by fuel",
      rows: Object.entries(pl.by_fuel).map(([f, v]) => ({ key: f, label: fuelById.get(f)?.name || f, value: v, color: fuelById.get(f)?.color || "#999", text: gw(v) })),
    });
    const b2 = lensButton(app, "planned_mw", "Map planned additions");
    if (b2) build.appendChild(b2);

    const ret = section(el, {
      title: S ? "Works scheduled to close" : "Planned retirements by year",
      kicker: "Operating units with a planned retirement year",
      note: "What owners have told EIA they plan to retire; plans change, and many retirements have been postponed.",
    });
    const years = Object.entries(plants.retiring_by_year || {}).filter(([y]) => +y >= 2026 && +y <= 2040);
    barList(ret, { ariaLabel: "Planned retirements by year", rows: years.map(([y, v]) => ({ key: y, label: y, value: v, color: "#b8432f", text: gw(v) })) });
    const b3 = lensButton(app, "retiring_mw", "Map planned retirements");
    if (b3) ret.appendChild(b3);

    if (queue) {
      const q = section(el, {
        title: S ? "The waiting line" : "Interconnection queue",
        kicker: "LBNL Queued Up, active requests at the end of 2025",
        note: `${gw(queue.active_mw)} of generation and storage was waiting to connect, by primary technology. Most queued capacity is never built.`,
      });
      barList(q, {
        ariaLabel: "Active queue by technology",
        rows: Object.entries(queue.by_type_mw).slice(0, 10).map(([t, v]) => ({ key: t, label: t, value: v, color: "#2f8fbf", text: gw(v) })),
      });
      const b4 = lensButton(app, "queue_gw", "Map the queue by county");
      if (b4) q.appendChild(b4);
    }

    const foot = el.appendChild(document.createElement("div"));
    foot.innerHTML = sourcesHtml(data, ["eia_860m_full", "lbnl_queue"], [
      "Capacity is not generation. For what actually ran, hour by hour, open the Grid replay.",
      "Planned and queued capacity overstate what will be built.",
    ]);
  });
  draw();
  app.energy?.voice?.onChange(() => { if (alive) draw(); });
  return { destroy() { alive = false; } };
}
