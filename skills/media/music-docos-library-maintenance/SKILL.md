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

See lessons 2 (partial DTO wipes), 3 (verify locks by direct GET), and 43
(`lock_item()` is the only way to set locks) — each cost live damage.

"Bios" is a people-enrichment job, not an item edit: bios live on Person records, the item only
carries credits. Identify series/film rows with `jellyfin.remote_search` /
`apply_identification` (lesson 7: media-type route, module auth helper, generic-Video credit trap).

### Source fallback order (see lessons 33, 36)

TMDB miss starts the hunt: TVmaze (keyless episode lists) -> BBC pid.json
(two pid shapes, HTML fallback) -> `media_lib/resolve.py` full chain with
provenance. BBC never overrides an ambiguous TMDB hit; programme search is
bot-walled from the box, so PID discovery is agent-side web search.

## Never stop at the first source that says nothing

See lesson 36: work the whole `resolve.py` chain with per-field provenance before calling a row unidentified.

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
- **Episode slots must match symmetrically — a release's `S01E02` is a folder label's `E02`.** Title comparison that strips episode markers on one side only refuses every episodic NEW placement as `different-title`. Compare slot-to-slot with a bare `E##` counting as season-unknown (wildcard), and refuse only on genuinely different episode numbers.


## The two libraries

| Library | Type | Root |
|---|---|---|
| Music Docos | movies/videos | `/data/musicdocos` |
| Music Docos Series | tvshows | `/data/musicdocos-series` |

Separate roots — so the same content can legitimately exist in both, which is what produces duplicates.

Jellyfin: `http://192.168.214.20:8096`, key `JELLYFIN_API_KEY` in `/root/.hermes/.env`. Media host is proxmox3 (`root@192.168.214.131`); library roots are `/mnt/zmedia/videos/musicdocos`, `/mnt/zmedia/videos/musicdocos-series` (Jellyfin `/data/<lib>` maps 1:1).

> **Topology caveat:** that proxmox3 view is stale for everything except `musicdocos`. Library and download file operations must run through CT105 on prox2 — see `jellyfin-administration` → "TOPOLOGY CORRECTION".

## Current tooling — `media-ops` (reach for this before writing a new script)

`/root/.hermes/profiles/media-maintainer/media-ops` is a tested package with git history and 449 pytest tests. Prefer it over hand-rolling another sweep:

| Need | Use |
|---|---|
| Find/replace higher-res downloads | `python3 -m cli.scan` → `cli.plan` → `cli.apply --ids 1,3` (id-gated, journaled, lockfile) |
| **Hunt a missing episode/album** | `python3 -m cli.source plan --targets targets.json` → `cli.source queue --ids N` (plugin verbs `media_source_plan` / `media_source_queue`). Broad pool → normalised match → ranked; `1ofN` sets are refused unless `--allow-incomplete` |
| 4-field exit gate | `python3 -m cli.verify --ids <itemIds>` |
| Place a completed download as a NEW library item | `python3 -m cli.ingest_new --spec <json> --refresh-lib <id> [--wait-rows 200]` — copy → size → probe → min-height → duration → 3-span decode; a failed gate deletes the staged copy so a half-good file never lands. It now fires `/Library/Media/Updated` itself, so the row appears without a manual ping. `--from-plan <needs-your-check.json> --ids N,M --library '<name>'` builds the spec from approved NEW rows (film path only — episodic rows are refused because the slot needs a disk check, so hand-route those). `cli.plan`/`media_plan` is **REPLACE-only** by design, so genuinely NEW episodes always fall to its check list; do not read that as a rejection. |
| Bios/portraits from providers | `media_lib.providers.chain.resolve(name, kind)` or `cli.meta plan` → `cli.meta apply` |
| Identify a series/film row (replaces inline heredocs) | `python3 scripts/identify_series.py --library 'Docos Series' --frag tmdbid-123 --title 'Name' --year 2020 --tmdb-id 123` / `scripts/identify_movie.py` — find-by-path, pinned-id apply, read-back verify, exit 0 only on id match |
| Discover series-shaped sets sitting in a flat library | `ssh <media-host> 'find <root> -type f ...' | python3 scripts/scan_series_shapes.py --from-stdin` — groups SxxEyy/part/ep/NofM filenames by stripped title, TMDB-matches each group, prints proposal JSON (read-only, moves nothing) |
| Hunt the watchlist (read-only sweep) | `python3 scripts/wanted.py` — runs every `state/wanted.json` entry through `hunt.plan`, prints ranked candidates, queues nothing |
| Fleet health (read-only) | `python3 -c "from media_lib import fleet; print(fleet.render(fleet.check()))"` — one ok/FAIL row per service; red rows are human decisions, never auto-repairs |
| Enrichment gap report | `python3 -m cli.meta plan` (read-only: ready / thin / gap / no-row counts) |
| Series 4-field audit (read-only shopping list) | `python3 scripts/audit_series.py --library 'Docos Series'` — flags `no-ids` (only when NEITHER Tmdb nor Imdb exists — an Imdb-only UK strand is identified, not a gap), `ov<N>c`, `no-img` per Series row; enrichment stays explicit per row |
| Episode audit (read-only, counts-first) | `python3 scripts/audit_episodes.py --library 'Music Docos Series' [--detail N] [--out path]` — rows WITHOUT a `Path` are virtual TMDB placeholder slots, reported as `virtual` and never counted as gaps; prints `N with media, M virtual slots ignored` plus flag combos, detail to JSON |
| Pin episode ids by name (bulk, dry-run default) | `python3 scripts/pin_episode_ids.py --library 'Music Docos Series' --all [--apply] [--with-overview]` — matches library episodes to TMDB episode titles on distinctive words, ties refused, never by episode number; one summary line per series |
| Prune finished torrents that duplicate the shelf | `python3 scripts/prune_lateral_dupes.py [--apply --ids <hashes>]` (dry-run default) | `tests/test_prune_dupes.py` | triple proof per torrent (shelf title match, no-upgrade on res tag, held copy probes healthy right now); multi-file and unmatched rows always skip; `--apply` requires explicit ids, no bulk switch |
| Resolve an unidentified title across every source | `python3 -c "from media_lib import resolve; print(resolve.resolve_series('<title>', '<year>'))"` — TMDB → TVmaze (+externals IMDb) → BBC pid catalogue → local nfo → search-engine IMDb discovery (verified) with per-field provenance; `missing_fields()` lists what is still open |
| A/B a sample against the library | `python3 -m scripts.meta_test_10 --seed N --sample 10` |

Agent-side tools (when the plugins are loaded): `media_scan`, `media_plan`, `media_apply`, `media_verify`, `media_source_plan`, `media_source_queue`, `meta_search`, `meta_resolve`. The `meta_apply` write path lives in `cli.meta apply`, not yet as a tool.

**One rule for all sourcing: never write a hunt script.** Pure search/filter/rank lives in `media_lib/hunt.py` (`plan()` ranks candidates read-only with token-set title matching, `queue()` takes ONE explicit candidate), and the queueing path stays `media_lib/source.py`. Four hand-rolled hunt scripts were deleted because duplicating that logic produced three separate bugs in a single session (narrow-query zeros, dotted-name mismatches, blind seed trust).

**Promote a repeated inline flow to a tested script instead of re-typing it.** A heredoc run three times is a script that lacks tests: `identify_series.py` / `identify_movie.py` / `scan_series_shapes.py` all began as inline snippets and earned `--help`, `main()`-wrapping, fixture tests and a skill-table row. When the user asks whether a script is ad hoc or suite, the answer must be suite with tests — or say plainly that it is ad hoc and why it cannot go through the suite yet.

**Verify external review citations against HEAD before implementing.** A cited file/line/function may describe code fixed days ago — the reviewer's clone goes stale, not the tree. Check each item against the live files first, implement only what is genuinely missing, and answer with a per-item verdict table (fixed-where / adopted / rejected-with-evidence / deferred-with-reason) so the re-review starts from what's actually new.

## Newer suite paths (see lessons 40-43)

- Aspect-flag audit + lossless SAR repair: `scripts/scan_aspect_flags.py` → `scripts/fix_aspect_remux.py`
  (lesson 40); ingest warns `ASPECT-FLAG` at placement via `media_lib/aspect.py`.
- Tdarr integration is suite-queues-per-file: `scripts/tdarr_queue.py --flow audio|sar|legacy
  --files ... [--apply]` (lesson 42); flows stay unlinked; lesson 41 governs the after-side.
- Swap metadata checklist: snapshot → swap → direct-listing wait → re-apply with LockData
  via `lock_item()` → direct-read verify (lessons 41, 43).

## Adding a series to Music Docos Series

1. Find the TMDB TV id first (`/search/tv`) — the library matches from the **folder name**, so encode it:
   `Name (Year) [tmdbid-<id>]/Season 1/Name (Year) - S01E01 - <Episode Title>.ext`
   Search BOTH namespaces (`/search/movie` + `/search/tv`) and read the overview before pinning an id — a same-name film can carry the same year as the wanted series, so title+year never disambiguates alone; the overview text is the verdict.
2. Get real episode titles from `/tv/<id>/season/1` — do not invent them. **Provider coverage is partial**: a series running 30 years can be listed only to season 10 while the library holds season 18, and the missing season 404s. Where a season is absent, take the SxxEyy↔year mapping from the Wikipedia episode table (it lists the season as one row per year) and write `IndexNumber`/`ParentIndexNumber` yourself via DTO — never renumber to make the provider fit.
3. **Hard-link** (`ln`) the source files in rather than copying across a filesystem.
4. Refresh the series library, wait ~45 s, then verify all four: `ProviderIds.Tmdb`, `ProviderIds.Imdb` (add it from `/tv/<id>/external_ids` if the scan misses it), a Primary image tag, and Overview > 50 chars.
   **No series-level id does not mean no ids at all.** An anthology/trilogy row with no TMDB *series* entry can still carry exact *film-level* ids on its episode rows — verify each part through TMDB movie `external_ids` (never from memory) and pin `Imdb` per episode before declaring "no ids by design".
   **A refresh re-resolves KNOWN rows — it does not discover NEW files.** For freshly placed episodes the refresh returns zero rows and reads as a failed placement. Ping each path first: `POST /Library/Media/Updated` with `{"Updates":[{"Path":"/data/...","UpdateType":"Created"}]}` (204 = queued, not done), then poll `/Shows/{id}/Episodes` for a row whose `Path` matches. Confirm the queue position in the server log — `"<series>" ("<path>") will be refreshed` — and expect the backlog ahead of it to cost minutes (10 folders ≈ 4 min).
   **Loose files at a library root are invisible to the scanner.** Four Storyville films sat at `/data/documentaries/` root through a touch, two library refreshes, a notify and 20 minutes of settling — zero rows. Moved into proper `Title (Year)/` movie folders + notify, all four surfaced already self-identified. The CIFS directory listing the server scans is cached; new subfolder entries register, root-level additions do not. Always place films as `Title (Year)/Title (Year).ext`, never loose at the root.
5. Set the display `Name` on the series and each episode explicitly — the scanner otherwise keeps the filename-derived name.
6. **Prove the series links its episodes** — a clean scan can still leave the Series item with `ChildCount=0` / `RecursiveItemCount=0`, so the card has nothing to navigate into and reads as a failed move. Check `GET /Users/<uid>/Items/<seriesId>` and, if it is 0, force `POST /Items/{seriesId}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh`. (Detail: `jellyfin-administration` → `references/series-structure.md`.)
7. Only then delete the old Music Docos rows.

### Episodic series: the ghost-slot rule (proven on Classic Albums)

A provider-driven scan creates a row for **every** episode the provider lists, including ones you hold no file for — Classic Albums had 90 rows and 14 files, with seasons 5, 7, 9 and 11–17 holding no media at all. Those ghosts are harmless to browse but they change how a placement behaves:

- **Check the DISK before choosing a slot — the library's numbering is not TMDB's.** Listing the target season first is mandatory. Proven: placing Deep Purple *Machine Head* at TMDB's `S04E03` and Def Leppard at `S04E01` put Def Leppard on top of an existing `S04E01 The Sex Pistols` file (the library numbered Sex Pistols as E01, TMDB calls it E02), so two files claimed one episode and one vanished from the UI. Fix is to lay the season out from what is already on disk (Sex Pistols E01, Dark Side E04, ghosts for the rest), put new files in the free slots, then write Name/Overview/IndexNumber yourself and lock them. Grep the season directory for its existing `SxxEyy` stems — never trust a provider's episode number alone. Enforced in code: `media_lib/slot_check.py` makes `place_new` refuse with `SLOT-COLLISION` before a byte copies when a different-stemmed file already holds the slot.
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

- **Provider name matching must squash punctuation, and the QUERY must be a substring of the provider name.** Two failures in one conversion batch: the query `Oh You Pretty Things The Story of British Music` never matched TMDB's `Oh! You Pretty Things: …` because the normaliser stripped spaces/dots/dashes/colons but not `!` (squash to alphanumerics instead), and lengthening a query to "help" it does the opposite — `multipart_to_series` requires `_norm(KEY) in _norm(provider_name)`, so the query must be SHORTER than or equal to the provider title. A file count that differs from the provider's episode count is a partial set: pass `ALLOW_PARTIAL=1` only when the runtimes corroborate the held parts, never to force a count to match.
- **A batch script must exclude its own output from its input glob.** Any script that scans a folder for inputs and writes its output into the same folder will, on re-run, ingest the previous output as a bonus input — proven on concat (`--apply` found the old output as a bonus "part", the doubled total verified against itself, and the deleting apply destroyed the only copies). Filter `names != out` before ordering, pin it with a test, and treat every same-folder input glob as suspect until it carries the exclusion; a self-consistent gate cannot catch this class of error by construction.
- **Classify a SAB failure before re-queueing.** `retry=True` with "RAR files failed to verify" usually clears on a second pass — retry it. "Repair failed, not enough repair blocks" never clears no matter how often it is retried; that title needs an alternate release (different group/indexer), not another attempt on the same NZB.
- **NFO-only folders are husks, not holdings.** Folders containing `.nfo`/`.jpg`/`.txt` but no video mean the media never landed or already moved — verify zero video files, remove the folder, and let the rescan clear the row. Do not treat them as missing episodes to hunt; log them as gaps only if no copy exists anywhere else.
- **Pagination bites.** `Limit=100` silently truncates the recursive item list — a path filter then matches nothing and you wrongly conclude "no items". Page with `Limit=500` + `StartIndex`, or loop until a short page.
- **Counts and verification come from paginated `ParentId` listings, never `searchTerm`.** Search returns a relevance-ranked index subset (a series with 500 episode rows can return 3 hits), so a search-based recount fabricates both
  "all done" and "hundreds missing". Enumerate with `ParentId` (+`Recursive`, per-series for episodes) and reconcile against the series row's `ChildCount`/`RecursiveItemCount` before reporting.
- **Rows without a `Path` are virtual TMDB slots, not missing media.** Jellyfin materialises an empty row for every episode the provider lists, so a 326-file library reported 876 rows and most of them read as imageless gaps. Always filter `Path`-less rows out of gap counts (`scripts/audit_episodes.py` returns them as `virtual`), and report "N with media, M virtual slots ignored" — never the raw row count.
- **Query one `IncludeItemTypes` value per call.** A comma-joined `"Episode,Movie,Video"` string silently returns extra item kinds (seasons/folders) — a 109-episode strand reported as 1,011 rows. Loop one type at a time, and sanity-check any count against a known figure (the series row's `RecursiveItemCount`) before reporting it.
- **Join hyphenated single letters before tokenising.** `tokens()` must map `T-Men` → `tmen`, never drop the `T` and leave `men` — the dropped letter collapsed two distinct titles onto one identical token and scored a 1.0 MATCH that queued a film over a same-named TV episode (only a duration gate refused). The same fix improves every `X-Men`-class title.
- **Title gates compare stems, not filenames, with headroom for release descriptors.** The `.mkv` extension counted as an unmatched "word", and a hunt-tuned extras budget refused genuine same-file placements as `different-title`. Hunts keep the strict budget; the ingest same-file check compares the stem and allows room for group/edition tags.
- **Token matching produces false positives.** Generic shared words (music, story, rock, a year, a month) pair unrelated titles — "Johnny Cash: American Rebel" is not a *Later... with Jools Holland* episode that happens to feature Johnny Cash. Confirm with a literal phrase search on the series name, an exact byte-size match, and only then ffprobe.
- An AVI container can hold h264; check the codec, not the extension.
- **Two rows sharing one display Name on different Paths is a misidentification, not a duplicate.** A wrong-name write can land the same title on two unrelated files — deleting either as a "dupe" destroys a unique programme. Read both Paths, resolve each file's true identity from filename and providers, and rename; nothing gets deleted.
- **A single long file with chapter marks at episode boundaries is the whole series joined, not a one-episode holding.** Check `ffprobe` chapters before declaring episodes missing or hunting the "rest" — evenly spaced ~59-minute chapters mean the set is complete in one file, and splitting it needs an explicit user yes.
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
| `python3 -m cli.bio portrait --id <personId> --url <img>` | attach a portrait to an imageless row (rejects SVG, <15 KB placeholders; verifies by `ImageTags.Primary` present + served image non-empty and plausibly sized, never by exact byte equality — Jellyfin re-encodes uploads so served bytes are larger than sent) |
| `python3 -m cli.bio portrait-search --names "A,B"` | read-only exact Commons portrait URLs via Wikidata P18 (editor-curated, no search gamble); attaching stays approval-gated |
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
- Big sweeps report counts-first: flag combos and worst-series tallies in chat (hundreds of tokens), full row detail to a JSON file for drilling in — never a thousands-line dump.

## Hand metadata: API writes beat NFOs on matched rows

- **Metadata-only passes never touch images, bios, or series structure (standing user orders).** Refresh with `ImageRefreshMode=None` + both ReplaceAll flags false; never POST to Person rows; refresh a Series with `Recursive=false` so episodes are untouched and the row stays a Series. A series keeps its type and its children — restructuring is a separate approved task, never a side effect of filling overviews.
- Local episode NFOs are IGNORED on provider-matched series rows (rows carrying Tvdb/TMDB ids keep provider names even after FullRefresh+ReplaceAll). Set Name/Overview/PremiereDate/IndexNumber via `POST /Items/{id}` with the full item body and `LockedFields: ["Name", "Overview"]` — locks survive later refreshes.
- NEVER run series-level `ReplaceAllMetadata=true` on a hand-built series: it re-fetches provider data and CLOBBERS episode names/overviews back to the (often wrong) upstream values. Use image-only refreshes (`ImageRefreshMode=FullRefresh`, no metadata replace) for artwork.
- Every version file needs its own same-stem `.nfo` — Jellyfin associates an NFO with one basename, so a ` - 720p` copy without its own NFO falls back to provider data.
- **Write a locked sidecar whenever you repair metadata out-of-band.** Repaired `Name`/`Overview`/`ProviderIds` live only in Jellyfin's SQLite, and a Tdarr container swap or a folder move drops the DB row and recreates it — wiping every lock. `media_lib/nfo.py` (`episode_nfo` / `movie_nfo` / `write_sidecar`) emits `<lockdata>true</lockdata>` plus `<uniqueid>` beside the file via base64 over the ssh helper; `cli.ingest_new` writes one automatically after a name repair. Sidecars do not override provider-matched rows while those rows exist — they restore the right values when the row is rebuilt.

## Post-ingest download sweep (standing user order)

Once a download is verified on the shelf (probed, decoded, row confirmed with overview+art), its download payload is deleted: remove the torrent **with files** (`deleteFiles=true`), then re-list the downloads dir to prove it is gone and check pool free space after. Rescue sidecars first (e.g. a `.srt` the ingest copy step skipped). Skip anything still encoding, still downloading, or music-domain (another maintainer's). Never delete a payload whose library row is unverified — the row check comes before the delete, not after.

**The sweep must cover three separate sinks, not just the filesystem** (proven 18 Oct 2026 — the user had to ask four times why "digested" titles were still in downloads):
1. **qBittorrent entries.** A payload can be gone from disk while the torrent entry lingers "seeding" nothing — a **ghost seeder**: `progress: 1.0`, `state` `forcedUP`/`stoppedUP`, but `os.path.exists()` on every `torrents/files` path returns `False`. The UI still shows the title as an active torrent, which is what the user sees as "still in downloads". Sweep `/api/v2/torrents/info` for `progress >= 0.999` **video** entries, cross-check each title against Jellyfin (`SearchTerm`, and cross-check the file exists on disk), and `POST /torrents/delete` with `deleteFiles=true` for the verified-digested ones. This is NOT covered by checking the filesystem — that's why filesystem-only sweeps kept reporting "clean" while the user kept seeing titles. Prefer this entry audit over restarting the download host: a container reboot to clear cosmetic entries interrupts every active download, while the audit removes only verified-consumed shells.
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
