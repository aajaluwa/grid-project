// Fuel categories shared by the plant layer, the grid replay and the panels. The pipeline (energy/build.py)
// writes the same ids; plants.json carries its own table, which wins when present.

export const FUELS = [
  { id: "gas", name: "Natural gas", story: "Gas works", color: "#f08a24", clean: false },
  { id: "coal", name: "Coal", story: "Coal works", color: "#5a5a5a", clean: false },
  { id: "nuclear", name: "Nuclear", story: "Reactor", color: "#e8d44d", clean: true },
  { id: "hydro", name: "Hydro", story: "Dam", color: "#3fa7d6", clean: true },
  { id: "wind", name: "Wind", story: "Wind farm", color: "#7fd1c0", clean: true },
  { id: "solar", name: "Solar", story: "Solar field", color: "#ffc93c", clean: true },
  { id: "battery", name: "Battery storage", story: "Battery", color: "#7bc96f", clean: true },
  { id: "pumped_storage", name: "Pumped storage", story: "Reservoir battery", color: "#2f7fb8", clean: true },
  { id: "geothermal", name: "Geothermal", story: "Steam vent", color: "#d9534f", clean: true },
  { id: "oil", name: "Oil", story: "Oil works", color: "#8b5a2b", clean: false },
  { id: "bio", name: "Biomass & waste", story: "Furnace", color: "#7a9a3a", clean: false },
  { id: "other", name: "Other", story: "Other works", color: "#a0a0a0", clean: false },
];

export const FUEL = new Map(FUELS.map((f) => [f.id, f]));

export function fuelColor(id) {
  return FUEL.get(id)?.color || "#a0a0a0";
}

export function fuelName(id, story = false) {
  const f = FUEL.get(id);
  if (!f) return id;
  return story ? f.story : f.name;
}

/** "1,234 MW" / "12.3 GW" */
export function fmtMW(mw) {
  if (mw == null || !Number.isFinite(mw)) return "–";
  if (Math.abs(mw) >= 10000) return `${(mw / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} GW`;
  return `${Math.round(mw).toLocaleString("en-US")} MW`;
}
