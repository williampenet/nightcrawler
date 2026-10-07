// Ratings queue sent to the feedback function (ADR-0005). Pure helpers + one network call;
// loaded by the page and by `node --test tests/js`. Items stay queued until the function
// accepts them; the key is stored on its own, never in the shared/exported state.
"use strict";

(function (root) {
  const QUEUE_KEY = "nightcrawler.feedback.queue";
  const TOKEN_KEY = "nightcrawler.feedback.token";
  const MAX_QUEUE = 200;
  const MAX_ITEMS = 50; // per request, as the function accepts
  const MAX_BODY = 4000; // the function accepts 4096 bytes
  const KINDS = new Set(["like", "unlike", "dislike", "wrong"]);
  const CONCERT_ID_RE = /^[0-9a-f]{12}$/;
  const ARTIST_KEY_RE = /^[a-z0-9]{1,100}$/;

  // Each queued item has a local id, so sent items are removed by id even if the queue
  // changed meanwhile (cap trimming, another tab). The id is never sent.
  let counter = 0;
  function newId() {
    if (root.crypto && typeof root.crypto.randomUUID === "function") return root.crypto.randomUUID();
    counter += 1;
    return `${Date.now().toString(36)}-${counter}-${Math.random().toString(36).slice(2)}`;
  }

  // A well-formed item, or null (same rules as the function).
  function makeItem(kind, concertId, artistKeys, id) {
    if (!KINDS.has(kind)) return null;
    const cid = typeof concertId === "string" && CONCERT_ID_RE.test(concertId) ? concertId : null;
    const keys = [...new Set(Array.isArray(artistKeys) ? artistKeys : [])]
      .filter((k) => typeof k === "string" && ARTIST_KEY_RE.test(k))
      .slice(0, 12);
    if (!cid && !keys.length) return null;
    const ok = typeof id === "string" && id.length > 0 && id.length <= 64;
    return { id: ok ? id : newId(), kind, concert_id: cid, artist_keys: keys };
  }

  // Saved queue is untrusted: keep only valid items, newest MAX_QUEUE.
  function parseQueue(text) {
    let raw;
    try {
      raw = JSON.parse(text || "[]");
    } catch {
      return [];
    }
    if (!Array.isArray(raw)) return [];
    return raw
      .map((x) => x && makeItem(x.kind, x.concert_id, x.artist_keys, x.id))
      .filter(Boolean)
      .slice(-MAX_QUEUE);
  }

  function enqueue(queue, item) {
    return item ? [...queue, item].slice(-MAX_QUEUE) : queue;
  }

  const wire = ({ kind, concert_id, artist_keys }) => ({ kind, concert_id, artist_keys });

  // The first items that fit in one request.
  function nextBatch(queue) {
    const out = [];
    for (const item of queue.slice(0, MAX_ITEMS)) {
      if (JSON.stringify({ items: [...out, item].map(wire) }).length > MAX_BODY) break;
      out.push(item);
    }
    return out;
  }

  // Sends the queue in batches. io: { load(), save(queue), fetch }. Returns
  // "sent" | "empty" | "unauthorized" | "error" (429 included: kept for later) | "unconfigured".
  async function flush(url, token, io) {
    if (!url || !token) return "unconfigured";
    let queue = io.load();
    if (!queue.length) return "empty";
    while (queue.length) {
      const batch = nextBatch(queue);
      let res;
      try {
        res = await io.fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ items: batch.map(wire) }),
        });
      } catch {
        return "error";
      }
      if (res.status === 401) return "unauthorized";
      // 400: the function rejects this batch for good; drop it rather than block the queue
      if (!res.ok && res.status !== 400) return "error";
      // re-read and remove by id: items queued or trimmed while in flight are handled right
      const sent = new Set(batch.map((x) => x.id));
      queue = io.load().filter((x) => !sent.has(x.id));
      io.save(queue);
    }
    return "sent";
  }

  // Warning while ratings wait for a send key or the key was refused (WIP-70), or null.
  // Only the count is shown ("avis" is the same in singular and plural).
  function pendingBanner(queueLength, hasToken, refused = false) {
    const n = Number.isInteger(queueLength) && queueLength > 0 ? queueLength : 0;
    if (!n) return null;
    if (hasToken && refused) return `${n} avis en attente : clé d'envoi refusée`;
    if (hasToken) return null;
    return `${n} avis en attente d'envoi : saisis ta clé d'envoi`;
  }

  const api = { QUEUE_KEY, TOKEN_KEY, MAX_QUEUE, makeItem, parseQueue, enqueue, nextBatch, flush, pendingBanner };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NCFeedback = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
