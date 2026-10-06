// Taste eval runner (WIP-52): how well scoring.js ranks the listener's own ratings.
// Reads {state, feedback, concerts, artists} as JSON on stdin and prints aggregated JSON
// only (counts and rates: no id, name or title). Method: docs/TASTE_EVAL.md.
// Scoring is never reimplemented here: every score comes from scoring.js.
"use strict";

const S = require("../../src/nightcrawler/web/scoring.js");

const SURE = 0.9; // "Tu as aimé …" and "Tu écoutes …" (scoreConcert)
const Z95 = 1.959963984540054; // two-sided 95 % normal quantile
const RATINGS = new Set(["like", "unlike", "dislike"]); // "wrong" rates the match, not the taste
const LISTS = ["liked", "disliked", "likedNames", "dislikedNames", "likedConcerts", "hidden"];

// Normalised names a rating of this concert touches, as rate() computes them.
function concertNames(c) {
  return [...new Set([...S.performerKeys(c), ...(c.artists || [])])];
}

// The state as the page holds it once the data is loaded: sanitised, saved ids mapped to
// current ids (app.js does the same with currentIds on load).
function pageState(raw, concerts) {
  const s = S.sanitizeState(raw);
  s.hidden = S.currentIds(concerts, s.hidden);
  s.likedConcerts = S.currentIds(concerts, s.likedConcerts);
  return s;
}

// id or alias -> current id (a current id wins over an alias, as in currentIds)
function idMap(concerts) {
  const m = new Map(concerts.map((c) => [c.id, c.id]));
  for (const c of concerts) for (const a of Array.isArray(c.aliases) ? c.aliases : []) if (!m.has(a)) m.set(a, c.id);
  return m;
}

// Labels {id -> "liked" | "disliked"} of current concerts, and where they come from.
// 1. state, concert-level evidence only: id in likedConcerts (and not hidden) -> liked;
//    id in hidden with every artist key in `disliked` (or, without an identified artist,
//    every performer name in `dislikedNames`) -> disliked. isLiked() through artist keys
//    alone is not a label: liking one concert of an artist lights up all of them.
// 2. feedback history (oldest first): the latest like / unlike / dislike of a concert wins,
//    if the state still shows it: a like only while isLiked(), a dislike only while the
//    concert is hidden. Otherwise it is stale (undone through a sibling concert of the same
//    artist, or by "Tout effacer"): no label.
function buildLabels(state, feedback, concerts) {
  const out = new Map();
  const source = new Map();
  const likedIds = new Set(state.likedConcerts);
  const hidden = new Set(state.hidden);
  const disliked = new Set(state.disliked);
  const dislikedNames = new Set(state.dislikedNames);
  for (const c of concerts) {
    const keys = c.artists || [];
    if (likedIds.has(c.id) && !hidden.has(c.id)) out.set(c.id, "liked");
    else if (hidden.has(c.id) && !likedIds.has(c.id)) {
      const names = keys.length ? keys : concertNames(c);
      const set = keys.length ? disliked : dislikedNames;
      if (names.length && names.every((k) => set.has(k))) out.set(c.id, "disliked");
    }
    if (out.has(c.id)) source.set(c.id, "state");
  }
  const ids = idMap(concerts);
  const byId = new Map(concerts.map((c) => [c.id, c]));
  const last = new Map();
  let pastEvents = 0;
  // "Pas pour moi" accounting (WIP-55): one row per artist key of a click, so concerts count
  const dislikes = { rows: 0, unpublished: new Set(), published: new Set(), ambiguous: new Set() };
  for (const ev of Array.isArray(feedback) ? feedback : []) {
    if (!ev || !RATINGS.has(ev.kind)) continue;
    const id = ids.get(ev.concert_id);
    if (ev.kind === "dislike") {
      dislikes.rows += 1;
      (id ? dislikes.published : dislikes.unpublished).add(id || ev.concert_id);
    }
    if (!id) {
      pastEvents += 1; // concert no longer published (past, or dropped)
      continue;
    }
    last.set(id, ev.kind);
  }
  let unliked = 0;
  let stale = 0;
  const staleLikedNames = new Set();
  for (const [id, kind] of last) {
    const holds = kind === "like" ? S.isLiked(state, byId.get(id)) : kind === "dislike" && hidden.has(id);
    if (!holds) {
      if (kind === "unlike") {
        if (out.has(id)) unliked += 1;
      } else stale += 1;
      if (kind === "like") for (const k of concertNames(byId.get(id))) staleLikedNames.add(k);
      out.delete(id);
      source.delete(id);
      continue;
    }
    out.set(id, kind === "like" ? "liked" : "disliked");
    source.set(id, "feedback");
  }
  // A dislike sharing an artist or name with a stale like is ambiguous: either it undid that
  // like (dislike through a sibling concert: without it the like would count) or a reset
  // did (then it would not). The history cannot tell them apart: no label, counted.
  let ambiguous = 0;
  for (const [id, label] of out) {
    if (label === "disliked" && concertNames(byId.get(id)).some((k) => staleLikedNames.has(k))) {
      out.delete(id);
      source.delete(id);
      ambiguous += 1;
      dislikes.ambiguous.add(id);
    }
  }
  return { labels: out, source, pastEvents, unliked, stale, ambiguous, last, hidden, dislikes };
}

// Why each concert with a "Pas pour moi" in the history did or did not become a label.
// Counts only, one per concert: published ids are already resolved through the site's
// aliases; unpublished ones are resolved through the store (input.stored_concerts:
// {id or alias: {id, date: "YYYY-MM-DD"}}), then split by date against input.today.
function dislikeReport(built, input, savedHidden) {
  const { dislikes: d, labels, last, hidden } = built;
  const raw = input.stored_concerts;
  const stored = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const resolve = (id) => (stored[id] && typeof stored[id].id === "string" ? stored[id].id : id);
  const unpublished = new Map(); // resolved id -> date or null
  for (const id of d.unpublished) {
    const day = stored[id] && typeof stored[id].date === "string" ? stored[id].date : null;
    unpublished.set(resolve(id), day || unpublished.get(resolve(id)) || null);
  }
  const out = {
    rows: d.rows,
    concerts: d.published.size + unpublished.size,
    labelled: 0,
    rated_again: 0, // the latest rating of that concert is a like or an unlike
    not_hidden: 0, // latest is the dislike, but the profile's `hidden` no longer holds it
    ambiguous: 0, // shares an artist or name with a stale like (see buildLabels)
    unpublished_past: 0,
    unpublished_upcoming: 0, // stored with a date from today on, but not in the site data
    unpublished_unknown: 0, // id not in the store (or no store dates given)
    hidden_in_profile: savedHidden,
    hidden_published: hidden.size,
  };
  for (const id of d.published) {
    if (labels.get(id) === "disliked") out.labelled += 1;
    else if (last.get(id) !== "dislike") out.rated_again += 1;
    else if (!hidden.has(id)) out.not_hidden += 1;
    else if (d.ambiguous.has(id)) out.ambiguous += 1;
  }
  const today = typeof input.today === "string" ? input.today : null;
  for (const day of unpublished.values()) {
    if (!day || !today) out.unpublished_unknown += 1;
    else if (day < today) out.unpublished_past += 1;
    else out.unpublished_upcoming += 1;
  }
  return out;
}

// What rate() writes for this rating, per list: what counts as "produced by" a concert.
function produced(c, label) {
  const keys = c.artists || [];
  if (label === "liked") return keys.length ? { liked: keys } : { likedConcerts: [c.id], likedNames: concertNames(c) };
  return { ...(keys.length ? { disliked: keys } : { dislikedNames: concertNames(c) }), hidden: [c.id] };
}

// What leave-one-out removes for this concert: broader than produced(), so a value saved by
// an older version of the page (e.g. a name liked before its artist was identified) goes too.
function removal(c, label) {
  const keys = c.artists || [];
  const names = concertNames(c);
  return label === "liked"
    ? { liked: keys, likedNames: names, likedConcerts: [c.id] }
    : { disliked: keys, dislikedNames: names, hidden: [c.id] };
}

// Per list, how many labelled concerts produced each value.
function producers(labelled) {
  const counts = Object.fromEntries(LISTS.map((k) => [k, new Map()]));
  for (const [c, label] of labelled) {
    for (const [list, values] of Object.entries(produced(c, label))) {
      for (const v of new Set(values)) counts[list].set(v, (counts[list].get(v) || 0) + 1);
    }
  }
  return counts;
}

// The state without what this concert's own label put there; a value another labelled
// concert also produced stays.
function withoutOwnLabel(state, c, label, counts) {
  const s = { ...state };
  const own = produced(c, label);
  for (const [list, values] of Object.entries(removal(c, label))) {
    const mine = new Set(own[list] || []);
    const others = (v) => (counts[list].get(v) || 0) - (mine.has(v) ? 1 : 0);
    const drop = new Set(values.filter((v) => others(v) <= 0));
    s[list] = (state[list] || []).filter((v) => !drop.has(v));
  }
  return s;
}

const round = (x) => (x === null ? null : Math.round(x * 1000) / 1000);

// Wilson score interval (Wilson 1927, doi:10.1080/01621459.1927.10502953).
function wilson(k, n, z = Z95) {
  if (!n) return null;
  const p = k / n;
  const z2 = z * z;
  const den = 1 + z2 / n;
  const centre = (p + z2 / (2 * n)) / den;
  const half = (z / den) * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return [round(Math.max(0, centre - half)), round(Math.min(1, centre + half))];
}

function tierOf(score) {
  if (score >= SURE) return "sure";
  return score > 0 ? "inferred" : "none";
}

// Per label: {id, label, score} with the leave-one-out score. Internal (tests): ids never
// leave the runner, evaluate() prints aggregates only.
function scoreLabels(input) {
  const inp = input && typeof input === "object" ? input : {};
  const concerts = (Array.isArray(inp.concerts) ? inp.concerts : []).filter((c) => c && typeof c.id === "string");
  const artists = inp.artists && typeof inp.artists === "object" && !Array.isArray(inp.artists) ? inp.artists : {};
  const state = pageState(inp.state, concerts);
  const built = buildLabels(state, inp.feedback, concerts);
  const byId = new Map(concerts.map((c) => [c.id, c]));
  const labelled = [...built.labels].map(([id, label]) => [byId.get(id), label]);
  const counts = producers(labelled);
  const scored = labelled.map(([c, label]) => {
    const reduced = withoutOwnLabel(state, c, label, counts);
    return { id: c.id, label, score: S.scoreConcert(c, artists, S.buildProfile(reduced, artists)).score };
  });
  const dislikes = dislikeReport(built, inp, new Set(S.sanitizeState(inp.state).hidden).size);
  return { concerts: concerts.length, scored, ...built, dislikes };
}

function evaluate(input) {
  const { concerts, scored, source, pastEvents, unliked, stale, ambiguous, dislikes } = scoreLabels(input);
  const tiers = Object.fromEntries(["sure", "inferred", "none"].map((t) => [t, { n: 0, liked: 0, disliked: 0 }]));
  const scores = { liked: [], disliked: [] };
  for (const { label, score } of scored) {
    const t = tiers[tierOf(score)];
    t.n += 1;
    t[label] += 1;
    scores[label].push(score);
  }
  for (const t of Object.values(tiers)) {
    t.precision = t.n ? round(t.liked / t.n) : null;
    t.wilson95 = wilson(t.liked, t.n);
  }
  // pairwise ranking accuracy: P(score(liked) > score(disliked)), ties count 0.5 (= AUC)
  let wins = 0;
  for (const l of scores.liked) for (const d of scores.disliked) wins += l > d ? 1 : l === d ? 0.5 : 0;
  const pairs = scores.liked.length * scores.disliked.length;
  const fromFeedback = [...source.values()].filter((s) => s === "feedback").length;
  return {
    concerts,
    labels: {
      total: scored.length,
      liked: scores.liked.length,
      disliked: scores.disliked.length,
      from_feedback: fromFeedback,
      from_state_only: scored.length - fromFeedback,
      unliked_by_feedback: unliked,
      stale_feedback: stale,
      ambiguous_dislikes: ambiguous,
      feedback_events_on_unpublished_concerts: pastEvents,
    },
    tiers,
    pairwise: { pairs, accuracy: pairs ? round(wins / pairs) : null },
    dislikes,
  };
}

module.exports = { buildLabels, dislikeReport, produced, removal, producers, scoreLabels, withoutOwnLabel, pageState, wilson, tierOf, evaluate };

if (require.main === module) {
  const chunks = [];
  process.stdin.on("data", (d) => chunks.push(d));
  process.stdin.on("end", () => {
    let input;
    try {
      input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      process.stderr.write("taste eval: invalid JSON on stdin\n"); // never echoes the input
      process.exit(2);
    }
    process.stdout.write(JSON.stringify(evaluate(input)) + "\n");
  });
}
