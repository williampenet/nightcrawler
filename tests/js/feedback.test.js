// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../../src/nightcrawler/web/feedback.js");

const CID = "0123456789ab";

test("makeItem keeps valid ids and keys only", () => {
  assert.deepEqual(F.makeItem("like", CID, ["asna", "asna", "Bad Key", 3]), {
    kind: "like",
    concert_id: CID,
    artist_keys: ["asna"],
  });
  assert.deepEqual(F.makeItem("wrong", "nope", ["asna"]).concert_id, null);
  assert.equal(F.makeItem("love", CID, []), null);
  assert.equal(F.makeItem("dislike", "nope", []), null);
  assert.equal(F.makeItem("like", CID, Array.from({ length: 20 }, (_, i) => `a${i}`)).artist_keys.length, 12);
});

test("parseQueue survives junk and caps the queue", () => {
  assert.deepEqual(F.parseQueue("{oops"), []);
  assert.deepEqual(F.parseQueue('{"a":1}'), []);
  assert.deepEqual(F.parseQueue(JSON.stringify([null, { kind: "x" }, { kind: "like", concert_id: CID }])), [
    { kind: "like", concert_id: CID, artist_keys: [] },
  ]);
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
  assert.equal(opts.keepalive, true);
  assert.equal(opts.headers.Authorization, "Bearer tok");
  assert.equal(JSON.parse(opts.body).items.length, 50);
});

test("flush keeps items on failure, reports a refused key, drops rejected batches", async () => {
  const item = F.makeItem("like", CID, ["asna"]);
  let x = io([item], [new Error("offline")]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
  assert.deepEqual(x.box.queue, [item]);
  x = io([item], [503]);
  assert.equal(await F.flush("https://f.example/", "tok", x), "error");
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
