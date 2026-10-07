# listing_jsonld fixtures (WIP-62)

Reconstructed from the capture of 2026-10-07 (~07:50 Europe/Paris, server HTML fetched without
JavaScript from epiceriemoderne.com, marchegare.fr, auditorium-lyon.com and opera-lyon.com).
The agent workspace has no network and WebFetch returns markdown, so these are not the original
pages: each listing keeps a few of the captured event links (plus links the reader must ignore),
and each detail page holds one JSON-LD script with the captured structure and values.

- Kept as captured: link paths, JSON-LD shape (top-level object, `@graph`, array of Events),
  `@type` spelling (`http://schema.org/Event` on opera-lyon.com), `name`, `url`, `startDate`
  (with or without UTC offset), `endDate`, `location.name`, the extra BreadcrumbList block.
- Left out: `image`, `description`, `logo`, organizer/performer/address details (truncated in
  the capture), page markup and CSS.
- Not in the capture, added to test the filters: the `scolaires`, `famille` and
  `atelier-sonore` slugs on the Auditorium listing (the category segments are real, the slugs
  are not), the off-host, `javascript:` and fragment links, and `epicerie_temples.html` (same
  structure as the captured Lemon Twigs page; date from its link and the 2026-10-06 published
  data in `tests/fixtures/coverage/`).
