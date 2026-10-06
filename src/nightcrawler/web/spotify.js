// Spotify login in the browser: OAuth Authorization Code + PKCE (no server, no secret).
// Pure helpers + two network calls; loaded by the page and by `node --test tests/js`.
"use strict";

(function (root) {
  const AUTHORIZE = "https://accounts.spotify.com/authorize";
  const TOKEN = "https://accounts.spotify.com/api/token";
  const API = "https://api.spotify.com/v1";
  const SCOPES = "user-top-read user-follow-read";
  // read-only scopes requested only by the one-off API test (WIP-48), never by the import
  const PROBE_SCOPES = SCOPES + " user-read-recently-played user-library-read playlist-read-private";
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

  function authorizeUrl({ clientId, redirectUri, challenge, state, scope = SCOPES }) {
    const u = new URL(AUTHORIZE);
    u.search = new URLSearchParams({
      client_id: clientId,
      response_type: "code",
      redirect_uri: redirectUri,
      code_challenge_method: "S256",
      code_challenge: challenge,
      state,
      scope,
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
    return { status: "ok", code: params.get("code"), verifier: saved.verifier, probe: saved.probe === true };
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

  // One-off test of what the Web API really answers to this app (Development Mode), to
  // replace assumptions by facts (WIP-48). Only HTTP statuses, counts and which fields are
  // present leave this function: no names, no ids, nothing is stored.
  // Reference for the expected restrictions: Spotify, "Introducing some changes to our Web API"
  // (2024-11-27) and "Web API Changelog - February 2026".
  async function probe(token, fetchImpl = fetch) {
    const rows = [];
    const call = async (label, path, describe) => {
      let status = 0;
      let detail = "";
      try {
        const r = await fetchImpl(API + path, { headers: { Authorization: `Bearer ${token}` } });
        status = r.status;
        if (r.ok && describe) detail = describe(await r.json());
      } catch (err) {
        detail = "réseau : " + String((err && err.name) || "erreur");
      }
      const shown = path.replace(/[?].*$/, "").replace(/\/artists\/[^/]+/, "/artists/{id}");
      rows.push({ label, endpoint: "GET " + shown, status, detail });
      return status;
    };
    const has = (o, k) => (o && Object.prototype.hasOwnProperty.call(o, k) ? "oui" : "non");
    const artistFields = (a) =>
      `genres: ${Array.isArray(a && a.genres) ? a.genres.length : "absent"}, ` +
      `popularity: ${has(a, "popularity")}, followers: ${has(a, "followers")}`;
    let artistId = null;
    let trackId = null;
    await call("Top artistes", "/me/top/artists?limit=5&time_range=medium_term", (d) => {
      const items = (d && d.items) || [];
      artistId = items[0] && items[0].id;
      return `${items.length} reçus, 1er : ${items[0] ? artistFields(items[0]) : "-"}`;
    });
    await call("Top titres", "/me/top/tracks?limit=5&time_range=medium_term", (d) => {
      const items = (d && d.items) || [];
      trackId = items[0] && items[0].id;
      return `${items.length} reçus, preview_url : ${items[0] ? has(items[0], "preview_url") : "-"}`;
    });
    await call("Artistes suivis", "/me/following?type=artist&limit=5", (d) =>
      `${(((d && d.artists) || {}).items || []).length} reçus`);
    await call("Écoutes récentes", "/me/player/recently-played?limit=5", (d) =>
      `${((d && d.items) || []).length} reçus`);
    await call("Titres sauvegardés", "/me/tracks?limit=5", (d) => `${((d && d.items) || []).length} reçus`);
    await call("Playlists", "/me/playlists?limit=5", (d) => `${((d && d.items) || []).length} reçues`);
    await call("Recherche (limite 10)", "/search?q=Air&type=artist&limit=10", (d) =>
      `${((((d && d.artists) || {}).items) || []).length} reçus`);
    await call("Recherche (limite 20)", "/search?q=Air&type=artist&limit=20", (d) =>
      `${((((d && d.artists) || {}).items) || []).length} reçus`);
    if (artistId) {
      const id = encodeURIComponent(artistId);
      await call("Artiste", `/artists/${id}`, artistFields);
      await call("Plusieurs artistes", `/artists?ids=${id}`, (d) => `${((d && d.artists) || []).length} reçus`);
      await call("Artistes proches", `/artists/${id}/related-artists`, (d) =>
        `${((d && d.artists) || []).length} reçus`);
      await call("Top titres d'un artiste", `/artists/${id}/top-tracks?market=FR`, (d) =>
        `${((d && d.tracks) || []).length} reçus`);
      await call("Recommandations", `/recommendations?seed_artists=${id}&limit=5`, (d) =>
        `${((d && d.tracks) || []).length} reçues`);
    }
    if (trackId) {
      await call("Caractéristiques audio", `/audio-features?ids=${encodeURIComponent(trackId)}`, (d) =>
        `${((d && d.audio_features) || []).filter(Boolean).length} reçues`);
    }
    return rows;
  }

  function probeText(rows, date) {
    return [`Test API Spotify, ${date}`]
      .concat(rows.map((r) => `${r.label} | ${r.endpoint} | HTTP ${r.status || "-"} | ${r.detail}`))
      .join("\n");
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
    probe,
    probeText,
    MAX_IMPORT,
    SCOPES,
    PROBE_SCOPES,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCSpotify = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
