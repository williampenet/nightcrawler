// Personal scoring, computed in the browser only (ADR-0002). Pure functions, no DOM:
// loaded by the page and by `node --test tests/js`.
"use strict";

(function (root) {
  const DISCOVERY_FANS = 20000; // fewer Deezer fans than this = little-known artist
  const MIN_STYLE = 0.15;

  // Same normalisation as the pipeline (artists.norm): no accents, lower case, a-z0-9 only.
  function norm(name) {
    return String(name || "")
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "");
  }

  function parseSeeds(text) {
    const seen = new Set();
    const out = [];
    for (const raw of String(text || "").split(/[\n,;]+/)) {
      const name = raw.trim().replace(/\s+/g, " ");
      const key = norm(name);
      if (name.length >= 2 && name.length <= 60 && key && !seen.has(key)) {
        seen.add(key);
        out.push(name);
      }
    }
    return out.slice(0, 200);
  }

  // Merge whole artist names (from an API) into a seed list without splitting them:
  // "Tyler, The Creator" stays one name.
  function mergeNames(existing, incoming) {
    const seen = new Set();
    const out = [];
    for (const raw of [...(existing || []), ...(incoming || [])]) {
      const name = String(raw || "").trim().replace(/\s+/g, " ");
      const key = norm(name);
      if (name.length >= 2 && name.length <= 60 && key && !seen.has(key)) {
        seen.add(key);
        out.push(name);
      }
    }
    return out.slice(0, 200);
  }

  // profile: { seeds: [{name, tags}], liked: [artistKey], disliked: [artistKey] }
  function buildProfile(state, artists) {
    const seedNames = new Map();
    for (const s of state.seeds || []) seedNames.set(norm(s.name), s.name);
    const liked = new Set(state.liked || []);
    const tagWeights = new Map();
    const add = (tags, w) => {
      for (const t of tags || []) tagWeights.set(t, (tagWeights.get(t) || 0) + w);
    };
    for (const s of state.seeds || []) add(s.tags, 1);
    for (const k of liked) if (artists[k]) add(artists[k].tags, 0.5);
    for (const k of state.disliked || []) if (artists[k]) add(artists[k].tags, -0.5);
    let norm2 = 0;
    for (const w of tagWeights.values()) if (w > 0) norm2 += w * w;
    const disliked = new Set(state.disliked || []);
    return { seedNames, liked, disliked, tagWeights, tagNorm: Math.sqrt(norm2) };
  }

  function isEmpty(profile) {
    return !profile.seedNames.size && !profile.liked.size;
  }

  function styleSimilarity(tags, profile) {
    if (!tags || !tags.length || !profile.tagNorm) return { sim: 0, shared: [] };
    let dot = 0;
    const shared = [];
    for (const t of tags) {
      const w = profile.tagWeights.get(t) || 0;
      dot += w;
      if (w > 0) shared.push(t);
    }
    return { sim: Math.max(0, dot) / (profile.tagNorm * Math.sqrt(tags.length)), shared };
  }

  // Best match among the concert's artists: { score, reason, discovery }
  function scoreConcert(concert, artists, profile) {
    let best = { score: 0, reason: null, discovery: false };
    const consider = (score, reason, artist) => {
      if (score > best.score) {
        // a discovery = a little-known artist reached through a related or style match
        // only for confidently identified artists: a homonym's fan count says nothing (WIP-40)
        const known = artist.confident === true && Number.isInteger(artist.fans);
        best = { score, reason, discovery: known && artist.fans < DISCOVERY_FANS && score <= 0.8 };
      }
    };
    for (const key of concert.artists || []) {
      const a = artists[key];
      if (!a || (profile.disliked && profile.disliked.has(key))) continue; // "pas pour moi" wins
      if (profile.seedNames.has(key)) consider(1, `Tu écoutes ${a.name}`, a);
      if (profile.liked.has(key)) consider(0.9, `Tu as aimé ${a.name}`, a);
      for (const rel of a.related || []) {
        const rk = norm(rel);
        if (profile.seedNames.has(rk)) consider(0.8, `Proche de ${profile.seedNames.get(rk)}`, a);
        else if (profile.liked.has(rk)) consider(0.7, `Proche de ${rel}`, a);
      }
      const { sim, shared } = styleSimilarity(a.tags, profile);
      if (sim >= MIN_STYLE) consider(0.6 * Math.min(1, sim), `Style : ${shared.slice(0, 3).join(", ")}`, a);
    }
    return best;
  }

  // "when" filter on an ISO start, given now (Date) and day keys in the zone's time zone
  function inWhen(mode, start, now, dayKey) {
    if (mode === "all") return true;
    const d = new Date(start);
    const today = dayKey(now);
    if (mode === "tonight") return dayKey(d) === today;
    if (mode === "7d") return d - now <= 7 * 864e5;
    if (mode === "weekend") {
      // from now until Sunday night of this week (Friday to Sunday if before Friday)
      // pure calendar arithmetic on the zone's day keys (no time zone shifts)
      const base = Date.parse(today + "T00:00:00Z");
      const dow = new Date(base).getUTCDay(); // 0 = Sunday
      const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
      const sunday = iso(base + ((7 - dow) % 7) * 864e5);
      const friday = iso(Date.parse(sunday + "T00:00:00Z") - 2 * 864e5);
      const k = dayKey(d);
      return k >= friday && k <= sunday;
    }
    return true;
  }

  // Links of a concert, one per distinct http(s) URL (WIP-42). Older data has no `links`:
  // fall back to the page and ticket fields.
  function concertLinks(c) {
    const given =
      Array.isArray(c.links) && c.links.length
        ? c.links
        : [{ label: "Page", url: c.url }, { label: "Billets", url: c.ticket_url }];
    const out = [];
    for (const l of given) {
      if (!l || typeof l.url !== "string" || !/^https?:\/\//i.test(l.url)) continue;
      if (out.some((x) => x.url === l.url)) continue;
      out.push({ label: String(l.label || "Lien"), url: l.url });
    }
    return out;
  }

  const api = { concertLinks, norm, parseSeeds, mergeNames, buildProfile, isEmpty, scoreConcert, styleSimilarity, inWhen };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
