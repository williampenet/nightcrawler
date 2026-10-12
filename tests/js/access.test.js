"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const A = require("../../src/nightcrawler/web/access.js");

const URL_ = "https://fn.example/";

test("owner needs a service, a key and no refusal", () => {
  assert.equal(A.isOwner({ feedbackUrl: URL_, token: "k", refused: false }), true);
  assert.equal(A.isOwner({ feedbackUrl: URL_, token: "", refused: false }), false);
  assert.equal(A.isOwner({ feedbackUrl: URL_, token: "   ", refused: false }), false);
  assert.equal(A.isOwner({ feedbackUrl: URL_, token: "k", refused: true }), false);
  assert.equal(A.isOwner({ feedbackUrl: "", token: "k", refused: false }), false);
  assert.equal(A.isOwner({ feedbackUrl: URL_, token: null, refused: false }), false);
});

test("the key field: stored key or the hidden #cle link, never a plain visitor", () => {
  assert.equal(A.keyFieldShown({ feedbackUrl: URL_, token: "", hash: "" }), false);
  assert.equal(A.keyFieldShown({ feedbackUrl: URL_, token: "", hash: "#gouts" }), false);
  assert.equal(A.keyFieldShown({ feedbackUrl: URL_, token: "", hash: "#cle" }), true);
  assert.equal(A.keyFieldShown({ feedbackUrl: URL_, token: "refused-or-not", hash: "" }), true);
  assert.equal(A.keyFieldShown({ feedbackUrl: "", token: "k", hash: "#cle" }), false);
});

test("public profile body", () => {
  assert.deepEqual(A.publicProfile({ data: { taste_text: "x" } }), { taste_text: "x" });
  for (const bad of [null, [], "x", {}, { data: [] }, { data: "x" }, { data: null }]) {
    assert.equal(A.publicProfile(bad), null);
  }
});
