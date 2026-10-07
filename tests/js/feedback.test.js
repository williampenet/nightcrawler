// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../../src/nightcrawler/web/feedback.js");

const CID = "0123456789ab";

test("makeItem keeps valid ids and keys only", () => {
  const item = F.makeItem("like", CID, ["asna", "asna", "Bad Key", 3]);
  assert.ok(typeof item.id === "string" && item.id.length > 0);
  assert.deepEqual({ ...item, id: undefined }, { id: undefined, kind: "like", concert_id: CID, artist_keys: ["asna"] });
  assert.notEqual(F.makeItem("like", CID, []).id, F.makeItem("like", CID, []).id);
  assert.deepEqual(F.makeItem("wrong", "nope", ["asna"]).concert_id, null);
  assert.equal(F.makeItem("love", CID, []), null);
  assert.equal(F.makeItem("dislike", "nope", []), null);
  assert.equal(F.makeItem("like", CID, Array.from({ length: 20 }, (_, i) => `a${i}`)).artist_keys.length, 12);
});

test("parseQueue survives junk and caps the queue", () => {
  assert.deepEqual(F.parseQueue("{oops"), []);
  assert.deepEqual(F.parseQueue('{"a":1}'), []);
  assert.deepEqual(
    F.parseQueue(JSON.stringify([null, { kind: "x" }, { id: "i1", kind: "like", concert_id: CID }])),
    [{ id: "i1", kind: "like", concert_id: CID, artist_keys: [] }],
  );
  let q = [];
  for (let i = 0; i < 250; i++) q = F.enqueue(q, F.makeItem("like", null, [`a${i}`]));
  assert.equal(q.length, F.MAX_QUEUE);
  assert.equal(q[0].artist_keys[0], "a50"); // oldest dropped
  assert.equal(F.enqueue(q, null), q);
});

test("nextBatch stays under 50 items and the body limit", () => {
  const small = Array.from({ length: 80 }, (_, i) => F.makeItem("like", CID, [`a${i}`]));
  assert.equal(F.nextBatch(small).length, 50);
  const big = Array.from({ length: 10 }, () =>
    F.makeItem("like", CID, Array.from({ length: 12 }, (_, i) => "x".repeat(90) + i)),
  );
  const b = F.nextBatch(big);
  assert.ok(b.length >= 1 && JSON.stringify({ items: b }).length <= 4000);
});

function io(queue, responses) {
  const calls = [];
  const box = { queue };
  return {
    box,
    calls,
    load: () => box.queue,
    save: (q) => (box.queue = q),
    fetch: async (url, opts) => {
      calls.push({ url, opts });
      const r = responses.shift();
      if (r instanceof Error) throw r;
      return { status: r, ok: r >= 200 && r < 300 };
    },
  };
}

test("flush sends in batches and removes sent items", async () => {
  const items = Array.from({ length: 60 }, (_, i) => F.makeItem("dislike", CID, [`a${i}`]));
  const x = io(items, [202, 202]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "sent");
  assert.equal(x.calls.length, 2);
  assert.deepEqual(x.box.queue, []);
  const opts = x.calls[0].opts;
  assert.equal(opts.method, "POST");
  assert.equal(opts.headers.Authorization, "Bearer tok");
  const sent = JSON.parse(opts.body).items;
  assert.equal(sent.length, 50);
  assert.deepEqual(Object.keys(sent[0]), ["kind", "concert_id", "artist_keys"]); // local id stays local
});

test("flush keeps items on failure, reports a refused key, drops rejected batches", async () => {
  const item = F.makeItem("like", CID, ["asna"]);
  let x = io([item], [new Error("offline")]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
  assert.deepEqual(x.box.queue, [item]);
  x = io([item], [503]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
  assert.deepEqual(x.box.queue, [item]);
  x = io([item], [429]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "error"); // rate limited: kept
  assert.deepEqual(x.box.queue, [item]);
  x = io([item], [401]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "unauthorized");
  assert.deepEqual(x.box.queue, [item]);
  x = io([item], [400]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "sent");
  assert.deepEqual(x.box.queue, []);
  assert.equal(await F.flush("", "tok", x), "unconfigured");
  assert.equal(await F.flush("https://f.example/", "", x), "unconfigured");
  assert.equal(await F.flush("https://f.example/", "tok", x), "empty");
});

test("items queued while a request is in flight survive, even at full cap", async () => {
  let q = [];
  for (let i = 0; i < F.MAX_QUEUE; i++) q = F.enqueue(q, F.makeItem("like", null, [`a${i}`]));
  const x = io(q, []);
  const added = F.makeItem("dislike", CID, ["new"]);
  let calls = 0;
  x.fetch = async (url, opts) => {
    calls++;
    if (calls === 1) {
      x.box.queue = F.enqueue(x.box.queue, added); // trims a0, the oldest, mid-request
      return { status: 503, ok: false };
    }
    return { status: 202, ok: true };
  };
  // first flush fails: queue = a1..a199 + added, nothing else lost
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
  assert.equal(x.box.queue.length, F.MAX_QUEUE);
  assert.equal(x.box.queue[0].artist_keys[0], "a1");
  assert.equal(x.box.queue.at(-1), added);
  // now succeed, with another enqueue during the first batch: only sent ids are removed
  const late = F.makeItem("like", CID, ["late"]);
  let n = 0;
  x.fetch = async (url, opts) => {
    if (n++ === 0) x.box.queue = F.enqueue(x.box.queue, late);
    return { status: 202, ok: true };
  };
  assert.equal(await F.flush("https://f.example/", "tok", x), "sent");
  assert.deepEqual(x.box.queue, []);
  assert.ok(n >= 5); // 201 items, 50 per request
});

test("a trim during the request does not remove unsent items", async () => {
  let q = [];
  for (let i = 0; i < F.MAX_QUEUE; i++) q = F.enqueue(q, F.makeItem("like", null, [`a${i}`]));
  const x = io(q, []);
  const added = F.makeItem("dislike", CID, ["new"]);
  let first = true;
  x.fetch = async () => {
    if (first) {
      first = false;
      x.box.queue = F.enqueue(x.box.queue, added); // a0 trimmed while a0..a49 are in flight
      return { status: 202, ok: true };
    }
    return { status: 503, ok: false }; // stop after the first batch
  };
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
  // a0..a49 sent (a0 was also trimmed): a50..a199 + added remain, nothing else lost
  assert.equal(x.box.queue.length, 151);
  assert.equal(x.box.queue[0].artist_keys[0], "a50");
  assert.equal(x.box.queue.at(-1), added);
});

test("pendingBanner warns only while ratings wait for a key (WIP-70)", () => {
  assert.equal(F.pendingBanner(39, false), "39 avis en attente d'envoi : saisis ta clé d'envoi");
  assert.equal(F.pendingBanner(1, false), "1 avis en attente d'envoi : saisis ta clé d'envoi");
  assert.equal(F.pendingBanner(39, true), null);
  assert.equal(F.pendingBanner(0, false), null);
  assert.equal(F.pendingBanner(-1, false), null);
  assert.equal(F.pendingBanner(undefined, false), null);
});
