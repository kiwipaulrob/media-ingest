# Ingest & intake — getting files into the library

Adding a new title or a replacement for a ghost entry: classification, the three intake checks, TMDB identification, the exit gate, placement, and downloader triage.

## Decide the target BEFORE copying

Query `Type`/`Path` in the live DB first:

- **Folder-type ghost row with NULL Size** → a name-matched drop-in lights up with NO scan.
- **No row at all** → new title; needs a library-scoped Refresh of that library only, after the copy.

Locate intake sources in the known download dir directly — never a full-tree `find` across multi-TB mounts; the walk stalls past SSH timeouts.

## The modular ingest pipeline — use it instead of one-off scripts

Ingest is implemented and tested in the `media-ops` repo
(`/root/.hermes/profiles/media-maintainer/media-ops`, git, ~140 tests) and exposed
as the `media-ingest` plugin. Drive it through the plugin verbs, or the CLIs when
working from a shell — never hand-roll a new intake script.

| Stage | CLI | Verb | Changes anything? |
|---|---|---|---|
| source | `python3 -m cli.source plan\|queue` | `media_source_plan`, `media_source_queue` | plan no / queue yes |
| scan | `python3 -m cli.scan` | `media_scan` | no |
| plan | `python3 -m cli.plan` | `media_plan` | no |
| replace | `python3 -m cli.apply --ids N` | `media_apply` | yes (REPLACE only) |
| place new | `python3 -m cli.ingest_new --spec F` | — | yes (NEW only) |
| verify | `python3 -m cli.verify` | `media_verify` | no |

`apply` retires an old file so it can only REPLACE a row that already exists; a
genuinely new episode has nothing to retire and goes through `ingest_new`
(`media_lib/place.py`). Both take explicit ids/paths and have **no "all" switch**
by design — an unread plan must not be able to change the library.

**Placement gates, in order — a failing gate REMOVES the staged copy, so a
half-good file never stays in the library:** copy → `SIZE_SRC == SIZE_DST` →
probe readable → `>= min_height` → duration within tolerance **of the source's own
duration** (not a caller-supplied number, so it always runs) → decode 3 spans
(5/50/95%). Copy, both probes and all decode spans are folded into **two** remote
calls; six separate `docker run` round-trips cost ~5.7 s per file for the same
gates.

**Placing a file does NOT make Jellyfin notice it.** A library-scoped Refresh
re-resolves metadata for KNOWN rows and will not discover a new file — two
placements sat invisible until forced. After placing, POST
`/Library/Media/Updated` with `{"Updates":[{"Path":<container path>,"UpdateType":"Created"}]}`
(204), then poll `GET /Users/{uid}/Items?searchTerm=<basename>` for the row by
EXACT `Path`; it appeared within ~80 s. Refresh afterwards for metadata.

**Re-read the row before writing metadata: clean names often self-fill.** Two
placed episodes arrived already named, with a provider overview and a Primary
image, simply because the scanner matched them — writing first would have
overwritten good data. Set `IndexNumber`/`ParentIndexNumber` and lock `Name`+
`Overview` only for what is genuinely missing. If the scanner leaves a row
unnamed and unnumbered (it did for the first two placements, then numbered the
next two), set both index fields through the full-DTO round-trip.

### Episode metadata when the provider has no season

A long-running strand's later seasons can be missing from TMDB entirely
(`/tv/<id>/season/<n>` → **404**). Do not conclude the episode has no metadata:

- Take the season/episode mapping from the **Wikipedia episode table**, which
  lists the broadcast year and running order — that is what resolved two
  episodes to their correct slots after TMDB 404d.
- TMDB still has the *earlier* seasons, so query those for overviews and stills
  even when the target season is absent.
- With no still available, **frame-grab** (`-ss <t> -frames:v 1 -vf scale=1280:-2`)
  and upload it Base64-encoded to `/Items/{id}/Images/Primary` (204). Try two or
  three timestamps — a frame at 5% is often a title card or black.

**A frame-grab written inside the container is not on the host.** `docker run …
-v /mnt/zmedia:/mnt/zmedia` writes `/tmp/x.jpg` into the *container's* filesystem,
so the `base64` that follows on the CT shell fails with an empty read. Write the
frame to a path on the mounted volume (`/mnt/zmedia/tmpgrab-*.jpg`), read it
there, then delete it.

**Budget copies on this path at ~39 MB/s.** Measured on the zmedia library
share at 1.3 GB per file (~33 s), well below the 143–171 MB/s figure for the
other array — never size a batch from a throughput number measured on a
different share.


## The three intake checks (in order)

1. **Extension must equal the real container.** Probe the source (`ffprobe -show_entries format=format_name`) and name the destination file accordingly — mkv bytes wearing a DB-expected `.mp4`/`.avi` name re-triggers the Cause-B demuxer failure. Collapse doubled container extensions from release posts (`file.mkv.mkv` → `file.mkv`) instead of copying the doubled name into the library. A wrong extension copied to the library must be deleted and re-copied with the right one; never leave both.
2. **MP4/M4V sources with `mov_text` subtitles cannot mux directly to Matroska** — the mux fails (`Subtitle codec ... is not supported`, empty output). Remux video + wanted audio only, stream-copy, no re-encode:
   `ffmpeg -i src.m4v -map 0:v:0 -map 0:a:<wanted> -c copy -disposition:a:0 default dst.mkv`
   (attached-pic `png`/`mjpeg` and `bin_data` streams are dropped with the subtitle). MP4s carrying an `mjpeg`/`png` attached-pic stream or `und` (untagged) language play as-is but should be offered as a clean-mkv remux: stream-copy video + wanted audio only, drop the attached-pic, and tag the kept streams `eng` (`-metadata:s:v:0 language=eng -metadata:s:a:0 language=eng`).

   **Audio-leads-video:** when stream 0 is audio, always map explicitly — count the wanted index among AUDIO only from a flat probe; never assume the wanted track is `a:0`. Rus-sourced rips routinely put dubs first with the English Original (often mono) last.

   Verify with a mid-file decode probe (`ffmpeg -ss <mid> -i dst.mkv -t 10 -f null -`, must exit 0) — a completed copy is not proof of playability.

   **Broadcast `.ts` captures remux via mkvmerge, not ffmpeg.** HDTV captures carry timestamp discontinuities that abort `ffmpeg -c copy` (`Can't write packet with unknown timestamp`, trailer error) — go straight to `mkvmerge -o dst.mkv --video-tracks 0 --audio-tracks <wanted> src.ts`, which tolerates the damage and drops `dvb_teletext`/data streams, then decode-probe the result as usual.

   **Remux non-interactively with `-y` (or `rm -f` the dest first).** A stale partial from a previous attempt makes ffmpeg stop at the overwrite prompt (`Not overwriting - exiting`), which reads as a remux failure but is only the prompt — never diagnose a remux until the dest path is confirmed absent.
3. **Audit and set audio defaults immediately after copy** — dubs (rus MVO/AVO) often ship flagged default; the rule and the `mkvpropedit` recipe are in `references/playback-and-repair.md`. Do the byte-identical size comparison src vs dst BEFORE any mkvpropedit flag pass — flag writes shift the file by a few KB. AFTER a flag pass, a sub-0.1% size delta against the source is expected and does not block torrent deletion — but only once a mid-file decode probe of the library copy passes; the probe result, not the delta, is the promotion signal. Then confirm the DB-expected basename matches exactly so the ghost lights up with no rescan.

Only after all three pass, remove the torrent AND its files from the downloader (`POST /api/v2/torrents/delete` with `hashes` + `deleteFiles=true` as form-encoded POST fields — `--data-urlencode`, never URL query params, which the endpoint rejects with `Missing required parameters` — the whitelisted-LAN auth bypass covers this write too, an empty-password login suffices — pass the FULL 40-character hashes, never a short prefix: a prefix is silently accepted but matches nothing, so the torrent survives while the call reads as success — then re-list to confirm zero remain, then `stat` the content path on disk — `deleteFiles=true` can clear the torrent record while leaving the full payload bytes behind; remove any leftovers by hand) — the library copy is then the only remaining instance.

## Identify: TMDB IDs, then metadata

New rows land with no provider links and no art. Clean `Title (Year)` names usually self-identify on the next refresh, but release-style filenames do not — give every new title explicit IDs. TMDB v3 keys live in `/root/.hermes/.env` as `TMDB_API_KEY` + `TMDB_READ_TOKEN` (mode 600).

**(a) Look up the id.** `GET https://api.themoviedb.org/3/search/movie?api_key=$TMDB_API_KEY&query=<urlencoded Title>&year=<YYYY>` (key auth) → take the top hit's numeric `id` as the Tmdb id. TMDB search is spelling-sensitive — a British-"s" spelling can return zero hits where the American-"z" spelling returns the full set, so retry with the alternate spelling or a shorter query before concluding no match. Sequels sharing a title each carry their own TMDB/IMDb ids — verify every part separately, never copy Part 1's ids across (Decline trilogy: Pt1 21137/tt0082252, Pt2 Metal Years 36724/tt0094980, Pt3 44848/tt0138393; Pussy Riot: A Punk Prayer (2013) 159032/tt2481238).

**(b) Cross-check.** `GET https://api.themoviedb.org/3/movie/<id>?language=en-US` with header `Authorization: Bearer $TMDB_READ_TOKEN` — the `imdb_id` field gives the `ttXXXXXXX`; `GET .../movie/<id>/external_ids` confirms `imdb_id` + `wikidata_id`. Record the verified pair. If TMDB returns no `imdb_id`, write Tmdb alone and leave Imdb absent — never write an empty string to complete the pair; a later Refresh often fills it from Jellyfin's own providers, and a blank write can clobber that. Worked example: Decline of Western Civilization (1981) = Tmdb 21137 + Imdb tt0082252, overview 'The Los Angeles punk music scene circa 1980...', runtime 100.

**(c) Write the IDs directly to the item — the ONLY working write path on 10.11.11.** `GET /Users/{uid}/Items/{itemId}` for the full DTO (bare `GET /Items/{itemId}` 400s), set `ProviderIds={"Tmdb":"<n>","Imdb":"<tt..>"}` + `ProductionYear=<YYYY>` on the full object, then `POST /Items/{itemId}` with the whole DTO back (204; send `X-Emby-Authorization` alongside the key). Do NOT touch LockedFields here — `ProductionYear` is not a valid MetadataField enum value and 400s the write. Verify the re-read shows the IDs + year. Never write provider IDs from memory — run (a) and (b) fresh for every title and every part. Never write placeholder or sentinel IDs (e.g. literal `"TMDBID"`) intending to fix them after — fetch the real ids first and write once. Memory transposes digits and conflates combined-cut/anthology records with part records, and the write returns 204 either way, so only the lookup catches it. When the row already carries an Imdb id that disagrees with the fresh lookup, resolve the OLD value first via `GET https://api.themoviedb.org/3/find/{old_imdb}?api_key=$TMDB_API_KEY&external_source=imdb_id` before calling the overwrite damage — an id with no TMDB record anywhere is stale data and replacing it is the fix; an id that resolves to a different title means the row was matched to the wrong work, which needs a human, not a silent swap.

**(d) Fetch metadata.** `POST /Items/{itemId}/Refresh?Recursive=true&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllImages=false&ReplaceAllMetadata=false` (204) — after ~25s the server fills Overview + TmdbCollection + Thumb/Logo. Proven on the Decline BDRemux item (`d6967e56…`): the direct POST stuck Tmdb 21137 + Imdb tt0082252 + year 1981, and Refresh added the punk-scene overview + collection 157671 + Thumb/Logo.

> **`RemoteSearch/Apply` is a dead end here — never use it for ID writes.** It returns 204 and logs `Setting provider id's ...: {}` while creating zero `BaseItemProviders` rows, and it 400s for manual title/overview edits. (An earlier version of this skill prescribed it for poster recovery; that procedure was wrong and has been replaced — see `references/playback-and-repair.md`.)

### Identifying an unknown recording (mystery file, no reliable filename)

Transcribe first, guess never: run tiny-whisper samples at three or more points (opening minutes, mid-file, closing credits) — presenters name-check the acts on mic and credit rolls name the programme, both of which beat any filename. Frame-grab across the runtime and read faces/captions visually, phrasing the verdict question neutrally (`natural proportions, or squeezed/stretched?` — a leading question gets a sycophantic answer). Sweep the likely series' TMDB season/episode overviews for guest-name matches before concluding no entry exists. For BBC material the programme page (`bbc.co.uk/programmes/<pid>`) is authoritative for one-offs, but the online episode guide lists only recent compilations and specials — never the historic regular run — so absence there proves nothing; the Programme Index (genome) sits behind a bot-wall, check it from a home browser instead.

## The mandatory 4-field exit gate

No ingest is complete until the item scores **4/4**. Re-read via `GET /Items?Recursive=true&IncludeItemTypes=Movie&searchTerm=<Title>&Fields=Path,ProviderIds,ImageTags,Overview,ProductionYear` and confirm: (1) `ProviderIds.Tmdb` present, (2) `ProviderIds.Imdb` present, (3) `ImageTags.Primary` present, (4) `Overview` length > 50 chars. If `searchTerm` returns zero rows, do NOT conclude the row is absent — digit-led titles (e.g. `24X36`) match nothing in search even when the row exists with correct IDs. Fall back to the paged library listing (`ParentId=<libId>` + `StartIndex`, filter by exact `Path`) before retrying discovery or re-ingesting.

If a field is missing after ~25s, wait 30s and re-read once (image download can race the DB write); if still missing, re-fire the per-item Refresh once more, then re-read. Hand over only with the 4/4 score quoted (Tmdb id + Imdb id + image keys + overview length). If a field genuinely cannot be filled (episode-parts in a Movies-type library where TMDB matching is ambiguous, episodes of a TV series ingested as Movie rows, or a title with no TMDB entry), report exactly which field is missing and why — **never silently hand over a 3/4 as complete**. TV-namespace TMDB ids never resolve through a Movies-library provider refresh, so series episodes sitting as Movie rows stay ID-less by design — either accept the plain rows or restructure as a Series (see `references/series-structure.md`); do not burn turns retrying the refresh.

## Naming, intake gate, and the NFO standard (authorised 29 Sep 2026)

Three standing conventions that stop metadata rotting at the source:

1. **Name-and-ID at ingest.** Every new film lands as `Title (Year) [tmdbid-XXX]/Title (Year).ext`; every series as `Name (Year) [tmdbid-XXX]/Season N/SxxEyy - ...`. Clean names self-identify on refresh; the embedded id survives renames and typos (the `Letters from Ima Jiwo` typo blocked auto-match for weeks). TV items live under the series library, never as Movie rows — a misfiled episode stays ID-less by design (proven: Comedy Connections S02E03).
2. **TMDB gate in the intake script.** `jellyfin-media-intake.py` annotates every NEW row with a TMDB candidate (`row['tmdb']`: `candidate` id/title/year/media, or `ambiguous` / `no-match` with the cleaned query) at plan time, capped at 15 lookups per run. Annotate-only — it never renames or writes — so ID-less arrivals get triaged the same day. Real films resolve to exact ids (verified: Iwo Jima → 1251, Swindle → 16149); rarities with no record (Fulford-class) correctly no-match and fall through to (3).
3. **NFO + frame-grab poster for the unmatchable.** No TMDB entry → hand-write `<full-media-basename>.nfo` (`<movie>` with title/plot/genre/studio from transcribed + visual evidence only, never invented credits) plus a `poster.jpg` frame-grab beside the media. Three hard-won rules: the NFO name must match the FULL media filename including double extensions (`OGWT.avi.mov` → `OGWT.avi.mov.nfo`, never `OGWT.avi.nfo`); apply with a **per-item** FullRefresh (`POST /Items/{id}/Refresh?...&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=true`, images flag false to keep curated art) — library-wide refreshes do not reliably pick up sidecars added after first scan; then re-read Overview/ImageTags to prove it landed. Proven 29 Sep 2026 on both Fulford docs, Oona and Me, and the OGWT special (4/4 on overviews + posters where TMDB had nothing).

## Batch lessons (verified on a ~30-title ingest)

- **Secrets do not cross hosts.** A script scp'd to proxmox3 cannot read the hermes host's `.env` — inject tokens through the environment (`TMDB_R=... ssh ... "TMDB_R=$TMDB_R python3 ..."`) and read them with `os.environ` in the script. Same for any credential.
- **Glob the real separator.** Release names on disk use spaces where memory writes dots — `*Rock n Roll High School*` matches, `*Rock.n.Roll.High.School*` silently matches nothing and a size-assert then fails far from the cause. When a glob finds nothing, `ls` the parent and copy the literal name.
- **Scope the queue to the target libraries BEFORE any per-title work.** Classify every complete torrent as in-scope / audio / out-of-scope first — on a mixed queue this halves the backlog (audio alone was ~40%) and keeps every later scan-and-check pass small. The video allowlist must include `.divx` alongside mkv/mp4/avi — DivX-era rips use it, and filtering on the big three alone silently classes real episodes as audio. Audio is download-only material (see below); out-of-scope titles belong to a different job, not this one.
- **Poll for the expected state; never sleep blind.** After triggering a scan or refresh, poll every ~30s for the concrete condition (row present with `nsrc>=1`, ghost count zero) instead of fixed multi-minute sleeps — scans finish fast on small libraries and slow on large ones, and a blind sleep is wrong in both directions.
- **The library is the source of truth, not the ingest ledger.** A ledger that starts mid-history knows nothing about rows ingested earlier, so "not in ledger" never means "not in library" — match every candidate against live `Items` + on-disk files before calling it new.
- Clean `Title (Year)` names + stacked `-partN` files usually **SELF-IDENTIFY** on the library-scoped Refresh — always re-read IDs after the refresh BEFORE doing a manual TMDB POST; the manual write is often unnecessary (Murder by Contract and the Notorious Criterion both auto-attached Tmdb+Imdb+art with no POST).
- When auto-match picks the WRONG variant (an American Revolution row matched 'An Inside Look' instead of the Ken Burns series), correct the Name and lock it via `LockedFields:["Name"]`.
- Rows ingested from `Title (Year) [tmdbid-XXX]` folders inherit the bracket suffix into `Name` — strip it with a DTO Name write to the clean title after the refresh (no lock needed; the folder id keeps matching stable). Always re-read the full `Name` (not a truncated search listing) before declaring it clean.
- Titles with no TMDB entry at all still get posters from a refresh once a clean Name + ProductionYear is POSTed — write those two fields even when IDs don't exist.
- Report a per-title table: src size, dst path, decode RC, JF row, 4-field score, torrent hash + delete-readiness.

## No-TMDB BBC programmes (TMDB-less procedure)

One-off docs (Disclosure, Horizon singles, regional programmes) often have NO
TMDB or IMDb entry at all — no ID POST can ever attach. Best achievable ceiling
is Name + Year + locked Overview + Primary image (report it as such,
field-by-field; never count these in the 4/4 batch hit-rate).
Procedure per item (proven on `Disclosure: Welcome to Glasgow`, 2026-09-12):
1.  Web-search `"<exact Title>" BBC iPlayer programme` → the
    `bbc.co.uk/programmes/<pid>` URL is the top hit; the 8-char pid
    (e.g. `m0030gjc`) is the stable BBC identity key — record it as provenance
    (tag/notes), it plays the role IMDb does for films.
2.  `curl` the programme page (plain UA, no key needed) and scrape
    `og:description` → this is a real BBC synopsis. Write it as `Overview`
    via the same DTO round-trip as the TMDB stamp, with
    `LockedFields: ["Name","Overview"]` so later refreshes can't clobber it.
    (DTO `POST /Items/{id}` → HTTP 204; re-read confirms.)
    The page also states the **runtime** and the broadcast credits — use both:
    compare the page's stated duration against `ffprobe -show_entries
    format=duration` on the local file (59 minutes vs 3538s = 59.0 min) as a
    cheap identity proof that you hold the right programme before writing
    anything, and take `<director>` / `<studio>` / the first-air date from the
    page's Credits and Broadcasts blocks for the sidecar NFO. Set
    `ProductionYear` to the FIRST broadcast year, not the last-repeat year the
    page leads with.
3.  **Poster — TMDB first, sidecar second, and the upload endpoint works.**
    `POST /Items/{id}/Images/Primary` takes the image **Base64-encoded**
    (`base64.b64encode(data)`, `Content-Type: image/jpeg`, expect 204 — raw
    bytes 500 with `FormatException... FromBase64`; proven Oct 2026, and the
    old claim that the endpoint is unusable is retired). At ingest: if a TMDB
    id is known, download poster + backdrop (`/t/p/original`) and place them
    as sidecars (`poster.jpg`/`backdrop.jpg` in folder items,
    `<basename>-poster.jpg` for loose files) AND/OR upload via the endpoint;
    sidecars on disk are what survive a row rebuild, so always keep them even
    when uploading. If no TMDB id is known (day-one releases like Oasis Oct
    2026), still place any sourced art and schedule a re-check — TMDB usually
    gains the entry within days, and the ingest-time miss is otherwise silent.
    **Every ingest verify pass must include an art check** (served-image bytes
    vs placed file): a row can show `img: Y` from a wrong auto-match while the
    curated art never landed. BBC-still specifics below (ichef sizing, 2:3
    padding, fanart keep) are unchanged.

    **Re-auditing a whole library for frame-grab posters:** items with curated
    sidecars need no check — audit only rows whose art came from the server.
    List primaries via `GET /Items/{id}/Images` and flag aspect ratio >= 1.3
    (landscape) as frame-grab suspects; portrait art is almost always a real
    poster and can be skipped sight-unseen (proven: 114 suspects out of 432
    sidecar-less rows). Items WITH a TMDB id take a TMDB poster blind;
    ID-less items need per-title research (BBC still, portrait, label art)
    with unsure candidates staged for a human pick, never deployed on a guess.

    Two refinements on the BBC still:

    - **The size segment in an `ichef` URL is free to change.** The page offers
      `208x208`/`480xn`/`640x360` variants of the SAME `<hash>`; rewriting the
      segment to `1200x675` yields the full-size original with no extra lookup.
      Grab the highest variant for the poster instead of the page's default.
    - **A BBC still is landscape (16:9) while a poster slot is 2:3 — pad it,
      never crop it.** Cropping a landscape frame to portrait slices through
      the subject's head (verified: a crop centred on the interviewee removed
      half his face). Pad to 2:3 on black instead:
      `ffmpeg -i still.jpg -vf "scale=600:338,pad=600:900:0:281:black" poster.jpg`
      — for an archive/documentary still the black bands read as deliberate.
      Keep the untouched landscape as `<basename>-fanart.jpg` alongside it.
4.  Capture at ingest time — BBC pages vanish when rights expire.
5.  Fallback when the BBC page is gone: **docuwiki.net via Wayback.**
    Live docuwiki is Cloudflare-walled (HTTP 403 to curl AND defuddle),
    but `web.archive.org/cdx` has snapshots back to 2007 — fetch a snapshot
    of the title's docupage for General Information (narrator/publisher/year),
    chapter-by-chapter synopsis, 800×450 cover + screenshots (direct image
    URLs), and category tags. Caveats: no IMDb/TMDB IDs (release-oriented,
    links ed2k/MVGroup posts), no structured API, and new titles may have no
    snapshot yet (Glasgow had none).
6.  `JINA_API_KEY` is in `/root/.hermes/.env` — Jina Reader is the fallback
    renderer if BBC ever bot-walls curl. Fetcher order is already
    TMDB → OMDB → TVDB, and locked fields survive refreshes, so a manual
    Overview/poster write is safe.

## Verified gotchas (9-title ingest, 11 Sep 2026)

- **`/Items?searchTerm=` matches `Name`, not `Path`.** After a library-scoped refresh, loose release-named files land as `BBC`, `BBC.Disclosure`, `BBC.Arena`, `BBC.P.Diddy…`, so searching the *film* title returns ZERO hits even though the row exists. Find new rows with `SortBy=DateCreated&SortOrder=Descending`, then write a clean `Name` through the DTO POST.
- **`GET /Items?ParentId=<movieId>` returns the WHOLE library, not the movie's children.** To confirm a local extra attached, use `GET /Items?Recursive=true&searchTerm=<extra name>` and check for `Type: Video`.
- **`GET /Items/{id}?Fields=…` returns 400.** Read the full DTO via `GET /Users/{uid}/Items/{id}` even when you only want specific fields.
- **Never name the admin-user-id shell variable `UID`** — bash's `UID` is readonly, so the assignment silently fails and every later call interpolates the wrong id (it renders as `0`), which reads as an API fault rather than a variable one. Use `ADMIN`/`userId`.
- **A partial payload POST returns 400 `"The request field is required."`** — the endpoint wants the whole DTO, not the fields you changed. The same response can *also* carry the `MetadataField` conversion error, so fix both at once: full DTO back, and `LockedFields` restricted to `["Name","Overview"]`. Lock Name/Overview when writing a Name/Overview (series parts, wrong auto-match); do not set `LockedFields` at all when writing provider IDs.
- **In-container ffmpeg/ffprobe are NOT on PATH** — `/usr/lib/jellyfin-ffmpeg/ffmpeg`. A probe script calling bare `ffmpeg` exits rc 127 with empty stderr, which looks like a decode failure but is a missing binary.
- **TMDB traps found on this batch:** BBC Arena's 2000 two-part *Clint Eastwood* profile has **no TMDB movie entry** (its IMDb record tt7947218 is a TV episode; Tmdb 295560 *Clint Eastwood: Out of the Shadows* is a different 87-min cut — never cross-assign it, the file is 132 min). *Louis Theroux: Surviving America's Most Hated Family* is **2019** (Tmdb 614477 / tt10639076), not 2011. BBC current-affairs strand episodes (*Disclosure: Welcome to Glasgow*) have no TMDB entry at all → 1/4 with a poster only; report the field-by-field gap rather than forcing an id.
- **A TMDB record with an empty `release_date`** (e.g. *Satiesfictions*, Tmdb 1289081) gives no verifiable year — cross-check the web before naming the folder; the id itself still writes fine.

## Placement & naming

**Before hunting or downloading ANY title, search every library for it first — and re-file rather
than re-download.** The user's standing instruction is "check it's not already there; if it is,
move it": a wanted title is frequently already held under a different library (a noir sitting in
Films, a documentary in Music Docos) or under a near-miss name, and the fix is a move, not a second
copy. Match on normalised name AND on the on-disk folder, across ALL libraries — `searchTerm` alone
misses rows whose `Name` is release-style. Only a genuine absence authorises a hunt, and when the
title IS held elsewhere, name the source library and the destination in the same breath so the move
is a single decision.

**Also check for a better copy already on disk before accepting a download.** A folder can hold a
higher-quality sibling the listing hid — a `- hi.mkv` (h264 1080 + DTS) sitting beside the `- low.mp4`
the row points at. List the destination folder and probe what is there before calling a torrent an
upgrade; the file you were about to fetch may already be beat.

**Ask the user which library a title belongs in before finishing** — Films, Film Noir, Documentaries, Music Docos, or dual-placed — unless they delegated the call. Never assume placement from genre alone. One standing route: documentaries ABOUT cinema and filmmaking (production histories, director portraits, genre studies) go to Film Documentaries (`/data/filmdocos`), not the general Documentaries shelf. Dual placement uses a hard link (same inode, zero extra disk): copy into one library, `ln` into the other (e.g. Sunset Boulevard / Chinatown).

- **Music Docos convention:** one folder per title (`Title (Year)/` holding the film plus art). **Documentaries** mixes loose files and folders — so a move into Music Docos means creating the folder and hard-linking the file into it, not dropping a loose file. Follow with a library-scoped Refresh of that library only, and verify the new row before touching the source entry.

### Moving titles between libraries in bulk (hardlink move)

For user-approved batch moves (e.g. growing a new section title by title): build a plan JSON first (name, container path, `nsrc` per row, read live from the API — never from memory of an earlier listing), dedupe the plan by exact `Path` (overlapping search terms return the same row twice), then `os.link` each file into a title folder under the destination root (`scripts/filmdocs-move.py` takes the plan file plus src/dst roots), rescan, verify rows + metadata, and delete the originals only after confirming `st_nlink >= 2` on each — the link count is what proves the destination holds the data, and a file at `nlink == 1` is the only copy. Rescan (API-purge any surviving ghost rows) and re-verify zero leftovers by exact path.

- **Finish candidate hunting with a full sorted-name eyeball pass, in ~160-row chunks.** Keyword/regex trawls plateau after a few passes — paraphrased titles (`Burden of Dreams`, `Room 237`, `F for Fake`) contain none of the section's keywords and only surface when a human reads every name. Dump `Name` for the whole library, sort, exclude moved/staged titles, and read the remainder end to end.
- **Verify the item is actually about the section's subject before moving it — and route misfiled fiction features to Films, never sideways.** A documentaries library accumulates feature films (`Cobra Verde`, `Upstream Color`) that match no keyword; moving one into a documentary section repeats the misfiling. When the subject is genuinely ambiguous (an essay film by a documentarian, a TV episode about a film), ask which shelf before linking.
- **Group by parent folder, but root-level files need their own title folders.** A file sitting directly in the library root groups under the library's own directory name, producing `Destination/LibraryName/` nesting that then has to be unpicked with renames. Detect root-level sources in the plan and assign explicit title folders before linking.
- **`searchTerm` tokenises on words, not substrings — and even plain multi-word queries can miss.** Searching `Magical Mystery` returns zero rows for `Arena.The.Beatles.Magical.Mystery.Tour.Part.1…` — the dots defeat multi-word matching. Worse, a plain two-word query (`Poem Naked`) returned zero rows for an existing `A Poem Is a Naked Person` row that single-token `Poem` found immediately. Never conclude "not in library" from one multi-word search: always retry with the single most distinctive token, and confirm any same-name hit by DateCreated + exact Path — an old row masquerades as a new ingest.

**Multi-part films use Jellyfin's stacked convention, not versions or merges:** one folder `Title (Year)` holding **spaced** part names — `Title (Year) - part1.mkv`, `Title (Year) - part2.mkv`. Per-part subfolders (`Part 1/…`, `Part 2/…` under the title folder) do NOT stack — one scan ignored subfoldered parts and picked up the same parts as sibling files, so keep parts as sibling files in the single title folder. **The spacing is load-bearing:** the unspaced `Title (Year)-part1.mkv` form does NOT stack. It produces a parent Movie row plus one loose `Video` row per remaining part, so the title shows as N entries instead of one — check what you actually got before writing any metadata.

**Verify the stack with `PartCount`, not `MediaSources`.** `GET /Items?Ids=<id>&Fields=PartCount` returns the part count (`3` for three stacked parts) on the Movie item; `MediaSources` stays at **1** even when the stack is correct, so reading `MediaSources` tells you nothing. If you renamed files between the spaced and unspaced forms, the stale rows survive until the next scan and still appear in `searchTerm` results — audit the **paged library listing** (`ParentId` + `StartIndex`, filter by exact `Path`) instead of trusting search, and let the next library-scoped Refresh clear the ghosts. `S01E01` tokens do NOT stack in a Movies-type library — they create one Movie row per file. Give the folder the clean title plus year and leave release-style tokens behind in the download dir; clean names auto-identify metadata and art with no manual work.

**Documentary series in a Movies-type library does not behave like TV — and "one folder with per-part Names" does NOT work.** `S01E01` tokens do not group or stack. But files sharing a folder with near-identical base names are **version-grouped into ONE Movie row holding several `MediaSources`** — so only one row exists, and the distinct per-part `Name` you intended is never created (one row carries one Name). Putting `(Part N)` in the filename makes it worse: `part`/`cd`/`disc` are part-stacking tokens, so the scanner tries to merge the files as parts of a single film, and a missing Part 1 leaves a broken stack — the symptom is fewer rows than files, silently hiding an episode.

**Give each episode its OWN folder**, which is what actually produces separate rows:

```
Music Docos/<Title> (<Year>) - Episode 2/<Title> (<Year>) - Episode 2.mkv
Music Docos/<Title> (<Year>) - Episode 3/<Title> (<Year>) - Episode 3.mkv
```

Then set a distinct per-part `Name` + `Overview` and lock both via `LockedFields: ["Name","Overview"]` so rescans keep them. Ingest available parts immediately rather than holding them for a complete set.

**Detect the collapse before writing any metadata:** `GET /Users/{uid}/Items/{itemId}` (or `Items?searchTerm=<title>&Fields=MediaSources`) — a `MediaSources` array with **more than one entry** means the files were merged as versions of one row, not split into episodes. Assert row count == episode count first; renaming files is cheap, but metadata written onto a merged row is wasted and the hidden episode stays hidden.

- Write per-episode `.nfo` + `tvshow.nfo` alongside as a portable backup, but do not rely on them — a Movies-type library ignores `episodedetails` season/episode tags.
- Metadata-write path that works: `GET /Users/{uid}/Items/{itemId}` for the full DTO → mutate Name/Overview/LockedFields → `POST /Items/{itemId}` with the whole DTO back (204). Use the lowercase-dashed Id form returned by `GET /Items?searchTerm=`, and verify with a DB re-read of Name/Overview, not the POST status alone.
- When consolidating duplicate source folders into one canonical folder, delete the old folders natively, then run a library-scoped Refresh of that library only — the pass purges rows whose paths no longer exist while keeping the new rows. Verify by DB re-read: the UI can lag a running scan and briefly hide the series.

### Two cuts, one entry — multi-version grouping

When the user wants two cuts of one film under a SINGLE row (feature cut + broadcast TV cut), do not stack (`- partN` plays sequentially — wrong) and do not make two rows. Place both files in ONE `Title (Year)/` folder with identical stems plus a version suffix after ` - ` (e.g. `Title (Year) - 1080p.mkv`, `Title (Year) - TV Cut.avi`). The scanner version-groups them into one Movie row — verify `len(MediaSources)==2` on the row (never `PartCount`; that signal means stacking, not versions). If the scan instead leaves two separate single-source rows, collapse them explicitly with `POST /Videos/MergeVersions?ids=<id1>,<id2>` (204) and re-read until exactly one row carries both file paths — never delete a file or a row to force the collapse. Art sidecars are unreliable on the merged row (the server can keep a frame-grab in Primary with no Backdrop slot) — upload poster + backdrop directly (`POST /Items/{id}/Images/Primary|Backdrop`, Base64-encoded body, `Content-Type: image/jpeg`, expect 204) and byte-prove each served image against the placed file. Then write ONE Overview describing both cuts with their runtimes, set the shared Tmdb/Imdb pair, lock `Name`+`Overview`, and only then delete the source torrent. Proven on Taxi to the Dark Side (Tmdb 13362 / tt0854678): 106-min feature + 78-min Why Democracy TV cut, one entry.

## Removing alternates and superseded rips

Deleting on-disk alternate cuts (`.orig.mkv`, `.remux-backup.mkv`, `.recode.mkv`) after the live file is confirmed leaves their Video rows as harmless dead entries — offer a purge as opt-in; never rescan to clean them while ghost-preservation is in force.

When a higher-quality replacement verifies (probe + decode + JF row), delete the older rip so only the keeper remains — this user prefers replace, not keep-both — but keep the original until playback of the replacement is confirmed.

### Confirm release provenance before replacing ONE part of a stacked entry

A partial torrent (`1of3`) is usually a **different release** from the full set already in the library, so swapping a single part silently produces a mixed-quality entry — one part at a different resolution or codec from the rest, changing mid-playback.

Name each part's release before touching anything: list the still-seeding torrent's files (`GET /api/v2/torrents/files?hash=<h>`) and compare their **byte sizes against the library files** — an exact size match identifies the release the library came from. If the parts you hold come from release A and the replacement is release B, you have two honest options: obtain the remaining parts from release B so the whole entry is uniform, or leave the set alone. Never swap one part and present the result as a clean replacement — tell the user the entry will be mixed and let them choose.

### Swapping a file in place without losing the row or its art

Order matters: **copy → verify → move aside → install → verify in Jellyfin → delete.** Never delete the superseded file before the replacement is confirmed live.

1. Stage the replacement **outside the library root** (a sibling directory on the same share, not a subdirectory of the library) so the scanner never indexes a half-written file.
2. Verify the staged copy: byte size matches the source, and a mid-file decode probe exits 0.
3. `mv` the current file aside into staging — do not delete it yet.
4. `mv` the replacement into the **exact same filename**. Keeping the name is what preserves the Jellyfin row, its provider IDs and its poster; renaming makes the scanner drop the old row and take the art with it.
5. Library-scoped Refresh, then re-read `PartCount` + `ProviderIds` + `ImageTags.Primary` and confirm the live stream reports the new codec/resolution.
6. Only then delete the old file from staging and remove the staging directory.

**`DELETE /Items/{id}` used to fail on a read-only media mount — that is no longer the case here.** As of 2026-09-12 the mounts are **read-write** and media deletion through Jellyfin is enabled (see SKILL.md, "Enabling real in-Jellyfin media deletion"). `DELETE /Items/{id}` now returns **204 and really unlinks the file** — verified end-to-end. If you ever see 500 `Access to the path ... is denied` again, something has reverted the write chain (fstab / CT bind / compose volume) rather than the delete being unavailable by design; the old staged-SQLite purge is now only a fallback for a genuinely read-only deployment.

## Auditing completed torrents before deleting their sources

Run `scripts/audit-completed-torrents.py` (read-only) to answer "is this torrent already in the library?" before deleting anything. It reports per torrent: `INGESTED` (byte-identical library file **and** a Jellyfin row), `COPIED, NO JF ROW`, `NOT IN LIBRARY`, or the album coverage verdicts. For ledger-driven batch deletes, `/root/.hermes/scripts/verify-ingested-deletes.py` cross-checks ledger hash prefixes against live qB hashes, byte-compares each source file with its library copy over ssh, confirms the Jellyfin row and exit-gate score, and only then deletes with hash-prefix matching, a completion gate, a count assertion, and a re-list.

**The namespace trap — this silently invalidates the whole audit.** qBittorrent's `content_path` is `/downloads/...`, which is its **container** namespace. On the host the same bytes are `/mnt/dfujh/downloads/...`. Comparing the raw `content_path` against the library makes **every single torrent report `SOURCE MISSING`** — a full page of confident false negatives that reads like a real result. Translate the prefix before any `os.path.exists` check. There is more than one download root: `/downloads/...` maps to `/mnt/dfujh/downloads/...`, but `/downloads-zmedia/...` maps to `/mnt/zmedia/downloads/...`. Resolve the host prefix per torrent from its own `save_path`/`content_path` — never assume a single mapping. Case differs across the same boundary: container library roots are lowercase (`/data/documentaries`) while the share dirs are CamelCase (`/mnt/zmedia/videos/Documentaries`) — a case-sensitive fragment match on the host form misses every container-pathed row and reads as MISS or ghost. Lowercase both sides before comparing. When the mapping itself is unknown, read the downloader container's binds directly (`docker inspect <name> --format '{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}'`) instead of hunting the path across hosts and containers — guessing the root costs more turns than the inspect does.

**A blanket `SOURCE MISSING` across every row is a path-mapping bug, not a finding — never report it as a result.** Two independent causes produce it, and both must be ruled out before any verdict is believed: (a) the namespace trap above, and (b) the script's own **library root is not visible from the host it runs on**. The script prints its library scan in the header line (`library: N files ...`) — **`library: 0 files` means it cannot see the media share at all, so every verdict is a false negative and the table is worthless.** Only run this audit from a host that can actually read the library root; anywhere else, verify the specific titles by hand against the Jellyfin API plus the on-disk files rather than quoting the table.

**ssh joins argv with spaces and the remote shell re-splits — pre-quote every path into a single remote command string.** Passing paths as separate ssh argv entries silently word-splits on spaces, so every `stat` returns empty and the audit reports files missing that exist. Build the remote loop with each path `shlex.quote()`d into one command (`for p in <q1> <q2> ...; do stat ...; done`), never `ssh host <script> <path with spaces>`.

**Audio has nowhere to go on this install.** The library is ~5,280 files of which only ~86 are audio — there is **no Jellyfin music library**. The 100%-complete queue is dominated by music albums (25 torrents / ~16 GB on the Sep-2026 audit) which are therefore *not ingested and cannot be*. **Never treat "not in library" as "safe to delete" for audio** — the download is the only copy.

Verified audit outcome: of 26 complete torrents, exactly **1** was `INGESTED` (South Asian Music at the BBC, 1.75 GB — byte-identical + Jellyfin 4/4) and **25 were albums not in the library**. Only the single `INGESTED` row was deletable.

**Separate file-safety from metadata-completeness when deciding a torrent delete.** File-safety is byte-identical library copy + Jellyfin row + passing decode probe; metadata-completeness is the 4/4 exit gate. Rows that can never reach 4/4 (BBC strand episodes with no TMDB entry, TMDB-less one-offs) must not hold their sources hostage forever — delete on file-safety once verified, and report the exact metadata gap field-by-field instead.

**A torrent added by bare magnet has no display name — `name` is the raw infohash.** It reads as an unidentified blob in every listing, and it is not corrupt metadata: the real title exists only in the file list (`torrents/files`) and the basename of `content_path`. Resolve identity from those, never from `name`.

**Never select torrents for deletion by name substring.** Several releases of one title share the same substring — a single documentary had four matching torrents, one complete and three stalled at 0.5%/66.9%/0%. Substring matching alone would delete the incomplete ones and destroy the only copy of those transfers. Match on **hash prefix**, additionally require `progress >= 0.999`, then **assert the expected match count before issuing the delete and abort on mismatch**. The count assertion is the part that catches the release you did not know was there; the completion requirement is the part that protects the partials. Re-list afterwards to confirm zero remain.

## Batch intake jobs: keep the model OUT of the loop

An agent cron job that probes and matches each download itself is quadratic — every tool round-trip re-sends the whole growing conversation. A measured 5-item run burned **5,481,154 prompt tokens / 124 min**, against ~293k tokens / 5.7 min for normal jobs on this box. Throughput was identical (~50k tok/min), so **~86% of wall time was pure context replay**, not media work.

Fixes, in order of impact:

1. **Move all deterministic work into the scanner script** — find the files, `ffprobe` the resolution, match against the library, emit a compact decision table. This costs zero tokens; the agent only decides and acts.
2. **Gate the agent with `monitor`, not `script`.** Unchanged scanner output suppresses the run entirely. The output MUST be byte-stable — no timestamps, no dict/JSON ordering drift. Verify with three runs plus `diff` before trusting it.
3. **Cap the batch** (5 rows works) and require the agent to clear the whole table each run — the ledger shrinking the table is what re-triggers the next run via the monitor.
4. Drop `continuity` (it injects the previous run's output on top) unless the job genuinely needs to see its own history.
5. Resolve the qB "namespace trap" in the scanner (`/downloads/...` → `/mnt/dfujh/downloads/...`) or every probe reports nothing. Map EACH save root separately — a naive prefix replace of `/downloads` mangles `/downloads-zmedia/...` into `<remote>-zmedia/...`, so every -zmedia probe fails while `/downloads` probes pass; `/downloads-zmedia/...` → `/mnt/zmedia/downloads/...`. All-PROBE-ERR with files MISSING from the script host means the download mounts are not visible there at all — probe where the mounts live instead of quoting the table.
6. Batch every remote probe into ONE `ssh` call, and cache probe results keyed by path so re-runs are ~0.5s instead of ~20s.
7. **When the deterministic layer already covers the whole job, drop the agent entirely.** If the scanner can decide every row and the actions are mechanical, the model adds nothing but latency and tokens — the measured step-change is ~25 min **per item** for the agent run versus seconds per item for a script, because nothing is re-sent through a conversation. Keep a model only for rows a script must not decide alone, and route those to a `needs-your-check.json` list rather than acting on them.

### Running intake as a standalone script

`/root/.hermes/scripts/jellyfin-media-intake.py` — deterministic, no model, and the replacement for the hourly agent job. It reuses the scanner's helpers (`qb_torrents`, `qb_files`, `probe_all`, `library_dump`, `best_match`) by loading it with `importlib`: the scanner's filename is hyphenated, so a plain `import` is impossible and the logic would otherwise be duplicated and drift.

- **Default is `--dry-run`; `--apply` is required to change anything.** `--limit`, `--max-gb`, `--max-minutes` and `--library` bound a run.
- **It acts only on `REPLACE`.** Everything else (`NEW`, `REVIEW-equal-res`, `REVIEW-lower-res`, `REVIEW-weak-match`) is written to `needs-your-check.json` and never touched — a wrong auto-match is far more expensive than a delayed one. Strictly-higher resolution alone is not higher quality — refuse the swap when the candidate loses on total bitrate or audio (a bitrate-starved 1080p with lossy stereo is a downgrade from a high-bitrate lower-res encode with DTS/subs); compare bitrate + audio + subs before replacing and route bitrate-losing candidates to the check list.
- **Automated in-place swap, in this exact order:** copy to `<dest>.part` → `mv` over the destination → `ffprobe` the destination → mid-file decode the destination → **only then** delete the old file. If the decode fails, remove the NEW file and leave the OLD one untouched. Keep the old **filename stem** and take the new **extension** (which must match the real container), so the Jellyfin row, its provider IDs and its poster survive. Idempotence comes from comparing byte sizes, so a killed run can simply be re-run.
- **Split the swap into two phases, and keep the gating decision in the script rather than in shell.** Phase A copies and *reports facts* (probe result, embedded title, new duration, old duration); the caller applies the content and duration gates; Phase B commits (delete or overwrite). Gating inside the remote shell puts the only copy of the safety logic somewhere untestable and unreviewable — exactly the code you most need to unit-test.
- **When the destination IS the old path** (same stem and extension, so the swap would overwrite in place) **stage the new file as `<dest>.new` and commit with `mv`.** Copying straight over the old path destroys the file you were about to compare durations against, so the duration gate silently passes everything — the one case where a partial copy is most dangerous.
- **Dedupe the target before acting.** Two releases can match the same library item within one run; act on the larger and route the other to the check list, otherwise the second silently overwrites the first and the run's own log no longer describes what is on disk.
- **Bound a run by estimated TIME, not by a row count.** The row cap only ever existed to keep an agent prompt short; with the model gone it means nothing. Cost each item before starting it (`size / measured_MBps + per-item overhead`) and stop when the budget would be exceeded — a count cap cannot protect you from a few very large items, a time budget can.
- **Measure the copy throughput; never assume it.** `dd` **without** `conv=fsync` reports page-cache speed and overstated this array by ~4x (666 MB/s reported versus 143–171 MB/s measured with the sync forced), which would size a batch far too large. One fsync-forced pass is the honest number:
  `dd if=<src> of=<dst-on-the-array> bs=4M conv=fsync`
- **Never plan against a stale library dump while another process may be swapping files.** A cached dump holds item IDs that stop resolving the moment anything replaces them, so a plan built on it proposes work that is already done. Refresh the dump before planning, and treat a matched item ID that no longer resolves as *the library changed underneath you* — refuse that row into the check list and re-plan. Never fall back to path-guessing or overwrite on an unresolved match. For the same reason, do not run intake concurrently with another intake run.
- **Normalise the hash form on BOTH sides, or the "already ingested" exclusion silently does nothing.** The ledger stores **12-character hash prefixes** while qBittorrent returns **40-character full hashes**, so a `full_hash not in known` test can never match — **every previously-ingested torrent is re-processed on every run**, the backlog never shrinks, and a title finished hours earlier comes back as a live candidate. Compare on the 12-char prefix (or store full hashes), then assert that no ledger hash appears in the candidate list before acting on anything.
- **Write the ledger entry per item, immediately after that item succeeds — never once at the end.** A killed or crashing run otherwise loses the record of every replacement it completed, and the next run cannot tell what is already done. The same applies to hand-driven (non-script) ingests: journal each title the moment it lands, or the next script run re-offers it as NEW.
- **A matched item whose `Path` is a DIRECTORY is a DVD/VIDEO_TS item, not a file.** Keep the `[ -f "$old" ]` test on the delete — that guard is what stops the swap from `rm`-ing a folder. But a folder-based target cannot be replaced by a sibling file: the new file lands alongside and the old folder survives as a duplicate, and the item then holds both. Detect it (`[ -d ]`) and route those rows to the check list rather than half-replacing them.
- **Multi-part items only ever get part 1.** The scanner maps a torrent to its single largest file, so a `part 1 of 3` item is replaced one part at a time while the rest stay at the old quality, silently yielding a mixed-quality entry. Never present a one-part swap as a clean replacement — see the provenance rule above.

### Auditing a run from the host — traps that misread the result

- **Batch queue logs carry release-named paths with spaces — never capture them with `\S+`.** A `FLAG <codec> <size>GB <path>` line parsed with `(/mnt/\S+)` truncates at the first space, so an exclusion set built on it fills with phantom prefixes and either skips the whole library or nothing at all. Split on the size token (`FLAG \S+\s+[\d.]+GB\s+(.+?)\s*$`), compare full paths, and assert the parsed set against a known sample before trusting any exclusion built on it.
- **`ls -la | awk '{print $9}'` silently truncates any filename containing spaces.** It prints only the first word, so `Good Hair (2009) 1080p h264.mkv` renders as `Good` and a directory path looks like a corrupt or clipped filename — which then sends you diagnosing the wrong thing for several turns. Use `substr($0, index($0,$9))` for the name field, or `find -printf`.
- **Never `pkill -f '<script>.py'` when that exact string appears in the command you are running.** `-f` matches against the shell's own command line, so the job kills its own shell: the call returns `exit_code -15` with no output at all and you lose the very diagnosis you were collecting. Identify the PID first (`ps -eo pid,cmd | grep <script> | grep -v grep`) and kill by PID, or bracket one character (`pkill -f '[j]ellyfin-media-intake'`).
- **Establish what the library ACTUALLY shows before trusting the run's own log.** Probe the library copy itself rather than reading the log line: `ffprobe` the destination for resolution, codec and `format_tags=title`, and `[ -d ]`/`[ -f ]` the old path. A path with no file extension is a folder item, so a logged "old removed" was really a no-op and both copies are still present.

### Matching release names to library titles — score it, then gate it

Release names crushed into one token (`TheWarOnDemocracy`) score **zero** on token-overlap (Jaccard) against `The War on Democracy`, which is why a second signal is needed: a squashed alphanumeric string compared by similarity ratio, scored `0.85` when one contains the other.

**A raw `max(token_overlap, squash_ratio)` is unsafe and has produced real misplacements.** Generic titles and stock phrases drive both signals over any usable threshold:

- `Good.Hair` against the film `Good` — one shared token out of two → Jaccard **0.5**.
- `Smash and Grab: The Story of the Pink Panthers` against `The Story Of The Guitar` — the shared phrase *"the story of the"* alone lifts the squash ratio to **~0.47**.

Both cleared 0.45 and were acted on, writing the wrong film into the right item's place. **Stopword filtering plus "require both signals" is necessary but NOT sufficient** — implemented on its own it still accepted `Good.Hair` → `Good (2008)` at **0.850**, because the containment boost fired on a 4-character substring (`good` inside `goodhair`). All six guards below are required before a REPLACE:

1. **Filter generic stopwords before scoring** (`the`, `a`, `and`, `of`, `story`, `part`, `series`, `life`, `world`, and similar), so a shared article or stock noun cannot carry a match by itself.
2. **Require BOTH signals to agree** — not the max of them. An overlap of one token, or similarity driven by a single common phrase, is not a match at any threshold.
3. **Require COVERAGE of the release's own identifying words.** At least ~60% of the release's distinctive tokens must also appear in the target. A release advertising a major word the target lacks is a different work: `Good.Hair` carries `hair`, which `Good (2008)` does not, and that alone is the reject signal. This is the guard that actually caught the misplacement — the others passed it.
4. **Only trust containment when it is a large share of the longer string.** `cand in sq or sq in cand` is strong evidence only when `len(shorter)/len(longer) >= ~0.6`. `good` inside `goodhair` is 0.5 and must NOT be boosted to 0.85.
5. **Compare word-order and punctuation-insensitively as well.** Join each side's tokens sorted alphabetically and take that ratio as an additional signal — without it `Anvil! The Story of Anvil` falls below the bar on the `!` alone and a genuinely correct match is refused. Take the best of the signals *and then* apply the guards above — never a single raw `max()`.
6. **Treat short token sets as unmatchable.** When either side reduces to one or two meaningful tokens there is no evidence to act on: emit REVIEW.

Working thresholds after the guards: **squash ratio >= 0.80 AND >= 1 shared distinctive token AND >= 60% release coverage**. The old `0.45` bar is unsafe on its own — do not carry it forward. Always surface an ambiguous match as REVIEW rather than acting on it; a wrong auto-match is far more expensive than a delayed one.

**Test the matcher against the known false pairs IN ISOLATION before letting it run unattended.** Build a synthetic one-item library dump containing only the disputed target and assert the pair is refused; then assert the genuinely-correct pairs are still accepted (`Good.Hair`/`Good Hair`, `Anvil The Story Of Anvil 2008 1080p BluRay…`/`Anvil! The Story of Anvil`, `The.Dust.Bowl`/`The Dust Bowl`). Testing only against the real whole-library dump is not enough — a wrong match can be outscored by a right one, so the bug stays hidden until the right item happens to be absent from the dump, which is exactly how the real misplacement got through.

**Known false-NEGATIVE class: an artist-prefixed release vs a title-only library row.** `Oasis Supersonic (2016) 1080p BRRip` scores coverage 1/2 against a library row named `Supersonic`, so the coverage guard refuses a correct match and it routes to REVIEW. That direction is safe (nothing is acted on) but it is noise in the check list — and it is NOT a reason to loosen the guard, because `Good.Hair` vs `Good` is the identical shape. Verify against TMDB before "fixing" the row name: movie 412924 is canonically titled **Supersonic**, so the library Name was already right and the release name was the marketing variant. The modular implementation pins both directions in `media-ops/tests/test_matcher.py`.

**Measure the CURRENT code before porting or rewriting a matcher.** Comments and docstrings describe intent, not behaviour: one matcher's docstring claimed the squashed-string signal handled run-together names, while the shared-token gate ran first and rejected exactly those — the signal was unreachable. Run the live functions against the known pairs (accept, refuse, ambiguous) FIRST, record the actual verdicts, and only then write tests. Port two fixes this exposed:

- **Strip release tags before computing coverage** (`1080p`, `1080p`, `BluRay`, `x264`, `-GROUP`, `WEB-DL`, …). Coverage over raw release tokens marks a correct match as a different work — `Anvil The Story Of Anvil 2008 1080p BluRay x264-GRP` scored 2/6 coverage against `Anvil! The Story of Anvil` and was refused.
- **A squash-exact name must win ahead of the token gate.** Run-together names (`TheWarOnDemocracy`) normalise to ONE token, so they can never share a token with `The War on Democracy`; require string equality to bypass the token rules, but gate it on length (a 2-character title still goes to REVIEW).
- **Never `os.path.splitext()` a release or folder name.** It splits at the LAST dot, so `Good.Hair` becomes `Good` — the exact wrong-title pair the guards exist to refuse — and `...x264-GRP` loses its group tag. Strip only a recognised video extension from the end (`.mkv/.mp4/.avi/…`).

**Gate on CONTENT, not on the name, before deleting anything.** Probe the new file's own embedded tag and compare it with the target item: `ffprobe -v error -show_entries format_tags=title -of default=nw=1:nk=1 <file>`. A wrong-file write shows up immediately (a `title` of `Storyville: Smash and Grab...` on an item named *The Story of the Guitar*). The tag is a **one-way veto**: a tag that contradicts the target blocks the swap, but an ABSENT tag proves nothing and must not by itself force REVIEW — most DVD-sourced rips carry no tag, so let the name-matching guards carry the decision and use the tag to catch what they miss. When both old and new are individual files (not a folder item), also compare **durations** (`-show_entries format=duration`) and refuse a swap differing by more than ~15% — a genuinely different work rarely has the same runtime.

**Two wrong-file shapes the name guards miss — resolve the target by content and container, never by name alone.** (a) A standalone film rip matching a box-set segment row: the swap upgrades the wrong entry (same film, wrong slot) while the standalone item stays untouched — check whether the target row lives inside a multi-film folder before swapping, and afterwards verify the intended item's own path changed. (b) A mislabelled torrent whose NAME names one work and whose FILES are another (series episode inside a film-named torrent): the file list (`torrents/files` sizes) plus the embedded `title` tag and the duration gate identify the real content — the torrent name is evidence of nothing.

### API gotchas that silently produce empty results

- **`GET /Items?...&Limit=0` returns ZERO items — it is not an "unlimited" sentinel.** This produced a library dump of 0 items across all five libraries in 121 bytes, which made every match report `none` and every recommendation read `NEW` — a plausible-looking table that was entirely wrong. Paginate with `StartIndex` + `Limit=500`, and assert the dump is non-empty before trusting any "no match" verdict.
- `ffprobe -show_entries stream=width,height,codec_name -of csv=p=0` emits **its own field order** (`codec_name,width,height`), not the order requested. Parse positionally by checking which fields are numeric.
- **Duplicate library entries hide behind folder-name capitalisation.** `The Devil And Daniel Johnston` and `The Devil and Daniel Johnston` are different directories holding the same film; a case-sensitive match silently turns a REPLACE into a second copy. Match case-insensitively.

- **`Fields` must be a comma STRING — never `','.join(fields)` on a string.** Joining a string iterates its characters, so `Fields=Path,MediaSources` becomes `P,a,t,h,,,M,e,d,…`: the server accepts it and returns **items with no `Path` at all**. A 2,092-item dump with zero paths then reads as "the library has no paths" and every title classifies as NEW. Accept either a list or a pre-joined string in any helper that builds query params, and assert the dump has a non-zero path count before matching against it.
- **`difflib.SequenceMatcher(None, '', '').ratio()` returns `1.0`.** Two titles that both reduce to no comparable tokens (short names, digits stripped) therefore score a PERFECT match. Guard every similarity call on both sides being non-empty before comparing.

### Ghost rows after deleting an old file

Deleting the superseded file leaves its item as a **ghost row** until the library is scanned — it still appears in searches and its `Path` no longer exists. Clear it with a **library-scoped Refresh of that ONE library** (never a library-wide rescan). Confirm the ghost is gone by re-reading the item count and re-searching the title. After the purge, re-read the keeper row's `ProviderIds` too — the Refresh can shed the surviving row's auto-attached IDs (row reads ID-less with art intact); one per-item FullRefresh restores them, so re-fire that before reaching for a manual TMDB write.

## Downloader queue triage

- An `error`-state torrent **never auto-retries and keeps holding one of its `max_active_downloads` slots** while doing nothing, so the queue behind it looks stopped. A transient write-path stall (CIFS source unresponsive) surfaces as a file error naming the partial file (`... .!qB ... No space left on device`) despite free space on the disk — recheck/resume the torrent after the path is healthy instead of re-adding it.
- `stalledDL`/`forcedDL` at ~0% with zero seeds and dead trackers cannot move client-side; replace the torrent rather than forcing retries. **`forcedMetaDL` showing `0.00GB` is the deadest signature of all** — metadata was never fetched, so the infohash has no reachable peers at all; it will not recover with time or a recheck and needs a different source.
- **A queue screenshot is a point-in-time snapshot, and usually a stale one.** The downloader web UI does not live-update after API-side deletes, so a torrent it still shows seeding may already have been deleted. Diff the image against a fresh `GET /api/v2/torrents/info` BEFORE acting on it — otherwise you re-run an ingest that is already finished and chase source files that no longer exist.
- **A complete/seeding torrent is NOT proof of ingestion.** Audit every complete name against a Jellyfin DB Path/Name row PLUS the on-disk library copy (size match + decode probe) before deleting its torrent and source files — completed-but-never-ingested files are the normal case after a downloader retarget, and deleting on progress alone destroys the only copy. **100% progress plus a full byte count is not even proof the bytes are valid** — a poisoned swarm delivers complete pieces of zero-filled garbage (`ffprobe` fails with `0x00 ... invalid as first byte of an EBML number`). Read the first bytes before any swap (`od -A x -t x1z <file> | head -2` — mkv must start `1a 45 df a3`); an all-zeros head means delete the torrent and its files, never ingest, and sweep the library folder for staged copies of the same garbage (`*.upnew`/`*.part`/`*.new`, confirmed with `cmp -s`) — a failed intake run may already have copied it there. Search by Name across ALL libraries first: a title can already live under a different library than the one being audited, and ingesting again creates a silent duplicate.
- Triage recent completions with `GET /api/v2/torrents/info?sort=completion_on&reverse=true` (no auth needed from the whitelisted LAN) — `forcedUP`/`queuedUP`/`stalledUP` at 100% with a real `completion_on` timestamp are done; anything still carrying `.!qB` under any download root's `temp/` dir (`/downloads/temp`, `/downloads-zmedia/temp`, ...) is a partial, never a source — regardless of how close to 100% it reads.
- For batch ingests, fan out parallel workers that copy+verify and report torrent hash plus delete-readiness, then centralize the actual torrent+file deletes afterwards — only after size match, decode probe, and Jellyfin row all pass.
- **`max_active_downloads` (default 3) parks the queue silently.** Newly added torrents sit in `queuedDL` and can never progress while the cap is saturated by ~dozens of incomplete torrents — this reads as "nothing is happening" but is the cap, not a fault. Either raise the setting or `POST /api/v2/torrents/setForceStart` with `value=true` for the batch (full hashes here too — a prefix leaves the torrent parked in `queuedDL` with no error), and tell the user which you changed.
- **Never source a replacement from an index's seeder count.** Those counts are stale and routinely claim seeds for empty swarms. Prove the swarm is dead, then verify each candidate by adding it and watching — full method, the qBittorrent 409-duplicate trap, and apibay search craft in `references/torrent-source-recovery.md`.
