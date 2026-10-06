// Taste profile synced with the feedback function (WIP-46, ADR-0005): pure helpers + GET/PUT.
// The profile is the listener's choices only (seed names and tags, ratings, hidden concerts);
// no Spotify token (ADR-0003). Loaded by the page and by `node --test tests/js`.
"use strict";

(function (root) {
  const S = typeof module !== "undefined" && module.exports ? require("./scoring.js") : root.NCScoring;
  const SYNC_KEY = "nightcrawler.profile.sync"; // {version, dirty, force}: this browser only
  const KEY_LISTS = ["liked", "disliked", "wrong", "likedNames", "dislikedNames"];
  const ID_LISTS = ["hidden", "likedConcerts"];
  const KEY_RE = /^[a-z0-9]{1,100}$/;
  const ID_RE = /^[0-9a-f]{12}$/;
  const MAX_SEEDS = 200;
  const MAX_LIST = 2000;

  // The profile part of a state, in the shape and limits the function accepts.
  function extract(state) {
    const s = state || {};
    const out = {};
    const seen = new Set();
    out.seeds = (Array.isArray(s.seeds) ? s.seeds : [])
      .filter((x) => x && typeof x.name === "string" && x.name.length > 0 && x.name.length <= 60)
      .filter((x) => !seen.has(S.norm(x.name)) && seen.add(S.norm(x.name)))
      .slice(0, MAX_SEEDS)
      .map((x) => ({
        name: x.name,
        tags: Array.isArray(x.tags) ? x.tags.filter((t) => typeof t === "string" && t.length > 0 && t.length <= 100).slice(0, 12) : null,
      }));
    const list = (v, re) => [...new Set(Array.isArray(v) ? v : [])].filter((x) => typeof x === "string" && re.test(x)).slice(-MAX_LIST);
    for (const k of KEY_LISTS) out[k] = list(s[k], KEY_RE);
    for (const k of ID_LISTS) out[k] = list(s[k], ID_RE);
    return out;
  }

  const isEmpty = (p) => !p.seeds.length && [...KEY_LISTS, ...ID_LISTS].every((k) => !p[k].length);

  // Conflict merge: union of the lists, server seeds first then local seeds it lacks.
  function merge(server, local) {
    const a = extract(server);
    const b = extract(local);
    const out = {};
    for (const k of [...KEY_LISTS, ...ID_LISTS]) out[k] = [...new Set([...a[k], ...b[k]])].slice(-MAX_LIST);
    out.seeds = extract({ seeds: [...a.seeds, ...b.seeds] }).seeds;
    return out;
  }

  const headers = (token) => ({ "Content-Type": "application/json", Authorization: `Bearer ${token}` });

  // {status: "found", data, version} | {status: "none"} | {status: "unauthorized" | "error"}
  async function pull(url, token, fetch) {
    try {
      const res = await fetch(url, { method: "GET", headers: headers(token) });
      if (res.status === 404) return { status: "none" };
      if (res.status === 401) return { status: "unauthorized" };
      if (!res.ok) return { status: "error" };
      const body = await res.json();
      if (!Number.isInteger(body.version)) return { status: "error" };
      return { status: "found", data: extract(body.data), version: body.version };
    } catch {
      return { status: "error" };
    }
  }

  // PUT with base_version; on 409 merges with the server copy (or, with force, overwrites it)
  // and retries once. {status: "ok", data, version, merged} | {status: "unauthorized" | "error"}
  async function push(url, token, data, base, fetch, force = false) {
    let merged = false;
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const res = await fetch(url, { method: "PUT", headers: headers(token), body: JSON.stringify({ data, base_version: base }) });
        if (res.status === 409 && attempt === 0) {
          const cur = await res.json();
          base = Number.isInteger(cur.version) ? cur.version : 0;
          if (!force) {
            data = merge(cur.data || {}, data);
            merged = true;
          }
          continue;
        }
        if (res.status === 401) return { status: "unauthorized" };
        if (!res.ok) return { status: "error" };
        const body = await res.json();
        if (!Number.isInteger(body.version)) return { status: "error" };
        return { status: "ok", data, version: body.version, merged };
      }
    } catch {
      /* offline or function down */
    }
    return { status: "error" };
  }

  // The profile URL next to the feedback URL ("https://x/" -> "https://x/profile").
  function profileUrl(feedbackUrl) {
    const u = new URL(feedbackUrl);
    u.pathname = u.pathname.replace(/\/?$/, "/profile");
    return u.href;
  }

  function parseSync(text) {
    try {
      const m = JSON.parse(text || "{}");
      return { version: Number.isInteger(m.version) && m.version >= 0 ? m.version : 0, dirty: m.dirty === true, force: m.force === true };
    } catch {
      return { version: 0, dirty: false, force: false };
    }
  }

  const api = { SYNC_KEY, extract, isEmpty, merge, pull, push, profileUrl, parseSync };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCProfile = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
