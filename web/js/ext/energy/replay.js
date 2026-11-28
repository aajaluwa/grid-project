// M2: the grid replay. One turn = one hour of EIA-930 data for every grid operator (balancing authority).
// A docked panel (right) holds the clock, the national mix and the selected operator; a world overlay draws each
// operator as a fuel-mix disc at its territory's anchor, sized by demand, and the power flowing between operators
// as arrows. Opened from the top bar ("Grid replay") or the URL (#replay=<hour index>).
import { fuelColor, fuelName, fmtMW } from "./fuels.js";
import { esc } from "../../format.js";

const MODE = "replay";
const FLOW_MIN_MW = 150;

export function createReplay(app, voice) {
  let grid = null, loading = null;
  let h = 0, playing = false, timer = 0, speed = 250, selected = null, dock = null;
  let anchors = new Map(); // id -> {x, y, ba}
  let flowsByBa = new Map();
  let moments = [];

  const ready = () => !!grid;
  const n = () => grid?.hours?.n || 0;
  const hourDate = (k) => new Date(Date.parse(grid.hours.start_utc) + k * 3600e3);

  function load() {
    if (!loading) {
      loading = app.loadData("grid").then((g) => {
        grid = g;
        if (!g) return null;
        anchors = new Map(g.bas.filter((b) => b.x != null).map((b) => [b.id, b]));
        flowsByBa = new Map();
        for (const f of g.hourly.interchange) {
          for (const id of [f.a, f.b]) {
            if (!flowsByBa.has(id)) flowsByBa.set(id, []);
            flowsByBa.get(id).push(f);
          }
        }
        if (h >= n()) h = n() - 1;
        moments = computeMoments();
        return g;
      });
    }
    return loading;
  }

  // ---- numbers for an hour --------------------------------------------------------------------------------------
  const val = (arr, k) => (arr && arr[k] != null ? arr[k] : null);
  function mixAt(ba, k) {
    const gen = grid.hourly.gen[ba] || {};
    const out = [];
    for (const f of grid.fuels) {
      const v = val(gen[f], k);
      if (v != null && v > 0) out.push([f, v]);
    }
    return out;
  }
  function usAt(k) {
    let demand = 0, reported = 0;
    const mix = new Map();
    for (const [ba, arr] of Object.entries(grid.hourly.demand)) {
      const d = val(arr, k);
      if (d != null) { demand += d; reported++; }
      for (const [f, v] of mixAt(ba, k)) mix.set(f, (mix.get(f) || 0) + v);
    }
    return { demand, reported, mix: [...mix.entries()].sort((a, b) => b[1] - a[1]) };
  }
  function netFlow(ba, k) {
    // positive = exporting
    let net = 0;
    for (const f of flowsByBa.get(ba) || []) {
      const v = val(f.v, k);
      if (v == null) continue;
      net += f.a === ba ? v : -v;
    }
    return net;
  }

  // Moments worth replaying, picked from the data itself (no hand-chosen events).
  function computeMoments() {
    const out = [];
    let best = { peak: [-1, -Infinity], low: [-1, Infinity], solar: [-1, -1], wind: [-1, -1], flow: [-1, 0, null] };
    for (let k = 0; k < n(); k++) {
      const u = usAt(k);
      if (u.reported < 0.85 * Object.keys(grid.hourly.demand).length) continue;
      const tot = u.mix.reduce((a, [, v]) => a + v, 0) || 1;
      const share = (f) => (u.mix.find(([x]) => x === f)?.[1] || 0) / tot;
      if (u.demand > best.peak[1]) best.peak = [k, u.demand];
      if (u.demand < best.low[1]) best.low = [k, u.demand];
      if (share("solar") > best.solar[1]) best.solar = [k, share("solar")];
      if (share("wind") > best.wind[1]) best.wind = [k, share("wind")];
    }
    for (const f of grid.hourly.interchange) {
      f.v.forEach((v, k) => { if (v != null && Math.abs(v) > Math.abs(best.flow[1])) best.flow = [k, v, f]; });
    }
    const pct = (x) => `${(100 * x).toFixed(0)}%`;
    if (best.peak[0] >= 0) out.push({ k: best.peak[0], label: "Peak demand", story: "The hungriest hour", text: fmtMW(best.peak[1]) });
    if (best.low[0] >= 0) out.push({ k: best.low[0], label: "Lowest demand", story: "The quietest hour", text: fmtMW(best.low[1]) });
    if (best.solar[0] >= 0) out.push({ k: best.solar[0], label: "Highest solar share", story: "The sun's finest hour", text: pct(best.solar[1]) });
    if (best.wind[0] >= 0) out.push({ k: best.wind[0], label: "Highest wind share", story: "The wind's finest hour", text: pct(best.wind[1]) });
    if (best.flow[2]) {
      const f = best.flow[2], from = best.flow[1] > 0 ? f.a : f.b, to = best.flow[1] > 0 ? f.b : f.a;
      out.push({ k: best.flow[0], ba: to, label: `Largest flow: ${from} → ${to}`, story: `The great caravan: ${from} → ${to}`, text: fmtMW(Math.abs(best.flow[1])) });
    }
    return out;
  }

  // ---- overlay ----------------------------------------------------------------------------------------------------
  const overlay = {
    id: "energy-replay", z: 40, space: "world", snapshot: true,
    get hidden() { return app.mode !== MODE || !grid; },
    draw(ctx, cam) {
      const s = cam.scale;
      const Rpx = app.w.grid.R * s;
      const zf = Math.max(0.55, Math.min(2.2, Rpx / 10));
      // flows first, under the discs
      for (const f of grid.hourly.interchange) {
        const v = val(f.v, h);
        if (v == null || Math.abs(v) < (Rpx < 10 ? FLOW_MIN_MW * 3 : FLOW_MIN_MW)) continue;
        const A = anchors.get(v > 0 ? f.a : f.b), B = anchors.get(v > 0 ? f.b : f.a);
        if (!A || !B) continue;
        const hot = selected && (f.a === selected || f.b === selected);
        if (selected && !hot) ctx.globalAlpha = 0.25;
        const wpx = Math.max(1, Math.min(9, Math.sqrt(Math.abs(v)) * 0.09 * zf));
        arrow(ctx, A.x, A.y, B.x, B.y, wpx / s, discR(A.id) / s, discR(B.id) / s, hot ? "#fff3c4" : "rgba(255,236,190,0.85)");
        ctx.globalAlpha = 1;
      }
      for (const b of anchors.values()) {
        const r = discR(b.id) / s;
        const mix = mixAt(b.id, h);
        const tot = mix.reduce((a, [, v]) => a + v, 0);
        if (selected && b.id !== selected) ctx.globalAlpha = 0.45;
        let a0 = -Math.PI / 2;
        if (tot > 0) {
          for (const [f, v] of mix) {
            const a1 = a0 + (v / tot) * Math.PI * 2;
            ctx.beginPath();
            ctx.moveTo(b.x, b.y);
            ctx.arc(b.x, b.y, r, a0, a1);
            ctx.closePath();
            ctx.fillStyle = fuelColor(f);
            ctx.fill();
            a0 = a1;
          }
        } else {
          ctx.beginPath();
          ctx.arc(b.x, b.y, r, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(120,120,120,0.6)";
          ctx.fill();
        }
        // ring: blue while exporting, orange while importing
        const net = netFlow(b.id, h);
        ctx.beginPath();
        ctx.arc(b.x, b.y, r, 0, Math.PI * 2);
        ctx.lineWidth = (b.id === selected ? 3 : 1.6) / s;
        ctx.strokeStyle = net > 50 ? "#5fb3ff" : net < -50 ? "#ff9a4a" : "rgba(20,16,10,0.9)";
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      // labels in screen space
      ctx.save();
      const dpr = ctx.getTransform().a / s;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = "600 11px -apple-system, system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const b of anchors.values()) {
        const rpx = discR(b.id);
        if (rpx < 9 && b.id !== selected) continue;
        const [sx, sy] = cam.toScreen(b.x, b.y);
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(10,12,16,0.85)";
        ctx.strokeText(b.id, sx, sy + rpx + 12);
        ctx.fillStyle = "#f4efe2";
        ctx.fillText(b.id, sx, sy + rpx + 12);
      }
      ctx.restore();
    },
  };

  function discR(id) {
    const d = val(grid.hourly.demand[id], h) ?? 0;
    const Rpx = app.w.grid.R * app.cam.scale;
    const zf = Math.max(0.55, Math.min(2.2, Rpx / 10));
    return Math.max(4, Math.min(56, Math.sqrt(Math.max(d, 0)) * 0.11 * zf));
  }

  function arrow(ctx, x0, y0, x1, y1, w, r0, r1, color) {
    const dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy);
    if (L < r0 + r1 + w * 3) return;
    const ux = dx / L, uy = dy / L;
    const sx = x0 + ux * r0, sy = y0 + uy * r0, ex = x1 - ux * (r1 + w * 0.5), ey = y1 - uy * (r1 + w * 0.5);
    const head = Math.max(w * 2.6, 6 / app.cam.scale);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = w;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(ex - ux * head * 0.8, ey - uy * head * 0.8);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - ux * head - uy * head * 0.6, ey - uy * head + ux * head * 0.6);
    ctx.lineTo(ex - ux * head + uy * head * 0.6, ey - uy * head - ux * head * 0.6);
    ctx.closePath();
    ctx.fill();
  }

  // ---- panel ------------------------------------------------------------------------------------------------------
  function fmtTime(k) {
    const d = hourDate(k);
    const utc = d.toLocaleString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
    const et = d.toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", hour12: true });
    return `${utc} UTC <span class="muted">(${et} Eastern)</span>`;
  }

  function mixBar(mix, total) {
    if (!total) return `<div class="en-bar empty"></div>`;
    const story = voice.story();
    const segs = mix.map(([f, v]) => `<span style="flex:${v};background:${fuelColor(f)}" title="${esc(fuelName(f, story))}: ${esc(fmtMW(v))} (${((100 * v) / total).toFixed(1)}%)"></span>`).join("");
    const legend = mix.slice(0, 6).map(([f, v]) => `<li><i style="background:${fuelColor(f)}"></i>${esc(fuelName(f, story))} <b>${((100 * v) / total).toFixed(0)}%</b></li>`).join("");
    return `<div class="en-bar">${segs}</div><ul class="en-legend">${legend}</ul>`;
  }

  function sparkline(arr, k, w = 300, hgt = 46) {
    const vals = arr.filter((v) => v != null);
    if (!vals.length) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const X = (i) => (i / (arr.length - 1)) * w, Y = (v) => hgt - 4 - ((v - lo) / span) * (hgt - 8);
    let d = "";
    arr.forEach((v, i) => { if (v != null) d += `${d ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`; });
    const cur = arr[k];
    const dot = cur != null ? `<circle cx="${X(k)}" cy="${Y(cur)}" r="3.5" fill="#ffd166"/>` : "";
    return `<svg class="en-spark" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" role="img" aria-label="Demand over the replay window">
      <path d="${d}" fill="none" stroke="#9fc7ff" stroke-width="1.5"/><line x1="${X(k)}" x2="${X(k)}" y1="0" y2="${hgt}" stroke="#ffd166" stroke-width="1" opacity="0.6"/>${dot}</svg>
      <div class="en-spark-axis muted"><span>${esc(fmtMW(lo))}</span><span>${esc(fmtMW(hi))}</span></div>`;
  }

  function render() {
    if (!dock) return;
    if (!grid) {
      dock.innerHTML = `<div class="en-panel"><p class="muted">${loading ? "Loading the grid…" : "grid.json is not built."}</p></div>`;
      return;
    }
    const story = voice.story();
    const us = usAt(h);
    const usGen = us.mix.reduce((a, [, v]) => a + v, 0);
    const title = story ? "The Power Council" : "Grid replay";
    const sub = story ? "Every realm's power, turn by turn (one turn = one hour)"
      : "Hourly demand, generation by fuel and flows between grid operators (EIA-930)";
    let detail;
    if (selected && grid.hourly.demand[selected]) {
      const b = grid.bas.find((x) => x.id === selected) || { id: selected, name: selected };
      const d = val(grid.hourly.demand[selected], h);
      const mix = mixAt(selected, h).sort((a, b) => b[1] - a[1]); // largest fuel first, as for the lower 48
      const gen = mix.reduce((a, [, v]) => a + v, 0);
      const net = netFlow(selected, h);
      const flows = (flowsByBa.get(selected) || []).map((f) => {
        const v = val(f.v, h);
        if (v == null) return null;
        const other = f.a === selected ? f.b : f.a;
        const out = f.a === selected ? v : -v;
        return [other, out];
      }).filter(Boolean).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6);
      const flowRows = flows.map(([o, v]) => `<li><button class="en-link" data-ba="${esc(o)}">${esc(o)}</button> ${v >= 0 ? (story ? "receives" : "imports") : (story ? "sends" : "exports")} <b>${esc(fmtMW(Math.abs(v)))}</b> ${v >= 0 ? "from" : "to"} ${esc(selected)}</li>`).join("");
      detail = `<section class="en-sec"><h3>${esc(b.id)} <span class="muted">${esc(b.name)}</span></h3>
        <div class="en-kpis"><div><span>${story ? "Hunger" : "Demand"}</span><b>${esc(fmtMW(d))}</b></div>
        <div><span>${story ? "Output" : "Generation"}</span><b>${esc(fmtMW(gen))}</b></div>
        <div><span>${net >= 0 ? (story ? "Sends out" : "Net export") : (story ? "Brings in" : "Net import")}</span><b>${esc(fmtMW(Math.abs(net)))}</b></div></div>
        ${mixBar(mix, gen)}
        <h4>${story ? "Hunger" : "Demand"} over ${grid.hours.n / 24} days</h4>${sparkline(grid.hourly.demand[selected], h)}
        ${flowRows ? `<h4>${story ? "Trade with neighbours" : "Flows with neighbours"} this hour</h4><ul class="en-list">${flowRows}</ul>` : ""}
        <p class="muted small">${b.region ? esc(b.region) + " · " : ""}${b.hexes} hexes of approximate territory${b.gen_only ? " · generation-only operator" : ""}</p>
        <button class="en-btn" data-clear>${story ? "Back to all realms" : "Show all operators"}</button></section>`;
    } else {
      const top = Object.entries(grid.hourly.demand).map(([id, arr]) => [id, val(arr, h)]).filter(([, v]) => v != null)
        .sort((a, b) => b[1] - a[1]).slice(0, 10);
      const rows = top.map(([id, v]) => {
        const b = anchors.get(id);
        return `<li><button class="en-link" data-ba="${esc(id)}">${esc(id)}</button> <span class="muted">${esc((b?.name || "").slice(0, 34))}</span> <b>${esc(fmtMW(v))}</b></li>`;
      }).join("");
      detail = `<section class="en-sec"><h4>${story ? "Hungriest realms" : "Largest operators by demand"} this hour</h4><ul class="en-list">${rows}</ul>
        <p class="muted small">Click an operator here or on the map for its mix, flows and demand curve.</p></section>`;
    }
    dock.innerHTML = `<div class="en-panel">
      <header class="en-head"><h2>${title}</h2><p class="muted small">${sub}</p>
      <button class="en-close" data-close aria-label="Close the grid replay">✕</button></header>
      <div class="en-clock"><div class="en-time">${fmtTime(h)}</div>
        <div class="en-controls">
          <button class="en-btn" data-step="-24" title="Back one day">⏮</button>
          <button class="en-btn" data-step="-1" title="Back one hour">◀</button>
          <button class="en-btn en-play" data-play title="Play / pause (Space)">${playing ? "⏸" : "▶"}</button>
          <button class="en-btn" data-step="1" title="Forward one hour">▶︎</button>
          <button class="en-btn" data-step="24" title="Forward one day">⏭</button>
          <select class="en-speed" aria-label="Replay speed">
            <option value="500" ${speed === 500 ? "selected" : ""}>Slow</option>
            <option value="250" ${speed === 250 ? "selected" : ""}>Normal</option>
            <option value="90" ${speed === 90 ? "selected" : ""}>Fast</option>
          </select></div>
        <input class="en-slider" type="range" min="0" max="${n() - 1}" value="${h}" aria-label="Hour of the replay">
      </div>
      ${moments.length ? `<section class="en-sec"><h4>${story ? "Legendary hours" : "Moments in this window"}</h4><div class="en-moments">${moments.map((m) =>
        `<button class="en-moment${m.k === h ? " on" : ""}" data-moment="${m.k}" ${m.ba ? `data-mba="${esc(m.ba)}"` : ""}><span>${esc(story ? m.story : m.label)}</span><b>${esc(m.text)}</b></button>`).join("")}</div></section>` : ""}
      <section class="en-sec"><h4>${story ? "The whole realm" : "Lower 48"} this hour</h4>
        <div class="en-kpis"><div><span>${story ? "Hunger" : "Demand"}</span><b>${esc(fmtMW(us.demand))}</b></div>
        <div><span>${story ? "Output" : "Generation"}</span><b>${esc(fmtMW(usGen))}</b></div>
        <div><span>Reporting</span><b>${us.reported} of ${Object.keys(grid.hourly.demand).length}</b></div></div>
        ${mixBar(us.mix, usGen)}</section>
      ${detail}
      <footer class="en-foot muted small">${esc(grid.vintage)}. Discs: fuel mix, sized by demand; ring blue = exporting, orange = importing.
        Arrows: flows ≥ ${FLOW_MIN_MW} MW (≥ ${FLOW_MIN_MW * 3} MW in the country view). Territories are approximate (from EIA-860M plant registrations). Values are as reported to EIA and can be revised.</footer>
    </div>`;
  }

  function onDockClick(e) {
    const t = e.target.closest("button, [data-ba]");
    if (!t) return;
    if (t.hasAttribute("data-close")) app.setMode(null);
    else if (t.hasAttribute("data-play")) toggle();
    else if (t.dataset.step) go(h + Number(t.dataset.step));
    else if (t.dataset.moment) { stop(); h = Number(t.dataset.moment); if (t.dataset.mba) select(t.dataset.mba, true); else go(h); }
    else if (t.dataset.ba) select(t.dataset.ba, true);
    else if (t.hasAttribute("data-clear")) select(null);
  }
  function onDockInput(e) {
    if (e.target.classList.contains("en-slider")) go(Number(e.target.value), true);
    if (e.target.classList.contains("en-speed")) {
      speed = Number(e.target.value);
      if (playing) { stop(); start(); }
    }
  }

  function go(k, fromSlider = false) {
    if (!grid) return;
    h = ((k % n()) + n()) % n();
    if (fromSlider) {
      const el = dock?.querySelector(".en-time");
      if (el) el.innerHTML = fmtTime(h);
      clearTimeout(go._t);
      go._t = setTimeout(render, 120);
    } else render();
    app.requestDraw();
    app.hash?.update?.(); // keep the hour in the URL (#replay=<hour>)
  }
  function start() {
    playing = true;
    timer = setInterval(() => go(h + 1), speed);
    render();
  }
  function stop() {
    playing = false;
    clearInterval(timer);
    timer = 0;
  }
  function toggle() {
    if (playing) { stop(); render(); } else start();
  }

  function select(id, fly = false) {
    selected = id || null;
    if (selected && grid) {
      const cells = [];
      grid.cell_ba.forEach((v, i) => { if (v === selected) cells.push(i); });
      app.setHighlight({ cells, color: "#ffd166", dimOthers: false });
      const b = anchors.get(selected);
      if (fly && b) app.flyTo?.(b.x, b.y, Math.max(app.cam.scale, app.cam.fitScale * 1.6));
    } else app.setHighlight(null);
    render();
    app.requestDraw();
  }

  app.registerOverlay(overlay);
  app.registerMode(MODE, {
    enter() {
      dock = app.ui.dock("right", { width: 380, label: "Grid replay" });
      dock.onclick = onDockClick;
      dock.oninput = onDockInput;
      dock.onchange = onDockInput;
      render();
      load().then(() => { render(); app.requestDraw(); });
    },
    exit() {
      stop();
      app.setHighlight(null);
      selected = null;
      app.ui.undock("right");
      dock = null;
    },
    onEscape() {
      if (selected) { select(null); return true; }
      return false;
    },
  });
  app.on("mapclick", (ev) => {
    if (app.mode !== MODE || !grid) return false;
    const id = ev.cell >= 0 ? grid.cell_ba[ev.cell] : null;
    select(id && grid.hourly.demand[id] ? id : null);
    return true;
  });
  addEventListener("keydown", (e) => {
    if (app.mode !== MODE || e.target.closest?.("input, select, textarea")) return;
    if (e.key === " ") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowRight" && e.shiftKey) go(h + 1);
    else if (e.key === "ArrowLeft" && e.shiftKey) go(h - 1);
  });
  app.hash?.register?.("replay", {
    get: () => (app.mode === MODE ? String(h) : null),
    set: (v) => {
      if (v == null) { if (app.mode === MODE) app.setMode(null); return; }
      h = Math.max(0, Number(v) || 0);
      if (app.mode !== MODE) app.setMode(MODE);
      else load().then(() => go(h));
    },
  });
  voice.onChange(() => render());
  app.ui.addTopButton({
    id: "btn-replay", icon: "⚡", label: "Grid replay", title: "Replay the last two weeks of the grid, hour by hour (EIA-930)",
    onClick: () => app.setMode(app.mode === MODE ? null : MODE),
  });
  return { open: () => app.setMode(MODE), ready, get hour() { return h; } };
}
