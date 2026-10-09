"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../../src/nightcrawler/web/visits.js");

const id = (n) => n.toString(16).padStart(12, "0");
const c = (n, aliases) => ({ id: id(n), aliases });
const H = 3600e3;

test("first visit: nothing is new, the list is remembered", () => {
  const r = V.visit(null, [c(1), c(2)], 1000);
  assert.equal(r.isNew.size, 0);
  assert.deepEqual(r.store, { base: [id(1), id(2)], seen: [id(1), id(2)], at: 1000 });
});

test("next visit: concerts absent from the previous visit are new", () => {
  const first = V.visit(null, [c(1)], 0).store;
  const r = V.visit(JSON.stringify(first), [c(1), c(2)], 5 * H);
  assert.deepEqual([...r.isNew], [id(2)]);
});

test("a reload within the same visit keeps the badges", () => {
  const first = V.visit(null, [c(1)], 0).store;
  const second = V.visit(first, [c(1), c(2)], 5 * H);
  const reload = V.visit(second.store, [c(1), c(2)], 5 * H + 10 * 60e3);
  assert.deepEqual([...reload.isNew], [id(2)]);
  // the visit after that one: concert 2 was seen, it is no longer new
  const later = V.visit(reload.store, [c(1), c(2)], 10 * H);
  assert.equal(later.isNew.size, 0);
});

test("a concert merged under a new id is not new when an alias was known", () => {
  const first = V.visit(null, [c(1)], 0).store;
  const r = V.visit(first, [c(9, [id(1)])], 5 * H);
  assert.equal(r.isNew.size, 0);
});

test("stored data is untrusted: bad shapes and ids are dropped", () => {
  assert.equal(V.parse("not json"), null);
  assert.equal(V.parse({ base: "x", seen: [], at: 1 }), null);
  assert.equal(V.parse({ base: [], seen: [], at: -1 }), null);
  assert.deepEqual(V.parse({ base: [id(1), "<img>", 3], seen: [], at: 2 }), { base: [id(1)], seen: [], at: 2 });
  // corrupt storage behaves as a first visit
  assert.equal(V.visit("{", [c(1)], 0).isNew.size, 0);
});

test("a clock gone backwards restarts the memory instead of flagging everything", () => {
  const first = V.visit(null, [c(1)], 10 * H).store;
  const r = V.visit(first, [c(1), c(2)], 1 * H);
  assert.equal(r.isNew.size, 0);
});

test("the stored lists are capped", () => {
  const many = Array.from({ length: V.MAX_IDS + 10 }, (_, i) => c(i + 1));
  const r = V.visit(null, many, 0);
  assert.equal(r.store.seen.length, V.MAX_IDS);
});

test("a concert missing for one run is not new when it comes back", () => {
  const v1 = V.visit(null, [c(1), c(2)], 0).store;
  const v2 = V.visit(v1, [c(1)], 5 * H).store; // concert 2 absent from that run
  const v3 = V.visit(v2, [c(1), c(2)], 10 * H);
  assert.equal(v3.isNew.size, 0);
});
