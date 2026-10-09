"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../src/nightcrawler/web/calendar.js");

test("monthGrid: October 2026 starts on a Thursday, Monday first (3 blanks), 31 days", () => {
  const g = C.monthGrid("2026-10");
  assert.equal(g.slice(0, 3).every((x) => x === null), true);
  assert.equal(g[3], "2026-10-01");
  assert.equal(g.length, 3 + 31);
  assert.equal(g.at(-1), "2026-10-31");
});

test("monthGrid: a month starting on Monday has no blank; February of a leap year has 29 days", () => {
  assert.equal(C.monthGrid("2027-02")[0], "2027-02-01"); // 1 Feb 2027 is a Monday
  assert.equal(C.monthGrid("2028-02").filter(Boolean).length, 29);
  assert.deepEqual(C.monthGrid("nope"), []);
});

test("addMonths and monthOf cross the year", () => {
  assert.equal(C.addMonths("2026-12", 1), "2027-01");
  assert.equal(C.addMonths("2027-01", -1), "2026-12");
  assert.equal(C.monthOf("2026-10-13"), "2026-10");
  assert.equal(C.monthOf("13/10/2026"), null);
});

test("marker: the strongest kind of the day", () => {
  assert.equal(C.marker([{ discovery: true }, { pick: true }]), "forYou");
  assert.equal(C.marker([{ pick: true }, { must: true }]), "must");
  assert.equal(C.marker([{ discovery: true }]), "discovery");
  assert.equal(C.marker([{}]), null);
  assert.equal(C.marker(undefined), null);
});

test("byDay groups in order and drops bad keys", () => {
  const m = C.byDay([{ k: "2026-10-10", n: 1 }, { k: "x", n: 2 }, { k: "2026-10-10", n: 3 }], (x) => x.k);
  assert.deepEqual([...m.keys()], ["2026-10-10"]);
  assert.deepEqual(m.get("2026-10-10").map((x) => x.n), [1, 3]);
});

test("flags: the ring follows the home's « Découvertes » section, not the bare verdict", () => {
  const picks = new Set(["a"]);
  const shown = C.flags({ c: { id: "b" }, v: { verdict: "discovery", section: "decouvertes" } }, picks);
  const below = C.flags({ c: { id: "c" }, v: { verdict: "discovery", section: "tout_voir" } }, picks);
  const pick = C.flags({ c: { id: "a" } }, picks);
  assert.equal(shown.discovery, true);
  assert.equal(below.discovery, false);
  assert.equal(pick.pick, true);
  assert.equal(pick.discovery, false);
});

test("frenchFirst: 1 -> 1er, other days untouched", () => {
  assert.equal(C.frenchFirst("dimanche 1 novembre"), "dimanche 1er novembre");
  assert.equal(C.frenchFirst("mercredi 11 novembre"), "mercredi 11 novembre");
  assert.equal(C.frenchFirst("samedi 31 octobre"), "samedi 31 octobre");
  assert.equal(C.frenchFirst("lundi 21 décembre"), "lundi 21 décembre");
});
