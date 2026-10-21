# Torrent source recovery — proving a swarm is dead, and hunting a replacement

Use when torrents sit at 0% forever, show `forcedMetaDL`, or a queue that a recheck/resume does not move. The goal is to separate **client fault** from **swarm death**, then find a source that actually has peers.

## Rule zero: an index's seeder count is not evidence

Public-index seed counts (The Pirate Bay above all) are stale — never garbage-collected, and routinely claim 3–10 seeds for a swarm that has none. **Do not build a replacement list from index counts and do not tell the user a title "is available" on that basis.** Recommend nothing you have not watched connect. Expect a low yield and report it honestly rather than presenting a candidate list as if it were verified.

## Step 1 — separate client fault from swarm death (read-only, seconds)

1. `GET /api/v2/sync/maindata` → `server_state.dht_nodes`. Hundreds of nodes = healthy DHT, so a torrent with 0 peers is not a DHT problem.
2. `GET /api/v2/torrents/info`. If **anything else is downloading**, the client and network are fine and the fault is per-swarm — stop debugging the client.
3. `GET /api/v2/torrents/trackers?hash=<h>` and read the tracker's **own** report:
   - `num_seeds=0` with `num_peers=1` from a tracker whose `status=2` means the tracker answered and the swarm is empty (`peers=1` is us).
   - `status`: `1` = not contacted yet, `2` = working, `4` = not working, `6` = skipping, unreachable. **`msg` is authoritative — quote it** ("timed out", "Host not found (authoritative)", "skipping tracker announce (unreachable)").
   - The `** [DHT] **` / `** [PeX] **` / `** [LSD] **` rows carry the same fields. All-zero alongside a healthy `dht_nodes` means genuinely no peers, not a blocked port.
4. **`forcedMetaDL` at `0.00GB` is the deadest signature** — metadata was never fetched, so no peer was ever reachable. It will not recover with time, a recheck, or more trackers.

**Do not misread the `endpoints` array.** Entries like `127.0.0.1:<listen_port>` and `172.17.0.2:<port>` are *our own* announced endpoints (the container's docker-local address), not the tracker's resolved addresses. Reading them as "the tracker resolves to localhost" sends you chasing a DNS bug that does not exist — `getent hosts <tracker-host>` from inside the CT shows the real public addresses.

## Step 2 — try the cheap fix exactly once

A torrent whose only announce URL is a dead private tracker deserves one pass, not a campaign:

```
POST /api/v2/torrents/addTrackers   hash=<h>&urls=<newline-separated trackers>
POST /api/v2/torrents/reannounce    hashes=<h>
```

Working set: `tracker.opentrackr.org:1337`, `open.stealth.si:80`, `tracker.torrent.eu.org:451`, `exodus.desync.com:6969`, `tracker.dler.org:6969`, `open.demonii.com:1337`, `tracker.openbittorrent.com:6968`, TPB's own, `bt1/bt2.archive.org:6969`, `tracker.uw0.xyz:6969` (all `udp://` unless noted).

If every responding tracker still reports 0 seeds after ~2 minutes, the swarm is dead. Stop — more trackers cannot conjure peers.

## Step 3 — the only real liveness test: add it and watch

Add the candidates, wait 2–5 minutes, then classify:

- **LIVE** — metadata arrived and (`progress > 0` or `num_seeds > 0` or `dlspeed > 0`). A small/well-seeded file can finish inside that window.
- **DEAD** — still `forcedMetaDL` at 0.00GB, 0 seeds, 0 connections after the wait.

`scripts/swarm-liveness-probe.py <infohash|magnet>...` does add → force-start → poll → classify and prints a verdict per candidate. It adds to the client and deletes nothing.

**A single DEAD verdict can be premature — re-check before condemning a candidate.** Thin swarms can take longer than the probe window to hand over metadata: a candidate classified DEAD at 300 s was downloading at 41 KB/s with a seed by the next check. Treat `forcedMetaDL` + 0 peers at 300 s as *probably* dead, re-read `torrents/info` once more before writing it off, and never tell the user a title is unavailable from one window alone. Conversely a `forcedDL` with metadata but 0 seeds is not yet live either — demand actual `dlspeed` or `num_seeds` before calling it LIVE.

## qBittorrent add pitfalls

- **`409 Conflict` on add is a duplicate, and the duplicate test is by FILES, not infohash.** A newer re-upload with a *different* infohash but identical file names/sizes is refused. Confirm via the log before blaming the magnet:
  `GET /api/v2/log/main?last_known_id=-1` → `Detected an attempt to add a duplicate torrent. Source: <magnet>. Existing torrent: "<name>"`.
  Consequence: to join the healthier swarm you must remove the existing torrent first, and its partial data is **not** reusable across infohashes. Removing a torrent is the user's call — ask, and flag that the partial data is discarded.
- **The add response is JSON**: `{"added_torrent_ids":[...],"failure_count":N,"success_count":M}`. Diff against a pre-add `GET /api/v2/torrents/info` to learn which candidates failed.
- **`max_active_downloads` (default 3) silently parks the queue.** With dozens of incomplete torrents, newly added ones sit in `queuedDL` and can never progress — it looks like "nothing is happening" but it is the cap. Raise the setting, or `POST /api/v2/torrents/setForceStart` with `value=true` for a batch (force-start bypasses queue limits). **Tell the user which you changed.**
- The `state` right after adding is the fastest diagnosis: `forcedMetaDL` = no metadata (dead); `forcedDL` + `num_seeds > 0` = live.

## Searching public indexes

**apibay.org (The Pirate Bay) requires a browser `User-Agent`** — a default script UA gets 403. It works with a Chrome UA string.

```
GET https://apibay.org/q.php?q=<urlencoded query>
```

JSON array; an entry with `id == "0"` means no results. Fields: `name`, `info_hash`, `seeders`, `leechers`, `size` (bytes as a string), `username` (uploader), `added` (epoch) — and remember the counts are not evidence (Rule zero). Pace ~1.5s between queries; rapid-fire starts 403ing even with the UA.

### Query craft

- Release-name queries beat title queries: the uploader string (`MVGroup`) and the part token (`1of2`, `S01E01`) are what discriminate.
- A book/EPUB needs the **author** name — a title-only query returns the video releases or nothing at all (a "no hits" result is usually a bad query, not a missing file).
- Match a candidate against the stalled torrent by lowercasing both and comparing `info_hash`. Same hash = the swarm moved; different hash = a different release (whose files may still be identical — see the 409 trap).
- Build a magnet directly: `magnet:?xt=urn:btih:<HASH>&dn=<urlencoded name>` — with the FULL 40-char `info_hash` from the API response. Never reuse a display-truncated prefix: a 12-char screen excerpt builds a malformed magnet whose add fails, which then misreads as a duplicate or a dead client. Re-read the field whenever the hash on screen is shortened.

### Indexes surveyed from this network — what an agent can actually call

**JSON APIs (no parsing, no JS — reach for these first)**

| Index | Endpoint | Notes |
|---|---|---|
| SolidTorrents | `https://solidtorrents.to/api/v1/search?q=<q>&sort=seeders` | **DEAD** — 301s / empty responses, corroborated by the torrent-indexer-sweep skill and direct attempts; do not plan around it, use LimeTorrents / Bitsearch instead |
| The Pirate Bay | `https://apibay.org/q.php?q=<q>` | JSON; needs a browser UA; pace ~1.5 s or it 403s |
| YTS | `https://yts.lt/api/v2/list_movies.json?query_term=<q>&limit=N` | Films only, but per-quality magnets with `seeds` + `hash`. `yts.mx` has no DNS and `yts.am` 403s from this network — use `yts.lt`. Spelling-sensitive; verify the returned title/year |
| EZTV | `https://eztv.re/api/get-torrents?limit=N&page=1` | TV only; 1.08 M records; each row has `magnet_url`, `seeds`, `peers` |
| Internet Archive | `https://archive.org/advancedsearch.php?q=<q>&output=json&rows=N` | Not an index — direct downloads, no swarm to die |
| Academic Torrents | `https://academictorrents.com/` | Serves **cached RSS/XML** per search by design; do not scrape `browse.php` |
| Libgen | `https://libgen.li/json.php?req=<q>` | Books; endpoint answers with `{"error":"No Request keys"}` until params are right. `libgen.is` times out; `libgen.rs`/`libgen.gs` do not resolve |

**HTML — parseable, no JS wall**

`torrentfunk.com` · `limetorrents.lol` (**best working HTML source**: full real magnets on detail pages; hrefs are relative `-torrent-<digits>.html` — see the torrent-indexer-sweep skill for the threaded sweep workflow) · `bitsearch.eu` (resolution inline in titles; infohash but no magnet on detail pages) · `therarbg.com` · `torrentdownloads.pro` · `audiobookbay.is` (needs SSL verification disabled) · `torrentz2.nz` · `torrentproject.se` · `torrentseeker.com` (meta-search over 100+ sites)

**Not usable by an agent from here**

- `torrentgalaxy.one` — returns 1.2 MB of HTML with **zero magnets**; results are JS-rendered and the search form POSTs to `/get-posts/`. Do not burn time on it.
- `1337x` + proxies — `.st` 403, `.gd` no DNS, `.is` serves a JS fingerprint challenge.
- `btdig.com` — the best DHT index, but 429s immediately at any pace, including a 35 s backoff.
- `knaben.eu`, `bitsearch.to`, `magnetdl.com` (522), `bt4gprx.com` (403), `nyaa.si`, `glodls.to` (500), `zooqle.com` (timeout).

**Prowlarr lives on CT101 — check it FIRST before hand-scraping indexes.** CT101 `media-download` on prox1 runs Prowlarr in Docker on `:9696` (API key in `/opt/prowlarr/config/config.xml`), backed by 13 enabled torrent indexers including The Pirate Bay, 1337x, LimeTorrents, Torrent9, TorrentDownload, YTS, Knaben, EZTV and TorrentProject2. `GET /api/v1/search?query=<q>&type=search` with header `X-Api-Key` returns machine-readable candidates (title, size, seeders, `magnetUrl`, `infoHash`) across all of them at once — pass the key ONLY as the `X-Api-Key` header; the `?apikey=` query-param form answers 401/empty. — the machine-readable surface the ad-hoc index table below is approximating by hand. A prior revision of this file claimed no aggregator existed on the LAN; that survey was wrong, and hand-scraping apibay one query at a time wasted whole sessions before the box was inventoried. When hunting, Prowlarr first, single-index scraping only for what Prowlarr misses.

**Cross-index reality check:** the same infohash carries wildly different seeder counts per index (one release read `S=3` on TPB, `S=24` on SolidTorrents, `S=91` on another, while a live announce and the client both showed 0). Different indexes cache stale scrapes at different times, so a *higher* number cross-index is not corroboration. Only the client's own peers count. A second index is worth using for **new infohashes it surfaces**, not for its numbers.

### What actually replaces these

Old XviD/PDTV encodes die; the same uploader's **newer 1080p/x265 re-uploads of the same title carry the live swarm**. Prefer, in order: newer re-upload → another group's BD/DVD encode → the Internet Archive copy (`https://archive.org/advancedsearch.php?q=...&output=json`, searchable by title — direct download, no swarm needed) → a platform capture via yt-dlp (see `media-torrent-hunting` § YouTube fallback — check the upload's format ceiling first, an SD upload is not an upgrade) → give up and say so.

## When the files themselves were deleted (`deleteFiles=true`)

Undelete is a race against the next write, so freeze FIRST and scan second:

1. Stop all torrents in the client (every active download risks overwriting the freed blocks) and remount the downloads filesystem read-only before running anything.
2. Run `xfs_undelete` against the block device with a date filter around the deletion window — recovered files are named by deletion timestamp, which is also the filter mechanism.
3. Expect a low yield if writes continued after deletion: a day of active downloads over the freed extents leaves only fragments (subtitle/cover-art sized). Report the shortfall honestly and pivot to re-sourcing rather than re-scanning.

## When the origin is a private forum

A release that is dead from **lack of seeders** gains nothing from logging into the tracker's forum: the forum hands back the same `.torrent` pointing at the same empty swarm. Do not spend the session fighting a bot-walled login to fix a seeding problem — say plainly that it cannot help, and pivot to public-index search + liveness testing, or an alternate delivery route. Raise the forum once if it seems useful; treat a failed login as answered, not as a puzzle to keep attacking.

### Private-tracker auth is a different mechanism from swarm death (MVGroup case)

The paragraph above covers *unseeded* releases. A *seeded* private-tracker release that never connects is usually missing auth, not peers — diagnose it before declaring death. MVGroup magnets ship a bare `http://www.mvgroup.org:2710/announce` with no passkey, and that endpoint is dead (announces time out). The live endpoints (`http://mvgforumtracker.mvgroup.org/tracker.php/announce`, `http://www.mvgroup.org/tracker.php/announce`) answer an unauthenticated announce with tracker `msg` **"Forbidden"** — read the client's trackers tab first: "Forbidden" is passkey rejection and is fixable; timeouts everywhere plus zero DHT peers is death. The passkey comes from the forum account (User CP, passkey/announce-URL section), is stored with the other keys, and is injected per torrent via `POST /api/v2/torrents/addTrackers` with the passkey-bearing announce URL followed by `reannounce`. One auth attempt per torrent; if "Forbidden" persists with the passkey in place, fall back to swarm-death handling.

## Bulk sweep with Prowlarr

`scripts/prowlarr-sweep.py` reads the qBittorrent queue, keeps every incomplete zero-seed torrent, recovers names from `magnet_uri` when the title is still a bare infohash, then sweeps Prowlarr and reports which titles have a candidate elsewhere. A 42-torrent sweep takes ~2 min once CloudFlare is warm (first query ~20 s while FlareSolverr solves, then ~4 s each).

**Never report Prowlarr's raw hit counts.** A general-purpose index returns confident garbage: Torrent9 matched *Camera Man* → `Batman.V.Superman.Dawn.Of.Justice` and *Born to be Wild* → `American.Horror.Story`, and because that noise carries the **highest** seeders it sorts to the TOP of the list. Raw counts claimed 24/26 stuck titles had candidates; title-filtering cut it to 20. Filter before you believe, and say in the report that you filtered.

**A relevance filter can hide real hits — scale the requirement to the query.** Requiring ≥2 overlapping distinctive terms makes any title whose distinctive set collapses to one word unmatchable: `TCM The Making of Psycho` reduces to `{psycho}` once *tcm* and *making* are treated as generic, so two genuine S=143 hits were silently dropped. Use 1 token → 1, 2 tokens → 2, else n−1.

**Substring token matches are a different-work trap — require phrase-level agreement.** Token overlap passes candidates that share scattered generic words with the query while being a different work entirely: a *Fake or Fortune* episode about Churchill's garden matched a *Churchills* query; *Cinema and the Second Sex* matched a *Sex and the Cinema* query; an *Einstein and Hawking* compilation matched a *Hawking's Universe* query. Require the candidate to carry the query's distinctive multi-word phrase as a unit, not the same words scattered across an unrelated title. **After any bulk add, re-read the added torrent names in the queue and delete wrong-work grabs immediately** — at 0% the removal costs nothing, while the mistake is invisible by ingest time.

**When the original names are lost, file size is an identity fingerprint.** Deleted-torrent logs often keep exact byte sizes but truncated or missing titles (genre-prefixed packs are the worst case). A broad query plus a ±5–6% size match re-identifies them — but size alone also matches junk, so every size-match still needs the title/junk filter above before adding.

**Separate SAME-release from DIFFERENT-release candidates.** A candidate whose name is near-identical to the stalled torrent (Jaccard ≥ ~0.6) is very likely the same dead swarm — the index is just reporting that swarm's stale count. Only a **different release** (another rip/encoder/resolution of the same title) is a real chance of live peers. Reporting a SAME-release hit as "available elsewhere" is the stale-count error wearing a new hat.

### Adding Prowlarr results to qBittorrent

**Resolve the fetch link per result, in this order: a `guid` that starts with
`magnet:` (TPB-backed results carry the magnet here and no `downloadUrl`) →
the Prowlarr-hosted `downloadUrl` proxy → `magnetUrl`.** Never POST the `guid`
blindly (for most indexers it is an HTML page the client cannot parse and the
add silently does nothing), and never POST when all three are empty — an empty
`urls` add fails and misreads as a client problem. After adding, verify by
infohash membership in the queue, not by the response status — a `202` from
`/add` is ambiguous (seen for both successfully queued adds and silently failed
ones), so only the membership check counts. Some indexer rows carry no `infoHash` at all — when it is absent, match by title instead of reading the candidate as missing. The `link` param is **base64url**, not a URL: calling `/download?link=<raw url>` returns HTTP 500 `Malformed input: 145 is an invalid input length`. **The proxied `downloadUrl` frequently 301-redirects to a `magnet:` URI instead of returning bytes** — stock HTTP clients raise on a non-HTTP redirect target rather than following it, so catch the redirect error and read the `Location` header: it carries the full `btih:` infohash, which is the magnet you needed. Three outcomes, all useful: a 200 with bytes is the `.torrent` itself (confirm by the `announce` key, save to disk for later); a 301 whose `Location` starts with `magnet:` is the infohash (parse it, build the magnet yourself); a 500 means the proxied link expired — re-search for a fresh one rather than retrying the dead URL. **Do not POST to `/api/v1/download` or `/api/v1/release`, and do not GET `/api/v1/download?guid=…&indexerId=…` — the POSTs return 405 and the GET 404.** The only working retrieval path is GETting the per-result `downloadUrl` straight from a fresh search response. TPB-backed results additionally carry no `downloadUrl` at all, so resolve those through the apibay fallback in the same pass instead of retrying the result. (zero of fourteen hits on one title) — when that happens, fall back to the apibay `q.php` API in the table above, which returns `info_hash` directly, and build `magnet:?xt=urn:btih:<HASH>&dn=<name>` yourself.

**qBittorrent 5.x `/api/v2/torrents/add` returns JSON, not the classic `"Ok."`.** `{"added_torrent_ids":[],"failure_count":0,"pending_count":1,"success_count":0}` means **accepted and pending metadata**, not failed. Checking for `== "Ok."` scored 14 real adds as "FAILED 0 ADDED". Verify by reading the queue for the infohashes — never by the response body.

**The decisive test is infohash membership, not title similarity.** Snapshot the client's hashes BEFORE adding, then classify each candidate: already-present vs new. On this queue 13 of 14 first-round "candidates" were the **exact infohashes already stalled in the client** — the queue count did not move at all (72 → 72). That is the strongest form of the stale-count proof: the alternative index is serving the same dead swarm under a different URL. A second pass restricted to infohashes the client did *not* have found 9 real ones, of which **2 went live within 4 minutes** (~4–5 MB/s, ~68% progress). Only ever report a hit as "available elsewhere" after that membership check.

### Keep a staged-vs-watching wanted list

A sourcing session ends with two kinds of outcome and they rot at different speeds, so keep them in one JSON file (`wanted-media.json`): **staged** entries carry everything an add needs later (release title, size, seeder count at search time, indexer, `infoHash`, full magnet, TMDB/IMDb ids, target library folder) — magnets and proxied links go stale, but the infohash never does, so a staged entry stays actionable indefinitely; **watching** entries are the nulls and thin swarms to re-search periodically, each with one line on WHY it is watching (zero hits, wrong-work noise, quality below the bar, needs user clarification). Re-search watching entries with fresh Prowlarr queries rather than re-reading old seeder counts, and promote to staged only on a newly resolved magnet or torrent file — never on a bigger number for the same infohash.

## Prove a release IS the title you want before downloading it

A near-identical title is not evidence. An older documentary, a soundtrack album, or a same-subject radio piece all match a film's name, and the mismatch is invisible until you have pulled GBs. Read the release's own provenance first — archive and DVD-rip groups ship an `_Info.txt` / `mediainfo.txt` beside the media, so the whole check costs a few KB:

1. Add the magnet, then poll `GET /api/v2/torrents/files?hash=<h>` until the list appears (~3 s once metadata is in).
2. Skip the media, fetch the text: `POST /api/v2/torrents/filePrio` with `hash`, `id=<n>`, `priority=0` for **every** file, then `priority=1` for the `.txt`/`.md5` entries (`0` = skip, `1` = normal, `7` = high).
3. Wait for those entries to hit `progress >= 1.0`, then read them from the torrent's `save_path`.
4. `POST /api/v2/torrents/delete` with `hashes` + `deleteFiles=true`, and re-list to confirm it is gone.

**Make the delete unconditional, not the next statement in the try block.** These text files are cp1252 (`0x92` is a smart quote), so a naive `utf-8` read raises `UnicodeDecodeError` and aborts the script *before* the delete runs — leaving your probe torrent sitting in the user's client. Decode with `errors="replace"` and run cleanup however the read ends.

**What the provenance settles:** `_Info.txt` names the broadcast channel, air date, director and production company plus the capture chain (`SOURCE: Digital TV Broadcast > ... > VIDEO_TS`), and the video/audio spec. A PAL 720x576 MPEG-2 `VIDEO_TS` is an SD capture regardless of how the torrent is titled; `mediainfo.txt` gives the true per-file duration.

**Upload date is provenance too.** A release posted *before* the title could have existed cannot be that title, and one dated to an artist's death or an anniversary is a re-upload of something older. Two copies of a "<Artist> Story" DVD here were dated two years before the film and on the artist's death date; both were the same older documentary, and the file list showed a single SD feature rather than the described cinematic release. Declining the download was the correct outcome — report what the release actually is rather than acquiring it on a title match.

## Handing off to ingest

A completed torrent is still not an ingested title. Verify the file on disk by byte size against the client's reported `size` **and** by probe (`ffprobe` must return real video streams with the expected dimensions) — size alone passes hole-ridden files whose unwritten extents read as zeros — then follow the ingest procedure — **copy, never move**, into the library.

**Never assume where the files are.** `save_path` is per-torrent: most land in the shared downloads dir, but some sit in the client's temp dir or a container-internal path — read `save_path` plus `GET /api/v2/torrents/files?hash=<h>` and build the source path from those, never from the torrent name. A `.​!qB` suffix means the client still considers the file incomplete (usually a tag-along file that never finished); the media may still be whole, but prove it. And per-file `progress: 1.0` is not proof either — a file can report 100% with zeroed extents — so read the head magic bytes (`1a 45 df a3` EBML for mkv, `RIFF` for avi) before any multi-GB copy; it costs one read and catches the corrupt case the progress field hides.
