// « Calendrier » (WIP-97, artboard Calendrier): pure helpers for the month grid. Days are
// "YYYY-MM-DD" keys in the zone's time zone (app.js dayKey), so the arithmetic is done on calendar
// dates in UTC and never shifts with the browser's own time zone.
// Loaded by the page and by `node --test tests/js/*.test.js`.
"use strict";

(function (root) {
  const KEY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
  const MONTH_RE = /^(\d{4})-(\d{2})$/;

  const utc = (y, m, d) => new Date(Date.UTC(y, m - 1, d));
  const iso = (date) => date.toISOString().slice(0, 10);

  // "2026-10" of a day key
  const monthOf = (key) => (KEY_RE.test(key) ? key.slice(0, 7) : null);

  // The month after (step 1) or before (step -1): "2026-12" + 1 -> "2027-01"
  function addMonths(ym, step) {
    const m = MONTH_RE.exec(ym);
    if (!m) return null;
    return iso(utc(Number(m[1]), Number(m[2]) + step, 1)).slice(0, 7);
  }

  // Monday-first grid of a month: null for the blank cells before the 1st, then one key per day.
  function monthGrid(ym) {
    const m = MONTH_RE.exec(ym);
    if (!m) return [];
    const y = Number(m[1]);
    const mo = Number(m[2]);
    const first = utc(y, mo, 1);
    const blanks = (first.getUTCDay() + 6) % 7; // Monday = 0
    const days = utc(y, mo + 1, 0).getUTCDate();
    const cells = Array(blanks).fill(null);
    for (let d = 1; d <= days; d++) cells.push(iso(utc(y, mo, d)));
    return cells;
  }

  // The taste marker of a day, from its items ({must, pick, discovery} flags): the strongest
  // one only, as on the artboard. "must" > "forYou" > "discovery" > null.
  function marker(items) {
    const list = Array.isArray(items) ? items : [];
    if (list.some((x) => x.must)) return "must";
    if (list.some((x) => x.pick)) return "forYou";
    if (list.some((x) => x.discovery)) return "discovery";
    return null;
  }

  // Items grouped by day key: Map key -> items, in the list's order.
  function byDay(items, dayOf) {
    const out = new Map();
    for (const x of Array.isArray(items) ? items : []) {
      const k = dayOf(x);
      if (!KEY_RE.test(k)) continue;
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(x);
    }
    return out;
  }

  const api = { monthOf, addMonths, monthGrid, marker, byDay };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCCalendar = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
