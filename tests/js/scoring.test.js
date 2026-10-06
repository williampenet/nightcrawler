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
