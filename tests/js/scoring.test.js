// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../src/nightcrawler/web/scoring.js");

const artists = {
  earth: { key: "earth", name: "Earth", fans: 50000, related: ["Sunn O)))", "Boris"], tags: ["drone", "doom metal"], confident: true },
  boris: { key: "boris", name: "Boris", fans: 9000, related: ["Merzbow"], tags: ["drone", "noise"], confident: true },
  popstar: { key: "popstar", name: "Popstar", fans: 9e6, related: [], tags: ["pop"], confident: true },
};
const c = (id, keys) => ({ id, start: "2026-10-10T20:00:00+02:00", artists: keys });

test("norm matches the pipeline", () => {
  assert.equal(S.norm("Sunn O)))"), "sunno");
  assert.equal(S.norm("Björk"), "bjork");
});

test("parseSeeds dedupes and splits", () => {
  assert.deepEqual(S.parseSeeds("Earth\nearth, Boris; \n x"), ["Earth", "Boris"]);
});

test("exact seed match wins", () => {
  const p = S.buildProfile({ seeds: [{ name: "Earth", tags: [] }] }, artists);
  const m = S.scoreConcert(c("1", ["earth"]), artists, p);
  assert.equal(m.score, 1);
  assert.equal(m.reason, "Tu écoutes Earth");
});

test("related artist match names the seed", () => {
  const p = S.buildProfile({ seeds: [{ name: "Sunn O)))", tags: [] }] }, artists);
  const m = S.scoreConcert(c("1", ["earth"]), artists, p);
  assert.equal(m.score, 0.8);
  assert.equal(m.reason, "Proche de Sunn O)))");
});

test("style similarity and discovery badge", () => {
  const p = S.buildProfile({ seeds: [{ name: "Unknown", tags: ["noise", "drone"] }] }, artists);
  const m = S.scoreConcert(c("1", ["boris"]), artists, p);
  assert.ok(m.score > 0 && m.score <= 0.6);
  assert.match(m.reason, /^Style : /);
  assert.equal(m.discovery, true); // 9,000 fans
  assert.equal(S.scoreConcert(c("2", ["popstar"]), artists, p).score, 0);
});

test("feedback: liked artists count, disliked tags push down", () => {
  const p = S.buildProfile({ seeds: [], liked: ["boris"], disliked: ["popstar"] }, artists);
  assert.equal(S.scoreConcert(c("1", ["boris"]), artists, p).reason, "Tu as aimé Boris");
  assert.equal(S.isEmpty(p), false);
  assert.equal(S.isEmpty(S.buildProfile({}, artists)), true);
});

test("when filter", () => {
  const key = (d) => d.toISOString().slice(0, 10);
  const now = new Date("2026-10-07T12:00:00Z"); // Wednesday
  assert.equal(S.inWhen("tonight", "2026-10-07T20:00:00Z", now, key), true);
  assert.equal(S.inWhen("tonight", "2026-10-08T20:00:00Z", now, key), false);
  assert.equal(S.inWhen("weekend", "2026-10-09T20:00:00Z", now, key), true); // Friday
  assert.equal(S.inWhen("weekend", "2026-10-08T20:00:00Z", now, key), false); // Thursday
  assert.equal(S.inWhen("weekend", "2026-10-12T20:00:00Z", now, key), false); // next Monday
  assert.equal(S.inWhen("7d", "2026-10-20T20:00:00Z", now, key), false);
});

test("disliked artist never comes back", () => {
  const p = S.buildProfile({ seeds: [{ name: "Earth", tags: [] }], disliked: ["earth"] }, artists);
  assert.equal(S.scoreConcert(c("1", ["earth"]), artists, p).score, 0);
});

test("no discovery badge for liked or unknown-fan artists", () => {
  const p = S.buildProfile({ liked: ["boris"] }, artists);
  assert.equal(S.scoreConcert(c("1", ["boris"]), artists, p).discovery, false);
  const noFans = { x: { key: "x", name: "X", related: ["Sunn O)))"], tags: [] } };
  const p2 = S.buildProfile({ seeds: [{ name: "Sunn O)))", tags: [] }] }, noFans);
  assert.equal(S.scoreConcert(c("2", ["x"]), noFans, p2).discovery, false);
});

test("weekend in Europe/Paris, late Sunday and across the DST change", () => {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris" });
  const key = (d) => fmt.format(d);
  const now = new Date("2026-10-21T10:00:00Z"); // Wednesday
  assert.equal(S.inWhen("weekend", "2026-10-25T21:30:00Z", now, key), true); // Sun 22:30 Paris (after DST)
  assert.equal(S.inWhen("weekend", "2026-10-25T23:30:00Z", now, key), false); // Mon 00:30 Paris
  assert.equal(S.inWhen("weekend", "2026-10-23T08:00:00Z", now, key), true); // Friday morning
  const sunday = new Date("2026-10-25T20:00:00Z");
  assert.equal(S.inWhen("weekend", "2026-10-25T21:00:00Z", sunday, key), true);
});

test("mergeNames keeps commas inside names", () => {
  assert.deepEqual(S.mergeNames(["Earth"], ["Tyler, The Creator", "earth", "x"]), ["Earth", "Tyler, The Creator"]);
});

test("no discovery badge for an artist we are not sure about (homonyms, WIP-40)", () => {
  const unsure = { asna: { key: "asna", name: "Asna", fans: 9000, related: ["Acid Arab"], tags: [], confident: false } };
  const p = S.buildProfile({ seeds: [{ name: "Acid Arab", tags: [] }] }, unsure);
  assert.equal(S.scoreConcert(c("1", ["asna"]), unsure, p).discovery, false);
});

test("a reported wrong match stops related and style guesses for that artist (WIP-41)", () => {
  const state = { seeds: [{ name: "Sunn O)))", tags: ["drone"] }] };
  const before = S.scoreConcert(c("1", ["earth"]), artists, S.buildProfile(state, artists));
  assert.equal(before.inferred, true);
  assert.equal(before.artist, "earth");
  const after = S.scoreConcert(c("1", ["earth"]), artists, S.buildProfile({ ...state, wrong: ["earth"] }, artists));
  assert.equal(after.score, 0);
  const seeded = S.buildProfile({ seeds: [{ name: "Earth", tags: [] }], wrong: ["earth"] }, artists);
  assert.equal(S.scoreConcert(c("1", ["earth"]), artists, seeded).score, 1); // own artists still match
});

test("a liked artist still counts after a wrong-match report", () => {
  const p = S.buildProfile({ seeds: [], liked: ["boris"], wrong: ["boris"] }, artists);
  assert.equal(S.scoreConcert(c("1", ["boris"]), artists, p).score, 0.9);
});

// ratings of concerts without an identified artist (WIP-47)
const anon = (id, performers) => ({ id, start: "2026-10-10T20:00:00+02:00", artists: [], performers });

test("a liked concert without artists scores 0.9", () => {
  const p = S.buildProfile({ likedConcerts: ["aaaaaaaaaaaa"] }, artists);
  assert.equal(S.isEmpty(p), false);
  const m = S.scoreConcert(anon("aaaaaaaaaaaa", []), artists, p);
  assert.equal(m.score, 0.9);
  assert.equal(m.reason, "Tu as aimé ce concert");
  assert.equal(m.artist, null);
  assert.equal(m.discovery, false);
});

test("a liked name matches another concert by its performers", () => {
  const p = S.buildProfile({ likedNames: S.performerKeys(anon("a", ["Les Dégâts"])) }, artists);
  const m = S.scoreConcert(anon("bbbbbbbbbbbb", ["Autre groupe", "LES DEGATS"]), artists, p);
  assert.equal(m.score, 0.9);
  assert.equal(m.reason, "Tu as aimé LES DEGATS");
  // once identified, the name is an artist key: it still counts
  const n = S.scoreConcert(c("2", ["earth"]), artists, S.buildProfile({ likedNames: ["earth"] }, artists));
  assert.equal(n.reason, "Tu as aimé Earth");
});

test("a disliked name is excluded like a disliked artist", () => {
  const p = S.buildProfile(
    { seeds: [{ name: "Earth", tags: ["drone"] }], likedNames: ["lesdegats"], dislikedNames: ["lesdegats", "earth"] },
    artists,
  );
  assert.equal(S.scoreConcert(anon("1", ["Les Dégâts"]), artists, p).score, 0);
  assert.equal(S.scoreConcert(c("2", ["earth"]), artists, p).score, 0);
});

test("a liked concert does not override an artist disliked after identification", () => {
  const p = S.buildProfile({ likedConcerts: ["aaaaaaaaaaaa"], disliked: ["earth"] }, artists);
  assert.equal(S.scoreConcert({ ...c("aaaaaaaaaaaa", ["earth"]), performers: ["Earth"] }, artists, p).score, 0);
  const p2 = S.buildProfile({ likedConcerts: ["aaaaaaaaaaaa"], dislikedNames: ["earth"] }, artists);
  assert.equal(S.scoreConcert(c("aaaaaaaaaaaa", ["earth"]), artists, p2).score, 0);
});

test("rate: like by id, then the concert gets an artist: pressed and undoable", () => {
  const before = anon("aaaaaaaaaaaa", ["Earth"]);
  const r1 = S.rate(S.defaultState(), before, "like", [before]);
  assert.deepEqual(r1.send, { kind: "like", keys: [] });
  assert.deepEqual(r1.state.likedConcerts, ["aaaaaaaaaaaa"]);
  assert.deepEqual(r1.state.likedNames, ["earth"]);
  const after = { ...before, artists: ["earth"] }; // identified on a later run
  const st = { ...S.defaultState(), ...r1.state };
  assert.equal(S.isLiked(st, after), true);
  const r2 = S.rate(st, after, "like", [after]);
  assert.deepEqual(r2.send, { kind: "unlike", keys: ["earth"] });
  assert.deepEqual(r2.state.likedConcerts, []);
  assert.deepEqual(r2.state.likedNames, []);
  assert.equal(S.isLiked({ ...st, ...r2.state }, after), false);
  assert.equal(S.scoreConcert(after, artists, S.buildProfile({ ...st, ...r2.state }, artists)).score, 0);
});

test("rate: dislike of a liked-by-id concert with artists clears the id", () => {
  const conc = c("aaaaaaaaaaaa", ["earth"]);
  const r = S.rate({ ...S.defaultState(), likedConcerts: ["aaaaaaaaaaaa"], likedNames: ["earth"] }, conc, "dislike", [conc]);
  assert.deepEqual(r.send, { kind: "dislike", keys: ["earth"] });
  assert.deepEqual(r.state.likedConcerts, []);
  assert.deepEqual(r.state.likedNames, []);
  assert.deepEqual(r.state.disliked, ["earth"]);
  assert.deepEqual(r.state.hidden, ["aaaaaaaaaaaa"]);
});

test("rate: unliking keeps names another liked concert still uses", () => {
  const a = anon("aaaaaaaaaaaa", ["Les Dégâts"]);
  const b = anon("bbbbbbbbbbbb", ["Les Dégâts", "Autre"]);
  let st = S.defaultState();
  for (const x of [a, b]) st = { ...st, ...S.rate(st, x, "like", [a, b]).state };
  assert.deepEqual(st.likedNames.sort(), ["autre", "lesdegats"]);
  const r = S.rate(st, b, "like", [a, b]);
  assert.equal(r.send.kind, "unlike");
  assert.deepEqual(r.state.likedConcerts, ["aaaaaaaaaaaa"]);
  assert.deepEqual(r.state.likedNames, ["lesdegats"]);
  // dislike is explicit: the name goes even if another liked concert has it
  const d = S.rate(st, b, "dislike", [a, b]);
  assert.deepEqual(d.state.likedNames, []);
  assert.deepEqual(d.state.dislikedNames.sort(), ["autre", "lesdegats"]);
});

test("performerKeys normalises, dedupes and drops empty names", () => {
  assert.deepEqual(S.performerKeys({ performers: ["Björk", "bjork", "!!!", 3] }), ["bjork"]);
  assert.deepEqual(S.performerKeys({}), []);
});

test("sanitizeState keeps well-typed rating fields only", () => {
  const s = S.sanitizeState({
    liked: ["earth", 1],
    likedConcerts: ["aaaaaaaaaaaa", null],
    likedNames: ["lesdegats", "Les Dégâts", "lesdegats", ""],
    dislikedNames: "earth",
    sort: "me",
  });
  assert.deepEqual(s.liked, ["earth"]);
  assert.deepEqual(s.likedConcerts, ["aaaaaaaaaaaa"]);
  assert.deepEqual(s.likedNames, ["lesdegats"]);
  assert.deepEqual(s.dislikedNames, []);
  assert.equal(s.sort, "me");
  assert.deepEqual(S.sanitizeState([1]), S.defaultState());
  assert.deepEqual(S.sanitizeState(null).likedConcerts, []);
});

// ---------------------------------------------------------------- tiers (WIP-53)

const sc = (id, score, start) => ({ c: { id, start }, m: { score } });
const ids = (xs) => xs.map((x) => x.c.id);

test("tiers: constants", () => {
  assert.equal(S.SURE_MIN, 0.9);
  assert.equal(S.DISCOVER_MAX, 10);
});

test("tiers: 0.9 exactly is sure, just below is discover, 0 is rest", () => {
  const t = S.tiers([
    sc("a", 0.9, "2026-10-10T20:00:00+02:00"),
    sc("b", 0.8999, "2026-10-10T20:00:00+02:00"),
    sc("c", 0, "2026-10-10T20:00:00+02:00"),
    sc("d", 1, "2026-10-09T20:00:00+02:00"),
  ]);
  assert.deepEqual(ids(t.sure), ["d", "a"]); // by date
  assert.deepEqual(ids(t.discover), ["b"]);
  assert.deepEqual(ids(t.rest), ["c"]);
});

test("tiers: sure is sorted by date, not by score", () => {
  const t = S.tiers([
    sc("late-1.0", 1, "2026-10-20T20:00:00+02:00"),
    sc("early-0.9", 0.9, "2026-10-08T20:00:00+02:00"),
  ]);
  assert.deepEqual(ids(t.sure), ["early-0.9", "late-1.0"]);
});

test("tiers: discover by score, ties by date, then input order", () => {
  const t = S.tiers([
    sc("style-late", 0.5, "2026-10-12T20:00:00+02:00"),
    sc("style-early", 0.5, "2026-10-11T20:00:00+02:00"),
    sc("proche", 0.8, "2026-10-30T20:00:00+02:00"),
    sc("same-1", 0.7, "2026-10-15T20:00:00+02:00"),
    sc("same-2", 0.7, "2026-10-15T20:00:00+02:00"),
  ]);
  assert.deepEqual(ids(t.discover), ["proche", "same-1", "same-2", "style-early", "style-late"]);
});

test("tiers: discover is capped, extra inferred matches go to rest in input order", () => {
  const input = [];
  for (let i = 0; i < 13; i++) input.push(sc(`i${i}`, 0.3 + i * 0.01, `2026-10-${10 + i}T20:00:00+02:00`));
  input.splice(2, 0, sc("zero", 0, "2026-10-01T20:00:00+02:00"));
  const t = S.tiers(input);
  assert.equal(t.discover.length, S.DISCOVER_MAX);
  assert.deepEqual(ids(t.discover), ["i12", "i11", "i10", "i9", "i8", "i7", "i6", "i5", "i4", "i3"]);
  assert.deepEqual(ids(t.rest), ["i0", "i1", "zero", "i2"]); // the list's own order, for "Tout voir"
});

test("tiers: each concert lands in exactly one tier; input untouched; empty input", () => {
  const input = [sc("x", 0.6, "2026-10-12"), sc("y", 1, "2026-10-11"), sc("z", 0, "2026-10-10")];
  const before = ids(input);
  const t = S.tiers(input);
  assert.deepEqual([...ids(t.sure), ...ids(t.discover), ...ids(t.rest)].sort(), ["x", "y", "z"]);
  assert.deepEqual(ids(input), before);
  assert.deepEqual(S.tiers([]), { sure: [], discover: [], rest: [] });
});

test("tiers: an inferred match is never sure, whatever its score", () => {
  const guess = { c: { id: "g", start: "2026-10-10" }, m: { score: 0.95, inferred: true } };
  const direct = { c: { id: "d", start: "2026-10-11" }, m: { score: 0.9, inferred: false } };
  const t = S.tiers([guess, direct]);
  assert.deepEqual(ids(t.sure), ["d"]);
  assert.deepEqual(ids(t.discover), ["g"]);
});

test("tiers: liked concert and liked performer name are sure, a style match discover", () => {
  const p = S.buildProfile(
    { seeds: [{ name: "Unknown", tags: ["noise", "drone"] }], likedConcerts: ["lc"], likedNames: ["lesdegats"] },
    artists,
  );
  const concerts = [
    { id: "lc", start: "2026-10-12T20:00:00+02:00", artists: [], performers: ["Someone"] },
    { id: "name", start: "2026-10-11T20:00:00+02:00", artists: [], performers: ["Les Dégâts"] },
    c("style", ["boris"]),
    c("none", ["popstar"]),
  ];
  const scored = concerts.map((x) => ({ c: x, m: S.scoreConcert(x, artists, p) }));
  const by = Object.fromEntries(scored.map((x) => [x.c.id, x.m]));
  assert.equal(by.lc.score, 0.9);
  assert.equal(by.name.score, 0.9);
  assert.equal(by.name.reason, "Tu as aimé Les Dégâts");
  assert.match(by.style.reason, /^Style : /);
  const t = S.tiers(scored);
  assert.deepEqual(ids(t.sure), ["name", "lc"]); // by date
  assert.deepEqual(ids(t.discover), ["style"]);
  assert.deepEqual(ids(t.rest), ["none"]);
});

test("tiers: scoreConcert direct matches are sure, inferred ones discover", () => {
  const p = S.buildProfile({ seeds: [{ name: "Earth", tags: [] }], liked: ["boris"] }, artists);
  const scored = [c("seed", ["earth"]), c("liked", ["boris"]), c("none", ["popstar"])].map((x) => ({ c: x, m: S.scoreConcert(x, artists, p) }));
  const t = S.tiers(scored);
  assert.deepEqual(ids(t.sure).sort(), ["liked", "seed"]);
  assert.deepEqual(ids(t.rest), ["none"]);
  const p2 = S.buildProfile({ seeds: [{ name: "Sunn O)))", tags: [] }] }, artists);
  const rel = c("rel", ["earth"]);
  assert.deepEqual(ids(S.tiers([{ c: rel, m: S.scoreConcert(rel, artists, p2) }]).discover), ["rel"]); // "Proche de" = 0.8
});

// ---------------------------------------------------------------- known artists (WIP-88)

test("isKnownMatch: a sure name match, unless the identity may be a homonym", () => {
  const all = { ...artists, air: { key: "air", name: "Air", fans: 800000, related: [], tags: [], confident: false, doubt: "ambiguous" } };
  const p = S.buildProfile({ seeds: [{ name: "Earth", tags: [] }, { name: "Air", tags: [] }] }, all);
  const earth = S.scoreConcert(c("1", ["earth"]), all, p);
  assert.equal(earth.doubt, null);
  assert.equal(S.isKnownMatch(earth), true);
  const air = S.scoreConcert(c("2", ["air"]), all, p);
  assert.equal(air.score, 1); // matched by name…
  assert.equal(air.doubt, "ambiguous");
  assert.equal(S.isKnownMatch(air), false); // …but Deezer has several "Air": left to the judge
  for (const doubt of S.HOMONYM_DOUBTS) assert.equal(S.isKnownMatch({ score: 1, doubt }), false);
  assert.deepEqual(S.HOMONYM_DOUBTS, ["ambiguous", "short_name", "reported"]);
  assert.equal(S.isKnownMatch({ score: 1, doubt: "low_fans" }), true);
  assert.equal(S.isKnownMatch({ score: 0.9, inferred: true }), false); // a guess is never known
  assert.equal(S.isKnownMatch({ score: 0.8999 }), false);
  assert.equal(S.isKnownMatch(null), false);
});
