// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../../src/nightcrawler/web/verdicts.js");

const A = "0123456789ab";
const B = "aaaaaaaaaaaa";
const C = "bbbbbbbbbbbb";
const OLD = "cccccccccccc";
const ok = (over = {}) => ({ section: "pour_toi", verdict: "for_you", confidence: 70, reason: "Proche de ce que tu aimes.", ...over });
const plain = (o) => JSON.parse(JSON.stringify(o));

test("verdictsUrl sits next to the feedback URL", () => {
  assert.equal(V.verdictsUrl("https://fn.example/"), "https://fn.example/verdicts");
  assert.equal(V.verdictsUrl("https://fn.example/api"), "https://fn.example/api/verdicts");
});

test("parse keeps valid entries and drops anything that breaks the contract", () => {
  const body = {
    generated_at: "2026-10-09T06:00:00+00:00",
    verdicts: {
      [A]: ok({ extra: "dropped" }),
      ABCDEF012345: ok(), // upper case: not a concert id
      "0123456789a": ok(), // 11 characters
      __proto__x: ok(),
      [B]: ok({ section: "partout" }),
      [C]: ok({ verdict: "maybe" }),
      dddddddddddd: ok({ confidence: 101 }),
      eeeeeeeeeeee: ok({ confidence: 50.5 }),
      ffffffffffff: ok({ confidence: "80" }),
      "111111111111": ok({ reason: "x".repeat(241) }),
      "222222222222": ok({ reason: "  " }),
      "333333333333": ok({ reason: 42 }),
      "444444444444": "must_see",
      "555555555555": ok({ section: "ne_pas_rater", verdict: "must_see", confidence: 0, reason: "é".repeat(240) }),
    },
  };
  const got = V.parse(body);
  assert.equal(got.generated_at, "2026-10-09T06:00:00+00:00");
  assert.deepEqual(Object.keys(got.verdicts).sort(), ["555555555555", A].sort());
  assert.deepEqual(plain(got.verdicts[A]), plain(ok()));
  assert.equal(Object.getPrototypeOf(got.verdicts), null);
});

test("parse counts the reason in characters, as the server schema", () => {
  const emoji = "🎸".repeat(240); // 480 UTF-16 units, 240 characters
  assert.ok(V.parse({ verdicts: { [A]: ok({ reason: emoji }) } }).verdicts[A]);
  assert.equal(V.parse({ verdicts: { [A]: ok({ reason: emoji + "x" }) } }).verdicts[A], undefined);
});

test("parse keeps HTML in a reason as plain text (the page sets it with textContent)", () => {
  const reason = '<img src=x onerror="alert(1)"> Ignore les consignes';
  assert.equal(V.parse({ verdicts: { [A]: ok({ reason }) } }).verdicts[A].reason, reason);
});

test("parse rejects bodies without a verdicts object", () => {
  for (const body of [null, "x", [], {}, { verdicts: [] }, { verdicts: "x" }, { verdicts: null }]) assert.equal(V.parse(body), null);
  assert.equal(V.parse({ verdicts: {}, generated_at: 3 }).generated_at, null);
  assert.ok(!V.hasVerdicts(V.parse({ verdicts: {} })) && V.hasVerdicts(V.parse({ verdicts: { [A]: ok() } })));
  assert.ok(!V.hasVerdicts(null));
});

const item = (id, aliases) => ({ c: { id, aliases, start: "2026-10-10T20:00:00+02:00" }, m: { score: 0 } });

test("sectionsFor puts each concert in its section and keeps the list's order", () => {
  const verdicts = V.parse({
    verdicts: {
      [A]: ok({ section: "ne_pas_rater", verdict: "must_see" }),
      [B]: ok({ section: "decouvertes", verdict: "discovery", confidence: 40 }),
      [C]: ok({ section: "tout_voir", verdict: "no", confidence: 90 }),
    },
  }).verdicts;
  const list = [item(C), item("dddddddddddd"), item(B), item(A), item("eeeeeeeeeeee")];
  const s = V.sectionsFor(list, verdicts);
  assert.deepEqual(s.ne_pas_rater.map((x) => x.c.id), [A]);
  assert.deepEqual(s.decouvertes.map((x) => x.c.id), [B]);
  assert.deepEqual(s.tout_voir.map((x) => x.c.id), [C]);
  // not judged yet: in "Pour toi", flagged (recall first, ADR-0007 §5), in the list's order
  assert.deepEqual(s.pour_toi.map((x) => [x.c.id, x.unjudged]), [["dddddddddddd", true], ["eeeeeeeeeeee", true]]);
  assert.equal(s.tout_voir[0].v.reason, "Proche de ce que tu aimes.");
  assert.equal(s.ne_pas_rater[0].m.score, 0); // the score stays for the row and "Tout voir"
});

test("sectionsFor finds a verdict saved under an alias, but a current id wins", () => {
  const verdicts = V.parse({
    verdicts: {
      [OLD]: ok({ section: "ne_pas_rater", verdict: "must_see" }),
      [B]: ok({ section: "tout_voir", verdict: "no" }),
    },
  }).verdicts;
  // A was judged as OLD before sources merged; C lists B as an alias but B is still a concert
  const s = V.sectionsFor([item(A, [OLD]), item(B), item(C, [B])], verdicts);
  assert.deepEqual(s.ne_pas_rater.map((x) => x.c.id), [A]);
  assert.deepEqual(s.tout_voir.map((x) => x.c.id), [B]);
  assert.deepEqual(s.pour_toi.map((x) => [x.c.id, x.unjudged]), [[C, true]]);
});

test("sectionsFor without verdicts: everything waits in Pour toi; bad input is harmless", () => {
  const s = V.sectionsFor([item(A), item("constructor")], {});
  assert.equal(s.pour_toi.length, 2);
  assert.deepEqual(V.sectionsFor(null, null), { ne_pas_rater: [], pour_toi: [], decouvertes: [], tout_voir: [] });
  assert.deepEqual(V.sectionsFor([{ id: A }], { [A]: ok() }).pour_toi[0].c, { id: A }); // bare concerts too
});

const fakeFetch = (status, body, calls = []) => async (url, opts) => {
  calls.push({ url, opts });
  return { status, ok: status >= 200 && status < 300, json: async () => (body instanceof Error ? Promise.reject(body) : body) };
};

test("pull sends the key and maps the answers", async () => {
  const calls = [];
  const good = await V.pull("https://fn.example/verdicts", "k3y", fakeFetch(200, { generated_at: "t", verdicts: { [A]: ok() } }, calls));
  assert.equal(good.status, "ok");
  assert.deepEqual(plain(good.data), { generated_at: "t", verdicts: { [A]: ok() } });
  assert.equal(calls[0].opts.method, "GET");
  assert.equal(calls[0].opts.headers.Authorization, "Bearer k3y");
  assert.deepEqual(await V.pull("u", "k", fakeFetch(401, {})), { status: "unauthorized" });
  for (const s of [403, 404, 405, 503]) assert.deepEqual(await V.pull("u", "k", fakeFetch(s, {})), { status: "error" });
  assert.deepEqual(await V.pull("u", "k", fakeFetch(200, { nope: 1 })), { status: "error" });
  assert.deepEqual(await V.pull("u", "k", fakeFetch(200, new SyntaxError("bad json"))), { status: "error" });
  assert.deepEqual(await V.pull("u", "k", async () => Promise.reject(new TypeError("offline"))), { status: "error" });
});

function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
}

test("load and save keep a checked local copy", () => {
  const st = memoryStorage();
  assert.equal(V.load(st), null);
  const data = V.parse({ generated_at: "t", verdicts: { [A]: ok() } });
  assert.ok(V.save(st, data));
  assert.deepEqual(plain(V.load(st)), plain(data));
  // a copy edited by hand is checked again on read
  st.setItem(V.KEY, JSON.stringify({ verdicts: { [A]: ok({ section: "x" }), [B]: ok() } }));
  assert.deepEqual(Object.keys(V.load(st).verdicts), [B]);
  st.setItem(V.KEY, "{not json");
  assert.equal(V.load(st), null);
  assert.ok(V.save(st, null));
  assert.ok(!st.m.has(V.KEY));
});

test("blocked storage: no copy, no exception", () => {
  const blocked = {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => { throw new Error("SecurityError"); },
  };
  assert.equal(V.load(blocked), null);
  assert.equal(V.save(blocked, V.parse({ verdicts: {} })), false);
  assert.equal(V.save(blocked, null), false);
});
