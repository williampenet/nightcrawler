// "Nouveaux" (WIP-95, PRD FR-6): the concerts added since the listener's last visit, computed in
// this browser only (ADR-0002). It replaces the old weekly watch's memory of reported events.
// A visit is a run of page loads less than VISIT_GAP apart, so a reload does not clear the badges.
// The first visit has no "before": nothing is new. Pure helpers, loaded by the page and by
// `node --test tests/js/*.test.js`. Stored data is untrusted: re-checked on read.
"use strict";

(function (root) {
  const KEY = "nightcrawler.visits";
  const VISIT_GAP = 3 * 3600e3; // ms between two loads that start a new visit
  const MAX_IDS = 3000; // a 60-day window holds ~420 concerts (Pipeline run 37927313110: 417)
  const ID_RE = /^[0-9a-f]{12}$/;

  const ids = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string" && ID_RE.test(x)).slice(0, MAX_IDS) : null);
  const time = (v) => (Number.isSafeInteger(v) && v >= 0 ? v : null);

  // {base: [ids known before this visit], seen: [ids at the last load], at: ms of the last load}
  function parse(raw) {
    let v = raw;
    if (typeof raw === "string") {
      try {
        v = JSON.parse(raw);
      } catch {
        return null;
      }
    }
    if (!v || typeof v !== "object" || Array.isArray(v)) return null;
    const base = ids(v.base);
    const seen = ids(v.seen);
    const at = time(v.at);
    return base && seen && at !== null ? { base, seen, at } : null;
  }

  // One page load. concerts: the current list ({id, aliases}); now: ms.
  // Returns {store (to save), isNew (Set of the current ids added since the last visit)}.
  // A concert merged since (WIP-42) is not new when one of its aliases was known.
  function visit(stored, concerts, now) {
    const list = Array.isArray(concerts) ? concerts : [];
    const current = list.map((c) => c.id).filter((id) => ID_RE.test(id)).slice(0, MAX_IDS);
    const prev = parse(stored);
    if (!prev || now < prev.at) return { store: { base: current, seen: current, at: now }, isNew: new Set() };
    const base = now - prev.at >= VISIT_GAP ? prev.seen : prev.base;
    const known = new Set(base);
    const isNew = new Set();
    for (const c of list) {
      const aliases = Array.isArray(c.aliases) ? c.aliases : [];
      if (!known.has(c.id) && !aliases.some((a) => known.has(a))) isNew.add(c.id);
    }
    return { store: { base, seen: current, at: now }, isNew };
  }

  const api = { KEY, VISIT_GAP, MAX_IDS, parse, visit };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCVisits = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
