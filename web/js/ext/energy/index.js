// grid-project energy extension: the energy layers (M1, M4), the grid replay (M2) and the Story / Analyst voices
// (M5), all on usaciv's extension API. Data: plants.json (lazy, ~1.2 MB), energy_infra.json (eager after the first
// frame), grid.json (lazy, when the replay opens) and energy_lenses.json (merged into the lens dock by main.js).
import { makeLayers } from "./layers.js";
import { createVoice } from "./voice.js";
import { createReplay } from "./replay.js";
import { FUEL } from "./fuels.js";

const TOGGLES = [
  { id: "plants", label: "Power plants", icon: "⚡", def: true, title: "Every operating plant in EIA-860M, thinned by zoom (sized by capacity, coloured by fuel)" },
  { id: "planned", label: "Planned plants", icon: "🏗️", def: false, title: "Planned units in EIA-860M (dashed rings)" },
  { id: "retired", label: "Retired plants", icon: "🏚️", def: false, title: "Plants retired since 2015 (grey rings with a cross)" },
  { id: "pipelines", label: "Pipelines", icon: "🛢️", def: true, title: "Natural gas (and crude, where published) pipelines from the EIA Energy Atlas, as schematic hex links" },
  { id: "sites", label: "Refineries & gas storage", icon: "🏭", def: true, title: "Petroleum refineries (EIA-820) and underground gas storage (EIA-191)" },
  { id: "datacenters", label: "Data centers", icon: "🖥️", def: true, title: "Data centers mapped in OpenStreetMap, one marker per hex with a count (incomplete)" },
];

export async function install(app) {
  const model = { w: app.w, plants: [], byCell: new Map(), pipes: null, sites: [], version: 0 };
  const on = Object.fromEntries(TOGGLES.map((t) => [t.id, t.def]));
  const voice = createVoice(app);
  const layers = makeLayers(model, on, { story: voice.story });
  for (const l of layers) app.registerStaticLayer(l);
  for (const t of TOGGLES) {
    app.ui.addMapToggle({
      id: t.id, label: t.label, icon: t.icon, title: t.title, default: t.def,
      get: () => on[t.id],
      set: (v) => { on[t.id] = !!v; app.requestDraw(); },
    });
  }

  const bump = () => { model.version++; app.requestDraw(); };


  app.loadData("energy_infra").then((d) => {
    if (!d) return;
    model.pipes = d.pipes;
    model.sites = d.sites || [];
    bump();
  });
  app.loadData("plants").then((d) => {
    if (!d) return;
    app.data.plants = d;
    const fuels = d.fuels.map((f) => f.id);
    for (const f of d.fuels) if (FUEL.has(f.id)) Object.assign(FUEL.get(f.id), { color: f.color });
    const col = Object.fromEntries(d.columns.map((c, i) => [c, i]));
    model.plants = d.rows.map((r) => ({
      name: r[col.name], state: r[col.state], fuel: fuels[r[col.fuel]], mw: r[col.mw], status: d.statuses[r[col.status]],
      year: r[col.year], x: r[col.x], y: r[col.y], cell: r[col.cell], ba: r[col.ba],
    }));
    model.byCell = new Map();
    for (const p of model.plants) {
      if (!model.byCell.has(p.cell)) model.byCell.set(p.cell, []);
      model.byCell.get(p.cell).push(p);
    }
    voice.refresh(); // the "Data <month>" label now knows the inventory month
    bump();
  });

  const replay = createReplay(app, voice);
  app.energy = { model, on, voice, replay };
}
