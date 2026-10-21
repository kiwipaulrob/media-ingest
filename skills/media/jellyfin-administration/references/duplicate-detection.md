# Finding duplicate media in the Jellyfin libraries

## Always verify by inode, not just content

A duplicate claim built on **size + hash is not safe**: a **hard link shares one inode**, so it has an identical
size *and* an identical hash. Content matching alone reports it as a duplicate when it is one file with two
names — and deleting one name frees **zero bytes** while removing the title from that library.

```bash
stat -c '%i %h %s %n' "$file"     # inode, link count, size, name
find /mnt/dguh/videos -type f -links +1   # every hard-linked media file
```

Verdict rule: **same inode (nlink=2) -> hard link, reclaims nothing. Different inodes, nlink=1 -> real copy.**

Cross-library pairs are frequently deliberate hard links (a title wanted in two libraries at zero disk cost).
Real example: 4 pairs — `Decline of Western Civilization` (26.8 GB), `Sunset Boulevard` (5.4 GB),
`Chinatown` (2.1 GB), `Pussy Riot: A Punk Prayer` (1.8 GB) — 36 GB already saved, **nothing** reclaimable.
Reporting those as 'duplicates' overstates the space freed by 36 GB.

## Fingerprint from the host, never container paths

The paths Jellyfin reports (`/data/movies/...`) **do not exist on the host**. Map them first:

| Jellyfin path | Host path (prox3) |
|---|---|
| `/data/movies` | `/mnt/dguh/videos/Films` |
| `/data/documentaries` | `/mnt/dguh/videos/Documentaries` |
| `/data/tvshows` | `/mnt/dguh/videos/TV` |
| `/data/filmnoir` | `/mnt/dguh/videos/filmnoir` |
| `/data/musicdocos` | `/mnt/dguh/videos/musicdocos` |

Case matters (`Films`, `Documentaries`, `TV` are capitalised). Hashing an unmapped path returns
`e3b0c44298fc1c14…` — **the sha256 of empty input** — which then compares 'equal' across every file. Assert the
fingerprint is non-empty and count reads vs paths before trusting any result.

Partial fingerprint is enough for triage: `head -c 8388608 | sha256sum` + `tail -c 8388608 | sha256sum` + exact size. But never weed on a partial fingerprint alone when the filenames claim different versions of the same work (`Theatrical Cut` vs `Original Cut`, `Director's Cut` vs `Extended`): two different cuts of one film can share the exact byte size, so run a FULL `sha256sum` of both files before verdicting. Same size + different full hash = two legitimately different versions — keep both under the single multi-version row, never force separate rows and never weed either.

## Two traps that manufacture fake duplicates

- **Jellyfin applies one tmdb/imdb id to every episode of a series.** Grouping by id then reports all episodes as
  duplicates of each other (Hijack -> 'Hijacked: Flight 73', Fisk -> 'Luzzu', Reggie Perrin -> 'S01E03',
  In the Red -> 'The Woman in Red'). Group by name+year, never by provider id.
- **Over-normalising titles collapses distinct content.** Stripping `part N`, `S01E01` or `1of2` makes multi-part
  films and separate episodes share a key — one pass reported '71 titles / 138 GB' this way against a true
  3.7 GB. Keep part/episode tokens in the key, and drop only a bracketed `(year)` so cross-library pairs match.
- **Same title, different year = different films**: *Notorious* 1946 vs 2009, *Planet of the Apes* 1968 vs 2001.

Also verify a group is the same content *unit* before calling it a duplicate: compare basenames after removing
only quality tokens (1080p, x265, hi/lo, hires/lores, LARGE). If the basenames still differ, they are different
episodes or parts, not duplicates.

### Unprobed siblings and missing filename tags

A probed legacy `.avi` next to an unscanned `.mkv` a few MB smaller is usually a remux pair left behind by an
`mkvmerge` pass — and the unprobed mkv is often the KEEPER (same streams, modern container), not the weed.
Never verdict a file with no stream rows: `ffprobe` it first, because the keep/weed direction can invert once
the streams are known. The same caution applies to filenames: a missing resolution tag is not a resolution
verdict. Backstop every tag-based skip with byte sizes — source bigger than the library copy means copy,
smaller means skip, equal means duplicate-candidate for the inode check.

### Triaging a same-name group: six shapes

A name+year group with >1 row is usually one of these — resolve every row's exact `Path` first
(`Fields=Path,MediaSources`), then match the shape instead of verdicting from the count:

| Shape | Example | Verdict |
|---|---|---|
| Split parts (`CD1/CD2`, `A/B/C`, `part 1`) | two AVIs forming one film | keep all — parts of one work, never weeds |
| Series parts/episodes (`06of14`, `Postscript`) | multi-file documentary | keep all — distinct programmes |
| Same inode, cross-library | one file visible in two libraries | hard link — library configuration, zero reclaimable |
| Same runtime, different size | 8.5 GB vs 4.6 GB encodes | two encodings — the keeper choice is the user's (see Keeper hierarchy) |
| One row's `Path` is a FOLDER | DVD-folder item beside a file row | not duplicates — the folder belongs to the recode workflow |
| Correct provider IDs, wrong file | row named for film X, carrying X's IMDb id, pointing at film Y's file | MISLINK, not a duplicate — see below |
| Release-named file loose at the library root | `the.39.steps.ws.pdtv.xvid-remax.avi` sitting beside folders, not inside one | inferior copy AND a hygiene fault — weed it and the root gets cleaner for free |
| Orphan VOB fragment nested inside another title's folder | `summer hours/VIDEO_TS/` holding 3 VOBs with no IFOs, inside Mid-August Lunch's folder | unplayable data from a different film, no row, no cost to park — never verdict the host folder on the fragment's contents |

### A mislinked row is not a weed — search the true title first

A row can carry the right provider IDs yet point at the wrong film's file (manual misidentification).
Before moving or purging anything, search the library for the file's TRUE title: if it returns zero rows,
the mislinked row is that title's only library access, and deleting it destroys playback outright — the fix
is renaming the files to stacked-part form plus a library-scoped Refresh, never a delete. A zero-row title
search never means the title is absent from disk: list the folder on the share host before concluding anything.

## Before touching anything: hot snapshot + watch-state plan

Snapshot the database with zero downtime before every weed or rebind batch: `sqlite3 jellyfin.db '.backup
<dest>'` (hot — no container stop), then prove the copy with `PRAGMA integrity_check` (must return `ok`)
plus row counts (`BaseItems`, `Users`). A batch that goes wrong restores from this file. Snapshot artwork presence
per row too (image columns in the backup DB, or `PrimaryImageAspectRatio` / `ImageTags` via the API) and diff
after — a rebind or refresh that silently strips posters reads as success on row counts alone.

Plan the watch-state carry-over in the same pass: when a pruned row clears, its Played flag and resume
position die with it. Resolve the victim-to-keeper id mapping FIRST, while the victim row still exists — the refresh that clears the
old row destroys the mapping, and basename matching cannot rebuild it when the survivor shares the name. The
`POST /Users/{userId}/Items/{keeperId}/UserData` write itself targets the keeper row, which survives, so it can
land any time after the mapping is captured — for every user, not just the admin. A weed that silently unwatches everyone's films reads as database pollution even when every file
decision was correct.

## Deleting

### Keeper hierarchy for same-runtime pairs

Standing preference: highest resolution wins; ties go to higher bitrate; a legacy codec (XviD/DivX/MPEG-4 Part 2) always loses to h264 at equal resolution. Extras are exempt wholesale. A same-title pair sitting in two libraries is usually an intentional hard-link placement (zero disk cost) — resolve as library configuration, never as deletes. When the right call is unclear (different cuts that probe near-identical, ambiguous provenance), ask instead of verdicting: a cautious ASK beats a wrong weed.

Remove one name of a **real** duplicate only, and prefer `rm` on a single path over library-level operations.
Never delete a hard-linked pair — resolve the library configuration that made both libraries see the same folder
instead.

Run this sequence every time; each step is cheap and each one has caught a wrong deletion:

1. **Re-prove equality at delete time.** `stat -c '%i %h %s'` both paths, then compare
   `head -c 8388608 | sha256sum` on both. Different inodes + equal size + equal first-8 MB hash = safe to remove
   either. Never act on a hash taken in an earlier pass. Exception: for CROSS-container pairs (avi vs mkv) the
   head always differs by construction (container headers), so a head mismatch falsely vetoes a correct delete —
   compare an 8 MB chunk from deep in the file at the same offset on both (`dd bs=1M skip=200 count=8 |
   sha256sum`). Equal size + equal duration + equal mid-chunk = same content, different mux.
2. **Victim `nlink` must be 1** and the two inodes must differ. Anything else is a hard link: it frees **zero**
   bytes and removes the title from that library — stop.
3. **The keeper must exist and decode** (`ffprobe -v error -show_entries stream=codec_type -of csv=p=0`) *before*
   anything is removed. A keeper that does not decode means the delete destroys the title outright.
4. `rm`, then confirm the victim is gone, the keeper is unchanged (same inode and size), and report the bytes
   freed.

### Same title, different size = different encoding, not a duplicate

A pair whose sizes differ is two encodings — but compare **durations before sizes**. Different runtimes mean different cuts, not a duplicate at all: keep both as separate entries (or ask), never offer one as the replacement for the other. Only same-runtime pairs are an encoding choice, and that choice belongs to the user. When they do choose one, verify the keeper decodes and state plainly that the two files were never proved identical.

### Same title, same size — probe-compare the download against the library file before ingesting

A finished torrent whose name matches a library title is not automatically new content. `stat` both files and `ffprobe` resolution, codec, and duration on each:

- **Byte-identical size + identical streams + identical duration = the same file.** Delete the torrent and its files; there is nothing to ingest.
- **Identical streams and duration but different hashes = the same encode in a different mux.** Swapping gains nothing — keep the library file and delete the torrent.
- Only a streams/duration difference (or a genuinely absent library file) justifies an ingest or a swap, and a swap is then the user's call per the encoding-choice rule above.

### Free-space readings lag — one `df` sample cannot prove a delete failed

`df -B1` sampled immediately after `rm` can still return the pre-delete value, which looks exactly like a failed
delete. Re-measure before reporting.

The delta is the victim's size **rounded up to a 4 KB block**, not the byte count. Compute it rather than treating
the gap as noise:

```python
alloc = -(-victim_bytes // 4096) * 4096     # what the filesystem actually frees
```

For a near-identical duplicate pair that rounded figure frequently equals the *keeper's* byte size exactly — which
reads as an impossible coincidence and looks like a bug in the measuring script. It is arithmetic. Check the rounded
value against the observed delta before suspecting the tooling.

The authoritative evidence remains the victim being gone and the keeper intact — not the counter.

### Rehome the victim's sidecar art before removing its folder

A victim that lived in its own folder (hyphenation-shadow duplicates: two folders, one title) often leaves `poster.jpg` / `fanart.jpg` behind when its media file is removed. Byte-compare each against the keeper folder's art first: identical files are safe to drop, unique ones move into the keeper folder, and only then remove the emptied shadow folder — deleting the folder with its art still inside destroys images the keeper row may be using.

If the victim was queued in a still-running encode/remux batch, its disappearance is expected, not a fault — the batch driver must guard-skip a missing path rather than abort.

### After removing: refresh only the libraries that lost files, then verify by EXACT PATH

`POST /Items/{libId}/Refresh` returns **204** and scans asynchronously. A `Deleted` path ping (`POST /Library/Media/Updated` with `UpdateType:"Deleted"`) is likewise accepted-then-queued: an accepted ping with the row still present minutes later is scan-backlog lag, not a failed purge — every other batch touching the library feeds the same queue, and re-firing the ping while queued is a no-op. Wait for the backlog to drain, then verify by exact path. Verifying by name or substring is
worthless here: the surviving copy has the **same basename** in another library, so a filename match reports
*still present* forever and a clean removal looks like a failure. Enumerate each library with `Fields=Path` and
compare full paths:

```python
d = get("/Items", ParentId=lib_id, Recursive="true", Fields="Path", Limit="2000")
indexed = {i["Path"] for i in d["Items"] if i.get("Path")}
assert deleted_path not in indexed     # stale row cleared
assert keeper_path in indexed          # survivor still indexed and playable
```

Query **every** library involved, including the one that only holds the keeper — omitting it makes the survivor
look missing and sends you chasing a fault that is not there.

### If a panel or queue drives the removals, guard the survivor

Any stored set of "paths that may be removed" (allowlist, JSON file, button list) must drop the removed path AND
every entry whose **keeper** is that path, at the moment the removal lands. Otherwise the surviving copy's entry
still names a file that no longer exists, and the panel's next click destroys the last copy of the title. Rebuild
the set from the audit and exclude any group where a member is already gone:

```python
resolved = {x["deleted"] for x in done} | {x["kept"] for x in done}
# then skip every group containing a resolved path before offering its members
```

Assert before saving that no entry references a resolved target.

### Audit only a quiescent library

Paginating `/Users/{id}/Items` with `StartIndex` while the library is still growing shifts every window — items land in pages already read and silently drop out of the audit, which then reports them as "not indexed". Confirm counts are stable across two checks minutes apart BEFORE trusting a disk-vs-library diff, and re-run the diff after quiescence if the first pass ran mid-scan.
