// M5: two voices for one map. Analyst mode (the default) uses plain labels; Story mode uses the strategy-game
// vocabulary. The voice lives in the URL (#voice=story) so a shared link opens in the same voice.
//
//   createVoice(app) → {story() → bool, set(story), onChange(fn)}
import { renderTopbar } from "../../ui/topbar.js";

// usaciv's yields, renamed for Analyst mode (what each one actually counts).
const PLAIN = {
  food: "Farm jobs", production: "Industry jobs", gold: "GDP", science: "Tech & science jobs", culture: "Arts jobs",
};

export function createVoice(app) {
  const nation = app.data.nation;
  const game = Object.fromEntries((nation?.yields || []).map((y) => [y.key, y.label]));
  let story = false;
  const fns = [];

  function apply() {
    document.body.classList.toggle("voice-story", story);
    document.body.classList.toggle("voice-analyst", !story);
    for (const y of nation?.yields || []) y.label = story ? game[y.key] : PLAIN[y.key] || game[y.key];
    try {
      renderTopbar(nation);
    } catch (err) {
      console.warn("voice: top bar re-render failed", err);
    }
    const turn = document.getElementById("turn");
    if (turn) {
      const asOf = app.data.plants?.as_of || "";
      turn.innerHTML = story
        ? `Turn <b>${asOf || nation.year}</b> · Story mode`
        : `Data <b>${asOf || nation.year}</b> · Analyst mode`;
    }
    if (btn) {
      btn.querySelector(".lbl").textContent = story ? "Story mode" : "Analyst mode";
      btn.title = story ? "Story mode: game vocabulary. Click for plain labels (Analyst mode)."
        : "Analyst mode: plain labels. Click for the game vocabulary (Story mode).";
      btn.setAttribute("aria-pressed", String(story));
    }
    for (const fn of fns) {
      try { fn(story); } catch (err) { console.error("voice listener failed", err); }
    }
    app.requestDraw();
  }

  const btn = app.ui.addTopButton({
    id: "btn-voice", icon: "🎭", label: "Analyst mode",
    title: "Switch between Analyst mode (plain labels) and Story mode (game vocabulary)",
    onClick: () => set(!story),
  });

  function set(v) {
    if (!!v === story) return;
    story = !!v;
    apply();
    app.hash?.soon?.();
  }

  app.hash?.register?.("voice", {
    get: () => (story ? "story" : null),
    set: (v) => { story = v === "story"; apply(); },
  });
  apply();
  return { story: () => story, set, refresh: apply, onChange: (fn) => fns.push(fn) };
}
