// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../src/nightcrawler/web/scoring.js");

test("lineupText lists every act when there are two or more (WIP-72)", () => {
  const c = { lineup: ["Tomoyuki Aoki", "Harutaka Mochizuki"] };
  assert.equal(S.lineupText(c), "Avec : Tomoyuki Aoki, Harutaka Mochizuki");
  const three = { lineup: ["Lucio Bukowski", "Anton Serra", "OSter Lapwass"] };
  assert.equal(S.lineupText(three), "Avec : Lucio Bukowski, Anton Serra, OSter Lapwass");
});

test("lineupText is null for one act, older data or junk", () => {
  assert.equal(S.lineupText({ lineup: ["Earth, Wind & Fire"] }), null);
  assert.equal(S.lineupText({}), null); // data published before WIP-72
  assert.equal(S.lineupText({ lineup: "A, B" }), null);
  assert.equal(S.lineupText({ lineup: ["Pord", " ", null, 3] }), null);
  assert.equal(S.lineupText(null), null);
});

test("lineupText keeps markup as plain text (the card sets it with textContent)", () => {
  const c = { lineup: ["<img src=x onerror=alert(1)>", "Pord"] };
  assert.equal(S.lineupText(c), "Avec : <img src=x onerror=alert(1)>, Pord");
});
