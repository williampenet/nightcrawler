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

  // Browser state (ADR-0002). Saved state is untrusted (old versions, manual edits):
  // keep only well-typed fields.
  function defaultState() {
    return {
      seeds: [], liked: [], disliked: [], hidden: [], wrong: [],
      // ratings of concerts without an identified artist (WIP-47): concert ids and
      // normalised performer names
      likedConcerts: [], likedNames: [], dislikedNames: [],
      sort: "date", when: "all", style: "", venue: "",
    };
  }

  const NAME_KEY_RE = /^[a-z0-9]{1,100}$/;

  function sanitizeState(raw) {
    const s = defaultState();
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return s;
    const strings = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string") : []);
    if (Array.isArray(raw.seeds)) {
      s.seeds = raw.seeds
        .map((x) => (typeof x === "string" ? { name: x, tags: null } : x))
        .filter((x) => x && typeof x.name === "string")
        .map((x) => ({ name: x.name, tags: Array.isArray(x.tags) ? strings(x.tags) : null }));
    }
    for (const k of ["liked", "disliked", "hidden", "wrong", "likedConcerts"]) s[k] = strings(raw[k]);
    for (const k of ["likedNames", "dislikedNames"]) s[k] = [...new Set(strings(raw[k]).filter((x) => NAME_KEY_RE.test(x)))];
    for (const k of ["sort", "when", "style", "venue"]) if (typeof raw[k] === "string") s[k] = raw[k];
    return s;
  }

  // Normalised performer names of a concert (same form as artist keys), for ratings of
  // concerts without an identified artist (WIP-47).
  function performerKeys(concert) {
    const perf = Array.isArray(concert.performers) ? concert.performers : [];
    return [...new Set(perf.filter((p) => typeof p === "string").map(norm).filter((k) => NAME_KEY_RE.test(k)))].slice(0, 12);
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
    // artists whose "Proche de" / "Style" match the listener reported as wrong (WIP-41)
    const noInfer = new Set(state.wrong || []);
    // names from concerts without an identified artist (WIP-47); a name is an artist key
    // once identified, so it counts for both
    const likedConcerts = new Set(state.likedConcerts || []);
    const likedNames = new Set(state.likedNames || []);
    for (const k of state.dislikedNames || []) disliked.add(k);
    return { seedNames, liked, disliked, noInfer, likedConcerts, likedNames, tagWeights, tagNorm: Math.sqrt(norm2) };
  }

  function isEmpty(profile) {
    return !profile.seedNames.size && !profile.liked.size && !(profile.likedConcerts && profile.likedConcerts.size) && !(profile.likedNames && profile.likedNames.size);
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
    let best = { score: 0, reason: null, discovery: false, artist: null, inferred: false };
    const consider = (score, reason, artist, inferred = false) => {
      if (score > best.score) {
        // a discovery = a little-known artist reached through a related or style match
        // only for confidently identified artists: a homonym's fan count says nothing (WIP-40)
        const known = artist.confident === true && Number.isInteger(artist.fans);
        best = {
          score,
          reason,
          discovery: known && artist.fans < DISCOVERY_FANS && score <= 0.8,
          artist: artist.key || null,
          inferred,
        };
      }
    };
    if (profile.likedConcerts && profile.likedConcerts.has(concert.id)) consider(0.9, "Tu as aimé ce concert", {});
    for (const p of Array.isArray(concert.performers) ? concert.performers : []) {
      const k = norm(p);
      if (profile.likedNames && profile.likedNames.has(k) && !profile.disliked.has(k)) consider(0.9, `Tu as aimé ${p}`, {});
    }
    for (const key of concert.artists || []) {
      const a = artists[key];
      if (!a || (profile.disliked && profile.disliked.has(key))) continue; // "pas pour moi" wins
      if (profile.seedNames.has(key)) consider(1, `Tu écoutes ${a.name}`, a);
      if (profile.liked.has(key) || (profile.likedNames && profile.likedNames.has(key))) consider(0.9, `Tu as aimé ${a.name}`, a);
      if (profile.noInfer && profile.noInfer.has(key)) continue; // reported as a wrong match
      for (const rel of a.related || []) {
        const rk = norm(rel);
        if (profile.seedNames.has(rk)) consider(0.8, `Proche de ${profile.seedNames.get(rk)}`, a, true);
        else if (profile.liked.has(rk)) consider(0.7, `Proche de ${rel}`, a, true);
      }
      const { sim, shared } = styleSimilarity(a.tags, profile);
      if (sim >= MIN_STYLE) consider(0.6 * Math.min(1, sim), `Style : ${shared.slice(0, 3).join(", ")}`, a, true);
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

  // Saved concert ids -> current ids. A merged concert answers to its id and its
  // `aliases` (the ids its listings had alone), so hidden concerts and shared links
  // survive a source appearing or disappearing (WIP-42). Unknown ids are dropped.
  function currentIds(concerts, saved) {
    const byId = new Map(concerts.map((c) => [c.id, c.id])); // a current id wins over an alias
    for (const c of concerts) {
      for (const id of Array.isArray(c.aliases) ? c.aliases : []) {
        if (!byId.has(id)) byId.set(id, c.id);
      }
    }
    return [...new Set(saved.filter((id) => byId.has(id)).map((id) => byId.get(id)))];
  }

  const api = { defaultState, sanitizeState, performerKeys, concertLinks, currentIds, norm, parseSeeds, mergeNames, buildProfile, isEmpty, scoreConcert, styleSimilarity, inWhen };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
