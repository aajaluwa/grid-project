// Static map layers for the energy data: every power plant (thinned by zoom, never by a size rule), gas and crude
// pipelines on hex links, and energy sites (refineries, gas storage, LNG terminals, data centers).
// All are drawn into the cached static map (render/map.js); nothing animates here.
//
//   makeLayers(model, on, ctx) → [layer, …]
//   model: {plants: [{name, state, fuel, mw, status, year, x, y, cell, ba}], byCell: Map, pipes, sites, version}
//   on:    {plants, planned, retired, pipelines, sites, datacenters} booleans, read live
//
// z slots: pipelines 54 (under roads at 55), plants 64 and energy sites 63 (above city tiles at 60, under the
// map-art sites at 65+), so plants stay readable over cities.
import { fuelColor, fuelName, fmtMW } from "./fuels.js";
import { esc } from "../../format.js";

/** Smallest plant (MW) drawn at a zoom: the map thins plants by zoom so the country view stays legible. */
export function minMWFor(Rpx) {
  return Rpx < 6 ? 400 : Rpx < 10 ? 150 : Rpx < 22 ? 40 : Rpx < 44 ? 5 : 0;
}

/** Marker radius in css px for a plant of `mw` at hex radius `Rpx` (area grows with capacity). */
export function plantRadius(mw, Rpx) {
  const k = 0.05 + 0.004 * Math.min(Rpx, 80);
  return Math.max(1.4, Math.min(16, Math.sqrt(Math.max(mw, 0)) * k));
}

const inVp = (x, y, vp, pad) => x > vp.x0 - pad && x < vp.x1 + pad && y > vp.y0 - pad && y < vp.y1 + pad;

export function makeLayers(m, on, { story = () => false } = {}) {
  const ver = () => `e${m.version}`;
  const statusOn = (p) => (p.status === "operating" ? on.plants : p.status === "planned" ? on.planned : on.retired);

  function visiblePlants(Rpx) {
    const min = minMWFor(Rpx);
    return m.plants.filter((p) => p.mw >= min && statusOn(p));
  }

  const plants = {
    id: "energy-plants", z: 64, minRpx: 0,
    visible: () => (on.plants || on.planned || on.retired) && m.plants.length > 0,
    sig: () => `${ver()}|${on.plants ? 1 : 0}${on.planned ? 1 : 0}${on.retired ? 1 : 0}`,
    draw(ctx, vp, w, o) {
      const s = vp.s;
      const list = visiblePlants(o.Rpx); // sorted largest first by the model, so small plants sit on top
      const alpha = o.lens ? 0.85 : 1;
      for (const p of list) {
        const r = plantRadius(p.mw, o.Rpx) / s;
        if (!inVp(p.x, p.y, vp, r * 2)) continue;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        if (p.status === "operating") {
          ctx.globalAlpha = alpha;
          ctx.fillStyle = fuelColor(p.fuel);
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.lineWidth = Math.max(0.6, Math.min(1.5, r * s * 0.18)) / s;
          ctx.strokeStyle = "rgba(20,16,10,0.75)";
          ctx.stroke();
        } else {
          // planned: hollow ring in the fuel colour; retired: grey ring with a cross
          ctx.lineWidth = Math.max(1, Math.min(2.2, r * s * 0.25)) / s;
          ctx.strokeStyle = p.status === "planned" ? fuelColor(p.fuel) : "rgba(60,60,60,0.85)";
          if (p.status === "planned") ctx.setLineDash([3 / s, 2 / s]);
          ctx.stroke();
          ctx.setLineDash([]);
          if (p.status === "retired") {
            const d = r * 0.6;
            ctx.beginPath();
            ctx.moveTo(p.x - d, p.y - d); ctx.lineTo(p.x + d, p.y + d);
            ctx.moveTo(p.x + d, p.y - d); ctx.lineTo(p.x - d, p.y + d);
            ctx.stroke();
          }
        }
      }
    },
    hit(x, y, Rpx) {
      const s = Rpx / m.w.grid.R;
      const min = minMWFor(Rpx);
      const cell = m.w.grid.cellAt(x, y);
      if (cell < 0) return null;
      let best = null, bestD = Infinity;
      const cells = [cell];
      for (let d = 0; d < 6; d++) cells.push(m.w.grid.neighbour(cell, d));
      for (const c of cells) {
        for (const p of m.byCell.get(c) || []) {
          if (p.mw < min || !statusOn(p)) continue;
          const r = plantRadius(p.mw, Rpx) + 3;
          const dpx = Math.hypot(p.x - x, p.y - y) * s;
          if (dpx <= r && dpx < bestD) { best = p; bestD = dpx; }
        }
      }
      return best ? { kind: "plant", data: best } : null;
    },
    tooltip: (it) => plantTip(it.data, story()),
  };

  const pipelines = {
    id: "energy-pipelines", z: 54, minRpx: 0,
    visible: () => on.pipelines && !!m.pipes,
    sig: ver,
    draw(ctx, vp, w, o) {
      const g = m.w.grid;
      const s = vp.s;
      const styles = m.pipes.classes || [];
      for (let k = 0; k < styles.length; k++) {
        const st = styles[k];
        ctx.strokeStyle = st.color;
        ctx.globalAlpha = o.lens ? 0.6 : 0.85;
        ctx.lineWidth = (st.width_px || 1.6) * Math.min(1.6, Math.max(0.7, o.Rpx / 18)) / s;
        ctx.setLineDash(st.dash ? st.dash.map((v) => v / s) : []);
        ctx.beginPath();
        for (const [a, b, cls] of m.pipes.links) {
          if (cls !== k) continue;
          const ax = g.cx(a), ay = g.cy(a), bx = g.cx(b), by = g.cy(b);
          if (!inVp(ax, ay, vp, 60) && !inVp(bx, by, vp, 60)) continue;
          ctx.moveTo(ax, ay);
          ctx.lineTo(bx, by);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    },
  };

  const SITE_STYLE = {
    refinery: { color: "#6b4a2f", shape: "square" },
    gas_storage: { color: "#5f8fb0", shape: "diamond" },
    lng: { color: "#2f6f9f", shape: "triangle" },
    datacenter: { color: "#8e5cc7", shape: "square" },
  };
  const siteOn = (it) => (it.kind === "datacenter" ? on.datacenters : on.sites);

  function siteList(Rpx) {
    // data centers: one marker per hex (count), so the dense clusters (Northern Virginia) stay readable
    // country view: only hexes with 3+ data centers; region view and closer: every hex that has one
    return m.sites.filter((it) => siteOn(it) && (it.kind !== "datacenter" || (it.count || 1) >= (Rpx < 12 ? 3 : 1)));
  }

  function drawShape(ctx, shape, x, y, r) {
    ctx.beginPath();
    if (shape === "square") ctx.rect(x - r, y - r, 2 * r, 2 * r);
    else if (shape === "diamond") { ctx.moveTo(x, y - r * 1.3); ctx.lineTo(x + r * 1.3, y); ctx.lineTo(x, y + r * 1.3); ctx.lineTo(x - r * 1.3, y); ctx.closePath(); }
    else { ctx.moveTo(x, y - r * 1.3); ctx.lineTo(x + r * 1.2, y + r * 0.9); ctx.lineTo(x - r * 1.2, y + r * 0.9); ctx.closePath(); }
  }

  const sites = {
    id: "energy-sites", z: 63, minRpx: 0,
    visible: () => (on.sites || on.datacenters) && m.sites.length > 0,
    sig: () => `${ver()}|${on.sites ? 1 : 0}${on.datacenters ? 1 : 0}`,
    draw(ctx, vp, w, o) {
      const s = vp.s;
      for (const it of siteList(o.Rpx)) {
        if (!inVp(it.x, it.y, vp, 40)) continue;
        const st = SITE_STYLE[it.kind] || SITE_STYLE.refinery;
        const base = it.kind === "datacenter" ? 1.6 + Math.sqrt(it.count || 1) * 0.8 : 2.8;
        const r = Math.min(8, base * Math.min(1.8, Math.max(0.6, o.Rpx / 16))) / s;
        drawShape(ctx, st.shape, it.x, it.y, r);
        ctx.fillStyle = st.color;
        ctx.fill();
        ctx.lineWidth = 1 / s;
        ctx.strokeStyle = "rgba(255,255,255,0.85)";
        ctx.stroke();
        if (it.kind === "datacenter" && (it.count || 1) > 1 && o.Rpx >= 22) {
          ctx.fillStyle = "#fff";
          ctx.font = `${Math.round(9)}px sans-serif`;
          ctx.save();
          ctx.setTransform(vp.dpr, 0, 0, vp.dpr, 0, 0);
          const [sx, sy] = o.toScreen(it.x, it.y);
          ctx.textAlign = "center";
          ctx.fillText(String(it.count), sx, sy + 3);
          ctx.restore();
        }
      }
    },
    hit(x, y, Rpx) {
      const s = Rpx / m.w.grid.R;
      let best = null, bestD = 10;
      for (const it of siteList(Rpx)) {
        const d = Math.hypot(it.x - x, it.y - y) * s;
        if (d < bestD) { best = it; bestD = d; }
      }
      return best ? { kind: "site", data: best } : null;
    },
    tooltip: (it) => siteTip(it.data, story()),
  };

  return [pipelines, sites, plants];
}

export function plantTip(p, story) {
  const status = p.status === "operating" ? (story ? "Working" : "Operating")
    : p.status === "planned" ? (story ? "Being built" : "Planned")
    : (story ? "Abandoned" : "Retired");
  const yearWord = p.status === "operating" ? "online" : p.status === "planned" ? "expected" : "retired";
  const yr = p.year ? `, ${yearWord} ${p.year}` : "";
  const ba = p.ba ? ` · grid operator ${esc(p.ba)}` : "";
  return `<b>${esc(p.name)}</b><br>${esc(fuelName(p.fuel, story))} · ${esc(fmtMW(p.mw))} net summer capacity<br>`
    + `<span class="muted">${status}${yr} · ${esc(p.state || "")}${ba} · EIA-860M</span>`;
}

const SITE_NAME = {
  refinery: ["Petroleum refinery", "Refinery"],
  gas_storage: ["Natural gas storage", "Gas vault"],
  lng: ["LNG terminal", "LNG harbour"],
  datacenter: ["Data center", "Data citadel"],
};

export function siteTip(it, story) {
  const kind = (SITE_NAME[it.kind] || [it.kind, it.kind])[story ? 1 : 0];
  if (it.kind === "datacenter") {
    const n = it.count || 1;
    const names = (it.names || []).slice(0, 4).map(esc).join(", ");
    return `<b>${n} data center${n === 1 ? "" : "s"}</b> in this hex<br>${names ? `<span class="muted">${names}${n > 4 ? ", …" : ""}</span><br>` : ""}`
      + `<span class="muted">OpenStreetMap (ODbL): crowd-mapped, incomplete; size not shown</span>`;
  }
  const detail = it.detail ? `<br>${esc(it.detail)}` : "";
  return `<b>${esc(it.name || kind)}</b><br>${esc(kind)}${detail}<br><span class="muted">${esc(it.source || "EIA Energy Atlas")}</span>`;
}
