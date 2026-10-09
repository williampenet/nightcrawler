// Home period chips (WIP-108): « Toutes les dates » is a chip of its own. node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const S = require("../../src/nightcrawler/web/scoring.js");

const html = fs.readFileSync(path.join(__dirname, "../../src/nightcrawler/web/index.html"), "utf8");
const chips = () => {
  const row = html.match(/<div class="periods"[^>]*>([\s\S]*?)<\/div>/)[1];
  return [...row.matchAll(/<button type="button" data-when="([^"]+)">([^<]+)<\/button>/g)].map((m) => [m[1], m[2]]);
};

test("four chips, in order, each one a period the filter knows", () => {
  assert.deepEqual(chips(), [
    ["tonight", "Ce soir"],
    ["weekend", "Ce week-end"],
    ["7d", "Cette semaine"],
    ["all", "Toutes les dates"],
  ]);
  assert.deepEqual(Object.keys(S.PERIODS), chips().map(([k]) => k));
  assert.equal(S.DEFAULT_PERIOD, "7d"); // the home opens on « Cette semaine » (PM, 2026-10-09)
});

test("a tap selects that chip; tapping the active chip keeps it (no hidden state)", () => {
  assert.equal(S.choosePeriod("7d", "all"), "all");
  assert.equal(S.choosePeriod("all", "tonight"), "tonight");
  // before WIP-108 a second tap on the active chip switched to every date
  for (const k of Object.keys(S.PERIODS)) assert.equal(S.choosePeriod(k, k), k);
  // an unknown value (old page, edited markup) changes nothing
  assert.equal(S.choosePeriod("weekend", "month"), "weekend");
  assert.equal(S.choosePeriod("weekend", undefined), "weekend");
  assert.equal(S.choosePeriod("weekend", "toString"), "weekend");
});

test("headings carry the period, none for « Toutes les dates »", () => {
  assert.equal(S.withPeriod("Pour toi", "tonight"), "Pour toi ce soir");
  assert.equal(S.withPeriod("Pour toi", "weekend"), "Pour toi ce week-end");
  assert.equal(S.withPeriod("Tous les concerts", "7d"), "Tous les concerts cette semaine");
  assert.equal(S.withPeriod("Pour toi", "all"), "Pour toi");
  assert.equal(S.withPeriod("Tous les concerts", "all"), "Tous les concerts");
});

test("« Toutes les dates » keeps a concert 3 months ahead; « Cette semaine » does not", () => {
  const key = (d) => d.toISOString().slice(0, 10);
  const now = new Date("2026-10-10T12:00:00Z");
  const far = "2027-01-15T20:00:00Z"; // the pipeline collects up to 400 days ahead (WIP-107)
  assert.equal(S.inWhen("all", far, now, key), true);
  assert.equal(S.inWhen("7d", far, now, key), false);
  assert.equal(S.inWhen("7d", "2026-10-15T20:00:00Z", now, key), true);
});

test("the saved-state default is the period the home opens on", () => {
  assert.equal(S.defaultState().when, S.DEFAULT_PERIOD);
  assert.equal(S.sanitizeState({}).when, S.DEFAULT_PERIOD);
});
