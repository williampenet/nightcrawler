"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const SP = require("../../src/nightcrawler/web/spotify.js");

test("PKCE challenge matches RFC 7636 example", async () => {
  const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
  assert.equal(await SP.challengeFor(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
});

test("random strings are url-safe and unique", () => {
  const a = SP.randomString();
  assert.match(a, /^[A-Za-z0-9_-]{64}$/);
  assert.notEqual(a, SP.randomString());
});

test("client id validation", () => {
  assert.equal(SP.validClientId("0123456789abcdef0123456789abcdef"), true);
  assert.equal(SP.validClientId(""), false);
  assert.equal(SP.validClientId("x".repeat(32)), false);
});

test("authorize URL", () => {
  const u = new URL(SP.authorizeUrl({ clientId: "c", redirectUri: "https://w.github.io/n/", challenge: "ch", state: "st" }));
  assert.equal(u.origin + u.pathname, "https://accounts.spotify.com/authorize");
  assert.equal(u.searchParams.get("code_challenge_method"), "S256");
  assert.equal(u.searchParams.get("redirect_uri"), "https://w.github.io/n/");
  assert.equal(u.searchParams.get("scope"), SP.SCOPES);
  assert.equal(u.searchParams.get("state"), "st");
});

test("artist names: top + followed, deduped, paginated", async () => {
  const calls = [];
  const fake = async (url) => {
    calls.push(url);
    const json = url.includes("/me/top/artists")
      ? { items: [{ name: "Earth" }, { name: "Boris" }, null] }
      : url.includes("after=c1")
        ? { artists: { items: [{ name: "Moondog" }], cursors: { after: null } } }
        : { artists: { items: [{ name: "earth" }, { name: "Sunn O)))" }], cursors: { after: "c1" } } };
    return { ok: true, status: 200, json: async () => json };
  };
  const { names, failed } = await SP.artistNames("tok", fake);
  assert.deepEqual(names, ["Earth", "Boris", "Sunn O)))", "Moondog"]);
  assert.equal(failed, 0);
  assert.equal(calls.length, 7); // 3 top + 2 followed pages + 1 liked page + recently played
});

test("artist names: liked and recent tracks add artists, most frequent first", async () => {
  const track = (...names) => ({ track: { artists: names.map((name) => ({ name })) } });
  const fake = async (url) => {
    let json = {};
    if (url.includes("/me/top/artists")) json = { items: [{ name: "Earth" }] };
    else if (url.includes("/me/following")) json = { artists: { items: [], cursors: {} } };
    else if (url.includes("/me/tracks?limit=50&offset=0"))
      json = { items: [track("Low"), track("Moondog", "Low"), track("Earth")], next: "p2" };
    else if (url.includes("/me/tracks")) json = { items: [track("Moondog")], next: null };
    else if (url.includes("recently-played")) json = { items: [track("Low"), track("Arca")] };
    return { ok: true, status: 200, json: async () => json };
  };
  const { names } = await SP.artistNames("t", fake);
  assert.deepEqual(names, ["Earth", "Low", "Moondog", "Arca"]);
});

test("the import asks for liked tracks and recently played", () => {
  const u = new URL(SP.authorizeUrl({ clientId: "c", redirectUri: "https://x/", challenge: "h", state: "s" }));
  assert.equal(u.searchParams.get("scope"),
    "user-top-read user-follow-read user-library-read user-read-recently-played");
});

test("403 means the account is not allowed on the app", async () => {
  const fake = async () => ({ ok: false, status: 403, json: async () => ({}) });
  await assert.rejects(SP.artistNames("tok", fake), /forbidden/);
});

test("partial import is reported, 401 throws", async () => {
  const flaky = async (url) =>
    url.includes("short_term")
      ? { ok: false, status: 503, json: async () => ({}) }
      : { ok: true, status: 200, json: async () => ({ items: [{ name: "A" }], artists: { items: [], cursors: {} } }) };
  const r = await SP.artistNames("t", flaky);
  assert.equal(r.failed, 1);
  await assert.rejects(SP.artistNames("t", async () => ({ ok: false, status: 401 })), /unauthorized/);
});

function store(initial) {
  const m = new Map(Object.entries(initial || {}));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), removeItem: (k) => m.delete(k), m };
}

test("callback: ok path consumes the saved entry", () => {
  const s = store({ k: JSON.stringify({ verifier: "V", state: "S" }) });
  assert.deepEqual(SP.consumeCallback("?code=C&state=S", s, "k"), { status: "ok", code: "C", verifier: "V", probe: false });
  assert.equal(s.m.size, 0);
});

test("callback: every failure is rejected and clears storage", () => {
  const cases = [
    ["?code=C&state=WRONG", { k: JSON.stringify({ verifier: "V", state: "S" }) }, "state"],
    ["?code=C", { k: JSON.stringify({ verifier: "V", state: "S" }) }, "state"],
    ["?code=C&state=S", {}, "state"],
    ["?code=C&state=S", { k: "not json" }, "state"],
    ["?error=access_denied&state=S", { k: JSON.stringify({ verifier: "V", state: "S" }) }, "denied"],
  ];
  for (const [search, init, reason] of cases) {
    const s = store(init);
    assert.deepEqual(SP.consumeCallback(search, s, "k"), { status: "error", reason });
    assert.equal(s.m.size, 0);
  }
  assert.deepEqual(SP.consumeCallback("?q=1", store(), "k"), { status: "none" });
});

test("token exchange sends exactly the PKCE fields, no secret", async () => {
  let sent = null;
  const fake = async (url, opts) => {
    sent = { url, body: Object.fromEntries(new URLSearchParams(String(opts.body))) };
    return { ok: true, json: async () => ({ access_token: "T", refresh_token: "R" }) };
  };
  const token = await SP.exchangeCode({ clientId: "c", redirectUri: "https://x/n/", code: "C", verifier: "V" }, fake);
  assert.equal(token, "T");
  assert.equal(sent.url, "https://accounts.spotify.com/api/token");
  assert.deepEqual(Object.keys(sent.body).sort(), ["client_id", "code", "code_verifier", "grant_type", "redirect_uri"]);
  await assert.rejects(SP.exchangeCode({}, async () => ({ ok: false, status: 400 })), /token 400/);
});

test("redirect URI ignores query, hash and index.html", () => {
  assert.equal(SP.redirectUriFor({ origin: "https://w.github.io", pathname: "/nightcrawler/index.html" }), "https://w.github.io/nightcrawler/");
  assert.equal(SP.redirectUriFor({ origin: "https://w.github.io", pathname: "/nightcrawler/" }), "https://w.github.io/nightcrawler/");
});

test("probe reports statuses and field presence only (WIP-48)", async () => {
  const answers = {
    "/me/top/artists": { status: 200, body: { items: [{ id: "a1", name: "Secret Name", genres: ["noise"] }] } },
    "/me/top/tracks": { status: 200, body: { items: [{ id: "t1", name: "Song" }] } },
    "/artists/a1/related-artists": { status: 404, body: {} },
    "/recommendations": { status: 404, body: {} },
    "/audio-features": { status: 403, body: {} },
  };
  const calls = [];
  const fake = async (url, opts) => {
    calls.push(opts.headers.Authorization);
    const path = new URL(url).pathname.replace("/v1", "");
    const a = answers[path] || { status: 200, body: {} };
    return { ok: a.status < 300, status: a.status, json: async () => a.body };
  };
  const rows = await SP.probe("tok", fake);
  const by = Object.fromEntries(rows.map((r) => [r.label, r]));
  assert.match(by["Top artistes"].detail, /genres: 1, popularity: non, followers: non/);
  assert.equal(by["Artistes proches"].status, 404);
  assert.equal(by["Caractéristiques audio"].status, 403);
  assert.ok(calls.every((h) => h === "Bearer tok"));
  const text = SP.probeText(rows, "2026-10-06T15:00");
  assert.ok(!text.includes("Secret Name") && !text.includes("a1") && !text.includes("tok"));
});

test("probe scopes are only used when asked", () => {
  const u = (scope) => new URL(SP.authorizeUrl({ clientId: "c", redirectUri: "https://x/", challenge: "h", state: "s", scope }));
  assert.equal(u(undefined).searchParams.get("scope"), SP.SCOPES);
  assert.match(u(SP.PROBE_SCOPES).searchParams.get("scope"), /playlist-read-private/);
});

test("liked tracks: a failed page stops the loop and is counted; at most 10 pages", async () => {
  const track = (name) => ({ track: { artists: [{ name }] } });
  let pages = 0;
  const fake = (failAt) => async (url) => {
    if (url.includes("/me/tracks")) {
      pages++;
      if (pages === failAt) return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ items: [track("LOW"), track("Low")], next: "more" }) };
    }
    return { ok: true, status: 200, json: async () => ({ items: [], artists: { items: [], cursors: {} } }) };
  };
  let r = await SP.artistNames("t", fake(2));
  assert.equal(pages, 2);
  assert.equal(r.failed, 1);
  assert.deepEqual(r.names, ["LOW"]);
  pages = 0;
  r = await SP.artistNames("t", fake(0));
  assert.equal(pages, 10);
});
