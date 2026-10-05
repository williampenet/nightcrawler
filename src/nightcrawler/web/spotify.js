// Spotify login in the browser: OAuth Authorization Code + PKCE (no server, no secret).
// Pure helpers + two network calls; loaded by the page and by `node --test tests/js`.
"use strict";

(function (root) {
  const AUTHORIZE = "https://accounts.spotify.com/authorize";
  const TOKEN = "https://accounts.spotify.com/api/token";
  const API = "https://api.spotify.com/v1";
  const SCOPES = "user-top-read user-follow-read";
  const CLIENT_ID_RE = /^[0-9a-f]{32}$/;
  const MAX_IMPORT = 100; // cap: each new seed costs one MusicBrainz call (1 per second)

  function base64url(bytes) {
    let s = "";
    for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function randomString(bytes = 48) {
    const buf = new Uint8Array(bytes);
    crypto.getRandomValues(buf);
    return base64url(buf); // 64 url-safe characters for 48 bytes
  }

  async function challengeFor(verifier) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    return base64url(digest);
  }

  function validClientId(id) {
    return typeof id === "string" && CLIENT_ID_RE.test(id);
  }

  function authorizeUrl({ clientId, redirectUri, challenge, state }) {
    const u = new URL(AUTHORIZE);
    u.search = new URLSearchParams({
      client_id: clientId,
      response_type: "code",
      redirect_uri: redirectUri,
      code_challenge_method: "S256",
      code_challenge: challenge,
      state,
      scope: SCOPES,
    }).toString();
    return u.toString();
  }

  // Read and always consume the redirect parameters and the saved PKCE entry.
  // Returns {status: "none"} | {status: "error", reason} | {status: "ok", code, verifier}.
  function consumeCallback(search, storage, key) {
    const params = new URLSearchParams(search);
    if (!params.has("code") && !params.has("error")) return { status: "none" };
    let saved = null;
    try {
      saved = JSON.parse(storage.getItem(key) || "null");
    } catch {
      saved = null;
    }
    try {
      storage.removeItem(key);
    } catch {
      /* storage blocked: nothing was saved anyway */
    }
    if (params.has("error")) return { status: "error", reason: "denied" };
    const st = params.get("state");
    if (!saved || typeof saved.verifier !== "string" || !st || saved.state !== st) {
      return { status: "error", reason: "state" };
    }
    return { status: "ok", code: params.get("code"), verifier: saved.verifier };
  }

  async function exchangeCode({ clientId, redirectUri, code, verifier }, fetchImpl = fetch) {
    const r = await fetchImpl(TOKEN, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: redirectUri,
        client_id: clientId,
        code_verifier: verifier,
      }),
    });
    if (!r.ok) throw new Error(`token ${r.status}`);
    const data = await r.json();
    if (!data || typeof data.access_token !== "string") throw new Error("token missing");
    return data.access_token;
  }

  function namesFrom(items) {
    return (Array.isArray(items) ? items : [])
      .map((a) => a && a.name)
      .filter((n) => typeof n === "string" && n.trim());
  }

  // Top artists (3 periods) + followed artists; only names leave this function.
  // Returns { names (≤ MAX_IMPORT, most listened first), failed (calls that did not answer) }.
  async function artistNames(token, fetchImpl = fetch) {
    let failed = 0;
    const get = async (path) => {
      const r = await fetchImpl(API + path, { headers: { Authorization: `Bearer ${token}` } });
      if (r.status === 403) throw new Error("forbidden"); // not in the app's user list
      if (r.status === 401) throw new Error("unauthorized");
      if (!r.ok) {
        failed++;
        return null;
      }
      return r.json();
    };
    const names = [];
    for (const range of ["medium_term", "long_term", "short_term"]) {
      const data = await get(`/me/top/artists?time_range=${range}&limit=50`);
      names.push(...namesFrom(data && data.items));
    }
    let after = "";
    for (let page = 0; page < 4; page++) {
      const data = await get(`/me/following?type=artist&limit=50${after ? `&after=${encodeURIComponent(after)}` : ""}`);
      const block = data && data.artists;
      names.push(...namesFrom(block && block.items));
      after = block && block.cursors && block.cursors.after;
      if (!after) break;
    }
    const seen = new Set();
    const unique = names.filter((n) => {
      const k = n.toLowerCase();
      return seen.has(k) ? false : seen.add(k);
    });
    return { names: unique.slice(0, MAX_IMPORT), failed };
  }

  // Redirect URI = the page URL without query, hash or a trailing index.html.
  function redirectUriFor(loc) {
    return loc.origin + loc.pathname.replace(/index\.html$/, "");
  }

  const api = {
    base64url,
    randomString,
    challengeFor,
    validClientId,
    authorizeUrl,
    consumeCallback,
    exchangeCode,
    artistNames,
    redirectUriFor,
    MAX_IMPORT,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCSpotify = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
