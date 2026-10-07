// "À trier" mode (WIP-73): candidates and random choice. node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../src/nightcrawler/web/scoring.js");

const dayKey = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris" }).format(d);
const NOW = new Date("2026-10-07T21:30:00+02:00");
const c = (id, start, extra = {}) => ({ id: id.padEnd(12, "0"), start, title: id, artists: [], performers: [], ...extra });

const CONCERTS = [
  c("a1", "2026-10-06T20:00:00+02:00"), // yesterday: past
  c("a2", "2026-10-07T20:00:00+02:00"), // tonight (already started, still today)
  c("a3", "2026-10-08T20:00:00+02:00", { artists: ["asna"] }), // liked artist
  c("a4", "2026-10-09T20:00:00+02:00", { artists: ["boris"] }), // disliked artist
  c("a5", "2026-10-10T20:00:00+02:00", { performers: ["Earth"] }), // disliked name
  c("a6", "2026-10-11T20:00:00+02:00", { aliases: ["ffffffffffff"] }), // hidden through an alias
  c("a7", "2026-10-12T20:00:00+02:00"), // liked by id
  c("a8", "2026-10-13T20:00:00+02:00"), // skipped in this session
  c("a9", "2026-10-14T20:00:00+02:00", { artists: ["asna", "moondog"] }), // only one artist liked
  c("aa", "not a date"),
];
const STATE = {
  ...S.defaultState(),
  liked: ["asna"],
  disliked: ["boris"],
  dislikedNames: ["earth"],
  hidden: ["ffffffffffff"],
  likedConcerts: ["a70000000000"],
};

test("sortCandidates keeps upcoming, unrated, not hidden, not seen concerts", () => {
  const got = S.sortCandidates(CONCERTS, STATE, new Set(["a80000000000"]), NOW, dayKey).map((x) => x.title);
  assert.deepEqual(got, ["a2", "a9"]);
  // seen may be an array; nothing rated: every upcoming dated concert
  const all = S.sortCandidates(CONCERTS, S.defaultState(), [], NOW, dayKey).map((x) => x.title);
  assert.deepEqual(all, ["a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"]);
  assert.deepEqual(S.sortCandidates(null, STATE, null, NOW, dayKey), []);
});

test("a rating through rate() removes the concert from the candidates", () => {
  for (const target of [CONCERTS[1], CONCERTS[8]]) {
    for (const kind of ["like", "dislike"]) {
      const { state } = S.rate(S.defaultState(), target, kind, CONCERTS);
      const ids = S.sortCandidates(CONCERTS, { ...S.defaultState(), ...state }, [], NOW, dayKey).map((x) => x.id);
      assert.ok(!ids.includes(target.id), `${target.title} ${kind}`);
    }
  }
});

test("pickRandom uses the injected rng, uniformly over the list", () => {
  const list = ["x", "y", "z"];
  assert.equal(S.pickRandom(list, () => 0), "x");
  assert.equal(S.pickRandom(list, () => 0.34), "y");
  assert.equal(S.pickRandom(list, () => 0.9999), "z");
  assert.equal(S.pickRandom(list, () => 1), "z"); // out of range: clamped, never undefined
  assert.equal(S.pickRandom(list, () => -3), "x");
  assert.equal(S.pickRandom(list, () => NaN), "x");
  assert.equal(S.pickRandom([], () => 0.5), null);
  assert.ok(list.includes(S.pickRandom(list))); // Math.random by default
  // equal thirds of [0, 1) map to each item
  const counts = { x: 0, y: 0, z: 0 };
  for (let i = 0; i < 300; i++) counts[S.pickRandom(list, () => i / 300)]++;
  assert.deepEqual(counts, { x: 100, y: 100, z: 100 });
});

test("sanitizeState keeps the written taste, cleaned", () => {
  const s = S.sanitizeState({ tasteText: "drone\u0000 ".repeat(1000), tasteTextAt: 12 });
  assert.equal(s.tasteText.length, 4000);
  assert.ok(!s.tasteText.includes("\u0000"));
  assert.equal(s.tasteTextAt, 12);
  assert.deepEqual([S.sanitizeState({ tasteText: 4, tasteTextAt: "x" }).tasteText, S.sanitizeState({}).tasteTextAt], ["", 0]);
});

test("blurTasteText shows the saved text when a sync landed while the box was focused", () => {
  assert.equal(S.blurTasteText("my draft", "my draft"), "my draft"); // typed last: kept
  assert.equal(S.blurTasteText("old text", "newer remote text"), "newer remote text");
  assert.equal(S.blurTasteText("a\u0000b", "ab"), "a\u0000b"); // same once cleaned: untouched
});
