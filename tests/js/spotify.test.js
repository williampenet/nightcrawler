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
  assert.equal(u.searchParams.get("scope"), "user-top-read user-follow-read");
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
  assert.equal(calls.length, 5);
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
  assert.deepEqual(SP.consumeCallback("?code=C&state=S", s, "k"), { status: "ok", code: "C", verifier: "V" });
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
