# qBittorrent Web API — reach it and drive it (verified v5.2.x, Oct 2026)

## Finding the endpoint

The WebUI/API host is **not** documented anywhere reliable and the proxy
reference carried a stale IP for months. Discover it, don't trust notes:

1. The client runs in the `media-download` LXC (CT101) on the Proxmox node.
   Find which node actually hosts it via the Proxmox MCP (`list_nodes` →
   `list_containers`), reading the container's `tags` for `qbittorrent`.
2. The container's own IP comes from `get_container_config` → `net0`
   (`ip=<a.b.c.d>/<cidr>`), NOT from any doc. On this install it is
   `192.168.214.11`, and the WebUI is on **port 8090**, not the default 8080
   (the linuxserver image maps host `8090` → container `8080`).
3. Prove it before scripting against it: `GET /api/v2/app/version` returns
   the bare version string (e.g. `v5.2.4`) — not JSON, read it as text.
   A `000` means wrong port; a `<title>…qBittorrent</title>` on `/` confirms
   you have the WebUI.

Do NOT try to exec into the container to reach the API: `pct exec` over the
Proxmox API is not exposed on this node (501), and SSH to the container host
is key-restricted. The API answers directly over HTTP from the Hermes box —
no SSH, no docker exec, no credential needed (see below).

## Authentication

The LAN subnet is **bypassed** (`192.168.214.0/24` whitelist +
`WebUI\HostHeaderValidation=false` + `CSRFProtection=false`), so unauthenticated
reads AND writes work from any box on that subnet:

```
curl -s http://192.168.214.11:8090/api/v2/torrents/info        # works, no cookie
```

No login, no SID cookie, no apikey. If a call unexpectedly 401s, the client
is a different instance than the one documented here — re-probe `/app/version`
rather than hunting for credentials.

## v5.x endpoint quirks that cost time

- **`/api/v2/torrents/add` needs POST form data.** A `GET …?urls=<magnet>`
  returns `405 Method Not Allowed`. POST `urls=<magnet>` as
  `application/x-www-form-urlencoded`.
- **`409 Conflict` on add means the infohash is ALREADY queued** — a cheap,
  reliable dedupe check. Use it instead of listing-and-matching by name.
- **`resume` is gone (404); use `start`.** Likewise `stop`, not `pause`.
  `setForceStart` needs `value=true|false` explicitly or it 400s.
- **One hash per mutating request.** `delete`, `setForceStart`, `recheck`,
  `reannounce` all reject pipe-joined (`a|b|c`) hash batches with a 400.
  Loop one POST per hash.
- **Responses can contain raw control characters** (embedded newlines in a
  release name, or a giant malformed `comment` blob). `json.loads` raises
  `Invalid control character` / `Expecting ':' delimiter`. Parse with
  `strict=False`, and `.get()` every field — one entry was observed missing
  `hash` and `name` entirely. Never assume a well-formed list.
- **A stale name-filter lies.** Matching queue entries by name substring
  misses torrents whose files are gone (see ghost-seeders below) and can
  sweep in unapproved entries. Match by infohash, always.

## Ghost-seeder sweep (post-ingest cleanup)

A torrent whose payload was ingested-and-deleted can linger at
`progress: 1.0` / `forcedUP` / `stoppedUP` while every file path under its
`content_path` is absent from disk. The WebUI still lists it as a live
torrent — which is why a filesystem sweep reports "clean" while the user
still sees the title "in downloads". To clear them:

1. `GET /api/v2/torrents/info`, keep entries with `progress >= 0.999`.
2. For each, `GET /api/v2/torrents/files?hash=` and test each `name` against
   the save path on the share host. All absent = ghost.
3. Confirm the title is genuinely in Jellyfin (short-phrase `SearchTerm`).
4. `POST /api/v2/torrents/delete` with `hashes=<h>&deleteFiles=true`, one hash
   per call, then re-list to prove each is gone.

This is the ONLY cleanup that removes a torrent entry; it never touches
partial (sub-100%) downloads — those are protected by `media-deletion-policy`.
See `music-docos-library-maintenance` → "Post-ingest download sweep" for the
full three-sinks model (qB entries, SAB `incomplete/`+`complete/`, `_FAILED_`
markers).

## Reporting download health (status answers)

A "how are downloads going?" answer is a states breakdown plus proof of motion, never a bare total:

- Report `states` counts from one fresh `/api/v2/torrents/info` fetch, then split in-progress into **actively downloading** (`dlspeed > 1024`) vs **stalled** (`progress > 0.05`, `dlspeed <= 1024`, `num_seeds == 0`). A queue of 139 with 0 actively downloading is stalled-or-parked, not working — the total alone hides that.
- Name the `queuedDL` wedge explicitly: entries parked by `max_active_downloads` are waiting for a slot, not dead. Do not present them as failures or re-source them.
- Pair every qB answer with the SAB queue (`mode=queue`: slots, status, percentage) — SAB idle with one paused slot plus qB at 0 B/s is the full picture, and either half alone misleads.

An *arr-style pipeline writes to the same client. A 24-entry queue grew to 143
mid-session from named music-doc torrents this agent never added. So:

- Re-fetch `/torrents/info` immediately before any batch op — a snapshot
  minutes old misattributes arrivals to you.
- Attribute by `added_on` inside your own action window, never by title.
- Never bulk-purge "stuck" `forcedMetaDL` / raw-infohash entries without an
  explicit user-approved hash list — most belong to the other pipeline.

## Music vs video ownership

Video (films, documentaries, music *docos*) is this agent's domain. Music
*audio* (albums, FLAC/MP3, discographies) is the music maintainer's — skip
those rows silently in every sweep. See `music-docos-library-maintenance` →
"The domain boundary".
