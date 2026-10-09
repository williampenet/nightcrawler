// Renders data/*.json and personalises it in the browser (ADR-0002).
// Every value comes from third-party sites or APIs: text is set with textContent only,
// links are kept only if they are plain http(s) URLs, ids are validated before use.
"use strict";

const S = window.NCScoring;
const SP = window.NCSpotify;
const FB = window.NCFeedback;
const P = window.NCProfile;
const V = window.NCVerdicts;
const PKCE_KEY = "nightcrawler.pkce"; // sessionStorage: verifier + state during the redirect only
let APP_CONFIG = {};
const STORE_KEY = "nightcrawler.v1";
const MB_API = "https://musicbrainz.org/ws/2/artist";
const LB_API = "https://api.listenbrainz.org/1/stats/user/";

// Time zone comes from the zone config (report.json); formatters are built once it is known.
let TZ, dayFmt, shortFmt, timeFmt, keyFmt, wdFmt, numFmt;
function setTimeZone(tz) {
  TZ = tz;
  dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
  shortFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short", timeZone: TZ });
  timeFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  wdFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", timeZone: TZ }); // "sam."
  numFmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", timeZone: TZ });
  keyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }); // YYYY-MM-DD
}
const dayKey = (d) => keyFmt.format(d);

const STATUS_LABEL = {
  structured: "agenda lisible",
  platform_only: "billetterie externe",
  no_agenda: "agenda non lisible",
  robots_blocked: "refus robots.txt",
  fetch_error: "site injoignable",
  no_website: "pas de site",
};

// ---------------------------------------------------------------- visible messages (WIP-47)

// A dismissible banner at the top of the page, for errors and Spotify connection news.
// Errors use role="alert", information role="status" (MDN, ARIA live regions:
// https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/alert_role
// https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/status_role).
// Registered before anything else runs, so an early failure is shown too.
function shortMessage(err) {
  const m = String((err && err.message) || err || "erreur inconnue").replace(/\s+/g, " ").trim();
  return m.length > 120 ? m.slice(0, 117) + "…" : m;
}

let bannerReturn = null; // the element that had focus when the banner appeared
let bannerTimer = 0;
function showBanner(text, kind = "error") {
  const box = document.getElementById("banner");
  if (!box) return;
  const out = box.querySelector(".banner-text");
  if (box.hidden) bannerReturn = document.activeElement;
  box.className = `banner ${kind}`;
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  box.hidden = false;
  // clear then set, so the same message twice is announced twice
  out.textContent = "";
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => (out.textContent = text), 50);
}

function notify(text, kind = "error") {
  setStatus(text);
  showBanner(text, kind);
}

document.getElementById("banner-close").addEventListener("click", () => {
  document.getElementById("banner").hidden = true;
  const back = bannerReturn && bannerReturn !== document.body && document.contains(bannerReturn) ? bannerReturn : null;
  bannerReturn = null;
  if (back) back.focus();
  else refocus(null); // first button of the list
});
window.addEventListener("error", (e) => showBanner(`Une erreur est survenue : ${shortMessage(e.error || e.message)}`));
window.addEventListener("unhandledrejection", (e) => showBanner(`Une erreur est survenue : ${shortMessage(e.reason)}`));

// ---------------------------------------------------------------- state (this browser only)

const DATA = { concerts: [], artists: {}, venues: [], report: null };
const defaultState = S.defaultState;
const sanitizeState = S.sanitizeState; // saved state is untrusted: see scoring.js
let state = loadState();

function loadState() {
  try {
    return sanitizeState(JSON.parse(localStorage.getItem(STORE_KEY) || "{}"));
  } catch {
    return defaultState(); // storage blocked or corrupt: the page still works, nothing is remembered
  }
}

function saveState() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch {
    /* private mode: keep the in-memory state */
  }
  profileChanged();
}

// ---------------------------------------------------------------- helpers

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function safeLink(url, text) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const a = el("a", text);
    a.href = u.href;
    a.rel = "noopener noreferrer nofollow";
    a.target = "_blank";
    return a;
  } catch {
    return null;
  }
}

function button(text, cls, onClick) {
  const b = el("button", text, cls);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- taste profile

async function fetchTags(name) {
  const q = 'artist:"' + name.replace(/"/g, " ") + '"';
  const url = `${MB_API}?query=${encodeURIComponent(q)}&fmt=json&limit=3`;
  try {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) return null; // throttled or down: retry on the next save
    const data = await r.json();
    for (const a of Array.isArray(data.artists) ? data.artists : []) {
      if (a && a.score === 100 && S.norm(a.name) === S.norm(name)) {
        const tags = (Array.isArray(a.tags) ? a.tags : [])
          .filter((t) => t && typeof t.name === "string")
          .sort((x, y) => (y.count || 0) - (x.count || 0));
        return tags.slice(0, 8).map((t) => t.name.toLowerCase());
      }
    }
  } catch {
    return null; // offline or blocked: the seed still works for exact and related matches
  }
  return []; // not found on MusicBrainz
}

let tagRun = 0; // id of the running tag loop; a new save or a reset makes older loops stop
let tagLoopBusy = false;

// lead: a line kept in front of the progress messages (the Spotify import count)
async function setSeeds(names, lead = "") {
  const known = new Map(state.seeds.map((s) => [S.norm(s.name), s]));
  state.seeds = names.map((n) => known.get(S.norm(n)) || { name: n, tags: null });
  saveState();
  render();
  const run = ++tagRun;
  while (tagLoopBusy) await sleep(200); // only one loop talks to MusicBrainz at a time
  if (run !== tagRun) return;
  tagLoopBusy = true;
  try {
    const todo = state.seeds.filter((s) => s.tags === null);
    let failed = 0;
    for (let i = 0; i < todo.length && run === tagRun; i++) {
      setStatus(`${lead}Récupération des styles : ${i + 1}/${todo.length}…`);
      const tags = await fetchTags(todo[i].name);
      if (tags === null) failed++;
      else todo[i].tags = tags;
      saveState();
      // MusicBrainz allows 1 request per second on average per client:
      // https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
      await sleep(1100);
    }
    if (run === tagRun) {
      setStatus(
        lead +
          (failed
            ? `Goûts enregistrés ; styles indisponibles pour ${failed} artiste(s), réessaie plus tard.`
            : `Goûts enregistrés ${whereText()}.`),
      );
      render();
    }
  } finally {
    tagLoopBusy = false;
  }
}

async function importListenBrainz(user) {
  const name = user.trim();
  if (!/^[\w.\-]{1,64}$/.test(name)) return setStatus("Nom d'utilisateur ListenBrainz invalide.");
  setStatus("Import depuis ListenBrainz…");
  try {
    const r = await fetch(`${LB_API}${encodeURIComponent(name)}/artists?count=50&range=all_time`);
    if (r.status === 204) return setStatus("ListenBrainz n'a pas encore calculé les statistiques de ce compte.");
    if (!r.ok) return setStatus(`ListenBrainz a répondu ${r.status}.`);
    const data = await r.json();
    const list = (data.payload && Array.isArray(data.payload.artists) ? data.payload.artists : [])
      .map((a) => a && a.artist_name)
      .filter((n) => typeof n === "string");
    const box = document.getElementById("seeds");
    box.value = S.parseSeeds([box.value, ...list].join("\n")).join("\n");
    setStatus(`${list.length} artistes importés : vérifie la liste puis enregistre.`);
  } catch {
    setStatus("Import impossible (réseau ou ListenBrainz indisponible).");
  }
}

// ---------------------------------------------------------------- Spotify (PKCE, in the browser)

function redirectUri() {
  return SP.redirectUriFor(location); // must equal the URI registered on the Spotify app
}

// Read the Spotify redirect first thing, before any network call: the code leaves the URL
// and the saved verifier is consumed even if the rest of the page fails to load.
const SPOTIFY_CALLBACK = (() => {
  if (!SP) return { status: "none" };
  let storage = null;
  try {
    storage = sessionStorage;
  } catch {
    storage = { getItem: () => null, removeItem: () => {} };
  }
  const cb = SP.consumeCallback(location.search, storage, PKCE_KEY);
  if (cb.status !== "none") history.replaceState(null, "", location.pathname + location.hash);
  return cb;
})();

async function connectSpotify(ev, probe = false) {
  try {
    // crypto.subtle only exists in secure contexts (HTTPS or localhost):
    // https://developer.mozilla.org/en-US/docs/Web/API/Crypto/subtle
    if (!window.crypto || !window.crypto.subtle) {
      return notify("Connexion Spotify impossible : ce navigateur ne permet pas le chiffrement nécessaire (page non sécurisée ?).");
    }
    const verifier = SP.randomString();
    const st = SP.randomString(16);
    const challenge = await SP.challengeFor(verifier);
    try {
      sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state: st, probe }));
    } catch {
      return notify("Connexion Spotify impossible : le stockage de session est bloqué dans ce navigateur.");
    }
    notify("Redirection vers Spotify…", "info");
    location.assign(
      SP.authorizeUrl({
        clientId: APP_CONFIG.spotify_client_id,
        redirectUri: redirectUri(),
        challenge,
        state: st,
        scope: probe ? SP.PROBE_SCOPES : SP.SCOPES,
      }),
    );
  } catch (err) {
    notify(`Connexion Spotify impossible : ${shortMessage(err)}`);
  }
}

// Back from Spotify: exchange the code, import names, forget the token.
async function finishSpotify() {
  const cb = SPOTIFY_CALLBACK;
  if (cb.status === "none") return;
  goTo("gouts");
  if (cb.status === "error") {
    return notify(
      cb.reason === "denied"
        ? "Connexion Spotify annulée."
        : "Connexion Spotify refusée : la réponse ne correspond pas à la demande.",
      cb.reason === "denied" ? "info" : "error",
    );
  }
  if (!SP.validClientId(APP_CONFIG.spotify_client_id)) return notify("Spotify n'est pas configuré.");
  try {
    notify("Import depuis Spotify…", "info");
    const token = await SP.exchangeCode({
      clientId: APP_CONFIG.spotify_client_id,
      redirectUri: redirectUri(),
      code: cb.code,
      verifier: cb.verifier,
    });
    if (cb.probe) return showProbe(await SP.probe(token)); // the token stays in this function only
    const { names, failed } = await SP.artistNames(token); // the token stays in this function only
    const box = document.getElementById("seeds");
    const merged = S.mergeNames(S.parseSeeds(box.value), names);
    box.value = merged.join("\n");
    state.sort = "me";
    document.getElementById("sort").value = "me";
    const done =
      `${names.length} artistes importés depuis Spotify` +
      (failed ? ` (import partiel : ${failed} requête(s) sans réponse, réessaie plus tard).` : ".");
    notify(done, failed ? "error" : "info");
    // styles are then read from MusicBrainz in the background (about 1 s per artist);
    // the progress line keeps the import count in front
    setSeeds(merged, done + " ").catch((err) => showBanner(`Une erreur est survenue : ${shortMessage(err)}`));
  } catch (err) {
    const why = String(err && err.message);
    notify(
      why === "forbidden"
        ? "Ce compte Spotify n'est pas autorisé sur l'app (à ajouter dans le tableau de bord Spotify)."
        : "Import Spotify impossible pour le moment.",
    );
  }
}

// Result of the one-off API test (WIP-48): a table and a text to copy into the chat.
function showProbe(rows) {
  const box = document.getElementById("spotify-probe");
  box.hidden = false;
  const body = box.querySelector("tbody");
  body.textContent = "";
  for (const r of rows) {
    const tr = el("tr");
    for (const v of [r.label, r.endpoint, String(r.status || "-"), r.detail]) tr.append(el("td", v));
    body.append(tr);
  }
  const text = SP.probeText(rows, new Date().toISOString().slice(0, 16));
  box.querySelector("textarea").value = text;
  notify("Test Spotify terminé : résultat sous « Mes goûts ».", "info");
}

function showTasteCount() {
  const n = document.getElementById("taste-text").value.length; // UTF-16 code units, as maxlength
  const fr = (x) => x.toLocaleString("fr-FR");
  document.getElementById("taste-text-count").textContent = `${fr(n)} / ${fr(S.MAX_TASTE_TEXT)} caractères`;
}

function setStatus(text) {
  document.getElementById("taste-status").textContent = text;
}

// ---------------------------------------------------------------- feedback

// The one rating path, shared by the list buttons and the "À trier" mode (WIP-73): the
// same state change and the same events sent to the feedback function.
function applyRating(concert, kind) {
  if (concert.id === deepLinkId) deepLinkId = null; // the user acted on it: normal rules apply again
  // without an identified artist the concert is rated by id and performer names (WIP-47)
  const { state: rated, send } = S.rate(state, concert, kind, DATA.concerts);
  Object.assign(state, rated);
  sendFeedback(send.kind, concert.id, send.keys);
  saveState();
  render();
}

function feedback(concert, kind, li) {
  const next = li && li.nextElementSibling && li.nextElementSibling.dataset.id;
  applyRating(concert, kind);
  if (routeOf(location.hash) === "calendrier") renderDays();
  if (kind === "like") {
    // the same button, so its new pressed state is announced
    const row = visibleRows().find((r) => r.dataset.id === concert.id);
    const like = row && row.querySelector("button[aria-pressed]");
    if (like) return like.focus();
  }
  // a rated row may leave its section: then the next row
  refocus(...(kind === "like" ? [concert.id, next] : [next]));
}

// ---------------------------------------------------------------- "À trier" mode (WIP-73)
// One upcoming, unrated concert at a time, drawn at random (S.sortCandidates, S.pickRandom),
// so the ratings are not limited to what the current ranking shows first. "Session" = this
// page load: skipped and rated concerts are not shown again until the page is reloaded.
const sorter = { seen: new Set(), rated: 0, current: null };

function sorterNext() {
  const list = S.sortCandidates(DATA.concerts, state, sorter.seen, new Date(), dayKey);
  sorter.current = S.pickRandom(list);
  renderSorterCard(list.length);
}

function renderSorterCard(left) {
  const card = document.getElementById("sorter-card");
  const c = sorter.current;
  const n = sorter.rated;
  document.getElementById("sorter-count").textContent = `${n} noté${n > 1 ? "s" : ""} dans cette session`;
  document.getElementById("sorter-actions").hidden = !c;
  card.replaceChildren();
  if (!c) {
    const done = el("p", "Plus rien à trier pour le moment : tous les concerts à venir sont notés ou passés.", "muted");
    done.tabIndex = -1;
    card.append(done);
    done.focus();
    return;
  }
  const title = el("h3", c.title, "sorter-title");
  title.tabIndex = -1; // focused on each new concert, so screen readers read it
  card.append(title);
  const start = new Date(c.start);
  const t = timeFmt.format(start);
  card.append(el("p", dayFmt.format(start) + (t === "00:00" ? "" : ` à ${t}`), "sorter-when"));
  card.append(el("p", c.venue_name, "venue"));
  const match = S.scoreConcert(c, DATA.artists, S.buildProfile(state, DATA.artists));
  if (match.reason) {
    const why = el("p", null, "why");
    why.append(el("span", match.reason));
    if (match.discovery) why.append(el("span", "Découverte", "badge"));
    card.append(why);
  }
  const links = el("p", null, "links");
  for (const l of S.concertLinks(c)) {
    const a = safeLink(l.url, l.label);
    if (a) links.append(a);
  }
  if (c.ai_extracted) {
    // EU AI Act transparency (WIP-66), as in the list
    const ai = el("span", "Lu par IA", "badge ai");
    ai.title = "Cette annonce a été lue par un modèle d'IA.";
    links.append(ai);
  }
  if (links.childNodes.length) card.append(links);
  card.append(el("p", `Encore ${left - 1} concert${left - 1 > 1 ? "s" : ""} à trier après celui-ci.`, "muted"));
  title.focus();
}

function sorterAct(kind) {
  const c = sorter.current;
  if (!c) return;
  sorter.seen.add(c.id);
  if (kind !== "skip") {
    applyRating(c, kind); // exactly the list buttons' path: S.rate + the feedback queue
    sorter.rated++;
  }
  sorterNext();
}

function setupSorter() {
  const dialog = document.getElementById("sorter");
  const opener = document.getElementById("sorter-open");
  if (!dialog || typeof dialog.showModal !== "function") return; // the button stays hidden
  opener.hidden = false;
  // a modal <dialog> makes the rest of the page inert and closes on Escape
  // (https://developer.mozilla.org/en-US/docs/Web/HTML/Element/dialog)
  opener.addEventListener("click", () => {
    dialog.showModal();
    sorterNext();
  });
  document.getElementById("sorter-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => opener.focus()); // back where the listener was
  document.getElementById("sorter-like").addEventListener("click", () => sorterAct("like"));
  document.getElementById("sorter-dislike").addEventListener("click", () => sorterAct("dislike"));
  document.getElementById("sorter-skip").addEventListener("click", () => sorterAct("skip"));
  // desktop shortcuts: J, N, Space. Space on a focused button or link keeps its own meaning
  // (it activates that control), so it means "Passer" only elsewhere in the dialog.
  dialog.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || !sorter.current) return;
    const onControl = e.target instanceof Element && e.target.closest("button, a, input, textarea, select");
    const key = e.key.toLowerCase();
    const kind = key === "j" ? "like" : key === "n" ? "dislike" : key === " " && !onControl ? "skip" : null;
    if (!kind) return;
    e.preventDefault();
    sorterAct(kind);
  });
}

// Server-side copy of the ratings (ADR-0005): queued here, sent when a URL and a key are set.
const fbStore = (fn, fallback) => {
  try {
    return fn();
  } catch {
    return fallback; // storage blocked: nothing is queued
  }
};
const fbLoad = () => fbStore(() => FB.parseQueue(localStorage.getItem(FB.QUEUE_KEY)), []);
const fbSave = (q) => fbStore(() => localStorage.setItem(FB.QUEUE_KEY, JSON.stringify(q)));
const fbToken = () => fbStore(() => localStorage.getItem(FB.TOKEN_KEY) || "", "");
let fbBusy = false; // one flush at a time; it also sends items queued while it runs

function feedbackUrl() {
  try {
    const u = new URL(APP_CONFIG.feedback_url || "");
    return u.protocol === "https:" ? u.href : "";
  } catch {
    return "";
  }
}

function sendFeedback(kind, concertId, keys) {
  if (!feedbackUrl()) return; // no service configured: nothing is queued
  fbSave(FB.enqueue(fbLoad(), FB.makeItem(kind, concertId, keys)));
  showPending();
  flushFeedback();
}

// Ratings queued without a key, or with a refused one, used to wait silently (WIP-70): say
// so above the list. The live region (#pending-live) stays in the page; only the box inside
// is hidden, and the text is written 50 ms after showing it, as in showBanner, so it is announced.
let fbRefused = false; // the last send got 401
let pendingShown = "";
let pendingTimer = 0;
function showPending() {
  const box = document.getElementById("pending-banner");
  const out = box.querySelector(".banner-text");
  const text = (feedbackUrl() && FB.pendingBanner(fbLoad().length, Boolean(fbToken()), fbRefused)) || "";
  if (text === pendingShown) return; // unchanged: not announced again
  pendingShown = text;
  clearTimeout(pendingTimer);
  out.textContent = "";
  box.hidden = !text;
  if (text) pendingTimer = setTimeout(() => (out.textContent = text), 50);
}

function askFeedbackKey() {
  document.getElementById("feedback-row").hidden = false;
  goTo("gouts", "feedback-key");
}

async function flushFeedback() {
  if (fbBusy || !feedbackUrl()) return;
  fbBusy = true;
  let result = "error";
  try {
    result = await FB.flush(feedbackUrl(), fbToken(), { load: fbLoad, save: fbSave, fetch: (u, o) => fetch(u, o) });
  } catch {
    /* storage or network trouble: the queue stays as it is */
  } finally {
    fbBusy = false;
  }
  const pending = fbLoad().length;
  if (result !== "error") fbRefused = result === "unauthorized"; // a network error tells nothing about the key
  if (result === "unauthorized") fbText = "Clé refusée";
  else if (result === "sent") fbText = "Avis envoyés au service";
  else fbText = pending ? `${pending} avis en attente` : "";
  showSyncStatus();
  showPending();
}

let fbText = "";
let syncText = "";
function showSyncStatus() {
  // the same message from both (e.g. "Clé refusée") is shown once
  document.getElementById("feedback-status").textContent = [...new Set([fbText, syncText].filter(Boolean))].join(" · ");
}

// ---------------------------------------------------------------- profile sync (WIP-46)
// The profile (seeds, ratings, hidden concerts) is kept on the function so it follows the
// listener on every device with the key (ADR-0002 amendment, ADR-0005). localStorage stays
// the working copy. The decisions are pure functions in profile.js (afterPull, afterPush).
const loadSync = () => fbStore(() => P.parseSync(localStorage.getItem(P.SYNC_KEY)), P.parseSync(""));
let sync = loadSync(); // {version: server version this copy is based on, dirty, force}
const saveSync = () => fbStore(() => localStorage.setItem(P.SYNC_KEY, JSON.stringify(sync)));
let syncReady = false; // the server copy was read (or could not be): changes are pushed
let syncedJson = null; // the profile as last read from or written to the server
let syncTimer = null;
let syncBusy = false;
let syncAgain = false;
const profileOn = () => Boolean(P && feedbackUrl() && fbToken());
const nowJson = () => JSON.stringify(P.extract(state));
const setSync = (text) => {
  syncText = text;
  showSyncStatus();
};
// where the taste is kept, for the page texts
const whereText = () => (profileOn() ? "dans ce navigateur et sur ton espace Nightcrawler (Scaleway, UE)" : "dans ce navigateur");
function showWhere() {
  for (const node of document.querySelectorAll("[data-where]")) node.textContent = whereText();
}

function profileChanged() {
  if (!syncReady || !P) return;
  if (nowJson() === syncedJson) {
    if (sync.dirty || sync.force) {
      Object.assign(sync, { dirty: false, force: false });
      saveSync();
    }
    return;
  }
  sync.dirty = true;
  saveSync();
  clearTimeout(syncTimer);
  syncTimer = setTimeout(pushProfile, 1500); // debounced: a burst of clicks is one request
}

function applyProfile(data) {
  Object.assign(state, P.toState(data)); // taste_text -> tasteText (WIP-73)
  const taste = document.getElementById("taste-text");
  if (document.activeElement !== taste) taste.value = state.tasteText; // never under the cursor
  showTasteCount();
  // saved ids are kept even when absent today; an alias gets its current id (WIP-42, WIP-59)
  state.hidden = S.keepIds(DATA.concerts, state.hidden);
  state.likedConcerts = S.keepIds(DATA.concerts, state.likedConcerts);
  const box = document.getElementById("seeds");
  if (document.activeElement !== box) box.value = state.seeds.map((x) => x.name).join("\n"); // never under the cursor
  if (state.seeds.some((x) => x.tags === null)) {
    // styles not read yet (on another device, or interrupted): restart the tag loop
    setSeeds(state.seeds.map((x) => x.name)).catch(() => {});
  } else {
    saveState();
    render();
  }
}

// On load (and when the key changes or the browser comes back online): the server copy wins,
// unless this browser has changes it could not send yet (merged) or a pending "Tout effacer".
async function startSync() {
  if (!profileOn()) return;
  const before = nowJson();
  const got = await P.pull(P.profileUrl(feedbackUrl()), fbToken(), (u, o) => fetch(u, o));
  syncReady = true;
  if (got.status === "unauthorized" || got.status === "error") {
    return setSync(got.status === "unauthorized" ? "Clé refusée" : "Profil non synchronisé");
  }
  const next = P.afterPull(sync, got, P.extract(state), nowJson() !== before);
  sync = next.sync;
  syncedJson = next.synced;
  saveSync();
  if (next.apply) applyProfile(next.apply);
  profileChanged(); // first upload, or local changes to send
  if (!sync.dirty) setSync("Profil synchronisé");
}

async function pushProfile() {
  if (syncBusy) return void (syncAgain = true);
  syncBusy = true;
  const snap = P.snapshot(sync, state); // a reset or a click during the request is seen after it
  const r = await P.push(P.profileUrl(feedbackUrl()), fbToken(), snap.data, snap.base, (u, o) => fetch(u, o), snap.force);
  syncBusy = false;
  if (r.status === "ok") {
    const next = P.afterPush(sync, snap, r, state);
    sync = next.sync;
    syncedJson = next.synced;
    saveSync();
    if (next.apply) applyProfile(next.apply);
    const dropped = P.droppedSeeds(state);
    if (!sync.dirty) {
      setSync(dropped ? `Profil synchronisé, sauf ${dropped} artiste(s) (200 au plus, noms de 60 caractères au plus)` : "Profil synchronisé");
    }
    profileChanged(); // sends again what changed meanwhile
  } else {
    const why = r.reason === "too-large" ? " : profil trop volumineux (64 Ko au plus), retire des artistes" : "";
    setSync(r.status === "unauthorized" ? "Clé refusée" : `Profil non synchronisé${why}`); // retried on next change, load or reconnection
  }
  syncAgain = false;
}

// ---------------------------------------------------------------- judge sections (WIP-86)
// With a send key, the home sections and their reasons come from the judge (ADR-0007 §4–5):
// GET /verdicts, a local copy shown at once and refreshed in the background (hides a cold
// start). Without a key, with a refused key or with no verdicts: the rule-based tiers (WIP-53).
let verdicts = null; // {generated_at, verdicts} as parsed by verdicts.js, or null
let verdictsRun = 0; // id of the latest request; an older answer (key changed) is ignored
const judgeOn = () => Boolean(V && profileOn() && V.hasVerdicts(verdicts));

async function startVerdicts() {
  if (!V || !profileOn()) return;
  const run = ++verdictsRun;
  const got = await V.pull(V.verdictsUrl(feedbackUrl()), fbToken(), (u, o) => fetch(u, o));
  if (run !== verdictsRun) return;
  const next = V.afterPull(got, verdicts); // the decision is pure and tested (verdicts.js)
  if (next.store !== "keep") fbStore(() => V.save(localStorage, next.show));
  if (JSON.stringify(next.show) === JSON.stringify(verdicts)) return;
  verdicts = next.show;
  rerender();
}

// The copy is dropped (in memory and in this browser) and an answer in flight is ignored.
function dropVerdicts() {
  verdictsRun++;
  verdicts = null;
  if (V) fbStore(() => V.save(localStorage, null));
}

// A new or cleared key: the copy read with the previous key is dropped, then read again.
function resetVerdicts() {
  dropVerdicts();
  rerender();
  startVerdicts();
}

window.addEventListener("online", () => {
  flushFeedback();
  if (!profileOn()) return;
  startVerdicts();
  if (syncedJson === null) startSync();
  else if (sync.dirty) {
    clearTimeout(syncTimer);
    pushProfile();
  }
});


// ---------------------------------------------------------------- routes (WIP-95)
// Bottom navigation: #concerts (home), #calendrier, #gouts; #sources opens « Mes goûts » at the
// sources table; a shared concert (#c-<id>) opens the home on it.

const ROUTES = ["concerts", "calendrier", "gouts"];
const routeOf = (hash) => {
  const h = String(hash || "").replace(/^#/, "");
  if (h === "sources") return "gouts";
  return ROUTES.includes(h) ? h : "concerts";
};

// Programmatic route change: synchronous (pushState fires no hashchange), so the caller's
// focus target wins over the heading; Back still works through popstate.
function goTo(route, focusId) {
  if (routeOf(location.hash) !== route) history.pushState(null, "", `#${route}`);
  showRoute(!focusId);
  const target = focusId && document.getElementById(focusId);
  if (target) target.focus();
}

function showRoute(moveFocus) {
  const route = routeOf(location.hash);
  for (const r of ROUTES) document.getElementById(`route-${r}`).hidden = r !== route;
  for (const a of document.querySelectorAll(".tabbar a")) {
    if (a.dataset.route === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (route === "calendrier") renderDays();
  if (location.hash === "#sources") document.getElementById("sources").scrollIntoView();
  else if (moveFocus) window.scrollTo(0, 0);
  if (moveFocus) {
    // the new screen's heading, so screen readers hear where they landed
    const h = document.querySelector(`#route-${route} h2`);
    if (h && !(route === "concerts" && deepLinkId)) {
      h.tabIndex = -1;
      h.focus({ preventScroll: true });
    }
  }
}

// ---------------------------------------------------------------- home state

// view: "pour_toi" | "nouveaux" | "tout"; not saved, the home always opens on « Pour toi »
let view = "pour_toi";
let NEW_IDS = new Set(); // concerts added since the last visit (visits.js), set once per load
let showAllMust = false; // « À ne pas rater » beyond the first MUST_CARDS cards
const MUST_CARDS = 4; // PRD FR-6: 1 to 4 must-see concerts a week
const openIds = new Set(); // rows whose details are open, kept across re-renders
let panelSeq = 0;
const PERIODS = { tonight: "ce soir", weekend: "ce week-end", "7d": "cette semaine" };

// rows of the screen on show (home or calendar)
const visibleRows = () => [...document.querySelectorAll("main > section:not([hidden]) [data-id]")];

// Render again after a background change (verdicts, profile sync); keyboard focus stays on its row.
function rerender() {
  const a = document.activeElement;
  const row = a && a.closest ? a.closest("main [data-id]") : null;
  render();
  if (routeOf(location.hash) === "calendrier") renderDays();
  if (row) refocus(row.dataset.id);
}

// keep keyboard users where they were: the first wanted row still visible (in the order
// given), else the first button of the list
function refocus(...wanted) {
  const rows = visibleRows();
  const row = wanted.map((id) => rows.find((r) => r.dataset.id === id)).find(Boolean);
  const target = (row && row.querySelector("button")) || document.querySelector("main > section:not([hidden]) button");
  if (target) target.focus();
}

// ---------------------------------------------------------------- rendering

function focusDeepLink() {
  if (!deepLinkId) return;
  const row = visibleRows().find((r) => r.dataset.id === deepLinkId);
  if (!row) return;
  row.classList.add("target");
  row.tabIndex = -1;
  row.scrollIntoView({ block: "center" });
  row.focus({ preventScroll: true });
}

// Deep link to one concert on this page: #c-<12 hex chars>
const DEEP_LINK_RE = /^#c-([0-9a-f]{12})$/;
let deepLinkId = null;

function concertLink(c) {
  if (c.url && safeLink(c.url, "")) return c.url; // only http(s) links travel
  return `${location.origin}${location.pathname}#c-${c.id}`;
}

function shareMessage(c) {
  const d = new Date(c.start);
  const t = timeFmt.format(d);
  const when = shortFmt.format(d) + (t === "00:00" ? "" : ` à ${t}`);
  return { title: c.title, text: `${c.title} — ${c.venue_name}, ${when}`, url: concertLink(c) };
}

// Share icon (FR-10): the phone's share sheet; on desktop the link is copied (the WhatsApp link
// is in the row's details).
function share(c) {
  const msg = shareMessage(c);
  if (navigator.share) return navigator.share(msg).catch(() => {});
  const text = `${msg.text} ${msg.url}`;
  if (navigator.clipboard) {
    return navigator.clipboard.writeText(text).then(
      () => showBanner("Lien copié : colle-le dans une conversation.", "info"),
      () => showBanner(`Copie impossible. Le lien : ${msg.url}`, "info"),
    );
  }
  showBanner(`Le lien : ${msg.url}`, "info");
}

// Inline stroke icons of the design system (24px grid, currentColor). Constant markup only.
const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  play: '<path d="M7 4l13 8-13 8z" fill="currentColor" stroke="none"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  share: '<path d="M12 15V3"/><path d="M7 8l5-5 5 5"/><path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  arrow: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
};
function icon(name, size) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" })) svg.setAttribute(k, String(v));
  svg.innerHTML = ICONS[name];
  return svg;
}

function iconButton(name, label, size, onClick) {
  const b = button("", "icon", onClick);
  b.setAttribute("aria-label", label);
  b.append(icon(name, size));
  return b;
}

// ListenButton (design system): Deezer's own widget for now (PM decision 2026-10-09, player in
// WIP-99), loaded only on click so nothing third-party loads before. filled: on a must-see card.
function listenButton(c, slot, filled) {
  const a = (c.artists || []).map((k) => DATA.artists[k]).find((x) => x && x.deezer_id);
  if (!a || !/^[0-9]+$/.test(String(a.deezer_id))) return null; // no extract: no button
  const label = el("span", "Écouter");
  const b = button("", filled ? "listen filled" : "listen", () => {
    const open = slot.querySelector("iframe");
    if (open) {
      open.remove();
      label.textContent = "Écouter";
      b.replaceChild(icon("play", filled ? 14 : 12), b.firstChild);
      b.setAttribute("aria-expanded", "false");
      return;
    }
    const f = document.createElement("iframe");
    f.src = `https://widget.deezer.com/widget/auto/artist/${a.deezer_id}/top_tracks`;
    f.title = `Extraits de ${a.name} sur Deezer`;
    f.loading = "lazy";
    f.allow = "encrypted-media; clipboard-write";
    f.className = "player";
    f.referrerPolicy = "no-referrer";
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups");
    slot.append(f);
    label.textContent = "Fermer";
    b.replaceChild(icon("close", filled ? 14 : 12), b.firstChild);
    b.setAttribute("aria-expanded", "true");
  });
  b.append(icon("play", filled ? 14 : 12), label);
  b.setAttribute("aria-expanded", "false");
  return b;
}

// The reason under a concert and its origin (ReasonTag): "IA" for the judge's sentence
// (judge_taste, WIP-86), "Tes goûts" for a fixed rule (known artist WIP-88, rule-based match
// WIP-25). Model output is set as text only (ADR-0007 Security); the tag is also spelt out for
// screen readers (EU AI Act transparency, PRD FR-5).
function reasonOf(x) {
  if (x.known && x.m && x.m.reason) return { ia: false, text: x.m.reason };
  if (x.v) return { ia: true, text: x.v.reason };
  if (x.m && x.m.reason) return { ia: false, text: x.m.reason };
  return null;
}

function reasonLine(x, showUnjudged) {
  const r = reasonOf(x);
  if (!r) return showUnjudged && x.unjudged ? el("p", "Pas encore jugé.", "reason muted") : null;
  const p = el("p", null, "reason");
  const tag = el("span", r.ia ? "IA" : "Tes goûts", "tag");
  tag.setAttribute("aria-hidden", "true");
  tag.title = r.ia ? "Raison écrite par un modèle d'IA d'après « Mon goût en mots »." : "Raison tirée de tes goûts par une règle fixe, sans IA.";
  const spoken = el("span", r.ia ? "Raison écrite par IA : " : "Raison tirée de tes goûts : ", "visually-hidden");
  p.append(tag, spoken, el("span", r.text));
  return p;
}

const newBadge = () => el("span", "Nouveau", "new");

// Details under the title (until the concert page, WIP-96): source and ticket links, the WhatsApp
// link, "Pas pour moi", "Mauvais rapprochement" and the "Lu par IA" mention (WIP-66).
function detailsPanel(x, item) {
  const c = x.c;
  const box = el("div", null, "more");
  box.id = `more-${++panelSeq}`; // a concert can be on the home and in the calendar
  const links = el("p", null, "links");
  for (const l of S.concertLinks(c)) {
    const a = safeLink(l.url, l.label); // merged sources (WIP-42)
    if (a) links.append(a);
  }
  const msg = shareMessage(c);
  const wa = safeLink("https://wa.me/?text=" + encodeURIComponent(`${msg.text} ${msg.url}`), "WhatsApp");
  if (wa) links.append(wa);
  if (links.childNodes.length) box.append(links);
  if (c.ai_extracted) box.append(el("p", "Annonce lue sur le site du lieu par un modèle d'IA : vérifie la date et l'heure.", "muted small"));
  const acts = el("p", null, "row");
  acts.append(button("Pas pour moi", "ghost", () => feedback(c, "dislike", item)));
  const m = x.m || {};
  if (m.inferred && m.artist) {
    // "Proche de…" / "Style…" is a guess: let the listener say it is wrong (WIP-41)
    const wrong = button("Mauvais rapprochement", "ghost", () => {
      if (c.id === deepLinkId) deepLinkId = null;
      const next = item.nextElementSibling && item.nextElementSibling.dataset.id;
      if (!state.wrong.includes(m.artist)) state.wrong.push(m.artist);
      saveState();
      sendFeedback("wrong", c.id, [m.artist]);
      render();
      refocus(c.id, next); // the row may leave its section: same row if still there, else the next
      showBanner("Noté : ce rapprochement ne sera plus utilisé.", "info");
    });
    wrong.setAttribute("aria-label", `Mauvais rapprochement : ${m.reason}`);
    acts.append(wrong);
  }
  box.append(acts);
  box.hidden = !openIds.has(c.id);
  return box;
}

// Title as a disclosure button (WAI-ARIA APG Disclosure pattern,
// https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/) opening the details panel.
function titleButton(c, panel, cls) {
  const h = el("h3", null, cls);
  const b = button(c.title, "title-btn", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    b.setAttribute("aria-expanded", String(open)); // the chevron turns with it (style.css)
    if (open) openIds.add(c.id);
    else openIds.delete(c.id);
  });
  b.setAttribute("aria-expanded", String(!panel.hidden));
  b.setAttribute("aria-controls", panel.id);
  b.append(icon("chevron", 16));
  h.append(b);
  return h;
}

function actionBar(x, item, slot, onCard) {
  const c = x.c;
  const bar = el("div", null, "actions");
  const listen = listenButton(c, slot, onCard);
  if (listen) bar.append(listen);
  bar.append(el("span", null, "sp"));
  const liked = S.isLiked(state, c);
  const like = iconButton("heart", "J'aime", onCard ? 20 : 18, () => feedback(c, "like", item));
  like.setAttribute("aria-pressed", String(liked));
  if (liked) like.classList.add("on");
  bar.append(like, iconButton("share", "Partager", onCard ? 20 : 18, () => share(c)));
  return bar;
}

// Venue and line-up; a listing read by a language model says so (ADR-0004, WIP-66, EU AI Act).
function venueLine(c) {
  const lineup = S.lineupText(c); // every act of the evening (WIP-72)
  const p = el("p", lineup ? `${c.venue_name} · ${lineup}` : c.venue_name, "venue");
  if (c.ai_extracted) {
    const ai = el("span", "Lu par IA", "ai-read");
    ai.title = "Cette annonce a été lue sur le site du lieu par un modèle d'IA.";
    p.append(" · ", ai);
  }
  return p;
}

// MustSeeCard (design system): apricot card, text only.
function mustSeeCard(x) {
  const c = x.c;
  const li = el("li", null, "card");
  li.dataset.id = c.id;
  const d = new Date(c.start);
  const t = timeFmt.format(d);
  const top = el("div", null, "card-top");
  top.append(el("span", shortFmt.format(d) + (t === "00:00" ? "" : ` · ${t}`), "when"));
  if (NEW_IDS.has(c.id)) top.append(newBadge());
  const panel = detailsPanel(x, li);
  const slot = el("div", null, "slot");
  li.append(top, titleButton(c, panel, "card-title"), venueLine(c));
  const why = reasonLine(x, true);
  if (why) li.append(why);
  li.append(actionBar(x, li, slot, true), panel, slot);
  return li;
}

// ConcertRow (design system): date column, then title, venue, reason and actions.
function concertRow(x, showUnjudged) {
  const c = x.c;
  const li = el("li", null, "crow");
  li.dataset.id = c.id;
  const d = new Date(c.start);
  const t = timeFmt.format(d);
  const date = el("div", null, "date");
  date.append(el("span", wdFmt.format(d).toUpperCase(), "wd"), el("span", numFmt.format(d), "num"));
  if (t !== "00:00") date.append(el("span", t, "hour"));
  for (const n of date.children) n.setAttribute("aria-hidden", "true");
  date.append(el("span", dayFmt.format(d) + (t === "00:00" ? "" : ` à ${t}`), "visually-hidden"));
  const body = el("div", null, "body");
  const panel = detailsPanel(x, li);
  const head = el("div", null, "head");
  head.append(titleButton(c, panel, "row-title"));
  if (NEW_IDS.has(c.id)) head.append(newBadge());
  const slot = el("div", null, "slot");
  body.append(head, venueLine(c));
  const why = reasonLine(x, showUnjudged);
  if (why) body.append(why);
  body.append(actionBar(x, li, slot, false), panel, slot);
  li.append(date, body);
  return li;
}

// Period filter (state.when) on every view; style and venue filters only in « Tout », where
// their controls are.
function filtered(forView = view) {
  const now = new Date();
  const hidden = new Set(S.currentIds(DATA.concerts, state.hidden)); // display only: saved ids stay
  const all = forView === "tout";
  return DATA.concerts.filter((c) => {
    if (c.id === deepLinkId) return true; // a shared link always shows its concert
    if (hidden.has(c.id)) return false;
    if (all && state.venue && c.venue_id !== state.venue) return false;
    if (all && state.style) {
      const tags = (c.artists || []).flatMap((k) => (DATA.artists[k] && DATA.artists[k].tags) || []);
      if (!tags.includes(state.style)) return false;
    }
    return S.inWhen(state.when, c.start, now, dayKey);
  });
}

// An artist the listener listens to (seeds) or has liked: the rule-based "sure" match (WIP-53),
// by name, so an identity flagged as a possible homonym or reported as wrong is left to the
// judge (S.HOMONYM_DOUBTS). Such a concert is always in "À ne pas rater" (WIP-88, William 2026-10-09).
const isKnownArtist = (x) => S.isKnownMatch(x.m);

const scored = (profile) => filtered().map((c) => ({ c, m: S.scoreConcert(c, DATA.artists, profile) }));

// The home sections. With the judge (WIP-86, PRD FR-6): « À ne pas rater », « Pour toi » (with
// the concerts not judged yet), « Découvertes ». Without it, fixed rules (WIP-53): the sure
// matches are « À ne pas rater », the best inferred ones « Pour toi ». null: empty profile.
function homeSections(list, profile) {
  if (judgeOn()) {
    const s = V.sectionsFor(list, verdicts.verdicts, isKnownArtist);
    return { must: s.ne_pas_rater, forYou: s.pour_toi, discover: s.decouvertes, all: [...s.ne_pas_rater, ...s.pour_toi, ...s.decouvertes, ...s.tout_voir] };
  }
  if (S.isEmpty(profile, DATA.concerts)) return null;
  const t = S.tiers(list);
  return { must: t.sure, forYou: t.discover, discover: [], all: list };
}

function rowList(items, showUnjudged, labelId) {
  const ul = el("ul", null, "rows");
  if (labelId) ul.setAttribute("aria-labelledby", labelId);
  for (const x of items) ul.append(concertRow(x, showUnjudged));
  return ul;
}

function seeAll(root, n) {
  if (!n) return;
  const b = button(n > 1 ? `Voir les ${n} concerts` : "Voir le concert", "see-all", () => setView("tout", true));
  b.append(icon("arrow", 18));
  root.append(b);
}

function heading(root, id, text, sub) {
  const h = el("h2", text);
  h.id = id;
  root.append(h);
  if (sub) root.append(el("p", sub, "section-sub"));
}

function renderPourToi(root, list, profile) {
  const sec = homeSections(list, profile);
  const period = PERIODS[state.when];
  if (!sec) {
    const hint = el("p", "Dis-moi ce que tu aimes dans ", "hint");
    const link = el("a", "« Mes goûts »");
    link.href = "#gouts";
    hint.append(link, " : des artistes, ou ton goût en quelques phrases. Les concerts pour toi apparaîtront ici.");
    root.append(hint);
    return seeAll(root, list.length);
  }
  if (!sec.must.length && !sec.forYou.length && !sec.discover.length) {
    root.append(el("p", `Rien à te proposer ${period || "pour l'instant"}.`, "muted empty"));
    return seeAll(root, list.length);
  }
  if (sec.must.length) {
    const box = el("section", null, "home-section");
    heading(box, "sec-must", "À ne pas rater");
    const ul = el("ul", null, "cards");
    ul.setAttribute("aria-labelledby", "sec-must");
    const shown = showAllMust ? sec.must : sec.must.slice(0, MUST_CARDS);
    for (const x of shown) ul.append(mustSeeCard(x));
    box.append(ul);
    const more = sec.must.length - shown.length;
    if (more > 0) box.append(button(`Voir ${more} autre${more > 1 ? "s" : ""} à ne pas rater`, "ghost wide", () => { showAllMust = true; render(); refocus(sec.must[MUST_CARDS].c.id); }));
    root.append(box);
  }
  if (sec.forYou.length) {
    const box = el("section", null, "home-section");
    heading(box, "sec-for-you", period ? `Pour toi ${period}` : "Pour toi");
    box.append(rowList(sec.forYou, true, "sec-for-you"));
    root.append(box);
  }
  if (sec.discover.length) {
    const box = el("section", null, "home-section");
    heading(box, "sec-discover", "Découvertes", "Des artistes que ton profil ne cite pas, dans des lieux qui te ressemblent.");
    box.append(rowList(sec.discover, false, "sec-discover"));
    root.append(box);
  }
  seeAll(root, list.length);
}

// Items with the judge's verdicts attached (reasons in « Nouveaux » and « Tout » too).
function judged(list, profile) {
  const sec = homeSections(list, profile);
  if (!sec || !judgeOn()) return list;
  const byId = new Map(sec.all.map((x) => [x.c.id, x]));
  return list.map((x) => byId.get(x.c.id) || x);
}

function render() {
  const root = document.getElementById("concerts");
  root.replaceChildren();
  const profile = S.buildProfile(state, DATA.artists);
  const list = scored(profile);
  for (const b of document.querySelectorAll(".views button")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
  for (const b of document.querySelectorAll(".periods button")) {
    if (b.dataset.when === state.when) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
  document.getElementById("tout-tools").hidden = view !== "tout";
  // « Tout » counts the concerts of the period (style and venue filters aside)
  document.getElementById("count").textContent = String(filtered("pour_toi").length);
  if (!list.length) {
    root.append(el("p", "Aucun concert pour ces filtres.", "muted empty"));
    return;
  }
  if (view === "pour_toi") return renderPourToi(root, list, profile);
  const items = judged(list, profile);
  if (view === "nouveaux") {
    const fresh = items.filter((x) => NEW_IDS.has(x.c.id));
    heading(root, "sec-new", "Nouveaux depuis ta dernière visite");
    if (!fresh.length) root.append(el("p", "Rien de nouveau depuis ta dernière visite.", "muted empty"));
    else root.append(rowList(fresh, false, "sec-new"));
    return;
  }
  heading(root, "sec-all", PERIODS[state.when] ? `Tous les concerts ${PERIODS[state.when]}` : "Tous les concerts");
  const sorted = state.sort === "me" ? [...items].sort((a, b) => b.m.score - a.m.score || a.c.start.localeCompare(b.c.start)) : items;
  root.append(rowList(sorted, false, "sec-all"));
}

function setView(v, moveFocus) {
  view = v;
  render();
  if (moveFocus) {
    window.scrollTo(0, 0);
    const b = document.querySelector(`.views button[data-view="${v}"]`);
    if (b) b.focus();
  }
}

// « Calendrier » until the month grid (WIP-97): every concert, day by day, no filter.
function renderDays() {
  const root = document.getElementById("days");
  root.replaceChildren();
  const profile = S.buildProfile(state, DATA.artists);
  const hidden = new Set(S.currentIds(DATA.concerts, state.hidden));
  const list = DATA.concerts.filter((c) => !hidden.has(c.id)).map((c) => ({ c, m: S.scoreConcert(c, DATA.artists, profile) }));
  let currentDay = null;
  let ul = null;
  for (const x of judged(list, profile)) {
    const start = new Date(x.c.start);
    const key = dayKey(start);
    if (key !== currentDay) {
      currentDay = key;
      const h = el("h3", dayFmt.format(start), "day");
      root.append(h);
      ul = el("ul", null, "rows");
      root.append(ul);
    }
    ul.append(concertRow(x, false));
  }
}

// Community agendas (Gancio instances from the zone config) credited in the footer.
function renderAgendaCredits(report) {
  const box = document.getElementById("agendas");
  const list = ((report.sources || {}).gancio || {}).instances || [];
  const links = list.map((i) => safeLink(i.url, i.name)).filter(Boolean);
  if (!box || !links.length) return;
  box.replaceChildren(links.length > 1 ? "Agendas communautaires : " : "Agenda communautaire : ");
  links.forEach((a, i) => box.append(...(i ? [", ", a] : [a])));
  box.append(".");
}

function renderSources(venues, report) {
  const s = report.probe_status || {};
  const art = report.artists || {};
  document.getElementById("coverage").textContent =
    `${report.venues} lieux trouvés, ${report.venues_with_website} avec un site, ` +
    `${s.structured || 0} avec un agenda lisible, ${report.venues_with_concerts} avec au moins un concert à venir. ` +
    `${art.identified ?? 0} artistes identifiés sur ${art.candidates ?? 0} noms lus.`;
  const order = Object.keys(STATUS_LABEL);
  const rows = [...venues].sort(
    (a, b) => order.indexOf(a.probe.status) - order.indexOf(b.probe.status) || b.concerts - a.concerts,
  );
  const tbody = document.getElementById("venue-rows");
  tbody.replaceChildren();
  for (const v of rows) {
    const tr = el("tr");
    const name = el("td");
    name.append((v.website && safeLink(v.website, v.name)) || el("span", v.name));
    tr.append(name, el("td", v.category));
    const method = v.probe.method ? ` (${v.probe.method})` : "";
    tr.append(el("td", (STATUS_LABEL[v.probe.status] || v.probe.status) + method, v.probe.status));
    tr.append(el("td", String(v.concerts)));
    tbody.append(tr);
  }
}

// ---------------------------------------------------------------- controls

function fillSelect(id, options, value) {
  const sel = document.getElementById(id);
  for (const [v, label] of options) {
    const o = el("option", label);
    o.value = v;
    sel.append(o);
  }
  sel.value = options.some(([v]) => v === value) ? value : options[0][0];
  return sel;
}

function setupControls() {
  const counts = new Map();
  for (const c of DATA.concerts) {
    for (const k of c.artists || []) for (const t of (DATA.artists[k] && DATA.artists[k].tags) || []) counts.set(t, (counts.get(t) || 0) + 1);
  }
  const styles = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 20).map(([t]) => [t, t]);
  const withConcerts = new Map(DATA.concerts.map((c) => [c.venue_id, c.venue_name]));
  const venues = [...withConcerts].sort((a, b) => a[1].localeCompare(b[1], "fr"));
  const bind = (id, key) =>
    document.getElementById(id).addEventListener("change", (e) => {
      state[key] = e.target.value;
      saveState();
      render();
    });
  fillSelect("sort", [["date", "Par date"], ["me", "Pour moi"]], state.sort);
  fillSelect("style", [["", "Tous les styles"], ...styles], state.style);
  fillSelect("venue", [["", "Tous les lieux"], ...venues], state.venue);
  for (const [id, key] of [["sort", "sort"], ["style", "style"], ["venue", "venue"]]) {
    bind(id, key);
    state[key] = document.getElementById(id).value; // a saved value that no longer exists resets
  }
  // period filters (WIP-95): tapping the active one again shows every date
  if (!Object.hasOwn(PERIODS, state.when)) state.when = "all";
  for (const b of document.querySelectorAll(".periods button")) {
    b.addEventListener("click", () => {
      state.when = state.when === b.dataset.when ? "all" : b.dataset.when;
      showAllMust = false;
      saveState();
      render();
    });
  }
  for (const b of document.querySelectorAll(".views button")) b.addEventListener("click", () => setView(b.dataset.view, false));
  // a saved id may be an alias since sources were merged (WIP-42): its current id is added.
  // Ids absent today are kept: a concert can be missing for one run (WIP-59).
  state.hidden = S.keepIds(DATA.concerts, state.hidden);
  state.likedConcerts = S.keepIds(DATA.concerts, state.likedConcerts);
  saveState();

  const box = document.getElementById("seeds");
  box.value = state.seeds.map((s) => s.name).join("\n");
  document.getElementById("save-seeds").addEventListener("click", () => {
    state.sort = "me";
    document.getElementById("sort").value = "me";
    setSeeds(S.parseSeeds(box.value));
  });
  if (SP && SP.validClientId(APP_CONFIG.spotify_client_id)) {
    document.getElementById("spotify-row").hidden = false;
    document.getElementById("spotify-connect").addEventListener("click", connectSpotify);
    document.getElementById("spotify-probe-run").addEventListener("click", (e) => connectSpotify(e, true));
  }
  if (feedbackUrl()) {
    document.getElementById("feedback-row").hidden = false;
    document.getElementById("feedback-status").hidden = false;
    const key = document.getElementById("feedback-key");
    key.value = fbToken();
    key.addEventListener("change", () => {
      fbStore(() => localStorage.setItem(FB.TOKEN_KEY, key.value.trim()));
      fbRefused = false; // a new key: unknown until the flush answers
      flushFeedback();
      showWhere();
      startSync();
      resetVerdicts();
      showPending(); // a cleared key brings the warning back at once
    });
    document.getElementById("pending-key").addEventListener("click", askFeedbackKey);
    showPending();
  }
  showWhere();
  // "Mon goût en mots" (WIP-73): saved on each change, synced 1.5 s later like the rest of the
  // profile. Personal data: never logged, never in the repo (kept in the event store, EU).
  const taste = document.getElementById("taste-text");
  taste.maxLength = S.MAX_TASTE_TEXT;
  taste.value = state.tasteText;
  showTasteCount();
  taste.addEventListener("input", () => {
    state.tasteText = S.cleanTasteText(taste.value);
    state.tasteTextAt = Date.now(); // edit time for the last-writer-wins merge (profile.js)
    showTasteCount();
    saveState();
  });
  taste.addEventListener("blur", () => {
    taste.value = S.blurTasteText(taste.value, state.tasteText); // a sync landed while focused
    showTasteCount();
  });
  document.getElementById("lb-import").addEventListener("click", () =>
    importListenBrainz(document.getElementById("lb-user").value),
  );
  document.getElementById("reset").addEventListener("click", () => {
    tagRun++; // stop a running tag loop
    if (profileOn()) {
      // the server profile is emptied too, even if another device changed it meanwhile
      Object.assign(sync, { dirty: true, force: true });
      saveSync();
    }
    state = Object.assign(loadState(), {
      seeds: [], liked: [], disliked: [], hidden: [], wrong: [], likedConcerts: [], likedNames: [], dislikedNames: [],
      tasteText: "", tasteTextAt: Date.now(),
    });
    dropVerdicts(); // this browser's copy of the judgements (the stored rows: separate ticket)
    if (window.NCVisits) fbStore(() => localStorage.removeItem(window.NCVisits.KEY)); // « Nouveau » memory
    NEW_IDS = new Set();
    saveState();
    box.value = "";
    taste.value = "";
    showTasteCount();
    setStatus(`Goûts et avis effacés ${whereText()}.`);
    render();
  });

  const onHash = () => {
    const m = DEEP_LINK_RE.exec(location.hash);
    if (m) {
      deepLinkId = S.currentIds(DATA.concerts, [m[1]])[0] || null;
      if (deepLinkId) openDeepLink();
    }
    showRoute(true);
    if (m && deepLinkId) focusDeepLink();
  };
  window.addEventListener("hashchange", onHash);
  window.addEventListener("popstate", onHash); // Back after goTo()'s pushState
}

// A shared concert: on the home, in the view that shows it (« Tout » when the sections do not).
function openDeepLink() {
  view = "pour_toi";
  render();
  if (!visibleRows().some((r) => r.dataset.id === deepLinkId)) {
    view = "tout";
    render();
  }
}

async function main() {
  try {
    const [concerts, venues, report, artists] = await Promise.all(
      ["concerts", "venues", "report", "artists"].map((n) => {
        const load = fetch(`data/${n}.json`).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
        return n === "artists" ? load.catch(() => ({})) : load; // the list works without artists
      }),
    );
    const okArtists = artists && typeof artists === "object" && !Array.isArray(artists);
    Object.assign(DATA, { concerts, venues, report, artists: okArtists ? artists : {} });
    APP_CONFIG = await fetch("app-config.json")
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}));
    if (!APP_CONFIG || typeof APP_CONFIG !== "object") APP_CONFIG = {};
    setTimeZone(report.zone.timezone || "Europe/Paris");
    const today = dayFmt.format(new Date());
    document.getElementById("today").textContent = `${today.charAt(0).toUpperCase()}${today.slice(1)} · ${report.zone.name}`;
    document.getElementById("generated").textContent =
      `Mis à jour le ${new Date(report.generated_at).toLocaleString("fr-FR", { timeZone: TZ })}.`;
    const m = DEEP_LINK_RE.exec(location.hash);
    deepLinkId = (m && S.currentIds(concerts, [m[1]])[0]) || null; // an alias leads to its concert
    if (V && profileOn()) verdicts = fbStore(() => V.load(localStorage), null); // shown at once
    if (window.NCVisits) {
      // « Nouveau » badges (WIP-95): the list compared with the last visit's, in this browser only
      const VS = window.NCVisits;
      const seen = VS.visit(fbStore(() => localStorage.getItem(VS.KEY), null), concerts, Date.now());
      NEW_IDS = seen.isNew;
      fbStore(() => localStorage.setItem(VS.KEY, JSON.stringify(seen.store)));
    }
    setupControls();
    setupSorter();
    if (deepLinkId) openDeepLink();
    else render();
    showRoute(false);
    renderSources(venues, report);
    renderAgendaCredits(report);
    focusDeepLink();
    startVerdicts(); // refreshes the local copy in the background
    startSync(); // before the Spotify import, so an import is merged rather than overwritten
    finishSpotify().catch((err) => notify(`Import Spotify impossible : ${shortMessage(err)}`));
    flushFeedback(); // ratings left over from a previous visit
  } catch (err) {
    document.getElementById("concerts").replaceChildren(el("p", "Données indisponibles.", "muted"));
    // a missing data file rejects with its HTTP status (a number, already said above);
    // anything else (invalid JSON, a script error) is shown in the banner
    if (typeof err !== "number") showBanner(`Une erreur est survenue : ${shortMessage(err)}`);
  }
}

main();
