// The judge's sections for the home page (WIP-86, ADR-0007 §4–5): pure helpers + GET /verdicts.
// The response is untrusted (model output relayed by the feedback function): only 12-hex ids,
// known sections and verdicts, an integer confidence 0–100 and a reason of 240 characters at
// most are kept; the page sets the reason with textContent. Personal data: never logged.
// Loaded by the page and by `node --test tests/js/*.test.js`.
"use strict";

(function (root) {
  const KEY = "nightcrawler.verdicts"; // localStorage: the last copy, shown at once on load
  const ID_RE = /^[0-9a-f]{12}$/;
  const SECTIONS = ["ne_pas_rater", "pour_toi", "decouvertes", "tout_voir"];
  const VERDICTS = ["must_see", "for_you", "discovery", "no"];
  // the section follows the verdict, as the store's CHECK (migration 003, judge.section): a
  // discovery under the confidence bar stays in "tout_voir"
  const SECTIONS_OF = {
    must_see: ["ne_pas_rater"],
    for_you: ["pour_toi"],
    discovery: ["decouvertes", "tout_voir"],
    no: ["tout_voir"],
  };
  const MAX_REASON = 240; // characters, as judge.SCHEMA (Python len counts code points)

  // The verdicts URL next to the feedback URL ("https://x/" -> "https://x/verdicts").
  function verdictsUrl(feedbackUrl) {
    const u = new URL(feedbackUrl);
    u.pathname = u.pathname.replace(/\/?$/, "/verdicts");
    return u.href;
  }

  // {generated_at, verdicts: {id: {section, verdict, confidence, reason}}} with every entry
  // that breaks the contract dropped, or null when the body itself is not usable.
  function parse(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return null;
    const raw = body.verdicts;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const verdicts = Object.create(null);
    for (const id of Object.keys(raw)) {
      const v = raw[id];
      if (!ID_RE.test(id) || !v || typeof v !== "object") continue;
      if (!VERDICTS.includes(v.verdict) || !SECTIONS_OF[v.verdict].includes(v.section)) continue;
      if (!Number.isInteger(v.confidence) || v.confidence < 0 || v.confidence > 100) continue;
      if (typeof v.reason !== "string" || !v.reason.trim() || [...v.reason].length > MAX_REASON) continue;
      verdicts[id] = { section: v.section, verdict: v.verdict, confidence: v.confidence, reason: v.reason.trim() };
    }
    const at = typeof body.generated_at === "string" && body.generated_at.length <= 40 ? body.generated_at : null;
    return { generated_at: at, verdicts };
  }

  const hasVerdicts = (data) => Boolean(data && data.verdicts && Object.keys(data.verdicts).length);

  // {status: "ok", data} | {status: "unauthorized" | "error"}
  async function pull(url, token, fetch) {
    try {
      const res = await fetch(url, { method: "GET", headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (res.status === 401) return { status: "unauthorized" };
      if (!res.ok) return { status: "error" }; // 403, 503 (store down), 404 (function not updated yet)
      const data = parse(await res.json());
      return data ? { status: "ok", data } : { status: "error" };
    } catch {
      return { status: "error" }; // offline, cold start timeout, invalid JSON
    }
  }

  // After a read (pure, as profile.afterPull). got: pull's answer; shown: the verdicts on
  // screen (the local copy, or null). show: the verdicts to render (null: rule-based tiers);
  // store: "save" (write show as the local copy), "clear" (remove it) or "keep" (leave it).
  // An error (offline, cold start, store down) keeps the copy: it is there to hide a cold
  // start (ADR-0007 §4). A refused key drops it: back to the rule-based tiers.
  function afterPull(got, shown) {
    if (got && got.status === "ok") return { show: got.data, store: "save" };
    if (got && got.status === "unauthorized") return { show: null, store: "clear" };
    return { show: shown || null, store: "keep" };
  }

  // The local copy: re-checked on read (storage is untrusted too); blocked storage = no copy.
  function load(storage) {
    try {
      return parse(JSON.parse(storage.getItem(KEY) || "null"));
    } catch {
      return null;
    }
  }

  function save(storage, data) {
    try {
      if (data) storage.setItem(KEY, JSON.stringify(data));
      else storage.removeItem(KEY);
      return true;
    } catch {
      return false; // private mode or quota: the page keeps the copy in memory only
    }
  }

  // The concert's verdict: its own id first, then a saved alias (sources merged, WIP-42) that is
  // not another concert's current id (a current id wins over an alias, as S.currentIds).
  function verdictFor(c, verdicts, currentIds) {
    const get = (id) => (typeof id === "string" && Object.prototype.hasOwnProperty.call(verdicts, id) ? verdicts[id] : null);
    if (get(c.id)) return get(c.id);
    for (const id of Array.isArray(c.aliases) ? c.aliases : []) {
      if (!currentIds.has(id) && get(id)) return get(id);
    }
    return null;
  }

  // Scored items ({c, m}, in the list's order) split into the four home sections. Each item
  // gets v (the verdict) or unjudged: true. A concert without a verdict is put in "pour_toi",
  // marked unjudged; the home leaves it out of « Pour toi » (homePicks, WIP-102).
  // isKnown(item) (WIP-88): a concert of an artist the listener listens to or has liked goes to
  // "À ne pas rater" whatever the judge says (a deterministic rule, like a followed artist).
  function sectionsFor(items, verdicts, isKnown) {
    const list = Array.isArray(items) ? items : [];
    const known = verdicts || {};
    const currentIds = new Set(list.map((x) => (x.c || x).id));
    const out = { ne_pas_rater: [], pour_toi: [], decouvertes: [], tout_voir: [] };
    for (const x of list) {
      const item = x.c ? x : { c: x };
      const v = verdictFor(item.c, known, currentIds);
      if (typeof isKnown === "function" && isKnown(item)) out.ne_pas_rater.push({ ...item, v, known: true });
      else if (v && SECTIONS.includes(v.section)) out[v.section].push({ ...item, v });
      else out.pour_toi.push({ ...item, unjudged: true });
    }
    return out;
  }

  // The home's « Pour toi » (PM, 2026-10-09, WIP-102; amends ADR-0007 §5): only the concerts that
  // very probably match, i.e. « À ne pas rater » (judge or known-artist rule) and the judge's
  // « Pour toi ». Not judged yet and Découvertes stay in « Tout ». sec: {must, forYou} lists of
  // items ({c, unjudged?}). Returns {must, picks}: Sets of concert ids.
  function homePicks(sec) {
    const must = new Set((sec.must || []).map((x) => x.c.id));
    const picks = new Set(must);
    for (const x of sec.forYou || []) if (!x.unjudged) picks.add(x.c.id);
    return { must, picks };
  }

  const api = { homePicks, KEY, SECTIONS, VERDICTS, MAX_REASON, verdictsUrl, parse, hasVerdicts, pull, afterPull, load, save, sectionsFor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCVerdicts = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
