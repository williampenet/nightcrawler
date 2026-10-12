// Who may act on the page (ADR-0008, WIP-118): William with his send key (owner), everyone else
// read-only (visitor). The page is public; the real protection is the send key checked by the
// feedback function on every write (ADR-0005). These are the page's rules only. Pure helpers,
// loaded by the page and by `node --test tests/js/*.test.js`.
"use strict";

(function (root) {
  const OWNER_HASH = "#cle"; // opens « Mes goûts » with the key field (no visible "log in")
  // the public showcase, written by the pipeline next to concerts.json (ADR-0008 Decision 1)
  const PUBLIC_VERDICTS = "data/verdicts.json";
  const PUBLIC_PROFILE = "data/profile.json";

  // Owner: a feedback service, a stored key, and no 401 from the service for that key.
  function isOwner({ feedbackUrl, token, refused }) {
    return Boolean(feedbackUrl && typeof token === "string" && token.trim() && !refused);
  }

  // The key field: shown to the owner, to a browser holding a refused key (so it can be fixed)
  // and on the hidden #cle link; never to a plain visitor.
  function keyFieldShown({ feedbackUrl, token, hash }) {
    if (!feedbackUrl) return false;
    return Boolean((typeof token === "string" && token.trim()) || hash === OWNER_HASH);
  }

  // data/profile.json: {data: {...}} -> the profile object, or null when unusable. Its fields are
  // sanitised afterwards by profile.js (toState), like a server copy.
  function publicProfile(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return null;
    const d = body.data;
    return d && typeof d === "object" && !Array.isArray(d) ? d : null;
  }

  const api = { OWNER_HASH, PUBLIC_VERDICTS, PUBLIC_PROFILE, isOwner, keyFieldShown, publicProfile };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCAccess = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
