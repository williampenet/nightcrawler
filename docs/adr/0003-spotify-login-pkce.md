# ADR-0003: Spotify login in the browser (OAuth Authorization Code + PKCE)

- **Status:** Accepted (PM decision 2026-10-05: personal app; architecture delegated to the agents)
- **Date:** 2026-10-05
- **Deciders:** William (PM), Claude (engineer)
- **Amends:** ADR-0002 (adds Spotify as a taste source)

## Context
PRD v2 makes Nightcrawler a personal app for William. Spotify's Web API still lets a logged-in user read their top artists and followed artists, but since February 2026 a Development Mode app allows **5 users at most** and its owner needs **Spotify Premium**; wider access requires Extended Quota, out of reach for this project. For one user (plus up to 4 friends) this is enough. ADR-0002 keeps the profile in the browser; the static page has no server.

## Options considered
| Option | Pros | Cons | Monthly cost |
|---|---|---|---|
| **A. Authorization Code + PKCE in the browser** | No server, no client secret; Spotify's recommended flow for public clients | Token lives briefly in the page; Development Mode user cap | €0 |
| B. Backend holding the client secret and refresh token | Silent re-sync, server-side notifications later | A server and a stored personal token, against ADR-0002 | €0–5 + ops |
| C. Manual import of the Spotify data export file | Works for anyone | Days of delay, manual, file handling | €0 |

## Decision
Option A.

1. "Connecter Spotify" creates a 64-character code verifier and a random `state`, keeps them in `sessionStorage`, and redirects to Spotify with the S256 challenge and the scopes `user-top-read user-follow-read`.
2. On return, before any other network call, the page reads `code` and `state`, **always deletes the saved entry**, and removes the code from the URL. A missing or different `state` is rejected.
3. The code is exchanged at Spotify's token endpoint (`client_id`, `code_verifier`, no secret). The access token stays in one function's scope: it is never stored, logged or put in a URL; the refresh token is ignored.
4. Top artists (medium, long, short term) and followed artists are read; **only names** are kept, capped at 100 (each new seed costs one MusicBrainz call), merged without splitting names that contain commas.
5. The client id is public. It lives in `config/app.yaml`; the pipeline publishes an allowlist of keys (`spotify_client_id` only) to `site/app-config.json`. Without a valid id (32 hex characters) the button is hidden.

## Security
- **Data:** artist names come from Spotify (a US company) into the browser; nothing goes back to Spotify beyond the OAuth exchange. Names then follow ADR-0002 (MusicBrainz tag lookups). No server stores anything.
- **OAuth:** PKCE S256, CSRF `state`, single-use verifier, exact redirect URI registered on the Spotify app (no open redirect), minimal read-only scopes.
- **Token:** in memory for one import only.
- **Config:** only allowlisted keys reach the public page; a malformed config file publishes nothing.

## Consequences
- Easier: one click fills "Mes goûts" with William's real listening.
- Harder: re-sync needs a new click (no refresh token kept); friends must be added by e-mail in the Spotify dashboard (max 4).
- To watch: Spotify policy changes on Development Mode.

## Cost impact
€0/month. Total vs budget: €0 / €20.

## Evidence (2026-10-06, WIP-48)

| Data | Call | HTTP | What came back |
|---|---|---|---|
| Top artists | `GET /me/top/artists` | 200 | items; **no `genres`, no `popularity`, no `followers` field** |
| Top tracks | `GET /me/top/tracks` | 200 | items; no `preview_url` |
| Followed artists | `GET /me/following?type=artist` | 200 | items |
| Recently played | `GET /me/player/recently-played` | 200 | items |
| Saved tracks | `GET /me/tracks` | 200 | items |
| Playlists | `GET /me/playlists` | 200 | items |
| Search, limit 10 | `GET /search` | 200 | 10 items |
| Search, limit 20 | `GET /search` | **400** | — |
| Artist | `GET /artists/{id}` | 200 | **no `genres`, no `popularity`, no `followers`** |
| Several artists | `GET /artists?ids=` | **403** | — |
| Related artists | `GET /artists/{id}/related-artists` | **403** | — |
| Artist top tracks | `GET /artists/{id}/top-tracks` | **403** | — |
| Recommendations | `GET /recommendations` | **404** | — |
| Audio features | `GET /audio-features` | **403** | — |

Sources: real test run by William on 2026-10-06 with the page's "Tester l'API Spotify" button
(WIP-48, PR #36; screenshot in the conversation), compared with Spotify's announcements
[2024-11-27](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) and the
[February 2026 changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026).
The changelog lists `followers` and `popularity` as removed; the missing `genres` field is
our measurement (not found in those pages).
