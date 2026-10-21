---
name: media-torrent-hunting
description: Use when hunting missing media via Prowlarr and qBittorrent.
---

# Hunting missing media via Prowlarr + qBittorrent

API-first alternative to scraping public indexer sites (see
torrent-indexer-sweep for the scrape path): one Prowlarr search fans out to
all enabled indexers and returns size/seeders/magnets as JSON; qBittorrent's
Web API queues and monitors the picks.

**This hunt is already implemented — extend it, never re-script it.** The
proven code is the `media-ops` package: `media_lib/source.py` (broad pool →
normalised match → rank → queue), `media_lib/sab.py` and `media_lib/qb.py` (the
downloader clients), `cli.source plan|queue|pool`, and the `media_source_plan` /
`media_source_queue` plugin verbs. `plan` is read-only and numbered; `queue`
takes explicit ids and has no "all" switch. Four hand-rolled hunt scripts were
deleted because duplicating this logic produced three separate bugs in one
session — a new one-off script will reproduce them.

## Prowlarr search

- **Every hunt runs BOTH sources: torrents AND Usenet.** A torrent miss is
  not a miss until the Usenet indexer (NZBgeek, `indexerIds=17`, plain string)
  has been searched too — proven Oct 2026: TraumaZone had zero torrent seeds
  while all 7 parts sat on Usenet. Scope with `indexerIds` per source so
  echoes don't pollute the other side's results. Usenet rows carry no seeders:
  judge by post age vs provider retention + grabs (high grabs = completable;
  ~1400-day posts are gambles — test one cheap part first to calibrate real
  retention before spending block space on the rest).

- Endpoint: `GET /api/v1/search?query=<q>` with `X-Api-Key` header.
- **Magnet lives in `magnetUrl` OR `guid`.** Some indexers put the real
  `magnet:?xt=` in `guid` while `magnetUrl` is a Prowlarr redirect link — a
  filter demanding `magnetUrl.startswith('magnet:')` silently drops those
  results as false NOMATCH. Accept the first `magnet:` value from either field.
- **Searches time out under load.** Retry once with a longer timeout
  (60-90s). If a narrow query returns nothing, retry BROAD (bare title) and
  filter by tokens in code — narrow subtitle tokens miss postings that omit them.
- **Sweep broad once, then fall back per target — the two miss DIFFERENT
  postings.** Ten per-title queries (`Classic Albums <artist> <album>`, scoped
  to the Usenet indexer) returned ZERO, while four broad queries returned 496
  postings and matched 9 of 12 in code; yet three other postings (Queen, Deep
  Purple, Iron Maiden) appeared ONLY in a per-title query. So: broad sweep →
  match in code → ONE targeted query for anything still unmatched. Do not loop
  three queries per title up front, and do not trust the broad pool alone.
- **Normalise separators before matching or dotted names never match.** Usenet
  posts read `Black.Sabbath.Paranoid...`, so `"black sabbath" in
  title.lower()` is False — three of four grabs failed on exactly that.
  Reduce `[_./-]+` to spaces on BOTH sides first. Same trap: `\bFLAC\b` does
  not match inside `_FLAC_` because underscore is a word character, which is
  how an album rip gets offered as an episode.
- **Canonicalize the request through TMDB (movie AND TV) before searching.** Run the title (+year) through `/search/movie` and `/search/tv` first and hunt with the pinned proper title, year, and runtime — a wrong-year request (e.g. `(2025)` for a film TMDB dates 2015, or a future year with no entry at all) burns narrow searches on tokens that cannot match and reads as unindexed content. A TMDB miss in both namespaces proves nothing by itself (YouTube-native series genuinely have none) — it only means no year/id assumption survives into the hunt.
- Pick: require all must-tokens in the title, seeds > 0, sane size bounds — seed count never overrides identity: a high-seed same-wordcount title (e.g. a horror film sharing one word with the wanted series) is still a miss, so confirm the title tokens BEFORE reading the seed column
  (50MB-60GB); junk-regex out porn/books/audiobooks. For film hunts also junk-regex
 `making of`, `behind the scenes`, `featurette`, `trailer`, `teaser` —
 ancillary titles outscore the film on seeds and otherwise win the pick. Bonus-score titles
  containing the wanted part marker (`2of3`, `ep2`, `complete`). When the
  want is a specific codec/resolution (e.g. 1080p x264), exclude the rest
  by token (`2160p`, `x265`, `hevc`, `10bit`, `av1`) — top-seeded HEVC/AV1
  postings otherwise crowd out the wanted encode. An empty
  genuine-match set is a miss only after a broader-query retry — over-strict
  tokens fail exactly like unindexed content. When the broad retry returns
  hundreds of hits with zero passing the token filter, that IS the completed
  negative — do not keep narrowing; report the miss with the top homonym
  classes as evidence (books, music albums, same-word TV episodes), so the
  user can see the title was searched, not skipped.
- **Advertised seeders are worthless evidence and must never outrank
  completeness or resolution.** Postings advertising 63 and 95 seeds both
  delivered 0 bytes and 0 peers; 19 of 34 queued releases sat at 0% for that
  reason. Rank by (complete set, then resolution, then seeds) and treat the
  seed column as a floor for OFFERING a pick, never as proof it is alive —
  liveness is `num_seeds` after a reannounce, not the index's claim.
- **Refuse to queue a `1ofN` posting without its counterpart.** A `1of2` is
  half the programme: two album episodes arrived as part 1 only, which would
  have filled an episode slot with a 25-minute fragment. Parse
  `NofM`/`part N` first, queue only complete sets, or label the row "part 1
  of 2" with the user's agreement — never silently as a whole episode.
- **Scope a search to specific indexers with `indexerIds` as a comma-separated STRING** (`indexerIds=17`, not a JSON list — a list 400s). Combine with the control-query rule: `indexerIds=<newId>` on a popular title must return hits before a zero on the real query counts as a miss.
 - Never invent an infohash or magnet — record only what the API returned.
- **Zero results means nothing until controls pass.** A dead indexer returns
  the same empty list as genuinely absent content — run 1-2 control queries
  for known-popular titles alongside; hundreds of hits there proves the
  subject is truly unindexed, not that Prowlarr is broken.
- **Name the outage before running controls: `GET /api/v1/health`.** An
  `Indexers unavailable due to failures for more than 6 hours: <names>`
  warning lists exactly which indexers are down — a zero scoped to one of
  them is an outage, not evidence, and no control query on that indexer can
  pass until it recovers. Scope controls to indexers reporting healthy.
- **User-named boards live in profile `places-to-look.md`.** When Prowlarr
  (torrent + usenet) and the public scrape path all miss, work that list —
  logins included — before declaring a title unfindable. Append any new
  board, index or login the user names; never ask twice for one already filed.
- **Login-walled boards: prove the challenge token before submitting.** Fill
  credentials from the vault (never type a password), then confirm the
  challenge actually rendered — Turnstile means a challenge iframe exists
  AND `cf-turnstile-response` is non-empty — before touching Login. phpBB
  counts each token-less submit toward CAPTCHA/lockout, so blind retries
  burn the account. If the challenge never renders on this egress, stop and
  route via FlareSolverr (recipe in Adding indexers) or ask the user to log
  in from home — never keep submitting. A FlareSolverr `Challenge solved!` on the page or POST clears the *network* challenge only — an *embedded login-form widget* (Turnstile div, image-captcha `cap_code_*` field) still needs its own token, and a POST that lands back on the login form naming one means the token is enforced server-side: stop after at most two such failures (each counts toward CAPTCHA/lockout) and ask the user to log in once from home.

## qBittorrent queue ops

Reach/API details (host discovery, unauthenticated LAN access, v5.x endpoint
renames, `409`-as-dedupe, ghost-seeder sweep, multi-writer queue) are in
`references/qbittorrent-web-api.md` — read it before scripting against the
client for the first time.

- **Verify every add.** After POST `/api/v2/torrents/add`, sleep 30-45s then
  confirm the infohash appears in `/api/v2/torrents/info` before reporting
  success. An add returning `pending_count: 1` whose total torrent count is
  still unchanged after ~3 minutes is a dead aggregator page-link, not a slow
  magnet — re-search preferring direct-magnet indexers (TPB/Knaben/YTS) and
  queue the magnet instead of re-adding the same link. Magnets show as bare hashes with no name until metadata resolves,
  so match by infohash — and allow 10-30 min of DHT/tracker announce before
  judging seed counts. Skip infohashes already queued (DUP, not failure).
- **Cull dead magnets fast, keep one live entry per wanted release.** Force-start fresh magnets and compare `num_seeds`: a candidate holding 0 seeds while a sibling candidate for the same title shows dozens is a dead swarm — delete it and move on rather than letting it sit queued. Two postings of the same release (same title/group, different indexers) queue as duplicate magnets — keep the seeded one, delete the other. A bare-hash magnet name that never resolves metadata is unjudgeable — replace it with a named posting before concluding anything about the release. qBittorrent
  progress lies: a torrent at 90-100% can hold all-zero head bytes
  (unfetched first pieces on a starved swarm) yet report complete. Before
  copying any download into the library, read the first bytes (zeros =
  corrupt) and ffprobe it — on either failure, `recheck` the torrent so it
  re-fetches the missing pieces instead of ingesting a broken file. This
  check also gates upgrades: never park a good library file for a download
  that has not passed it. Also compare the finished file's byte size against
  the torrent's expected total — a download missing only its tail pieces
  looks full-size and passes head-byte checks while still short (a ~1KB
  deficit is exactly what an unfinished tail looks like); never stage an
  upgrade file that is short, even when a second copy agrees with it.
- **No magnet? use `downloadUrl` plus apikey query param.** When a result has
  no usable magnet, pass its `downloadUrl` as the qBittorrent `urls=` value
  with `?apikey=<PROWLARR_KEY>` appended — Servarr APIs accept the key as a
  query parameter and qB cannot send custom headers.
- **A result carrying NEITHER a magnet nor a `downloadUrl` cannot be queued,
  and that is the indexer's fault, not the client's.** These show up as
  "no url" failures on a result the search happily listed. Re-query with
  different phrasing (add the `SxxEyy` token, drop subtitle words) and take
  the next posting — never report the episode as unavailable because the
  first posting was link-less.
- **A `downloadUrl` can 301 to a magnet.** Aggregator-sourced results
  (Rutracker via Knaben/TorrentDownload) redirect the download link to a
  `magnet:?xt=` URL — a fetcher that only reads response bodies crashes on
  the redirect. Catch the 301, take the magnet from the `Location` header,
  and add it via qB `urls=` instead of the torrent-file path. A 500 from
  one indexer's download endpoint is that indexer's stale cache, not proof
  the release is dead — resolve the next indexer's `downloadUrl` before
  giving up (one indexer's link 500ing while another 301s to a live magnet
  for the same title is normal).
- **If `guid` is an indexer HTML page, scrape the magnet off the page.**
  Some indexers return a details-page URL with real seeders but no magnet
  field — curl the page with a browser User-Agent and grep `magnet:`; the
  page magnet usually carries a full public-tracker list baked in.
- **Mine the `guid` path for a bare 40-hex infohash before fetching anything.**
  Aggregator result pages embed the hash in the URL path while `infoHash`
  comes back empty — regex `[0-9a-fA-F]{40}` on the guid identifies the
  release with zero extra requests, and the magnet builds straight from it
  (`magnet:?xt=urn:btih:<HASH>&dn=<urlencoded title>`). Shorter hex segments
  are internal catalogue ids, not hashes — match 40 exactly or fetch the page.
- **Resolve every hash-less candidate to its infohash and compare it against
  the stalled hash — that comparison, not the seeder count, decides
  same-swarm vs alternative.** GET the `downloadUrl` with redirects disabled:
  a 301 whose `Location` is a magnet hands over the hash. Identical hash
  means the index is echoing the dead swarm and its seed count is stale
  cache however high it reads — skip without adding. Different hash means a
  real alternative worth queueing.
- **v5.x endpoint renames.** `pause`/`resume` are gone (404) — use `stop`/
  `start`. `setForceStart` requires an explicit `value=true|false` or it
  400s. `reannounceAll` does not exist (`Endpoint does not exist`) — POST
  `reannounce` with `hashes=` instead. When a familiar call fails, check
  `/api/v2/app/version` first (it
  returns bare text like `v5.2.3`, not JSON — read it as text); do
  not retry the old name.
- **One hash per mutating request.** `setForceStart`, `setLocation`,
  `recheck` and deletes reject pipe-joined hash batches with 400 — loop one
  hash per POST.
- **Stall triage, in order:** (1) `reannounce` with `hashes=` (zero-risk
  first nudge — there is no `reannounceAll` endpoint);
  (2) recheck `error`-state torrents — bad-piece flags often clear;
  (3) clear `forced` via `setForceStart value=false` — forced torrents bypass
  the max-active limit and dilute connections across dozens of stalled
  downloads instead of concentrating on 10 at a time.
- **After files move, `recheck` recomputes truth from the new location.**
  Watch `checkingDL` climb and take the finished `progress` + `amount_left`
  as authoritative — pre-move progress numbers describe files that no longer
  exist at the old path, so never act on them without the recheck.
- **The reannounce count is the dead-swarm verdict.** Reannounce every
  zero-progress torrent once, then recount peers after ~25 s. A batch of ~100
  yielding ~1 with peers means the swarms are dead and waiting changes
  nothing — hunt replacements and say so plainly, rather than reporting
  "still downloading". A handful waking is normal churn; near-zero is the signal.
- **Diagnose before re-hunting:** connected + DHT nodes but 0 B/s across
  high-progress torrents with 0 seeds means dead swarms, not a broken
  client — hunt alternative releases rather than restarting the same magnets.
- **Empty expected paths mean migration until proven otherwise.** When
  library or download dirs that should exist resolve to nothing, search
  MemOS memory for a recorded move/migration BEFORE diagnosing corruption
  or data loss — a planned relocation looks identical to an emptied disk
  from the mount, and the plan is usually already written down.
- **Survey torrent contents with legacy containers included.** A video
  filter of only `.mkv/.mp4/.avi` misfiles DivX-era rips (`.divx`, plus
  `.ogm`/`.rmvb`/`.vob`) as audio-only and real episodes get skipped.
- **Dead-tracker bypass via public tracker injection.** A torrent stuck at
  metadata whose only tracker is dead (port closed) or rejects announces
  from the server can still resolve if any peer announces publicly: POST its
  hash to `/api/v2/torrents/addTrackers` with 4-6 healthy public UDP
  trackers, reannounce, then watch `num_seeds`/`num_leechs` on the
  per-torrent trackers endpoint — peers the dead tracker never returns do
  show up there. Only rescues torrents with at least one publicly announcing
  peer; content seeded strictly inside a closed community stays dark.
- **Community-tracker quirks.** RuTracker gates login POSTs behind
  Cloudflare (`Just a moment...` 403) while index GETs pass clean — route
  the login through a solver session (above), never plain curl. Repeated
  failures arm an image-captcha (`cap_sid` + a dynamic `cap_code_<hex>`
  field); a POST landing back on the login form naming the code means the
  account is flagged, and each further miss counts toward lockout — stop
  and get one human-browser login before retrying. MVGroup public magnets carry no passkey by
  design (open tracker, forum-distributed links); a `Forbidden` announce from
  a datacenter IP is edge filtering, not account auth — do not send the user
  hunting the site for a passkey that does not exist. Generally: a tracker
  that works for home IPs but 403s server announces is IP-filtered, and the
  fix is alternate peer discovery (above), not credentials.

## Batch hunts (many titles at once)

- Fan out per-title chunks to parallel subagents (5-8 titles each) with
  the quality floor stated up front (e.g. 1080p preferred, 720p minimum,
  never CAM/TS/unknown-quality, 1+ seeds) — one query's tokens always miss
  postings another catches, so let each hunter try 1-2 variants per title.
- Require every hunter to verify its queues against `/torrents/info`
  (name present, not just HTTP 202) and to identity-check each pick —
  token overlap queues wrong releases (a same-name TV episode, a
  lookalike film), and the fix is a full-hash delete plus re-hunt, never
  "close enough". When the REQUEST itself is ambiguous (one title, several
  distinct works — e.g. a bare "History of England" with no presenter or
  year), hunt the literal string once, report that it is ambiguous, and ask
  which work is wanted before queueing anything — never resolve ambiguity
  by picking the closest match.
- Aggregate from result files/queues, not summaries: re-list the queue
  afterwards and confirm each wanted title resolved to a live entry.
- Report alternative-hunt results one line per title with status term +
  progress % + seed/grab health + next step (queued / left stalled /
  prune candidate) — a bare success count hides which titles are still
  uncovered.

## Adding indexers to Prowlarr

- Build the POST `/api/v1/indexer` body from the `/api/v1/indexer/schema`
  entry: set `priority` 1–50 (the schema default 0 is rejected), `baseUrl`
  WITH trailing slash (a missing slash concatenates the path
  into the hostname and fails as DNS), `appProfileId` from
  `GET /api/v1/appprofile` (0 is rejected — use the `Standard` profile id),
  `definitionName` (e.g. `NZBgeek` — omitted it 400s) and `protocol`
  (`torrent` or `usenet`), and `redirect: true` for `protocol: usenet` (rejected when false).
  Read the 400 response body — it names the exact failing property.
- Verify an indexer's API key directly before adding it: a trial or
  unprivileged key passes every local check and fails only at Prowlarr's
  create-time validation. `GET <apiUrl>?t=caps&apikey=<key>` then
  `t=search&q=test` — an `<error code="101" description="Trial Account
  Only"/>` means the account tier lacks API search, and no body shape
  will fix the add.
- For challenge-gated login pages, attach the flaresolverr proxy tag and
  allow a long client timeout — solve + login + validation runs minutes,
  and a short timeout fails a working config.
- Prove the solver before blaming credentials: POST
  `{"cmd":"request.get","url":"<login page>","maxTimeout":55000}` to
  FlareSolverr `/v1` and require `Challenge solved!` —
  `Challenge not detected!` is the same healthy outcome (no network
  challenge on that egress), not a failure to solve.
- For solver-side authenticated POSTs (tracker logins Cloudflare gates
  while leaving GETs clean), reuse one session: `sessions.create`, then
  `request.post` with `session` + urlencoded `postData` — the session's
  clearance cookies ride along and a page-GET 403 becomes a solved POST.
  Read the returned page for the site's own verdict (wrong-password vs
  captcha-token messages); the solver status only covers the network gate.
- Container-level curls reaching the site prove nothing about the login
  flow — the create-time connection test is the gate; debug that, not curl.

## Part-gap inventory (finding what's missing)

- Group filenames by normalized series title and part/episode markers
  (`part N`, `NofM`, `pt N`, `SxxEyy`, `ep N`); report spans with holes.
- Exclude S00 specials before grouping — S00E04 merged with S01E04
  fabricates gaps in a complete season.
- A lone `part 1` / `1ofN` proves nothing alone; the series total comes from
  external knowledge (actual episode count), never from the files held.
- Same-name traps: verify the found title is the same work (year, path
  context) before queueing — remakes and same-named shows abound. Prove
  identity with at least two of: ffprobe runtime within ~5% of the known
  runtime (a ~4% shortfall reads as PAL speedup on a genuine long cut, not a
  different film), the indexer's imdbId resolving to the wanted title,
  release-title disclaimer tokens (`NOT the ... FILM`) marking the other work.
- **Zero hits on a held title may mean it is misnamed, not absent.**
  Extract frames across the runtime (`ffmpeg -ss <t> -frames:v 1`) and
  vision-scan them for title cards, channel bugs, and name captions; confirm
  the true title — the exact scene release often exists under it.

## Series-episode lineup hunts (user picks by artist, not filename)

- Fan Prowlarr across general + season + year queries (e.g. bare title,
  `S60`, `2024`, `2023`) and aggregate — one query's tokens always miss
  postings the others catch; hundreds of raw results collapse to a short
  season/episode list after token filtering.
- **Hunt a season as a PACK, not episode by episode.** Ten per-episode
  queries (`<series> S01e02 <guest names>`) returned zero results each while
  a bare series-title search on the same indexer returned the COMPLETE
  season packs in seconds. Episode-level postings are rare for broadcast
  strands; season packs are the norm. Run the bare title first and only fall
  back to per-episode queries if the pack hunt comes back empty.
- **Check the downloader's existing queue by broad title before hunting
  anything.** Season packs queued in an earlier session (or by another
  agent) sit in `/api/v2/torrents/info` under names a per-episode query will
  never match — a bare-title sweep of the queue is what reveals a 91%-done
  season pack already fetching. Searching the indexer first and adding a
  duplicate wastes the whole hunt.
- **Prefer the highest-resolution pack and let the SD one go.** When the
  queue holds the same season at two qualities, the HD pack supersedes; note
  the SD pack as droppable rather than fetching episodes to fill it.
- Build the choosable lineup from TMDB, not filenames: `GET
  /tv/{id}/season/{n}` returns per-episode `guest_stars`, so each entry
  reads as season/episode + air date + artists — the form the user actually
  selects from. Number the shortlist (they reply with numbers).
- Grade tracker hits against the lineup by episode identity (season/episode
  tokens, air year), not title overlap — compilation and best-of postings
  share the series name while matching no episode.
- **A hit naming the album is usually the ALBUM, not the episode.** For
  album-per-episode series the index is dominated by the record itself:
  30–200 MB, with `FLAC`/`MP3`/`320 KBPS`/`DVDA`/`Deluxe Edition`/`[1969]`
  in the title. An episode is ~0.5–2 GB and carries broadcast tokens
  (`1080p`, `HDTV`, `WEB`, `x264`, `MVGroup`). Classify by size AND tokens
  **before** queueing: a nine-hit "found" list collapsed to one real
  episode once sizes were read, and seven were album downloads. Report
  album-hits as evidence the episode is genuinely unindexed, never as finds —
  and never queue them (audio is not this agent's domain).

When every index carries only the same dead infohash (proven by resolving
each hash-less candidate to its hash first) and archive.org has nothing,
hunt YouTube before giving up. yt-dlp is NOT installed on CT101 — run it via uv (`/root/.local/bin/uvx yt-dlp`, installs ephemeral on first use):

- Search: `yt-dlp --flat-playlist --print '%(title)s | %(id)s |
  %(duration_string)s' 'ytsearchN:<title> <year> documentary'` — a runtime
  near the broadcast length is the identity signal; trailers and clips share
  exact titles with the feature and prove nothing.
- Check the ceiling with `--list-formats` BEFORE downloading. A `no JS
  runtime` warning plus only sub-HD formats means the extractor is capped,
  not the upload — re-run with deno on PATH. If the max is still ≤360p,
  the upload itself is SD: report that plainly, never as an HD find.
- Download merged mp4, constrained to playable codecs (`-f 'bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec^=mp4a]/bestvideo[height<=1080]+bestaudio/best' --merge-output-format mp4`) — the unconstrained default picks AV1+Opus, which violates the house no-AV1 rule and may not direct-play; a platform rip must arrive playable, not merely HD. Stage into a path proven writable with `touch` from the CT shell first — `/mnt/downloads-fujh` is empty and read-only there while `/mnt/zmedia/downloads` (qB's `/downloads-zmedia`) accepts writes; a `Permission denied` mid-download is a path error, not a fetcher fault. When running yt-dlp from the Hermes box rather than the CT shell, `/mnt/zmedia` is not mounted there at all: download to local `/tmp`, verify, then `scp` to the share host's downloads dir.
- Verify the result like any download (ffprobe streams + mid-file decode)
  before ingesting; a platform rip never outranks a real release, it only
  beats nothing.
- Check an archive.org hit's `metadata.mediatype` via the metadata API
  before treating it as a video source — same-title items are often
  audio-only (`mediatype: audio`, e.g. a soundtrack MP3) with no video file
  at all, which reads as a find until the file list is inspected.
- NZ On Screen streams are Brightcove CENC/FairPlay DRM (manifest URLs
  contain `bccenc`/`cenc`) and yt-dlp refuses them; a site login changes
  nothing — encryption is CDN-level for anonymous and logged-in sessions
  alike. Treat NZOS titles as stream-only and keep hunting other sources.

## Usenet downloader (SABnzbd on CT101)

SABnzbd is the Usenet half of the fetch role — full deploy + provider-server recipe in `references/usenet-setup.md` (linuxserver Docker shape, `set_config` API, NNTP auth proof). Prowlarr indexer rules above apply unchanged to Usenet indexers (`protocol: usenet` needs `redirect: true`; trial API keys fail validation — verify with `t=caps`/`t=search` first).

- Queue by NZB URL, not by file: `mode=addurl&name=<Prowlarr downloadUrl>&nzbname=<title>&cat=<cat>&apikey=<SABKEY>` (the Prowlarr `downloadUrl` carries its own apikey param — pass it through untouched). Then verify like any add: `mode=queue&output=json` must show the slot with a climbing `percentage` and `status=Downloading` — a returned `nzo_id` alone only proves acceptance.
- Read the history `fail_message` before retrying anything — the taxonomy decides: `Aborted, cannot be completed` means the backbone lacks the articles and no retry will fix it (the fill is a second provider block as fill server, not persistence); `Repair failed, N short` by a small N is worth exactly one retry; a write error with free disk is transient — retry. Retry via `mode=retry&value=<nzo_id>` from the history slot.
- Every grab spends block data, so grabs get per-item approval like deletes do: present candidates one at a time as library-holding vs Usenet-twin (resolution/codec/size on both sides, plus post age and grab count as the completion signal) with an explicit Grab/Skip choice — never batch-queue a shortlist.

## Where to run fetchers (homelab placement)

- Run media fetchers on CT101 media-download (holds deno, qBittorrent, Prowlarr and the FUJH download mounts); serve from CT105 jellyfin. Run CPU encode/recode work on CT110 hermes-code (prox2, 4 cores/8GB, ffmpeg, zmedia bind-mount) — never on CT101 (2 cores, contended with download traffic) and never on CT100/CT106. Keep fetch, encode, and serve roles split.
- yt-dlp YouTube extraction needs a JS runtime (`deno` at `/root/.deno/bin`, on PATH when invoking) — without it only sub-HD HLS formats are offered and full-resolution formats fail as unavailable, which reads as "the video is only 360p" when it is really the extractor that is capped.
- Never install fetchers on CT100 (keep writes off it) or CT106 (dev-only container).
- Point downloader output at the shared download mount (e.g. under /downloads), not the container rootfs. On unprivileged LXCs the mount root itself is server-root-owned and unwritable — create app dirs inside an existing world-writable subdir, never at the mount root.
- CIFS-backed mounts need `file_mode=0777,dir_mode=0777` in the host fstab or the container-mapped user cannot write through the bind — a bare `rw` mount reads fine and writes `Permission denied`, which mimics a read-only share. After changing host mount options, restart the container to rebind (the running bind still points at the old superblock). Prove with `touch` from inside the container before launching any job that writes.
- For region-locked sources (BBC iPlayer is UK-only), verify a matching-exit proxy/VPN first — catalogues list fine from outside the region but downloads fail without it. Absence from the iPlayer cache means nothing aired in the ~30-day window, not a fault — schedule a recurring exact-match grab for series between seasons.
- Prefer get_iplayer manual install (binary to /usr/local/bin, profile in ~/.get_iplayer) over distro packages, which are not dev-endorsed; the snap build uses a different profile path, so do not mix them.

## Relocating a read-only download dir

- Probe writability with `touch` from INSIDE the downloader container —
  host-root success does not prove the container user can write. Reads
  succeeding while all writes fail is the signature of forced-mode CIFS
  shares; chmod is refused at protocol level, so relocate, never repair.
- Stop all torrents first, then copy with `cp -rn --no-preserve=all`
  (CIFS refuses timestamp/permission preservation — log noise, not data
  loss), temp dir included so partials survive.
- Verify before flipping: DONE markers for each phase plus a full listing
  diff (pre-existing target dirs are the only expected extra) and sizes
  within margin.
- Flip with `setLocation` one hash at a time, `recheck` all, `start`, and
  wait out `checkingDL` — partials resume from verified pieces, never zero.
- `setLocation` does NOT move the per-torrent temp path: migrated torrents
  in `error` state whose log cites the OLD temp dir need `setDownloadPath`
  with params `id` + `path` (v5.x shape — `hashes` + `downloadPath` 400s),
  then recheck + start.
- Delete old contents only after rechecks show matching progress — never
  on copy-complete alone. If the container user is denied deletes on a
  forced-mode share, delete from the share host itself as the share owner
  rather than fighting container permissions.

## Standing gates (see media-deletion-policy before any delete)

- Removing a wrongly-added torrent: `deleteFiles=false` unless the files are
  proven redundant; anything beyond exact-duplicate removal needs user approval.
- **Deleting a payload the library already holds needs three proofs and two
  refusals.** Proofs: the full 40-char hash (never the 12-char ledger form),
  `progress >= 0.999`, and a library row that exists — then, after the delete,
  re-read that row's size to prove the library copy survived (`cp --reflink`
  on the same volume makes deletions look dangerous; the re-read settles it
  either way). Refuse outright: a torrent whose OTHER files are still
  downloading (a `1of14` pack with part 1 finished is still fetching — deleting
  it kills the remaining parts), and a completed download that is an
  un-ingested UPGRADE over the shelf copy (that is a pending replace, not a
  duplicate).
- **Build every delete batch as an explicit hash list taken from the verified
  set — never as an exclusion filter** (`all except X`): exclusion patterns
  silently sweep in unapproved entries, including ones under hold orders.
  Re-check holds before any batch runs, and read the queue back after each delete.
- An unanswered question is never approval — no "recommended default"
  proceeds on a timed-out prompt. The block covers destructive steps only
  (swaps, deletes, queue changes): read-only prep and staging new files
  without touching originals may proceed so the answer can act on results,
  not estimates.
- **Approval requests carry the full picture inline**: what the item is, why
  the decision matters, and what each choice concretely does — never assume
  prior research was read.
