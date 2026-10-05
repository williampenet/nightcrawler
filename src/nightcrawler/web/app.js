// Renders data/*.json and personalises it in the browser (ADR-0002).
// Every value comes from third-party sites or APIs: text is set with textContent only,
// links are kept only if they are plain http(s) URLs, ids are validated before use.
"use strict";

const S = window.NCScoring;
const SP = window.NCSpotify;
const PKCE_KEY = "nightcrawler.pkce"; // sessionStorage: verifier + state during the redirect only
let APP_CONFIG = {};
const STORE_KEY = "nightcrawler.v1";
const MB_API = "https://musicbrainz.org/ws/2/artist";
const LB_API = "https://api.listenbrainz.org/1/stats/user/";

// Time zone comes from the zone config (report.json); formatters are built once it is known.
let TZ, dayFmt, shortFmt, timeFmt, keyFmt;
function setTimeZone(tz) {
  TZ = tz;
  dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
  shortFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short", timeZone: TZ });
  timeFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
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

// ---------------------------------------------------------------- state (this browser only)

const DATA = { concerts: [], artists: {}, venues: [], report: null };
let state = loadState();

function defaultState() {
  return { seeds: [], liked: [], disliked: [], hidden: [], sort: "date", when: "all", style: "", venue: "" };
}

// Saved state is untrusted (old versions, manual edits): keep only well-typed fields.
function sanitizeState(raw) {
  const s = defaultState();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return s;
  const strings = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string") : []);
  if (Array.isArray(raw.seeds)) {
    s.seeds = raw.seeds
      .map((x) => (typeof x === "string" ? { name: x, tags: null } : x))
      .filter((x) => x && typeof x.name === "string")
      .map((x) => ({ name: x.name, tags: Array.isArray(x.tags) ? strings(x.tags) : null }));
  }
  for (const k of ["liked", "disliked", "hidden"]) s[k] = strings(raw[k]);
  for (const k of ["sort", "when", "style", "venue"]) if (typeof raw[k] === "string") s[k] = raw[k];
  return s;
}

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

async function setSeeds(names) {
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
      setStatus(`Récupération des styles : ${i + 1}/${todo.length}…`);
      const tags = await fetchTags(todo[i].name);
      if (tags === null) failed++;
      else todo[i].tags = tags;
      saveState();
      await sleep(1100); // MusicBrainz: at most 1 request per second
    }
    if (run === tagRun) {
      setStatus(
        failed
          ? `Goûts enregistrés ; styles indisponibles pour ${failed} artiste(s), réessaie plus tard.`
          : "Goûts enregistrés dans ce navigateur.",
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

async function connectSpotify() {
  const verifier = SP.randomString();
  const st = SP.randomString(16);
  try {
    sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state: st }));
  } catch {
    return setStatus("Connexion impossible : le stockage de session est bloqué dans ce navigateur.");
  }
  const challenge = await SP.challengeFor(verifier);
  location.assign(
    SP.authorizeUrl({ clientId: APP_CONFIG.spotify_client_id, redirectUri: redirectUri(), challenge, state: st }),
  );
}

// Back from Spotify: exchange the code, import names, forget the token.
async function finishSpotify() {
  const cb = SPOTIFY_CALLBACK;
  if (cb.status === "none") return;
  document.getElementById("taste").open = true;
  if (cb.status === "error") {
    return setStatus(
      cb.reason === "denied"
        ? "Connexion Spotify annulée."
        : "Connexion Spotify refusée : la réponse ne correspond pas à la demande.",
    );
  }
  if (!SP.validClientId(APP_CONFIG.spotify_client_id)) return setStatus("Spotify n'est pas configuré.");
  try {
    setStatus("Import depuis Spotify…");
    const token = await SP.exchangeCode({
      clientId: APP_CONFIG.spotify_client_id,
      redirectUri: redirectUri(),
      code: cb.code,
      verifier: cb.verifier,
    });
    const { names, failed } = await SP.artistNames(token); // the token stays in this function only
    const box = document.getElementById("seeds");
    const merged = S.mergeNames(S.parseSeeds(box.value), names);
    box.value = merged.join("\n");
    state.sort = "me";
    document.getElementById("sort").value = "me";
    await setSeeds(merged);
    setStatus(
      `${names.length} artistes importés depuis Spotify` +
        (failed ? ` (import partiel : ${failed} requête(s) sans réponse, réessaie plus tard).` : "."),
    );
  } catch (err) {
    const why = String(err && err.message);
    setStatus(
      why === "forbidden"
        ? "Ce compte Spotify n'est pas autorisé sur l'app (à ajouter dans le tableau de bord Spotify)."
        : "Import Spotify impossible pour le moment.",
    );
  }
}

function setStatus(text) {
  document.getElementById("taste-status").textContent = text;
}

// ---------------------------------------------------------------- feedback

function feedback(concert, kind, li) {
  if (concert.id === deepLinkId) deepLinkId = null; // the user acted on it: normal rules apply again
  const keys = concert.artists || [];
  const next = li && li.nextElementSibling && li.nextElementSibling.dataset.id;
  const toggle = (list, values, on) => {
    const set = new Set(list);
    for (const v of values) on ? set.add(v) : set.delete(v);
    return [...set];
  };
  if (kind === "like") {
    const on = !keys.every((k) => state.liked.includes(k)); // second click undoes
    state.liked = toggle(state.liked, keys, on);
    state.disliked = toggle(state.disliked, keys, false);
  } else {
    state.disliked = toggle(state.disliked, keys, true);
    state.liked = toggle(state.liked, keys, false);
    state.hidden = toggle(state.hidden, [concert.id], true);
  }
  saveState();
  render();
  // keep keyboard users where they were: same row, or the next one if it was hidden
  const wanted = kind === "like" ? concert.id : next;
  const row = [...document.querySelectorAll("#concerts li")].find((r) => r.dataset.id === wanted);
  const target = (row && row.querySelector("button")) || document.querySelector("#concerts button");
  if (target) target.focus();
}

// ---------------------------------------------------------------- rendering

function focusDeepLink() {
  if (!deepLinkId) return;
  const row = [...document.querySelectorAll("#concerts li")].find((r) => r.dataset.id === deepLinkId);
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

function shareControls(c) {
  const msg = shareMessage(c);
  const wrap = el("span", null, "share");
  if (navigator.share) {
    // phones: the native share sheet (WhatsApp, Signal, SMS, mail…)
    wrap.append(
      button("Partager", "ghost", () => navigator.share(msg).catch(() => {})),
    );
    return wrap;
  }
  const wa = safeLink("https://wa.me/?text=" + encodeURIComponent(`${msg.text} ${msg.url}`), "WhatsApp");
  if (wa) wrap.append(wa);
  if (navigator.clipboard) {
    const copy = button("Copier le lien", "ghost", () =>
      navigator.clipboard.writeText(`${msg.text} ${msg.url}`).then(
        () => {
          copy.textContent = "Copié";
          setTimeout(() => (copy.textContent = "Copier le lien"), 2000);
        },
        () => {},
      ),
    );
    wrap.append(copy);
  }
  return wrap;
}

function listenButton(c, body) {
  const a = (c.artists || []).map((k) => DATA.artists[k]).find((x) => x && x.deezer_id);
  if (!a || !/^[0-9]+$/.test(String(a.deezer_id))) return null;
  const b = button("Écouter", "ghost", (ev) => {
    const open = body.querySelector("iframe");
    if (open) {
      open.remove();
      ev.target.textContent = "Écouter";
      ev.target.setAttribute("aria-expanded", "false");
      return;
    }
    const f = document.createElement("iframe"); // loaded only on click: nothing third-party before
    f.src = `https://widget.deezer.com/widget/auto/artist/${a.deezer_id}/top_tracks`;
    f.title = `Extraits de ${a.name} sur Deezer`;
    f.loading = "lazy";
    f.allow = "encrypted-media; clipboard-write";
    f.className = "player";
    f.referrerPolicy = "no-referrer";
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups");
    body.append(f);
    ev.target.textContent = "Fermer";
    ev.target.setAttribute("aria-expanded", "true");
  });
  b.setAttribute("aria-expanded", "false");
  return b;
}

function concertRow(c, match, showDate) {
  const li = el("li");
  li.dataset.id = c.id;
  const start = new Date(c.start);
  const t = timeFmt.format(start);
  li.append(el("span", showDate ? shortFmt.format(start) : t === "00:00" ? "—" : t, "time"));
  const body = el("div", null, "body");
  body.append(el("strong", c.title));
  body.append(el("span", c.venue_name + (showDate && t !== "00:00" ? ` · ${t}` : ""), "venue"));
  const names = (c.artists || []).map((k) => DATA.artists[k] && DATA.artists[k].name).filter(Boolean);
  const perf = names.length ? names : c.performers || [];
  if (perf.length) body.append(el("span", perf.join(" · "), "performers"));
  if (match.reason) {
    const why = el("span", null, "why");
    why.append(el("span", match.reason));
    if (match.discovery) why.append(el("span", "Découverte", "badge"));
    body.append(why);
  }
  const actions = el("span", null, "links");
  const page = c.url && safeLink(c.url, "Page");
  const ticket = c.ticket_url && c.ticket_url !== c.url && safeLink(c.ticket_url, "Billets");
  for (const x of [page, ticket, listenButton(c, body), shareControls(c)]) if (x) actions.append(x);
  if ((c.artists || []).length) {
    const liked = c.artists.every((k) => state.liked.includes(k));
    const like = button(liked ? "Aimé" : "Pertinent", liked ? "ghost on" : "ghost", () => feedback(c, "like", li));
    like.setAttribute("aria-pressed", String(liked));
    actions.append(like);
    actions.append(button("Pas pour moi", "ghost", () => feedback(c, "dislike", li)));
  }
  body.append(actions);
  li.append(body);
  return li;
}

function filtered() {
  const now = new Date();
  const hidden = new Set(state.hidden);
  return DATA.concerts.filter((c) => {
    if (c.id === deepLinkId) return true; // a shared link always shows its concert
    if (hidden.has(c.id)) return false;
    if (state.venue && c.venue_id !== state.venue) return false;
    if (state.style) {
      const tags = (c.artists || []).flatMap((k) => (DATA.artists[k] && DATA.artists[k].tags) || []);
      if (!tags.includes(state.style)) return false;
    }
    return S.inWhen(state.when, c.start, now, dayKey);
  });
}

function render() {
  const root = document.getElementById("concerts");
  root.replaceChildren();
  const profile = S.buildProfile(state, DATA.artists);
  const list = filtered().map((c) => ({ c, m: S.scoreConcert(c, DATA.artists, profile) }));
  document.getElementById("count").textContent = `${list.length} concert${list.length > 1 ? "s" : ""}`;
  if (!list.length) {
    root.append(el("p", "Aucun concert pour ces filtres.", "muted"));
    return;
  }
  if (state.sort === "me") {
    if (S.isEmpty(profile)) {
      root.append(el("p", "Ajoute quelques artistes dans « Mes goûts » pour trier les concerts pour toi.", "hint"));
    }
    const matched = list.filter((x) => x.m.score > 0).sort((a, b) => b.m.score - a.m.score || a.c.start.localeCompare(b.c.start));
    const rest = list.filter((x) => x.m.score === 0);
    if (matched.length) {
      root.append(el("h2", `Pour toi (${matched.length})`));
      const ul = el("ul", null, "concerts");
      for (const x of matched) ul.append(concertRow(x.c, x.m, true));
      root.append(ul);
    }
    if (rest.length) {
      root.append(el("h2", `Autres concerts (${rest.length})`));
      const ul = el("ul", null, "concerts");
      for (const x of rest) ul.append(concertRow(x.c, x.m, true));
      root.append(ul);
    }
    return;
  }
  let currentDay = null;
  let ul = null;
  for (const x of list) {
    const start = new Date(x.c.start);
    const key = dayKey(start);
    if (key !== currentDay) {
      currentDay = key;
      root.append(el("h2", dayFmt.format(start)));
      ul = el("ul", null, "concerts");
      root.append(ul);
    }
    ul.append(concertRow(x.c, x.m, false));
  }
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
  fillSelect("when", [["all", "Toutes les dates"], ["tonight", "Ce soir"], ["weekend", "Ce week-end"], ["7d", "7 prochains jours"]], state.when);
  fillSelect("style", [["", "Tous les styles"], ...styles], state.style);
  fillSelect("venue", [["", "Tous les lieux"], ...venues], state.venue);
  for (const [id, key] of [["sort", "sort"], ["when", "when"], ["style", "style"], ["venue", "venue"]]) {
    bind(id, key);
    state[key] = document.getElementById(id).value; // a saved value that no longer exists resets
  }
  const ids = new Set(DATA.concerts.map((c) => c.id));
  state.hidden = state.hidden.filter((id) => ids.has(id)); // forget concerts that are gone
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
  }
  document.getElementById("lb-import").addEventListener("click", () =>
    importListenBrainz(document.getElementById("lb-user").value),
  );
  document.getElementById("reset").addEventListener("click", () => {
    tagRun++; // stop a running tag loop
    state = Object.assign(loadState(), { seeds: [], liked: [], disliked: [], hidden: [] });
    saveState();
    box.value = "";
    setStatus("Goûts et avis effacés de ce navigateur.");
    render();
  });

  for (const btn of document.querySelectorAll("nav button")) {
    btn.addEventListener("click", () => {
      for (const other of document.querySelectorAll("nav button")) {
        const on = other === btn;
        other.setAttribute("aria-pressed", String(on));
        document.getElementById(other.dataset.tab).hidden = !on;
      }
    });
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
    document.getElementById("zone").textContent =
      `${concerts.length} concerts dans les ${report.zone.window_days} prochains jours · ${report.zone.name}`;
    document.getElementById("generated").textContent =
      `Mis à jour le ${new Date(report.generated_at).toLocaleString("fr-FR", { timeZone: TZ })}.`;
    const m = DEEP_LINK_RE.exec(location.hash);
    deepLinkId = m && concerts.some((c) => c.id === m[1]) ? m[1] : null;
    setupControls();
    render();
    renderSources(venues, report);
    focusDeepLink();
    finishSpotify();
  } catch {
    document.getElementById("concerts").replaceChildren(el("p", "Données indisponibles.", "muted"));
  }
}

main();
