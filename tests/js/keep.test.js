// node --test tests/js — saved concert ids survive a day of absence (WIP-59)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../src/nightcrawler/web/scoring.js");
const P = require("../../src/nightcrawler/web/profile.js");

const A = "aaaaaaaaaaaa";
const B = "bbbbbbbbbbbb";
const C = "cccccccccccc";
const id = (i) => i.toString(16).padStart(12, "0");

test("a concert absent for one run and back the next stays hidden", () => {
  const day0 = [{ id: A }, { id: B }];
  const day1 = [{ id: B }]; // A missing: source failure
  const day2 = [{ id: A }, { id: B }];
  let hidden = S.keepIds(day0, [A]);
  hidden = S.keepIds(day1, hidden); // what the page saves on load (app.js)
  assert.deepEqual(hidden, [A]);
  assert.deepEqual(S.currentIds(day1, hidden), []); // display: nothing to hide today
  hidden = S.keepIds(day2, hidden);
  assert.deepEqual(S.currentIds(day2, hidden), [A]); // back, and still hidden
});

test("the profile upload never shrinks because of absence", () => {
  const saved = { hidden: [A, B], likedConcerts: [C] };
  const before = P.extract(saved);
  const today = [{ id: B }]; // A and C absent
  const after = P.extract({ hidden: S.keepIds(today, saved.hidden), likedConcerts: S.keepIds(today, saved.likedConcerts) });
  assert.deepEqual(after.hidden, before.hidden);
  assert.deepEqual(after.likedConcerts, before.likedConcerts);
  assert.deepEqual(S.keepIds([], [A, B]), [A, B]); // empty data (all sources down)
});

test("an alias resolves to the current id and is kept", () => {
  const today = [{ id: A, aliases: [B] }];
  const hidden = S.keepIds(today, [B]);
  assert.deepEqual(hidden, [B, A]); // saved alias kept, current id added
  assert.deepEqual(S.currentIds(today, hidden), [A]);
  assert.deepEqual(S.keepIds(today, hidden), [B, A]); // stable on the next load
  assert.ok(S.isLiked({ likedConcerts: S.keepIds(today, [B]) }, today[0]));
});

test("un-liking a concert removes its saved aliases too", () => {
  const c = { id: A, aliases: [B], performers: ["Asna"] };
  const { state } = S.rate({ likedConcerts: [B, A] }, c, "like", [c]);
  assert.deepEqual(state.likedConcerts, []);
});

test("the cap drops the oldest ids, never the absent ones first", () => {
  const saved = Array.from({ length: S.MAX_IDS + 1 }, (_, i) => id(i));
  const today = [{ id: id(0) }]; // the oldest is the only one present today
  const kept = S.keepIds(today, saved);
  assert.equal(kept.length, S.MAX_IDS);
  assert.ok(!kept.includes(id(0)) && kept.includes(id(S.MAX_IDS)));
  assert.equal(P.extract({ hidden: saved }).hidden[0], id(1));
  assert.equal(P.merge({ hidden: saved.slice(0, 300) }, { hidden: saved.slice(300) }).hidden.length, S.MAX_IDS);
});

test("both capped id lists stay well under the 64 KB profile body (handler.py)", () => {
  const ids = Array.from({ length: S.MAX_IDS }, (_, i) => id(i));
  const body = JSON.stringify({ data: P.extract({ hidden: ids, likedConcerts: ids.map((x) => x.replace(/^0/, "f")) }), base_version: 1 });
  assert.ok(body.length < 65536 / 4, `${body.length} bytes`);
});

test("merge at the cap: new ids from both devices are kept, the oldest shared ones go", () => {
  const shared = Array.from({ length: 480 }, (_, i) => id(i));
  const serverNew = Array.from({ length: 30 }, (_, i) => id(1000 + i));
  const localNew = Array.from({ length: 30 }, (_, i) => id(2000 + i));
  const out = P.merge({ hidden: [...shared, ...serverNew] }, { hidden: [...shared, ...localNew] }).hidden;
  assert.equal(out.length, S.MAX_IDS);
  assert.ok([...serverNew, ...localNew].every((x) => out.includes(x)));
  assert.deepEqual(out.slice(0, 440), shared.slice(40)); // 540 ids: the 40 oldest dropped
});

test("an old liked id alone does not switch on the tiered view", () => {
  const p = S.buildProfile({ likedConcerts: [A] }, {});
  assert.equal(S.isEmpty(p, [{ id: B }]), true); // A absent today
  assert.equal(S.isEmpty(p, [{ id: A }, { id: B }]), false);
  assert.equal(S.isEmpty(p), false); // without today's list: unchanged behaviour
});

test("Tout effacer: a pending reset is not merged back with the server copy", async () => {
  const server = { hidden: [A], likedConcerts: [B], liked: ["asna"] };
  const reset = P.extract({}); // the state right after "Tout effacer"
  const pulled = P.afterPull({ version: 1, dirty: true, force: true }, { status: "found", data: P.extract(server), version: 3 }, reset, false);
  assert.equal(pulled.apply, null); // nothing from the server copy enters the state
  assert.equal(pulled.sync.force, true);
  const snap = P.snapshot(pulled.sync, reset);
  const sent = [];
  const fetch = async (url, o) => {
    sent.push(JSON.parse(o.body));
    return sent.length === 1
      ? { status: 409, ok: false, json: async () => ({ data: P.extract(server), version: 4 }) } // another device wrote meanwhile
      : { status: 200, ok: true, json: async () => ({ version: 5 }) };
  };
  const r = await P.push("https://x/profile", "t", snap.data, snap.base, fetch, snap.force);
  assert.deepEqual([r.status, r.merged], ["ok", false]);
  assert.ok(P.isEmpty(sent[1].data)); // the overwrite is empty, not the server lists
  assert.equal(sent[1].base_version, 4);
});
