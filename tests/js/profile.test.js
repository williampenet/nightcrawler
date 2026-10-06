// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../../src/nightcrawler/web/profile.js");

const CID = "0123456789ab";
const EMPTY = { seeds: [], liked: [], disliked: [], wrong: [], likedNames: [], dislikedNames: [], hidden: [], likedConcerts: [] };

test("extract keeps the profile fields in the function's shape and limits", () => {
  const p = P.extract({
    seeds: [{ name: "Asna", tags: ["drone", 3] }, { name: "ASNA", tags: null }, { name: "x".repeat(61) }, { name: "Boris" }],
    liked: ["asna", "asna", "Bad Key"],
    hidden: [CID, "nope"],
    sort: "me",
  });
  assert.deepEqual(p, { ...EMPTY, seeds: [{ name: "Asna", tags: ["drone"] }, { name: "Boris", tags: null }], liked: ["asna"], hidden: [CID] });
  assert.ok(P.isEmpty(P.extract({ sort: "me" })) && !P.isEmpty(p));
  assert.equal(P.extract({ wrong: Array.from({ length: 2100 }, (_, i) => `a${i}`) }).wrong.length, 2000);
});

test("merge is a union, server seeds first", () => {
  const server = { seeds: [{ name: "Asna", tags: ["drone"] }], liked: ["asna"], hidden: [CID] };
  const local = { seeds: [{ name: "asna", tags: null }, { name: "Boris", tags: null }], liked: ["boris"], dislikedNames: ["x"] };
  assert.deepEqual(P.merge(server, local), {
    ...EMPTY,
    seeds: [{ name: "Asna", tags: ["drone"] }, { name: "Boris", tags: null }],
    liked: ["asna", "boris"],
    dislikedNames: ["x"],
    hidden: [CID],
  });
});

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test("pull reads the profile, 404 and failures", async () => {
  const calls = [];
  const fetch = async (u, o) => (calls.push([u, o.method, o.headers.Authorization]), reply(200, { data: { liked: ["asna"] }, version: 3 }));
  assert.deepEqual(await P.pull("https://f/profile", "k", fetch), { status: "found", data: { ...EMPTY, liked: ["asna"] }, version: 3 });
  assert.deepEqual(calls, [["https://f/profile", "GET", "Bearer k"]]);
  assert.deepEqual(await P.pull("u", "k", async () => reply(404, {})), { status: "none" });
  assert.deepEqual(await P.pull("u", "k", async () => reply(401, {})), { status: "unauthorized" });
  assert.deepEqual(await P.pull("u", "k", async () => { throw new Error("offline"); }), { status: "error" });
});

test("push retries once after a 409, merging or (force) overwriting", async () => {
  const sent = [];
  let n = 0;
  const fetch = async (u, o) => {
    sent.push(JSON.parse(o.body));
    return n++ === 0 ? reply(409, { data: { liked: ["asna"] }, version: 5 }) : reply(200, { version: 6 });
  };
  const local = { ...EMPTY, liked: ["boris"] };
  const r = await P.push("u", "k", local, 4, fetch);
  assert.deepEqual(r, { status: "ok", data: { ...EMPTY, liked: ["asna", "boris"] }, version: 6, merged: true });
  assert.deepEqual(sent.map((b) => b.base_version), [4, 5]);
  n = 0;
  const forced = await P.push("u", "k", EMPTY, 4, fetch, true);
  assert.deepEqual(forced.data, EMPTY);
  const always409 = async () => reply(409, { data: null, version: 1 });
  assert.deepEqual(await P.push("u", "k", EMPTY, 0, always409), { status: "error" });
  assert.deepEqual(await P.push("u", "k", EMPTY, 0, async () => reply(503, {})), { status: "error" });
});

test("profileUrl and parseSync", () => {
  assert.equal(P.profileUrl("https://f.example"), "https://f.example/profile");
  assert.equal(P.profileUrl("https://f.example/fn/"), "https://f.example/fn/profile");
  assert.deepEqual(P.parseSync("{oops"), { version: 0, dirty: false, force: false });
  assert.deepEqual(P.parseSync('{"version":4,"dirty":true}'), { version: 4, dirty: true, force: false });
});
