// node --test tests/js — taste eval runner (WIP-52), synthetic profile, offline.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const T = require("../../eval/taste/run.js");
const S = require("../../src/nightcrawler/web/scoring.js");

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

test("a key another labelled concert also produced is kept (two dislikes)", () => {
  // two likes on one artist cannot happen: the second click on a lit "J'aime" is an unlike
  const D2 = concert("ddddddddddd2", ["popstar"]);
  const state = { disliked: ["popstar"], hidden: [D.id, D2.id] };
  const feedback = [{ concert_id: D.id, kind: "dislike" }, { concert_id: D2.id, kind: "dislike" }];
  const s = T.pageState(state, [D, D2]);
  const counts = T.producers([[D, "disliked"], [D2, "disliked"]]);
  const d = T.withoutOwnLabel(s, D, "disliked", counts);
  assert.deepEqual([d.disliked, d.hidden], [["popstar"], [D2.id]]);
  assert.equal(run({ state, feedback, concerts: [D, D2], artists }).labels.disliked, 2);
});

test("producers mirror rate(): a keyed like does not keep a keyless concert's name", () => {
  // K (no artist, performer "Boris") and C (artist boris) both liked: K's name is K's alone
  const K = concert("kkkkkkkkkkk1", [], ["Boris"]);
  const state = { liked: ["boris"], likedConcerts: [K.id], likedNames: ["boris"] };
  const got = T.scoreLabels({ state, feedback: [like(K.id), like(C.id)], concerts: [C, K], artists });
  const score = Object.fromEntries(got.scored.map((x) => [x.id, x.score]));
  assert.equal(score[K.id], 0); // C's "boris" key does not match a concert without artists
  assert.equal(score[C.id], 0.9); // K's liked name still matches C's artist
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
  const state = { liked: ["earth", "popstar"], likedConcerts: [E.id], disliked: ["boris"], hidden: [C.id] };
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
  assert.equal(got.stale, 0);
});

test("stale feedback: a like the state no longer shows, a dislike no longer hidden", () => {
  const got = T.buildLabels(T.pageState({}, [A, D]), [like(A.id), { concert_id: D.id, kind: "dislike" }], [A, D]);
  assert.equal(got.labels.size, 0);
  assert.equal(got.stale, 2);
});

test("a hidden concert whose artist is no longer disliked is not a dislike label", () => {
  const got = T.buildLabels(T.pageState({ hidden: [D.id] }, [D]), [], [D]);
  assert.equal(got.labels.size, 0);
});

test("tiers, precision and pairwise accuracy (ties count 0.5)", () => {
  // seed Earth gives: A (earth) 1 -> sure; C (boris, related to Earth) 0.8 -> inferred;
  // F (sunn, style "drone") 0.6 -> inferred; D (popstar) 0 -> none; E (no artist) 0 -> none.
  const state = {
    seeds: [{ name: "Earth", tags: ["drone"] }],
    liked: ["earth", "boris"], likedConcerts: [E.id], likedNames: ["mysteryband"],
    disliked: ["popstar", "sunn"], hidden: [D.id, F.id],
  };
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

test("where the dislikes went: one count per reason, concerts not rows (WIP-55)", () => {
  const G = concert("ggggggggggg1", ["earth"]);
  const [PAST, SOON, UNKNOWN] = ["999999999990", "999999999991", "999999999992"];
  const dislike = (id) => ({ concert_id: id, kind: "dislike" });
  const state = { liked: ["sunn"], disliked: ["popstar", "earth"], hidden: [D.id, G.id, "777777777777"] };
  const feedback = [
    like(A.id), // stale: "earth" is not liked any more
    dislike(D.id), dislike(D.id), // one click on a concert with two artist keys: two rows
    dislike(F.id), like(F.id), // rated again
    dislike("ccccccccccc0"), // alias of C, which is not hidden
    dislike(G.id), // shares "earth" with the stale like: ambiguous
    dislike(PAST), dislike(SOON), dislike(UNKNOWN),
  ];
  const input = { state, feedback, concerts: [A, C, D, F, G], artists, today: "2026-10-06", stored_dates: { [PAST]: "2026-10-01", [SOON]: "2026-10-20" } };
  const out = run(input);
  assert.deepEqual(out.dislikes, {
    rows: 8, concerts: 7, labelled: 1, rated_again: 1, not_hidden: 1, ambiguous: 1,
    unpublished_past: 1, unpublished_upcoming: 1, unpublished_unknown: 1,
    hidden_in_profile: 3, hidden_published: 2,
  });
  assert.equal(out.labels.disliked, out.dislikes.labelled);
  // without the store dates every unpublished id is "unknown"
  assert.equal(run({ ...input, stored_dates: undefined }).dislikes.unpublished_unknown, 3);
  const text = JSON.stringify(out);
  for (const secret of [PAST, SOON, UNKNOWN, D.id, G.id, "777777777777"]) assert.ok(!text.includes(secret), secret);
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

// ---- replay: state and history built by clicking with S.rate(), as the page does.
// Ground truth for a label = the score of that concert after replaying every click except
// the ones on it. "reset" is « Tout effacer » (empty state; the feedback rows stay).
const G = concert("ggggggggggg1", ["earth"], ["Earth"]);
const G2 = concert("ggggggggggg2", ["earth"], ["Earth"]);
const KX = concert("kkkkkkkkkkk2", [], ["Xan"]);
const X = concert("xxxxxxxxxxx1", ["xan"], ["Xan"]);
const N = concert("nnnnnnnnnnn1", ["boris"], ["Boris"]);
const P = concert("ppppppppppp1", ["popstar"]);
const P2 = concert("ppppppppppp2", ["popstar"]);
const M = concert("mmmmmmmmmmm1", [], ["Mystery Band"]);
const ALL = [G, G2, KX, X, N, P, P2, M];
const RA = { ...artists, xan: { key: "xan", name: "Xan", fans: 100, related: [], tags: ["drone"], confident: true } };
const SEEDS = [{ name: "Sunn", tags: ["drone"] }];

function replay(clicks, skip) {
  let st = { ...S.defaultState(), seeds: SEEDS };
  const feedback = [];
  for (const click of clicks) {
    if (click === "reset") {
      st = S.defaultState();
      continue;
    }
    const [kind, c] = click;
    if (c.id === skip) continue;
    const r = S.rate(st, c, kind, ALL);
    st = { ...st, ...r.state };
    feedback.push({ concert_id: c.id, kind: r.send.kind });
  }
  return { state: st, feedback };
}

function checkReplay(clicks) {
  const { state, feedback } = replay(clicks);
  const got = T.scoreLabels({ state, feedback, concerts: ALL, artists: RA });
  for (const { id, score } of got.scored) {
    const c = ALL.find((x) => x.id === id);
    const truth = S.scoreConcert(c, RA, S.buildProfile(T.pageState(replay(clicks, id).state, ALL), RA)).score;
    assert.equal(score, truth, `leave-one-out vs replay for ${id}`);
  }
  return got;
}

const labelsOf = (got) => Object.fromEntries(got.scored.map((x) => [x.id, x.label]));

test("replay: concerts with and without artists", () => {
  const got = checkReplay([["like", KX], ["like", X], ["like", N], ["dislike", P], ["dislike", M], ["like", G]]);
  assert.deepEqual(labelsOf(got), { [KX.id]: "liked", [X.id]: "liked", [N.id]: "liked", [P.id]: "disliked", [M.id]: "disliked", [G.id]: "liked" });
});

test("replay: unlike through a sibling concert", () => {
  // G2 shows "J'aime" lit through "earth": clicking it sends an unlike and drops "earth"
  const got = checkReplay([["like", G], ["like", G2], ["like", N]]);
  assert.deepEqual(labelsOf(got), { [N.id]: "liked" });
  assert.equal(got.stale, 1);
});

test("replay: dislike through a sibling concert", () => {
  // G2's dislike undid G's like: leaving G2 out, the replay gets "earth" liked back (0.9),
  // which the reduced state cannot know (a reset would give 0): G2 is excluded, counted.
  const got = checkReplay([["like", G], ["dislike", G2], ["like", N], ["dislike", P]]);
  assert.deepEqual(labelsOf(got), { [N.id]: "liked", [P.id]: "disliked" });
  assert.deepEqual([got.stale, got.ambiguous], [1, 1]);
});

test("replay: a dislike after a reset that shares the artist of a stale like is excluded too", () => {
  const got = checkReplay([["like", G], "reset", ["dislike", G2], ["dislike", P]]);
  assert.deepEqual(labelsOf(got), { [P.id]: "disliked" });
  assert.deepEqual([got.stale, got.ambiguous], [1, 1]);
});

test("replay: « Tout effacer » makes earlier ratings stale", () => {
  const got = checkReplay([["like", G], ["dislike", P], "reset", ["like", N], ["dislike", M]]);
  assert.deepEqual(labelsOf(got), { [N.id]: "liked", [M.id]: "disliked" });
  assert.equal(got.stale, 2);
});

test("replay: two dislikes of the same artist, then a like of a third concert", () => {
  checkReplay([["dislike", P], ["dislike", P2], ["like", KX], ["like", X]]);
});
