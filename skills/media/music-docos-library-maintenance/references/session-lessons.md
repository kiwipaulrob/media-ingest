# Durable lessons — media library maintenance

Hard-won rules from live operation. Each cost real damage or a false report. Read before touching
metadata, libraries or the ingest pipeline.

## 1. Use the pipeline; never hand-build a spec

`cli.scan` → `cli.plan` → `cli.ingest_new` / `cli.apply`. The planner classifies NEW / REPLACE /
review and runs the quality comparison. A spec assembled from `scan.json`'s `all_videos` bypassed it
and produced, in one pass: a duplicate (planner said REPLACE, spec said NEW), seven phantom
placements (paths already consumed by an earlier ingest), and release-junk folder names.

Existence-check every source before it enters a spec (`[-f "$p"]`). The scan is a snapshot.

## 2. Never write a partial metadata DTO

`POST /Items/{id}` replaces metadata wholesale. Posting `{"LockedFields": [...]}` alone stored
`Name` as null and blanked `Overview` on a live entry.

Correct protocol: GET the DTO fresh, change only the target fields, **always include `Name`**, omit
`Genres`, POST, then re-read and diff the fields you did not intend to change.

## 3. Verify locks by direct GET, never by search

Search-result DTOs omit `LockedFields` and read back null even when the lock is set — which produced
a false "locking is broken" report on a lock that had worked. Use
`GET /Users/{uid}/Items/{id}?Fields=LockedFields`.

## 4. A multi-source item needs minutes, not 90 seconds

Only a per-item FullRefresh repopulates runtime; a library refresh never does. With several media
sources the refresh takes longer than 90 s. Checking too early reported three failures that were
actually successes (hardlink test, Doors merge, Costello merge). Wait 2–3 minutes and look again.

## 5. Library names are not disk names

Jellyfin `/data/<lib>` → host `/mnt/zmedia/videos/<folder>`:

    musicdocos → musicdocos        musicdocos-series → musicdocos-series
    documentaries → Documentaries  docuseries → DocuSeries
    filmdocos → FilmDocumentaries  filmnoir → filmnoir
    movies → Films                 tvshows → unresolved, resolve before use

String substitution produced 12 failed moves. Find the file by name, or resolve from CT105's mount.

## 6. Membership in several libraries is a hardlink — judge by LINK COUNT

This share is CIFS and assigns a **different synthetic inode to every path**, even for one file. So
`%i` cannot detect a hardlink; comparing inodes called real hardlinks "two copies" and called the
reverse too.

Use `stat -c "%h links %s %n"` on both paths. `ln` and `ln -f` both work (a fresh pair goes to
links=2). Do not "de-duplicate" a hardlink — removing one name just removes that library entry.

## 7. Identify IS agentic — one route, one body shape

    jellyfin.remote_search(item_id, name, year, provider_ids)
    jellyfin.apply_identification(item_id, result, replace_images=False)

Route is the media type: `POST /Items/RemoteSearch/Movie`. Provider-named routes 404; bare search
info 500s; the body is `{"SearchInfo": {...}, "ItemId": ...}`. A hand-rolled `X-Emby-Token` header
401s where `jellyfin._request()` succeeds — use the module's helper.

**Credits on a generic Video item cannot be written directly**: POSTing `People`/`ProviderIds`
returns 204 and silently discards both. Identify is the only path that attaches them.

## 8. Bios are People records, not item text

Use `media_lib/enrich.py`: `build_plan(parent_id, limit, min_bio=80)` is read-only and lists credited
people with no bio; `apply_person(person_id, bio, source, lock=True)` writes one, locked. Chain is
TMDB credits → Wikipedia (exact title, reject disambiguation pages on content) → Last.fm for music
domain. `< 80` chars is a partial one-liner, not a failure.

## 9. An empty torrent folder is not an ingest candidate

Packs whose videos were already ingested leave only `.txt`, `.jpg` and `.nfo` residue. Check for
video files on disk before ingesting *or* deleting; the label may also lie (a torrent named S56
delivered S65).

## 10. `temp/` paths are valid only mid-download

qBittorrent moves a completed file out of `temp/`. A placement that cites the `temp/` path fails with
`INCOMPLETE-SOURCE` — correctly, because the path is gone. Re-check the content path after completion.

## 11. Deletion needs evidence, and so does keeping

Delete a source only after the library copy is verified (probe + decode + sync + row runtime), and
check byte size or checksum first. A guard that matched "John Lennon" (space) against a filename with
dots deleted the only source of a title that later turned out to be truncated.

For source-vs-series consolidation, hash the keepers first and delete only exact matches: collect the
keeper md5 set, then per candidate file delete iff its hash is in the set, printing kept-vs-removed per
file. Sizes alone are not identity — equal sizes with different hashes are different encodes, not dupes.

## 12. Probe before declaring a fault

`decode_check` failed playable 1080i HDTV captures because it treated any decoder output as
corruption, and `on_media()` raises on ffmpeg's non-zero exit after recovered warnings. It now uses
`query()` and ignores known-benign chatter (`mmco:`, `reference picture missing`) while still failing
on real errors. A warning is not a verdict.

## 13. Portrait uploads are re-encoded — byte equality is the wrong check

Jellyfin resizes and re-encodes an uploaded image, so the served bytes are LARGER than what you
sent (392,612 vs 294,457 on one, 338,152 vs 253,613 on another). A verify step that compares
`len(served) == len(uploaded)` therefore reports failure on a write that succeeded — 35 of 44 in one
pass, turning a real result of 35 portraits into a reported 0.

Verify a portrait by `ImageTags.Primary` being present on the person row and the served image being
non-empty and plausibly similar in size (±50%), never by exact equality.

Corollary: report counts from the server's state, not from your own exit paths. The log said
`written=0` while the library held 35 new portraits.

## 14. Probe availability before running a bulk pass

The same pass skipped 356 of 400 names because those Wikipedia articles carry no image at all. Ten
probes would have revealed that the cohort had nothing to find, before spending a 400-name run and
Wikimedia's IP-wide rate budget (a follow-up Wikidata call then returned 429).

## 15. Remote search needs the MEDIA TYPE route — Movie is not universal

`POST /Items/RemoteSearch/Movie` works for films and silently fails for series: it returns zero
candidates, or three unrelated titles from the same year. Supplying a correct TMDB id does NOT help,
because the endpoint matches on title/year and ignores the id.

    jellyfin.remote_search(item_id, name, year, provider_ids, media_type="Series")

Four series failed identification in one session before this was found (The West, The Ascent of Money,
Welcome to Lagos, and an earlier one); all identified immediately once `Series` was used. The same
applies to `Episode`, `Person`, `BoxSet` — match the route to the item type.

Symptom to recognise: series shows `ProviderIds {}`, no overview, no artwork, and every episode sits at
`SNoneENone` with 0.0 min. Episode numbering comes from a matched series, so an unmatched series leaves
its episodes as an unordered list.

## 16. `- partN` files are not joined into multi-part movies here

Naming two files `Title - part1.mkv` / `Title - part2.mkv` in one folder did NOT produce a single item
with two parts on this server (Easy Rider, Unforgivable Blackness). The item showed only part 1 at its
own runtime. Do not assume Jellyfin joins parts; verify the item's media-source count after a
restructure, and prefer the `cd1`/`cd2` convention or a single merged file if joining is required.

The version suffix must be the release's own label, never a coined one: renaming a file labelled
`Born to Be Wild` to `Broadcast Version` to fit the merge pattern misdescribes the content. Keep the
original wording after the dash.

## 17. `delete_item` deletes media, not just a library row

`jellyfin.delete_item()` calls `DELETE /Items/{id}`, and the server unlinks the item's **files**. For a
Series/Season/Folder item it takes the whole folder with it.

This destroyed 5.3 GB of media: a conversion produced a wrong series entry, and deleting the Series row to
"clear it" deleted the six files underneath. No torrent or download copy existed, so it was unrecoverable.

The function now refuses container types unless `allow_container=True` is passed explicitly.

**To remove a stale row, move the files first and let the scan drop the row on its own.** Never delete a row
while its media still sits beneath it. A row is cheap; the media may be the only copy.

Before any batch delete or concat `--apply`, confirm a same-day snapshot exists and is browsable — a
morning snapshot recovered the only copies of a film after a script bug deleted everything including its
own output. Recoverability is checked before the run, not discovered after it.

## 18. A count match is not a mapping

Six files against six episodes passes every count-based check and can still be completely wrong. The American
Revolution (2025) held six 59-minute files against six 116-131 minute episodes — half the series, mapped 1:1
and titled as if complete.

Compare **runtimes** before renaming: ffprobe each file, divide by the provider's per-episode runtime, and
refuse when the ratio is outside 0.75-1.33. Wikipedia and the broadcaster's own page give the series total,
which settles it when the provider is thin — 715 minutes across six episodes is decisive.

## 19. Jellyfin mounts each library folder individually — not the videos root

Jellyfin runs as **Docker inside CT105 (`jellyfin`, 192.168.214.20)**, compose file `/opt/jellyfin/docker-compose.yml`.
Media volumes are one-per-library:

  /mnt/zmedia/videos/DocuSeries       -> /data/docuseries
  /mnt/zmedia/videos/musicdocos-series -> /data/musicdocos-series
  /mnt/zmedia/videos/filmdocuseries   -> /data/filmdocuseries

Consequence: creating a folder on disk does **not** make it usable by the API. `POST /Library/VirtualFolders`
returns **400** for a path that is not mounted, and the request itself looks valid. Add the compose volume,
`docker compose up -d` from `/opt/jellyfin`, then create the library.

Check `find / -maxdepth 1 -type d` inside the container, not on the host — `/data` does not exist on proxmox2
or in CT105, only inside the Jellyfin container.

## 23. A batch script must exclude its own output from its input glob

Any script that scans a folder for inputs and writes its output into the same folder will, on re-run
(`--apply`, retry), ingest the previous output as a bonus input. The verification gate cannot catch this:
the inflated total is self-consistent (a doubled concat verifies against its own doubled duration), and
the cleanup step then deletes everything including the output — destroying the only copies.

Filter the output filename out of the input list (`exclude_output()` in `concat_parts.py`, with a
behavioral test), and treat every same-folder input glob in every script as suspect until it carries the
exclusion.

## 24. Jellyfin merges alternate versions only on joint discovery

Two files with correct `<stem>` / `<stem> - <label>` naming still land as two rows when the scanner
discovered them weeks apart — per-item refreshes and library rescans do not merge them after the fact.
To force the merge: move the files out, rescan until the stale rows vanish, move the files back
together, rescan. Only attempt this when both rows are metadata-bare (no provider ids, no overview),
so the re-import costs nothing.

## 25. Library collection type is immutable after creation

`POST /Library/VirtualFolders/LibraryOptions` accepts a `CollectionType` field and returns success, but the
type does not change. To move a library from `movies` to `mixed` you must delete and recreate it.

`mixed` exists in 12.2 and can be created, but Jellyfin's own docs discourage it: *"generic folder view …
unreliable metadata results"*, and jellyfin-meta discussion #46 proposes removing it. Series metadata
(episode numbers, titles, seasons) degrades in a mixed library — do not use it to host series.

## 26. Placement rule for documentary series

  music subject     -> Music Docos Series   /mnt/zmedia/videos/musicdocos-series
  film subject      -> Film Docos Series    /mnt/zmedia/videos/filmdocuseries
  everything else   -> Docos Series         /mnt/zmedia/videos/DocuSeries

(Display names were abbreviated on 26 Oct: Documentaries->Docos, Film Documentaries->Film Docos,
Documentary Series->Docos Series, Film Documentary Series->Film Docos Series. Disk folder names did NOT
change, so path mappings still read Documentaries / FilmDocumentaries / DocuSeries.)

`scripts/multipart_to_series.py` takes `LIB=` and refuses an unknown value rather than defaulting.
## 27. Renaming a library needs the dedicated Name endpoint

`POST /Items/{id}` with `{"Name": ...}` is **accepted and silently ignored** for library items, the same way
`CollectionType` is ignored by `LibraryOptions`. The endpoint that actually renames a library is:

  POST /Library/VirtualFolders/Name?name=<old>&newName=<new>

Renaming changes only the display name; library paths and item ids are untouched, so scans and API calls by
folder keep working.

Display names now use "Docos":

  Documentaries             -> Docos
  Film Documentaries        -> Film Docos
  Documentary Series        -> Docos Series
  Film Documentary Series   -> Film Docos Series

## 28. Check both download clients before sourcing anything

A search hit that matches bytes already held or in flight is a duplicate trip, not a find. Before
queueing any download, list qBittorrent torrents and the SAB queue/history for the title: a "new"
result at the same size as the held file, or a job already queued under another name, means the
title is handled. Two same-night re-queues of already-delivered episodes came from skipping this
check and reading the search output as if the shelf were empty.

## 29. An empty `find` is a suspect pattern, not an empty result

Release names use dots and spaces unpredictably (`Gandhi.Ep1` on disk vs `Gandhi Ep1` in the
pattern), and a type filter (`-name '*.mp4'`) silently drops real files of other containers. A find
that returns nothing proves the pattern failed until a second spelling or an unfiltered listing
confirms it. Verify against a known listing before concluding absence — and never treat absence as
licence to download.

## 30. Ad hoc shapes that survive a night get promoted to tested modules

Fifteen hand-rolled hunt loops shared one shape (search, filter, rank, queue)
with per-copy filter bugs; the suite had no series discovery until three
series were found by eyeballing. Both are now tested modules (`media_lib/hunt.py`
plan/queue, `scripts/scan_series_shapes.py`, `media_lib/fleet.py`,
`scripts/wanted.py` + `state/wanted.json`). External surveys (admirarr,
clawarr-suite, homelab-agent-skills) found nothing installable — all assume
Sonarr/Radarr movie+TV flows, not an approval-gated documentary library —
but contributed two stolen ideas: doctor-style fleet-health rows and a
wanted-list-driven hunt sweep, both read-only.

## 31. External review working from a stale tree

A review brief citing exact file/function/line numbers described 8 bugs as
live that HEAD had already fixed (tree current, reviewer's clone two days
old — the cited failures matched our own fix-comments verbatim). Lesson:
verify every citation against the tree before implementing, and answer with
a `docs/REVIEW-RESPONSE.md` verdict table so the re-review skips to what is
genuinely new. Real gains from that review: bio locks, playing-guard,
--from-plan, 4-field pinning, .nfo sidecars, TVmaze fallback, url-rels,
container-tag synopsis.

## 32. Compare episode markers as parsed slots, never as word tokens

A release's `S01E02` and a folder label's `E02` are the same slot, but word-set
comparison sees two different tokens and calls them different programmes — which
refused every episodic NEW placement at the ingest gate. Strip episode-marker
tokens from both sides before comparing words, then enforce slot compatibility
separately: equal episode numbers match with season 0 as wildcard (a bare `E02`
carries no season), different episode numbers never match. Parse the bare `Exx`
form too, or the label side has no slot to compare with.

## 33. BBC pid.json has two shapes — version PIDs carry no programme block

`programmes/<pid>.json` returns `{"programme": {...}}` for episode/series PIDs
but `{"version": {...}}` for version/clip PIDs. Resolve those via
`version.parent.programme.pid` and fetch again. And when the parsed title and
synopsis are both empty, fall through to the HTML scrape — returning the empty
result reports success with no data, which reads as "BBC has nothing" and ends
a hunt that should have continued.

## 32. No ad hoc inline sweeps — promote or perish

Every inline heredoc that survives first contact gets promoted to a tested
script the same night (`audit_series.py` for the 4-field series sweep,
`slot_check.py`, `hunt.plan/queue`, `scan_series_shapes.py`). Importable
single-purpose functions, pure flag/match logic with fixture tests, live
proof before commit. The rule for tonight's leftovers: per-row Jellyfin
writes stay manual (they are approval-gated by nature), everything
read-only or repeated becomes a script.
