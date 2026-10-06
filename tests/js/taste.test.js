// node --test tests/js — taste eval runner (WIP-52), synthetic profile, offline.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const T = require("../../eval/taste/run.js");

const RUNNER = path.join(__dirname, "..", "..", "eval", "taste", "run.js");

const artists = {
  earth: { key: "earth", name: "Earth", fans: 50000, related: ["Boris"], tags: ["drone", "doom metal"], confident: true },
  boris: { key: "boris", name: "Boris", fans: 9000, related: ["Earth"], tags: ["drone", "noise"], confident: true },
  sunn: { key: "sunn", name: "Sunn", fans: 90000, related: [], tags: ["drone"], confident: true },
  popstar: { key: "popstar", name: "Popstar", fans: 9e6, related: [], tags: ["pop"], confident: true },
};
const START = "2026-10-10T20:00:00+02:00";
const concert = (id, keys, performers = [], aliases = []) => ({ id, start: START, artists: keys, performers, aliases });
const A = concert("aaaaaaaaaaa1", ["earth"]);
const B = concert("bbbbbbbbbbb1", ["earth"]); // never rated, but isLiked() through "earth"
const C = concert("ccccccccccc1", ["boris"], [], ["ccccccccccc0"]);
const D = concert("ddddddddddd1", ["popstar"]);
const E = concert("eeeeeeeeeee1", [], ["Mystery Band"]);
const F = concert("fffffffffff1", ["sunn"]);
const like = (id) => ({ concert_id: id, kind: "like" });
const run = (input) => T.evaluate(input);

test("leave-one-out removes the concert's own artist key", () => {
  const state = { liked: ["earth"] };
  const out = run({ state, feedback: [like(A.id)], concerts: [A], artists });
  assert.equal(out.labels.liked, 1);
  assert.equal(out.tiers.sure.n, 0); // without its own like, nothing is left to match
  assert.equal(out.tiers.none.liked, 1);
  const reduced = T.withoutOwnLabel(T.pageState(state, [A]), A, "liked", T.producers([[A, "liked"]]));
  assert.deepEqual(reduced.liked, []);
});

test("a key another labelled concert also produced is kept", () => {
  const A2 = concert("aaaaaaaaaaa2", ["earth"]);
  const out = run({ state: { liked: ["earth"] }, feedback: [like(A.id), like(A2.id)], concerts: [A, A2], artists });
  assert.equal(out.tiers.sure.liked, 2);
});

test("a liked key from a concert that is not labelled is removed (no leakage)", () => {
  // B is not labelled: liking A must not reach A again through B
  const out = run({ state: { liked: ["earth"] }, feedback: [like(A.id)], concerts: [A, B], artists });
  assert.equal(out.labels.total, 1);
  assert.equal(out.tiers.none.liked, 1);
});

test("concert-level likes and dislikes lose their ids and names", () => {
  const state = {
    likedConcerts: [E.id], likedNames: ["mysteryband"], disliked: ["popstar"], hidden: [D.id],
  };
  const counts = T.producers([[E, "liked"], [D, "disliked"]]);
  const s = T.pageState(state, [D, E]);
  const e = T.withoutOwnLabel(s, E, "liked", counts);
  assert.deepEqual([e.likedConcerts, e.likedNames], [[], []]);
  const d = T.withoutOwnLabel(s, D, "disliked", counts);
  assert.deepEqual([d.disliked, d.hidden], [[], []]);
  const out = run({ state, feedback: [], concerts: [D, E], artists });
  assert.deepEqual([out.labels.liked, out.labels.disliked, out.labels.from_state_only], [1, 1, 2]);
  assert.equal(out.tiers.none.n, 2);
});

test("labels: state evidence, latest feedback wins, aliases, unknown concerts", () => {
  const state = { liked: ["earth"], likedConcerts: [E.id], disliked: ["popstar"], hidden: [D.id] };
  const feedback = [
    like(F.id), { concert_id: F.id, kind: "unlike" }, // undone
    { concert_id: D.id, kind: "dislike" }, like(D.id), // changed mind: liked
    { concert_id: "ccccccccccc0", kind: "dislike" }, // alias of C
    { concert_id: E.id, kind: "unlike" }, // undoes the state label
    like("999999999999"), // concert no longer published
    { concert_id: A.id, kind: "wrong" }, // not a taste rating
  ];
  const s = T.pageState(state, [A, B, C, D, E, F]);
  const got = T.buildLabels(s, feedback, [A, B, C, D, E, F]);
  assert.deepEqual(Object.fromEntries(got.labels), { [D.id]: "liked", [C.id]: "disliked" });
  assert.equal(got.pastEvents, 1);
  assert.equal(got.unliked, 1);
});

test("a hidden concert whose artist is no longer disliked is not a dislike label", () => {
  const got = T.buildLabels(T.pageState({ hidden: [D.id] }, [D]), [], [D]);
  assert.equal(got.labels.size, 0);
});

test("tiers, precision and pairwise accuracy (ties count 0.5)", () => {
  // seed Earth gives: A (earth) 1 -> sure; C (boris, related to Earth) 0.8 -> inferred;
  // F (sunn, style "drone") 0.6 -> inferred; D (popstar) 0 -> none; E (no artist) 0 -> none.
  const state = { seeds: [{ name: "Earth", tags: ["drone"] }], disliked: ["popstar", "sunn"], hidden: [D.id, F.id] };
  const feedback = [like(A.id), like(C.id), like(E.id), { concert_id: D.id, kind: "dislike" }, { concert_id: F.id, kind: "dislike" }];
  const out = run({ state, feedback, concerts: [A, C, D, E, F], artists });
  assert.deepEqual(out.tiers.sure, { n: 1, liked: 1, disliked: 0, precision: 1, wilson95: T.wilson(1, 1) });
  assert.equal(out.tiers.inferred.n, 2); // C liked (0.8), F disliked: its own "sunn" dislike is removed
  assert.equal(out.tiers.inferred.precision, 0.5);
  assert.deepEqual([out.tiers.none.liked, out.tiers.none.disliked], [1, 1]); // E, D
  // liked scores {1, 0.8, 0}, disliked {F style, 0}: F < 0.8 <= 1
  // wins: 1>F, 1>0, 0.8>F, 0.8>0, 0<F, 0=0 (0.5) -> 4.5 / 6
  assert.equal(out.pairwise.pairs, 6);
  assert.equal(out.pairwise.accuracy, 0.75);
});

test("no labels, empty or malformed input", () => {
  for (const input of [{}, null, { state: null, feedback: null, concerts: "x", artists: [] }, { concerts: [A], state: {} }]) {
    const out = run(input);
    assert.equal(out.labels.total, 0);
    assert.equal(out.pairwise.accuracy, null);
    assert.equal(out.tiers.sure.precision, null);
    assert.equal(out.tiers.sure.wilson95, null);
  }
});

test("Wilson 95 % interval", () => {
  assert.equal(T.wilson(0, 0), null);
  assert.deepEqual(T.wilson(5, 10), [0.237, 0.763]);
  assert.deepEqual(T.wilson(10, 10), [0.722, 1]);
});

test("output is aggregated: no id, artist key or name", () => {
  const state = { liked: ["earth"], likedConcerts: [E.id], likedNames: ["mysteryband"] };
  const text = JSON.stringify(run({ state, feedback: [like(A.id)], concerts: [A, B, C, E], artists }));
  for (const secret of [A.id, B.id, E.id, "earth", "Earth", "mysteryband", "Mystery"]) {
    assert.ok(!text.includes(secret), secret);
  }
});

test("CLI: JSON in, JSON out; invalid input is not echoed", () => {
  const ok = spawnSync("node", [RUNNER], { input: JSON.stringify({ state: {}, feedback: [], concerts: [], artists: {} }) });
  assert.equal(ok.status, 0);
  assert.equal(JSON.parse(ok.stdout).labels.total, 0);
  const bad = spawnSync("node", [RUNNER], { input: "not json: secret-artist" });
  assert.equal(bad.status, 2);
  assert.ok(!String(bad.stderr).includes("secret-artist"));
});
