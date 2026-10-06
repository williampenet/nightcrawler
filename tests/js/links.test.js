// node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../src/nightcrawler/web/scoring.js");

test("concertLinks renders every merged link once, http(s) only", () => {
  const c = {
    url: "https://lamarquise.example/molotovs",
    ticket_url: "https://www.ticketmaster.fr/molotovs",
    links: [
      { label: "Page", url: "https://lamarquise.example/molotovs" },
      { label: "Billets", url: "https://www.ticketmaster.fr/molotovs" },
      { label: "Billets", url: "https://www.ticketmaster.fr/molotovs" },
      { label: "x", url: "javascript:alert(1)" },
    ],
  };
  assert.deepEqual(S.concertLinks(c), [
    { label: "Page", url: "https://lamarquise.example/molotovs" },
    { label: "Billets", url: "https://www.ticketmaster.fr/molotovs" },
  ]);
});

test("concertLinks falls back to url and ticket_url for older data", () => {
  assert.deepEqual(S.concertLinks({ url: "https://a.example", ticket_url: "https://a.example" }), [
    { label: "Page", url: "https://a.example" },
  ]);
  assert.deepEqual(S.concertLinks({ url: null, ticket_url: "https://t.example" }), [
    { label: "Billets", url: "https://t.example" },
  ]);
});
