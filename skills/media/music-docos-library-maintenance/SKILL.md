---
name: music-docos-library-maintenance
description: "Use when maintaining the Music Docos library."
---

# Music Docos / Music Docos Series maintenance

Moving series into the Series library, and removing duplicates between the two.





> **Read `references/session-lessons.md` first.** Rules that each cost real damage or a false report: pipeline-first, no partial DTOs, verify locks by direct GET, refresh timing, path map, hardlink-by-link-count, agentic Identify, bios are People records, empty torrents, temp paths, deletion evidence, probe before declaring a fault.

## Library paths on disk, and why "duplicate" usually is not one

Jellyfin's library roots are NOT the folder names on disk. Verified mapping (host
`/mnt/zmedia/videos/`, which the Tdarr node sees as `/media/videos/`):

| Jellyfin root | Folder on disk |
|---|---|
| `/data/musicdocos` | `musicdocos` |
| `/data/musicdocos-series` | `musicdocos-series` |
| `/data/documentaries` | `Documentaries` (capital D) |
| `/data/docuseries` | `DocuSeries` |
| `/data/filmdocos` | `FilmDocumentaries` |
| `/data/filmnoir` | `filmnoir` |
| `/data/movies` | `Films` (not "movies") |
| `/data/tvshows` | **unresolved** — no folder of that name exists; resolve from CT105's `/data` mount before touching it |

Never translate `/data/<lib>` by string substitution. Guessing produced 12 failed moves and
several empty directories. Resolve the root first, or find the file by name.

### Membership in several libraries is a HARDLINK, not a copy

A title can legitimately appear in two libraries with **one** copy of the bytes.

**Judge by LINK COUNT (%h), never by inode.** This share is CIFS (`//192.168.214.132/zmedia`)
and the client assigns a DIFFERENT synthetic inode to every path, even for a genuine hardlink —
a controlled test with two names on one inode reported `inode=A links=2` and `inode=B links=2`
for the same bytes. Comparing `%i` therefore concludes "different files" when they are one.
Comparing `%i` also concluded the reverse earlier in the same session. Both were wrong.

The reliable test:

```
stat -c "%h links  %s  %n" path-a path-b
```

- both report `links >= 2` → hardlink, one copy, two names
- one reports `links == 1` → not linked to the other yet

Creating one works: `ln src dst`, and `ln -f src dst` over an existing file both succeed on
this mount (verified: a fresh pair went from links=1 to links=2, and an `ln -f` target went to
links=3 with three names on one copy). After linking, check that BOTH paths show `links >= 2`.


Worked example from a live audit (all on device 49):

| Title | Verdict |
|---|---|
| Sunset Boulevard (1950) filmnoir + Films | hardlink, inode 880083, links 2 — one copy |
| Chinatown (1974) filmnoir + Films | hardlink, inode 878925, links 2 — one copy |
| The Harder They Come (1972) musicdocos + Films | same inode 1378884, links 1 — one copy |
| The Making of Psycho (2005) Documentaries + FilmDocumentaries | were two copies (links=1/2); now hardlinked, links=2 both |
| Pussy Riot: A Punk Prayer Documentaries + musicdocos | were two copies (links=2/1); now hardlinked, links=2 both |

### After moving sources, wait before judging the row

A library-scoped refresh never repopulates runtime; only a per-item FullRefresh does. On an item
with MULTIPLE media sources the refresh takes longer than 90 seconds — an item consolidated to
three sources still reported 0.0 min at the 90-second mark and was correct a few minutes later.

Check at two to three minutes. Concluding "the refresh failed" from a single early read has
produced three false failure reports in one session (hardlink test, Doors merge, Costello merge).

Consequences:

- **Do not "de-duplicate" a hardlink.** Deleting one name removes only that library entry; the
  bytes persist under the other name. Treating them as wasted space is wrong — they cost nothing.
- **Jellyfin shows one item per library even for a single hardlinked file.** Two rows are not
  evidence of two copies.
- **Real duplicates (different inodes) are the only ones worth consolidating** — and then only
  as alternate versions in one folder, named `<primary> - <label>.ext`, never by deleting.



## Before any metadata write, check what already exists

There is dedicated code and a reference for people enrichment — use them instead of improvising
against the API:

- `media_lib/enrich.py`: `build_plan(parent_id, limit, min_bio=80)` (read-only gap list),
  `apply_person(person_id, bio, source, lock=True)` (one write, no raise on skip), `MIN_BIO = 80`.
- `references/people-enrichment.md`: the full chain (TMDB credits → Wikipedia → Last.fm → portraits),
  with the traps and verification rules.

### Two mistakes this rule prevents, both made in one session

1. **A partial DTO wipes fields you never touched.** Posting `{"LockedFields": [...]}` alone stored
   `Name` as null and blanked `Overview`. Always: GET the DTO fresh, change only the target fields,
   **include `Name`**, omit `Genres`, then re-read and diff the fields you did not intend to change.
2. **Never verify `LockedFields` through a search.** Search-result DTOs omit it and read back
   null even when the lock is set — which produced a false report that locking had failed. Verify
   with `GET /Users/{uid}/Items/{id}?Fields=LockedFields`.

"Bios" is a people-enrichment job, not an item edit: bios live on Person records, the item only
carries credits.




### Identify is automatable — one route, one body shape

```
jellyfin.remote_search(item_id, name, year, provider_ids)   -> candidate list (read-only)
jellyfin.apply_identification(item_id, result, replace_images=False)   -> what the UI button does
```

Rules learned by failing at it first: the route is the MEDIA TYPE one, `POST /Items/RemoteSearch/Movie`.
Sending the bare search info returns **500**; provider-named routes (`/RemoteSearch/Tmdb`,
`/RemoteSearch/TheMovieDb`, `/RemoteSearch/Imdb`) return **404**. The body is
`{"SearchInfo": {...}, "ItemId": ...}`.

And the 401 that looked like an auth wall was self-inflicted: a hand-rolled `X-Emby-Token` header
fails, while `jellyfin._request()` (which uses `auth_header()`) succeeds on the same route. Use the
module's request helper — never build the auth header by hand.

Identify also fixes what a credit write cannot: on a generic Video item in a music library, POSTing
`People` and `ProviderIds` returns **204 and silently discards both**. Identifying the item attaches
the credits properly (nine people plus Tmdb/Imdb/Tvdb ids on this entry).

### When TMDB has no match, fall back to the BBC programme site — never to guessing

TMDB under-covers BBC-original documentaries (strand episodes, one-offs, pre-2000 archive).
A TMDB no-match is not evidence of absence. The BBC programme site carries the title, synopsis
and full episode guide, and its pages are server-rendered enough to parse with stdlib:
`media_lib/bbc.py` (`programme(pid)` for title+synopsis, `episode_guide(pid)` for n/m-numbered
episodes, `gate_with_fallback(release)` which runs `tmdb.gate` first and consults the BBC only
on a clean no-match). Proven: Hawking's Universe episode guide, Bitter Lake / It Felt Like a
Kiss / A303 synopses applied as sourced overviews.

Two constraints on the fallback. First, **the BBC never overrides an ambiguous TMDB hit** — it
is a gap-filler, not an appeals court; an ambiguous hit stays ambiguous. Second, **programme
search from the media box is bot-walled** (DDG serves a consent page, Bing serves junk), so PID
discovery happens agent-side via web search (`site:bbc.co.uk/programmes <title>`) and the code
consumes PIDs — do not build a runtime path that depends on search-engine scraping from the box.
A correct BBC absence is also a result: an ITV strand has no BBC page, and that ends the hunt
instead of failing it.

## Never hand-build an ingest spec — the planner already decided

The sanctioned path is `cli.scan` → `cli.plan` → `cli.ingest_new` / `cli.apply`. `cli.plan`
classifies every item (NEW / REPLACE / review) and runs the quality comparison against what
is held. Treat a hand-assembled spec as a defect, not a shortcut.

Building a spec directly from `scan.json`'s `all_videos` produced all three failure modes in
a single pass:

- **A duplicate.** The planner had already classed The Killers and White Riot as REPLACE; the
  hand-built spec placed them as NEW, so the library held two copies of the same film under
  two folder names.
- **Seven phantom placements.** Paths listed by the scan no longer existed on disk (an entire
  S65 folder that was never downloaded). The scan is a snapshot, not a guarantee.
- **Release-junk folder names** (`White.Riot.2019.NORDIC.1080p.WEBRip...`) because the spec
  derived titles from filenames instead of the planner's resolved names.

Rules that follow:

- **Build the spec from `plan.json`, never from `scan.json`.**
- **Existence-check every source before it enters a spec** — `[-f "$path"]`. This one filter
  prevents every phantom row, and it belongs before the spec is written, not inside the gate.
- **REPLACE belongs to `cli.apply`.** `ingest_new` is for titles the library does not hold; a
  replacement must target the existing path or it becomes a second item.
- **When the held copy cannot be measured, stop.** "Unmeasurable" is not "worse" and not
  "absent" — that conflation is what produced the partials deletion and the near-miss on a
  correct duplicate.


## Ingest only what is BETTER than what is held

A finished download is not a reason to ingest. Placing every completed torrent creates a
second library entry for a title that is already on the shelf, which then has to be cleaned
up by hand and leaves a dead row behind in Jellyfin (that cleanup has been done twice).

Before placing, ask `quality.decide(source_name, destination_label)`:

| verdict | action |
|---|---|
| `no-local-copy` | ingest — nothing comparable is held |
| `upgrade` | ingest (or `cli.apply` to swap in place) — measurably better |
| `same-or-worse`, `marginal`, `different-title` | **skip** — the held copy is equal or better |
| `held-unknown`, `unknown`, check failed | **review** — do not ingest |

`cli/ingest_new.py` enforces this itself now, printing `HELD-BETTER` or `REVIEW` per row, so a
hand-built spec cannot bypass it.

Two traps this closes:

- **A better encode under a different folder name is still a duplicate.** The Killers film is
  held as `The.Killers.Live.From.The.Royal.Albert.Hall...`; a TMDB-derived destination
  (`The Killers: Live from the Royal Albert Hall (2009)`) is a *second* item, not a
  replacement. Compare the title against what is held before choosing a path, and use
  `cli.apply` to replace in place.
- **"Could not measure the held copy" is not "we hold nothing".** That conflation produced a
  dozen needless downloads before; the same rule applies at ingest, where the cost is a
  duplicate instead of wasted bandwidth.


## The two libraries

| Library | Type | Root |
|---|---|---|
| Music Docos | movies/videos | `/data/musicdocos` |
| Music Docos Series | tvshows | `/data/musicdocos-series` |

Separate roots — so the same content can legitimately exist in both, which is what produces duplicates.

Jellyfin: `http://192.168.214.20:8096`, key `JELLYFIN_API_KEY` in `/root/.hermes/.env`. Media host is proxmox3 (`root@192.168.214.131`); library roots are `/mnt/zmedia/videos/musicdocos`, `/mnt/zmedia/videos/musicdocos-series` (Jellyfin `/data/<lib>` maps 1:1).

> **Topology caveat:** that proxmox3 view is stale for everything except `musicdocos`. Library and download file operations must run through CT105 on prox2 — see `jellyfin-administration` → "TOPOLOGY CORRECTION".

## Current tooling — `media-ops` (reach for this before writing a new script)

`/root/.hermes/profiles/media-maintainer/media-ops` is a tested package with git history and ~140 pytest tests. Prefer it over hand-rolling another sweep:

| Need | Use |
|---|---|
| Find/replace higher-res downloads | `python3 -m cli.scan` → `cli.plan` → `cli.apply --ids 1,3` (id-gated, journaled, lockfile) |
| **Hunt a missing episode/album** | `python3 -m cli.source plan --targets targets.json` → `cli.source queue --ids N` (plugin verbs `media_source_plan` / `media_source_queue`). Broad pool → normalised match → ranked; `1ofN` sets are refused unless `--allow-incomplete` |
| 4-field exit gate | `python3 -m cli.verify --ids <itemIds>` |
| Place a completed download as a NEW library item | `python3 -m cli.ingest_new --spec <json> --refresh-lib <id> [--wait-rows 200]` — copy → size → probe → min-height → duration → 3-span decode; a failed gate deletes the staged copy so a half-good file never lands. It now fires `/Library/Media/Updated` itself, so the row appears without a manual ping. `cli.plan`/`media_plan` is **REPLACE-only** by design, so genuinely NEW episodes always fall to its check list; do not read that as a rejection. |
| Bios/portraits from providers | `media_lib.providers.chain.resolve(name, kind)` or `cli.meta plan` → `cli.meta apply` |
| Enrichment gap report | `python3 -m cli.meta plan` (read-only: ready / thin / gap / no-row counts) |
| A/B a sample against the library | `python3 -m scripts.meta_test_10 --seed N --sample 10` |

Agent-side tools (when the plugins are loaded): `media_scan`, `media_plan`, `media_apply`, `media_verify`, `media_source_plan`, `media_source_queue`, `meta_search`, `meta_resolve`. The `meta_apply` write path lives in `cli.meta apply`, not yet as a tool.

**One rule for all sourcing: extend `media_lib.source`, never write a hunt script.** Four hand-rolled hunt scripts were deleted because duplicating its logic produced three separate bugs in a single session (narrow-query zeros, dotted-name mismatches, blind seed trust).

## Adding a series to Music Docos Series

1. Find the TMDB TV id first (`/search/tv`) — the library matches from the **folder name**, so encode it:
   `Name (Year) [tmdbid-<id>]/Season 1/Name (Year) - S01E01 - <Episode Title>.ext`
   Search BOTH namespaces (`/search/movie` + `/search/tv`) and read the overview before pinning an id — a same-name film can carry the same year as the wanted series, so title+year never disambiguates alone; the overview text is the verdict.
2. Get real episode titles from `/tv/<id>/season/1` — do not invent them. **Provider coverage is partial**: a series running 30 years can be listed only to season 10 while the library holds season 18, and the missing season 404s. Where a season is absent, take the SxxEyy↔year mapping from the Wikipedia episode table (it lists the season as one row per year) and write `IndexNumber`/`ParentIndexNumber` yourself via DTO — never renumber to make the provider fit.
3. **Hard-link** (`ln`) the source files in rather than copying across a filesystem.
4. Refresh the series library, wait ~45 s, then verify all four: `ProviderIds.Tmdb`, `ProviderIds.Imdb` (add it from `/tv/<id>/external_ids` if the scan misses it), a Primary image tag, and Overview > 50 chars.
   **A refresh re-resolves KNOWN rows — it does not discover NEW files.** For freshly placed episodes the refresh returns zero rows and reads as a failed placement. Ping each path first: `POST /Library/Media/Updated` with `{"Updates":[{"Path":"/data/...","UpdateType":"Created"}]}` (204 = queued, not done), then poll `/Shows/{id}/Episodes` for a row whose `Path` matches. Confirm the queue position in the server log — `"<series>" ("<path>") will be refreshed` — and expect the backlog ahead of it to cost minutes (10 folders ≈ 4 min).
5. Set the display `Name` on the series and each episode explicitly — the scanner otherwise keeps the filename-derived name.
6. **Prove the series links its episodes** — a clean scan can still leave the Series item with `ChildCount=0` / `RecursiveItemCount=0`, so the card has nothing to navigate into and reads as a failed move. Check `GET /Users/<uid>/Items/<seriesId>` and, if it is 0, force `POST /Items/{seriesId}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh`. (Detail: `jellyfin-administration` → `references/series-structure.md`.)
7. Only then delete the old Music Docos rows.

### Episodic series: the ghost-slot rule (proven on Classic Albums)

A provider-driven scan creates a row for **every** episode the provider lists, including ones you hold no file for — Classic Albums had 90 rows and 14 files, with seasons 5, 7, 9 and 11–17 holding no media at all. Those ghosts are harmless to browse but they change how a placement behaves:

- **Check the DISK before choosing a slot — the library's numbering is not TMDB's.** Listing the target season first is mandatory. Proven: placing Deep Purple *Machine Head* at TMDB's `S04E03` and Def Leppard at `S04E01` put Def Leppard on top of an existing `S04E01 The Sex Pistols` file (the library numbered Sex Pistols as E01, TMDB calls it E02), so two files claimed one episode and one vanished from the UI. Fix is to lay the season out from what is already on disk (Sex Pistols E01, Dark Side E04, ghosts for the rest), put new files in the free slots, then write Name/Overview/IndexNumber yourself and lock them. Grep the season directory for its existing `SxxEyy` stems — never trust a provider's episode number alone.
- **A placed file does NOT bind to its ghost slot.** The scan purges the ghost and creates a NEW unnumbered row named after the file, so `Name` is the bare series title and `IndexNumber`/`ParentIndexNumber` are `null`. Set all four (`Name`, `Overview`, `IndexNumber`, `ParentIndexNumber`) via DTO and lock `Name`+`Overview`, or a later refresh puts the generic name back.
- **When the newcomer's number is already taken, de-collide instead of overwriting.** A release-named episode (broadcast order) can claim an `SxxEyy` the library already gave a different episode under another ordering (proven: a release called S09E03 landing on a library S09E03 of a different title). Do not renumber either row — strip the newcomer's episode number so it stands as its own unnumbered row, refresh, then identify it individually.
- **Name episodes by their subject, not the series.** `Name = "<Artist>: <Album>"` and open the overview with the book identity ("Classic Albums: the making of X's 1976 album Y.") — a library of rows all called "Classic Albums" cannot be browsed or searched.
- **`1of2` postings are half a programme.** Parse the part marker before queueing or placing; ingest part 1 alone only with the part explicit in the title.
- Same-series numbering differs by source: Classic Albums on TMDB (id 9048) stops at season 10, so seasons 14, 17 and 18 exist only in the library — take the year↔season mapping from the Wikipedia episode table (one row per year) and write the numbers yourself. S18 = the 2021 run (Who Sell Out, Soul II Soul, Suede, Gil Scott-Heron, Morrison Hotel, Freak Out); S17 = 2020; S14 = 2017.
- The six Dutch-language episodes (Focus, Herman Brood, De Dijk, Shocking Blue, Bettie Serveert, Boudewijn de Groot, Cuby + Blizzards) are a **separate VPRO run (2011–12)**, not Eagle Rock — which is why no indexer returns them as `Classic Albums` video; every hit for those titles is the music album on FLAC/MP3/DVD-Audio.
- **Ten Eagle Rock episodes have no release anywhere** (verified across torrent indexers plus all 73 Usenet postings): Carly Simon *No Secrets*, Gil Scott-Heron *Pieces of a Man*, Judas Priest *British Steel*, Simply Red *Stars*, Queen *A Night at the Opera*, Grateful Dead *Anthem to Beauty*, Deep Purple *Machine Head*, Iron Maiden *Number of the Beast*, Stevie Wonder *Songs in the Key of Life*, Paul Simon *Graceland* — treat a re-hunt as wasted effort unless a new source appears.

## Deduping (remove Music Docos references to Series content)

Before deleting anything, prove the Series holds the **best** version of each file with `ffprobe`: compare `codec_name`, `width×height` and video `bit_rate` per pair — not file size, not the filename. A larger AVI can still be the worse copy; `mpeg4` (MPEG-4 Part 2) at 608×416 loses to `h264` 720×540. If the Music Docos copy is better, upgrade the Series copy first.

Delete order matters: **verify a separate copy exists first** (compare inodes). `DELETE /Items/{id}` unlinks the file, and for a folder-shaped item it can take the whole folder with it.

## Pitfalls

- **A concat script must exclude its own previous output from the part list.** A re-run (`--apply`, or a retry) finds the old output in the folder and ingests it as a bonus "part": the doubled total is self-consistent, so a duration gate passes, and a deleting apply then removes everything including the output — the only copies destroyed. Filter `names != out` before ordering, and pin it with a test; the gate cannot catch this class of error by construction.
- **Classify a SAB failure before re-queueing.** `retry=True` with "RAR files failed to verify" usually clears on a second pass — retry it. "Repair failed, not enough repair blocks" never clears no matter how often it is retried; that title needs an alternate release (different group/indexer), not another attempt on the same NZB.
- **NFO-only folders are husks, not holdings.** Folders containing `.nfo`/`.jpg`/`.txt` but no video mean the media never landed or already moved — verify zero video files, remove the folder, and let the rescan clear the row. Do not treat them as missing episodes to hunt; log them as gaps only if no copy exists anywhere else.
- **Pagination bites.** `Limit=100` silently truncates the recursive item list — a path filter then matches nothing and you wrongly conclude "no items". Page with `Limit=500` + `StartIndex`, or loop until a short page.
- **Counts and verification come from paginated `ParentId` listings, never `searchTerm`.** Search returns a relevance-ranked index subset (a series with 500 episode rows can return 3 hits), so a search-based recount fabricates both
  "all done" and "hundreds missing". Enumerate with `ParentId` (+`Recursive`, per-series for episodes) and reconcile against the series row's `ChildCount`/`RecursiveItemCount` before reporting.
- **Token matching produces false positives.** Generic shared words (music, story, rock, a year, a month) pair unrelated titles — "Johnny Cash: American Rebel" is not a *Later... with Jools Holland* episode that happens to feature Johnny Cash. Confirm with a literal phrase search on the series name, an exact byte-size match, and only then ffprobe.
- An AVI container can hold h264; check the codec, not the extension.
- **Never call a multipart title "missing parts" from a path listing.** A correctly stacked item reports `MediaSources` = 1 (the first part only) while `PartCount` = N. Compare `PartCount` against the folder's file count; a path-based audit produces false "unindexed" hits on every healthy stack.

## Artwork rules (learned Oct 2026)

- **Internet images first, always.** Source order: TMDB poster/backdrop (when a provider ID exists) → BBC programme page art (`ichef` 1280x720) → Wikimedia/official portraits → YouTube thumbnails. `ffmpeg` frame grabs are a last resort only, and must be revisited. **A frame grab must write its output INTO the mounted media volume** (`/mnt/zmedia/...jpg`), never `/tmp`: ffmpeg runs in a throwaway container, so a `/tmp` file exists inside that container and is gone by the time the CT shell tries to `base64` it — the command silently returns empty and reads as a codec failure. Grab at ~900 s (600/1500 s fallbacks) and `rm` the temp file in the same shell chain.
- **File naming that Jellyfin actually reads.** For loose movies the recognised art file is `<moviefilename>.jpg` (same stem as the video). To change a row's poster, overwrite the `<stem>.jpg` and refresh with `ImageRefreshMode=FullRefresh&ReplaceAllImages=true`, then prove it with an md5 of `GET /Items/{id}/Images/Primary` against the file. Direct upload also works but only with a Base64-encoded body (`base64.b64encode(data)`, Content-Type image/jpeg) — raw bytes fail with a 500 from the server's Base64 decoder, which reads as a broken endpoint when it is an encoding mistake.
- **Backdrops are standard on every ingest.** Save the top TMDB backdrop as `backdrop.jpg` beside the poster; backfill recent ingests the same way. Clients paint screens from whatever backdrops exist, so one stray backdrop dominates the whole library — check every library root for a loose `fanart.jpg` (it is adopted as the *library's* backdrop) and delete any that is not deliberate.
- **Person rows get provider matches, not just names.** After setting cast on a film, resolve each name through TMDB person search and disambiguate same-name hits by reading the biography (a real bio beats an empty shell — lower id is the tiebreak only when both are empty). Write the Tmdb id onto each Person row via DTO, then run the Refresh People scheduled task — bios and headshots arrive from the provider; verify with an Overview-length recount. Where TMDB holds only empty shells, hand-write a one-line bio from the subject's Wikipedia page and upload a portrait (Base64 body, same as posters). For multi-title passes, follow `references/people-enrichment.md` (title-match gate, pin ids on Person rows not credits, Wikipedia exact-title fallback, never-clobber), and use **`cli/bio.py`** — the single entry point that replaced the old `scripts/bio-worklist.py` → `bio-run.py` → `alias-run.py` sweep scripts (they re-implemented the provider chain and went untested; the approved alias pairs now live in `state/alias-approved.json` as data, not code):

| Command | Does |
|---|---|
| `python3 -m cli.bio worklist [--limit N]` | read-only gap list (ready / thin / suspect / gap / no-row) |
| `python3 -m cli.bio fill [--ids 1,4] [--limit N]` | write planned bios — never-clobber, locks `Name`+`Overview`, journals; **`suspect` is never applied** |
| `python3 -m cli.bio alias [--file state/alias-approved.json]` | write user-approved alias bios (prose only for band pages) |
| `python3 -m cli.bio portrait --id <personId> --url <img>` | attach a portrait to an imageless row (rejects SVG, <15 KB placeholders; byte-verifies) |
| `python3 -m cli.bio wikidata --names "A,B"` | honest one-liners where no prose exists |

**`ready` now means ready.** `chain.collision_reason()` screens candidates at plan time and demotes them to `suspect`, which is shown but never auto-applied: Last.fm's numbered sense lists ("There is more than one artist with the name Chris White: 1)…" — 530 chars that used to score ready), Wikipedia disambiguation intros ("…may refer to: bishop, priest, horticulturist"), prose from a musician-only provider on a crew credit, and a crew credit carrying an unrelated life (TMDB answered a Director with "George Scott was a Canadian professional wrestler", 1100 chars, scored ready — caught now only in a *defining* sentence, because a keyword scan also flags a director whose film "followed a retired boxer"). Tests pin each real string in `tests/test_bio_guard.py`. **Known blind spot, deliberately not "fixed":** a coherent biography of the wrong person (Last.fm's "Peter Grant … British singer … debut album New Vintage" for Led Zeppelin's late manager) has no textual tell — it still needs the identity gate and a human read, and a test asserts it stays visible rather than silently passing.
- **Review sweep state in ONE searchable HTML widget** for the chat — never by opening films one at a time. That is the *scanning* shape; **decisions** on individual rows are a clickable one-at-a-time `clarify` flow (see "Presenting decisions to the user").
- **A bio that EXISTS can still be WRONG — audit content, not presence.** An overview-length recount scores a locked wrong-life bio as complete (an 18-char "Welsh darts player" extract sat locked on the Chills frontman because the row was matched to a same-name actor). Treat any bio under ~80 chars, or one naming a discipline the credit contradicts, as a suspect: check the row's `Imdb` resolves to the same person as its `Tmdb` record before trusting either, then rewrite Name+Overview together and re-lock.
- **Screen wrong-person bios by the STATED BIRTH year, never by any year in the text.** Library subjects are modern, so a pre-1900 life sitting on a modern credit is a name collision — but match only birth shapes: `(born <date>, 19xx)`, `(18xx – 19yy)`, `(c. 1560 – …)`, `born in 19xx`. Any-year matching misfires constantly: a 1953-born actor's bio mentions an unrelated `1600`, a founding father's `1789` is the year he took office, Shakespeare's `1616` is his death. Rows where no birth shape matches are **undetected, not modern** — bucket them and read them, never count them as clean. Source-authorship and archive credits are CORRECT bios (Shakespeare on *Romeo + Juliet*, Grimm on *Tangled*, a president as archive footage); only a modern crew/profession credit carrying a 19th-century life is a collision. Method + repair protocol: `references/people-enrichment.md` → "Pre-1900 collision audit".
- **Correcting a wrong bio is the one licensed exception to never-clobber, and it needs a signature guard.** Re-read the row and refuse the write unless the current Overview still contains the flagged signature (the collision's year) — that stops a stale plan overwriting a row already fixed. Journal old + new text per repair, and poll the read-back (0/1/2/3 s) before declaring failure: Jellyfin's GET lags a successful write, so a single read reports FAILED on a write that landed.
- **Launch long sweeps with `persist_on_release=true`.** A background worker launched without it is killed by a session reset (`agent_close`) mid-run; a pass killed before its first write loses the whole run, and the log shows only the resume header as evidence. Pair it with notify-on-complete so the result is not polled for.

## Presenting decisions to the user (standing preference)

A choose-between-many-rows decision is a **clickable, one-at-a-time** flow — never a wall of text:

- Use the `clarify` tool: one question per person, ≤5 per call, each quoting the **actual current text** (the bio verbatim, trimmed) plus the credit that makes it suspect, choices ordered with the recommendation first (e.g. "Clear (wrong person)" / "Keep as is" / "Leave for later").
- Never ask for typed `[n CLEAR]`-style codes, and never present a 40-row table whose row numbers the user must transcribe. The user reviews on a **phone**, cannot open files, and will not type row IDs.
- Files (`state/*.json`, `*.md`) are the secondary record for auditability, not the presentation.

## Hand metadata: API writes beat NFOs on matched rows

- **Metadata-only passes never touch images, bios, or series structure (standing user orders).** Refresh with `ImageRefreshMode=None` + both ReplaceAll flags false; never POST to Person rows; refresh a Series with `Recursive=false` so episodes are untouched and the row stays a Series. A series keeps its type and its children — restructuring is a separate approved task, never a side effect of filling overviews.
- Local episode NFOs are IGNORED on provider-matched series rows (rows carrying Tvdb/TMDB ids keep provider names even after FullRefresh+ReplaceAll). Set Name/Overview/PremiereDate/IndexNumber via `POST /Items/{id}` with the full item body and `LockedFields: ["Name", "Overview"]` — locks survive later refreshes.
- NEVER run series-level `ReplaceAllMetadata=true` on a hand-built series: it re-fetches provider data and CLOBBERS episode names/overviews back to the (often wrong) upstream values. Use image-only refreshes (`ImageRefreshMode=FullRefresh`, no metadata replace) for artwork.
- Every version file needs its own same-stem `.nfo` — Jellyfin associates an NFO with one basename, so a ` - 720p` copy without its own NFO falls back to provider data.

## Post-ingest download sweep (standing user order)

Once a download is verified on the shelf (probed, decoded, row confirmed with overview+art), its download payload is deleted: remove the torrent **with files** (`deleteFiles=true`), then re-list the downloads dir to prove it is gone and check pool free space after. Rescue sidecars first (e.g. a `.srt` the ingest copy step skipped). Skip anything still encoding, still downloading, or music-domain (another maintainer's). Never delete a payload whose library row is unverified — the row check comes before the delete, not after.

**The sweep must cover three separate sinks, not just the filesystem** (proven 18 Oct 2026 — the user had to ask four times why "digested" titles were still in downloads):
1. **qBittorrent entries.** A payload can be gone from disk while the torrent entry lingers "seeding" nothing — a **ghost seeder**: `progress: 1.0`, `state` `forcedUP`/`stoppedUP`, but `os.path.exists()` on every `torrents/files` path returns `False`. The UI still shows the title as an active torrent, which is what the user sees as "still in downloads". Sweep `/api/v2/torrents/info` for `progress >= 0.999` **video** entries, cross-check each title against Jellyfin (`SearchTerm`, and cross-check the file exists on disk), and `POST /torrents/delete` with `deleteFiles=true` for the verified-digested ones. This is NOT covered by checking the filesystem — that's why filesystem-only sweeps kept reporting "clean" while the user kept seeing titles.
2. **SABnzbd `incomplete/` and `complete/` dirs.** **NEVER DELETE PARTIALS — standing user rule (18 Oct 2026).** Failed/stalled NZB jobs leave partial payloads whose titles are already in the library; those partials look deletable but are NOT — they are resumable (a retry continues from them) and are often UPGRADE copies in flight (better resolution than the placeholder on the shelf). I deleted 13 GB of them unilaterally and was corrected. The sweep may clean only COMPLETED + digested payloads. Live downloads and anything with no library row (a genuine gap) are always kept.
3. **`_FAILED_*` marker dirs in `complete/`.** Leftovers of failed jobs; usually empty `rmdir`-able markers, but check size and title first.

**Do not delete on a long search-term miss.** A Jellyfin `SearchTerm` with many words can return 0 for a title that IS in the library (the search is token-strict); re-probe with a short distinctive phrase ("Wattstax", "Quincy Jones") before declaring a title absent.

**When the user asks "what partials are there / compare to what's on disk", produce a
read-only table — never delete from it.** This is the report that keeps partials safe:
one row per in-progress download with (title, `%`, downloaded/total size, seed count)
next to (library copy size + resolution, verdict). The verdict column is the point —
it classifies each partial as **upgrade in flight** (partial is a better copy than the
shelved one — e.g. a 1080p fetch against a 640p placeholder), **completing a series**
(missing episodes), or **genuine gap** (not in the library at all). Presenting it this
way shows the user *why* a partial is worth keeping without touching anything, and it
is the form they have asked for. Filter music/audio rows out of the table (domain
boundary, below) rather than showing them "for completeness".

**The domain boundary — this agent owns VIDEO, never AUDIO.** "Music Docos" is a *video* library (films, concert DVDs, documentaries, however musical the subject). Music *audio* — albums, EPs, compilations, artist discographies, FLAC/MP3/AC3 releases, and the artists' own recordings — belongs to the music maintainer, and so does every torrent, SAB job, and download folder for that audio. **Leave music alone: do not list, triage, re-announce, re-source, force-start, or delete audio items**, even when they share the qBittorrent client or sit beside video partials in the same queue. A queue sweep reports video only; audio rows are skipped silently rather than shown "for completeness". Seeing an audio title in the same client is not licence to manage it.

## Verify before ingest (integrity gate)

- Probe every download before it touches the library: `ffprobe` streams + 3-sample mid-file decode (`ffmpeg -ss <5/50/90%> -t 5 -f null -`). A full VP9/Opus batch from 2024 carried missing reference frames and Opus packet errors at every sample — containers report sane durations while the streams are unplayable in spots.
- Quarantine failures in `_replaced/` (labelled CORRUPT) and re-source; never ingest-then-check. Prefer AVC/AAC YouTube formats where available — less fragile in transit than VP9/Opus.

## Verification checklist (series moves)

- Both item counts before/after — Music Docos should drop by exactly the number removed.
- Series file count and sizes unchanged.
- Cross-library byte-size duplicate re-check returns 0.
- No dangling rows: every remaining Music Docos path still exists on disk.
