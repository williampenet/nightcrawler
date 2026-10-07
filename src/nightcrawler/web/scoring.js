// Personal scoring, computed in the browser only (ADR-0002). Pure functions, no DOM:
// loaded by the page and by `node --test tests/js`.
"use strict";

(function (root) {
  const DISCOVERY_FANS = 20000; // fewer Deezer fans than this = little-known artist
  const MIN_STYLE = 0.15;
  // Tiers of the default view (WIP-53). Provisional values: they will be tuned by the
  // taste eval (WIP-52).
  const SURE_MIN = 0.9; // direct match: artist listened to (1), liked artist / concert / name (0.9)
  const DISCOVER_MAX = 10; // at most this many inferred matches ("Proche de", "Style") shown

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
      // "Mon goût en mots" (WIP-73): the written taste profile and its edit time (ms, for the
      // last-writer-wins sync merge in profile.js)
      tasteText: "", tasteTextAt: 0,
    };
  }

  // Written taste (WIP-73): at most MAX_TASTE_TEXT UTF-16 code units (what the textarea's
  // maxlength and the counter count), no NUL (PostgreSQL's jsonb rejects \u0000:
  // https://www.postgresql.org/docs/current/datatype-json.html) and no lone surrogate (not
  // encodable as UTF-8 by the function), so the function always accepts it.
  const MAX_TASTE_TEXT = 4000;
  function cleanTasteText(v) {
    if (typeof v !== "string") return "";
    // a pair cut in two by the cap is dropped first; any other lone surrogate becomes U+FFFD
    const t = v.replace(/\u0000/g, "").slice(0, MAX_TASTE_TEXT).replace(/[\ud800-\udbff]$/, "");
    return typeof t.toWellFormed === "function" ? t.toWellFormed() : t;
  }
  const cleanTime = (v) => (Number.isSafeInteger(v) && v >= 0 ? v : 0);

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
    s.tasteText = cleanTasteText(raw.tasteText);
    s.tasteTextAt = cleanTime(raw.tasteTextAt);
    return s;
  }

  // Normalised performer names of a concert (same form as artist keys), for ratings of
  // concerts without an identified artist (WIP-47).
  function performerKeys(concert) {
    const perf = Array.isArray(concert.performers) ? concert.performers : [];
    return [...new Set(perf.filter((p) => typeof p === "string").map(norm).filter((k) => NAME_KEY_RE.test(k)))].slice(0, 12);
  }

  // "J'aime" is pressed for a concert whose artists are all liked, or which was liked by
  // id before its artists were identified (WIP-47).
  function isLiked(state, concert) {
    const keys = concert.artists || [];
    return (keys.length > 0 && keys.every((k) => (state.liked || []).includes(k))) || (state.likedConcerts || []).includes(concert.id);
  }

  // Applies "like" (a toggle) or "dislike" to the state. Pure: returns the new rating
  // fields and what the feedback function is sent ({kind, keys}). Concerts without an
  // identified artist are rated by id and performer names, sent with no artist key.
  // concerts: the current list, so unliking keeps the names another liked concert uses.
  function rate(state, concert, kind, concerts) {
    const keys = concert.artists || [];
    const names = [...new Set([...performerKeys(concert), ...keys])];
    const toggle = (list, values, on) => {
      const set = new Set(list || []);
      for (const v of values) on ? set.add(v) : set.delete(v);
      return [...set];
    };
    const s = {
      liked: [...(state.liked || [])],
      disliked: [...(state.disliked || [])],
      hidden: [...(state.hidden || [])],
      likedConcerts: [...(state.likedConcerts || [])],
      likedNames: [...(state.likedNames || [])],
      dislikedNames: [...(state.dislikedNames || [])],
    };
    const on = kind === "like" && !isLiked(state, concert); // a second click undoes
    if (on) {
      if (keys.length) s.liked = toggle(s.liked, keys, true);
      else {
        s.likedConcerts = toggle(s.likedConcerts, [concert.id], true);
        s.likedNames = toggle(s.likedNames, names, true);
      }
      s.disliked = toggle(s.disliked, keys, false);
      s.dislikedNames = toggle(s.dislikedNames, names, false);
    } else {
      s.liked = toggle(s.liked, keys, false);
      // its saved aliases too, or the next load would add the current id back (WIP-59)
      s.likedConcerts = toggle(s.likedConcerts, [concert.id, ...(Array.isArray(concert.aliases) ? concert.aliases : [])], false);
      // names still used by another liked concert stay liked
      const kept = new Set();
      if (kind === "like") {
        const byId = new Map((concerts || []).map((c) => [c.id, c]));
        for (const id of s.likedConcerts) {
          const other = byId.get(id);
          if (other) for (const k of [...performerKeys(other), ...(other.artists || [])]) kept.add(k);
        }
      }
      s.likedNames = toggle(s.likedNames, names.filter((k) => !kept.has(k)), false);
      if (kind === "dislike") {
        if (keys.length) s.disliked = toggle(s.disliked, keys, true);
        else s.dislikedNames = toggle(s.dislikedNames, names, true);
        s.hidden = toggle(s.hidden, [concert.id], true);
      }
    }
    return { state: s, send: { kind: kind === "dislike" ? "dislike" : on ? "like" : "unlike", keys } };
  }

  // "À trier" mode (WIP-73): concerts to rate, drawn at random so the ratings are not limited
  // to what the current ranking surfaces. A candidate is upcoming (its day, in the zone's time
  // zone, is today or later), not liked (isLiked), not hidden (aliases resolved), has no
  // disliked artist or performer name, and was not shown in this session (seen: ids skipped or
  // rated). Pure: dayKey(Date) -> "YYYY-MM-DD" in the zone, as for inWhen.
  function sortCandidates(concerts, state, seen, now, dayKey) {
    const list = Array.isArray(concerts) ? concerts : [];
    const hidden = new Set(currentIds(list, state.hidden || []));
    const disliked = new Set([...(state.disliked || []), ...(state.dislikedNames || [])]);
    const done = seen instanceof Set ? seen : new Set(seen || []);
    const today = dayKey(now);
    return list.filter((c) => {
      if (!c || done.has(c.id) || hidden.has(c.id) || isLiked(state, c)) return false;
      if ([...(c.artists || []), ...performerKeys(c)].some((k) => disliked.has(k))) return false;
      const start = new Date(c.start);
      return !Number.isNaN(start.getTime()) && dayKey(start) >= today;
    });
  }

  // One item drawn uniformly with rng() in [0, 1) (Math.random by default), or null.
  function pickRandom(list, rng = Math.random) {
    if (!Array.isArray(list) || !list.length) return null;
    const r = Number(rng());
    const u = Number.isFinite(r) ? Math.min(Math.max(r, 0), 1 - Number.EPSILON) : 0;
    return list[Math.floor(u * list.length)];
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
    // Names from concerts without an identified artist (WIP-47). A stored name and an
    // artist key are the same string by construction (both are norm(name): see artists.norm
    // in the pipeline), so a liked or disliked name also applies to the artist once the
    // pipeline identifies it, and to any homonym with the same normalised name.
    const likedConcerts = new Set(state.likedConcerts || []);
    const likedNames = new Set(state.likedNames || []);
    for (const k of state.dislikedNames || []) disliked.add(k);
    return { seedNames, liked, disliked, noInfer, likedConcerts, likedNames, tagWeights, tagNorm: Math.sqrt(norm2) };
  }

  // concerts: today's list. Saved concert ids are kept when absent (WIP-59), so a liked id
  // counts only if its concert is in today's data: old ids alone do not switch on the tiers.
  function isEmpty(profile, concerts) {
    const ids = profile.likedConcerts || new Set();
    const likedToday = concerts ? concerts.some((c) => ids.has(c.id)) : ids.size > 0;
    return !profile.seedNames.size && !profile.liked.size && !likedToday && !(profile.likedNames && profile.likedNames.size);
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
    // a liked concert counts unless one of its artists, identified on a later run, is disliked
    const vetoed = (concert.artists || []).some((k) => profile.disliked && profile.disliked.has(k));
    if (!vetoed && profile.likedConcerts && profile.likedConcerts.has(concert.id)) consider(0.9, "Tu as aimé ce concert", {});
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

  // Splits scored concerts ([{c, m}], m from scoreConcert) into the default view's tiers
  // (WIP-53): sure = direct matches (score >= SURE_MIN and not inferred), by date;
  // discover = the best other matches (score > 0: "Proche de", "Style"), by score then
  // date, at most DISCOVER_MAX; rest = everything else, in input order.
  // Pure: the input array is not changed.
  function tiers(scored) {
    const byDate = (a, b) => String(a.c.start).localeCompare(String(b.c.start));
    const list = Array.isArray(scored) ? scored : [];
    const isSure = (x) => x.m.score >= SURE_MIN && !x.m.inferred; // a guess is never "sûr"
    const sure = list.filter(isSure).sort(byDate);
    const discover = list
      .filter((x) => x.m.score > 0 && !isSure(x))
      .sort((a, b) => b.m.score - a.m.score || byDate(a, b))
      .slice(0, DISCOVER_MAX);
    const picked = new Set([...sure, ...discover]);
    return { sure, discover, rest: list.filter((x) => !picked.has(x)) };
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
  // survive a source appearing or disappearing (WIP-42). Unknown ids are dropped: for
  // display only, never to rewrite the saved lists (see keepIds, WIP-59).
  function currentIds(concerts, saved) {
    const byId = new Map(concerts.map((c) => [c.id, c.id])); // a current id wins over an alias
    for (const c of concerts) {
      for (const id of Array.isArray(c.aliases) ? c.aliases : []) {
        if (!byId.has(id)) byId.set(id, c.id);
      }
    }
    return [...new Set(saved.filter((id) => byId.has(id)).map((id) => byId.get(id)))];
  }

  // Saved concert ids (hidden, likedConcerts) as they are kept and synced (WIP-59). Never
  // pruned because an id is absent from today's data: a concert can be missing for a day
  // (source failure, id change, past). A saved alias stays and its current id is added, so
  // the plain `c.id` checks see it. Only the oldest ids beyond MAX_IDS are dropped (the
  // lists are in insertion order). Display resolves current ids with currentIds.
  const MAX_IDS = 500;
  function keepIds(concerts, saved) {
    const kept = [...new Set(saved)];
    // Alias recency: the added current id goes to the end, so the cap treats it as the
    // newest entry while the saved alias keeps its older place and is dropped first. The
    // rating then survives as long as possible under its current id.
    for (const id of currentIds(concerts, kept)) if (!kept.includes(id)) kept.push(id);
    return kept.slice(-MAX_IDS);
  }

  const api = { SURE_MIN, DISCOVER_MAX, MAX_IDS, MAX_TASTE_TEXT, cleanTasteText, sortCandidates, pickRandom, tiers, defaultState, sanitizeState, performerKeys, isLiked, rate, concertLinks, currentIds, keepIds, norm, parseSeeds, mergeNames, buildProfile, isEmpty, scoreConcert, styleSimilarity, inWhen };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
