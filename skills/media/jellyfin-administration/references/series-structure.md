# Series in a Movies-type library — audit, classify, combine

Music Docos and the other media libraries here are `Movies`-type, not `Series`-type, so a
multi-part or multi-episode programme has NO native episode structure. How it lands depends on
filename accidents rather than intent — which is why the same library ends up with one series
collapsed by luck and twenty split into one entry per episode.

**Standing preference: one entry per SERIES, carrying as much per-episode information as
possible — not one entry per episode.**

## Step 1 — Audit: find the series, and how each is currently represented

One paged pass gets every field needed. Do this pass FIRST and report the inventory before
proposing any restructuring — it is itself the deliverable for a "what series do I have"
request.

```
GET /Items?ParentId=<libId>&Recursive=true
    &IncludeItemTypes=Movie,Video,Series,Episode
    &Fields=Path,MediaSources,ProductionYear,PartCount
    &StartIndex=<n>&Limit=500
```

`Limit=0` returns ZERO items — it is not an "unlimited" sentinel. Paginate in steps of 500.
Resolve `<libId>` fresh from `GET /Users/{uid}/Views` every session — library Ids change
across upgrades/recreations (Music Docos Series changed Id on 11 Oct 2026 and the old Id
400s with `Invalid parent id`).

Group two ways, then merge the results:

1. **By parent folder** — the strongest signal in a Movies-type library. Any folder holding 2+
   items is a series candidate. **Exclude the library root**: loose files there bucket into one
   giant false "series" (a hundred-plus unrelated titles) that swamps the real ones.
2. **By normalised name stem** for items carrying an episode marker
   (`episode N | ep N | part N | pt N | vol N | S01E01 | NofM`). Strip the marker and any
   trailing year/parenthetical before comparing, and normalise case and punctuation first —
   ONE programme commonly appears under several spellings (`Later... with Jools Holland`,
   `Later Live... with Jools Holland`, `Later with Jools Holland`), and treating each spelling
   as its own series reports the same show several times.

Report per series: episode count, how many Jellyfin ENTRIES those episodes form, and a verdict
of COMBINED vs SPLIT.

## Step 2 — Classify: two different problems needing different mechanics

| Class | Shape | Signal | Target |
|---|---|---|---|
| **Part-series** | ONE work delivered in N parts | consecutive `part N` / `Pt N`, often one folder per part | one entry in a **Movies** library, `PartCount` = N |
| **Episodic series** | N distinct programmes in a strand | `S01E01` / `Episode N`, often ONE folder holding them all | one entry in a **Shows** library — see "Episodic series" below |

Decide the class per series **before** touching any file. A 10-part Ken Burns documentary and
a 60-episode weekly music show look similar in a listing but need opposite treatment;
applying the wrong one yields a broken stack or a merged row holding unrelated episodes.

**Part-stacking is the WRONG target for an episodic series.** It buys one entry but carries NO
episode structure: no episode titles, overviews, air dates or per-episode images, and the parts
are not individually addressable. When the goal is "one entry carrying as much per-episode
information as possible", an episodic series needs a Shows-type library, regardless of whether
the content is a documentary.

## Part-series → one Movies entry (proven recipe)

A part-series is the opposite case: ONE work in N parts, already on disk as N files or N per-part
folders. Consolidate them into a single entry:

1. **One `Title (Year)/` folder holds every part.** Move the parts in and remove the emptied
   per-part folders. In a Movies-type library the folder name is the item name.
2. **Rename to `Title (Year) - partN.ext`** — a space around the hyphen, none inside `partN`.
   The unspaced `Title (Year)-part1.mkv` form does NOT stack; it yields a parent Movie row plus
   loose `Video` rows. **The file BASE must contain no other ` - `** — the scanner splits on a
   hyphen to find the part/version token, so a base that itself carries one (a band name plus a
   film title, e.g. `<Artist> - <Film>.DVDRip - part1.mkv`) splits at the wrong hyphen and yields
   one row per file. A dash in the enclosing FOLDER is harmless: `Arena - The Brian Epstein
   Story (1998)/The Brian Epstein Story (1998) - part1.mpg` stacks correctly. Verify with
   `PartCount` either way.
3. **Write a `movie.nfo` into the folder** carrying `<title>`, `<year>`, `<plot>`, `<studio>` and,
   when known, `<uniqueid type="tmdb" default="true">NNN</uniqueid>`. Without it the scanner
   re-guesses the match on every scan — and a merged multi-part folder is exactly where a wrong
   guess is hardest to spot. Keep the plot factual about what is held (which parts or episodes are
   present) instead of pasting a synopsis that overstates the set. Source real episode titles, overviews
  and air dates from the provider's season list (`GET /3/tv/{id}/season/1` returns every
  episode's `name`, `overview` and `air_date` in one call) and combine them with any notes the
  user supplied — do not invent episode details.
4. **Install `poster.jpg`** from the provider's original-size path
   (`https://image.tmdb.org/t/p/original<file_path>`, `file_path` from `/3/movie/{id}/images` or
   `/3/tv/{id}/images`).
5. **Refresh the ITEM, not the library**:
   `POST /Items/{id}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh` → 204.
6. **Verify with `Fields=PartCount,ImageTags,ProviderIds,Overview`.** `PartCount` must equal the
   number of parts, and the image/overview fields must be read back rather than assumed — a 204
   on the refresh confirms nothing about the result.

Verified on a 61-row library: eight part-series consolidated in one pass, several of which had been
sitting as three or four separate rows with no provider ids and no art.

Two traps:

- **A TV id is the wrong namespace for a Movies-type library.** Setting one resolves the *movie*
  side of that id and can attach an unrelated film's overview. When the work exists on the provider
  only as a TV entry (a broadcast strand), leave the id off and write the `movie.nfo` yourself —
  then **read the resulting overview text back and check it describes this work.**
- **Never promise a backdrop.** `poster.jpg` fills the Primary image reliably; the backdrop slot did
  not fill from a sidecar (`fanart.jpg` or `backdrop.jpg`) even after a full image refresh. Report
  only the image slots you have actually read back from `ImageTags`.

A lone part on disk is not a part-series: with only `part 1` held there is nothing to stack — leave
it alone and note the missing parts rather than renaming anything.

**A stack built around missing middle parts must be renumbered when they arrive.** If only
parts 1, 2, 4, 5 are held, name them `part1..part4` (consecutive — the scanner stacks by
sequence, not by episode number) and record the true episode mapping in the `movie.nfo`
plot. When the missing part lands, rename every later part up by one so part numbers track
true episode order again, then Refresh. Pre-staging per-episode `.nfo` files for the missing
parts (named on the future canonical filename) costs nothing and means the metadata is
waiting when the file arrives.

## Appending a new part to a healthy stack

A new episode for an already-stacked part-series is an append, never a replacement:
existing parts stay byte-identical and only `PartCount` grows.

1. **Confirm the stack is healthy first** — `PartCount` equals the media-file count in the
   folder. Appending onto a broken stack bakes the breakage in.
2. **Name the new file `part{N+1}` matching the siblings' convention exactly** — same
   spacing, same stem. A mismatched marker either fails to stack or surfaces as a loose row.
3. **Stage-copy on the host that holds the share natively, with byte-size verification,
   and abort if the destination already exists.** Copy to a staging name, compare byte
   counts source vs staged, then rename into place — never overwrite an existing part.
4. **Refresh library-scoped (an item refresh cannot discover a new file), then verify
   `PartCount` equals the new file count.** A surgical `POST /Library/Media/Updated` with
   `UpdateType: Created` on the new path speeds discovery ahead of the refresh backlog.
5. **Prove the new part plays through the container path** — probe codec/dimensions plus a
   mid-file decode sample — before calling the append done.

## Attaching an alternate VERSION to a part-stacked item

A stacked part-series can also carry a different CUT of the same work (a condensed single-file
edit, an SD copy beside an HD one) so the user picks it in the version selector. `POST
/Videos/MergeVersions?Ids=<stackRow>,<versionRow>` (204) links them and leaves `PartCount` intact —
verified: a 2-part stack gained `MediaSources` 2 while still reporting `PartCount: 2`, and its parts
stayed individually playable.

- **Stage the file into the target folder, merge, then delete the staged copy.** The merge is
  reversible: if it misbehaves, remove the staged file and re-read the item. Verify by reading BOTH
  rows — the stack row's `MediaSources` should list the part AND the version.
- **MergeVersions always leaves a second doorway row.** It links the files under both rows rather
  than collapsing them, so the old row survives showing the same two sources. That is the tool's
  designed behaviour, not a failed merge — report it as the end state and do not delete a file to
  force a single row.
- **A version-directory row appears beside a stack, not merged with it.** Dropping a
  `<stem> - <version>` file into a part-stack folder does NOT version-group it (the part-stack owns
  that base name), so an unmerged version surfaces as its own row named from the FOLDER — e.g. a
  condensed cut left in its original folder became a row called `Arena Brian Epstein Story (1998)`.
  Merge it deliberately if one entry is wanted.
- **Prefer a sidecar `<stem> - partN`-style name only for parts.** For a version, any distinct name
  works because the merge, not the filename, does the linking — the filename only decides the label
  the user sees in the picker.

## Episodic series → a Shows-type library (proven recipe)

A `Shows` library is the ONLY Jellyfin structure that produces per-episode metadata and images.
In a `Movies` library the episode-image slot does not exist at all (`thumb` → Primary →
**Episode** only), so no amount of Movie-side naming will ever surface a per-episode still.

```
<library root on disk>/
└── Jazz (2001) [tmdbid-20861]/        ← [tmdbid-...] pins the match; avoid guessing
    ├── Season 01/
    │   ├── Jazz (2001) S01E01 Gumbo.mp4
    │   └── … S01E02–S01E10
    └── Season 00/                     ← extras live here as TMDB specials
        └── Jazz (2001) S00E08 Swinging with Change.avi
```

Rules that make it work:

- **`Season 01`, not `S01`.** Season folders must be spelled out for the scan to place files.
- **Episode images are sidecar `-thumb.jpg`** beside the video, named on the episode basename.
  When a metadata provider supplies stills (TMDB does for many documentaries) this is automatic.
- **`[tmdbid-NNNN]` in the series folder name** — parsing it removes the risk of matching a
  same-titled film. Also supported: `{...}`, `(...)`, and the `tmdb` / `imdb` / `tvdb` aliases.
- **Bonus discs and featurettes are TMDB *specials***, not lost extras. Map them to `Season 00`
  and they inherit real titles and images: on Jazz, `07 Swinging with Change.avi` → `S00E08`,
  `13 Extra - Interview with Ken Burns.avi` → `S00E14`, and so on. Check the provider's season 0
  episode list BEFORE inventing names.
- **Embedded season/episode tokens in filenames are a CLAIM, not truth — verify each against the
  provider's list.** The same rule as disc numbers, and it bites on explicit `sNNeNN` names too: a
  set laid out as `... s18e01.mkv` / `s18e05.mkv` had no such season on the provider at all (the
  series stopped at S10), and the files belonged at `S10E09`, `S10E13` and so on. Match on the
  episode TITLE from `GET /3/tv/{id}/season/{n}` and re-slot the file; trust the provider's
  numbering over any token an uploader typed. Wrong slots still scan "successfully" — they simply
  attach the wrong episode's title, date and synopsis, which is why the mismatch has to be checked
  rather than assumed absent.
- **When episodes have NO usable titles (generic `Episode N` rows), number by constraint, not by
  token.** A releaser pack labelled `S01` whose numbers run to E31–E33 can only belong to the one
  provider season that actually contains those numbers; corroborate with any dated filenames in
  the set (a file dated 13 Sep 2008 landing exactly on the provider's S03E02 airdate confirms the
  whole placement). Two independent signals must agree before the season is assigned.
- **Never attribute an episode's director, writer or credits from memory.** Anthology strands give
  each episode to a DIFFERENT filmmaker, and recall routinely swaps them (a 7-part blues series
  had a widely-misremembered directing credit). `GET /3/tv/{id}/season/{n}` returns each episode's
  `crew` with `job: Director` alongside `name`/`overview`/`air_date` in the same call, so the
  per-episode credits cost nothing extra — read them, then check the first two against the
  filenames before writing the rest. Report the provider's credit, not your recollection.

### A new media folder is INVISIBLE until the container is recreated

Adding the directory to the compose file is not enough — the bind mount only takes effect when
the container is recreated (`docker compose up -d`), and Jellyfin cannot scan what it cannot see.
After recreating, confirm from inside the container that the path is mounted and populated
BEFORE creating the library, or the scan silently returns nothing:

```
docker exec jellyfin sh -c "mount | grep <name>; find /data/<name> -type f | wc -l"
```

### Migrate by COPY, prove EVERY file, then remove

Copy into the new structure, scan, verify the match, and remove the originals only after the user
signs off. Decode-check with Jellyfin's bundled ffmpeg (`/usr/lib/jellyfin-ffmpeg/ffmpeg`) — the
container has no `ffmpeg` on `PATH`, and a shell `&&` chained after a pipe will report success even
when ffmpeg failed.

**Before deleting a batch, prove EVERY source file has a counterpart in the destination — not a
sample.** A hash on two files proves the tooling works, not that *this* file arrived. Index the
destination's media files by byte size in one pass, check every source against that index, and
**abort the entire deletion if even one source has no size match**. Size collisions are rare enough
to make this a sound screen, and it converts "I copied them earlier" into evidence.

**Keep the delete list explicit** — a hardcoded list of the folders being superseded, never
"everything absent from the destination". Anything deliberately retained (a different cut, a DVD-only
episode with no HD source) must be named in a KEEP set or the sweep takes it. Where a retained file
duplicates an episode that *was* migrated, say so in the report and let the user decide rather than
resolving it silently.

### The source library keeps ghost rows for everything you moved out

Moving files does NOT remove their rows: each one survives pointing at a path that no longer
exists, and the user sees them as leftover single episodes sitting beside the new series entry —
the report reads as "I don't see the series, only a couple of separate episodes". A library-scoped
Refresh does not reliably purge them, and the client hides nothing, so they read as duplicates.

Purge them deliberately, in one pass, with the disk as the arbiter:

1. Enumerate the source library (`ParentId` + `Recursive`, `Fields=Path,MediaSources`) and keep
   the rows whose paths sit under the moved folder.
2. Deduplicate every path (the row's `Path` plus every `MediaSources[].Path`), map container→host,
   and test them ALL in ONE ssh pass that prints a verdict per path.
3. `DELETE /Items/{id}` only where the row has exactly ONE source and EVERY path under it is
   confirmed ABSENT on disk. With in-Jellyfin deletion enabled the server unlinks whatever the row
   still lists, so deleting a row whose file survives destroys media — the absence check is the
   safety gate, not a formality.
4. Recount the source library and report zero remaining rows for the moved title.

A ghost row that shares a name with a live entry is the dangerous case: match on the full path,
never on the title, when deciding what to delete.

### A migration preserves the quality you already had — profile the result and report it

Moving files into a Shows structure fixes *organisation*, not *content*. The new library inherits the
source's exact codec and resolution mix, and the metrics that naturally get reported on completion —
episode count, metadata coverage, image coverage, child links — can all be green while the library is
unplayable in browsers and uniformly low-resolution. Report the quality profile alongside the counts,
per series, before calling a migration done:

```
GET /Items?ParentId=<seriesId>&Recursive=true&IncludeItemTypes=Episode
    &Fields=MediaSources&Limit=400          # then tally MediaStreams[0].Codec/Width/Height
```

Measured on a 111-episode migration that reported "111 episodes, 111 images, 12 posters": **82
everages (74%) were `mpeg4`** — the Cause D codec, unplayable in a browser — and **85 (77%) were
360p or below**. None of that was visible in the success summary, and all of it drove the follow-up
recommendations. The count of episodes is not a measure of what was accomplished.

This is also where you learn whether recoding is even the right remedy: see the codec∩resolution
overlap rule in `references/container-and-codec-policy.md` — when the legacy-codec set and the
low-resolution set mostly coincide, replacement beats recoding because a recode locks the low
resolution in permanently.

### After the first scan, PROVE the series links its episodes

A freshly created Shows library can scan every file correctly — right titles, overviews, stills,
airdates — and STILL leave the Series item with **`ChildCount = 0` and `RecursiveItemCount = 0`**.
The seasons and episodes exist and carry the correct `SeriesId`/`SeasonId`; the parent's child
links were simply never built. The user-visible symptom is exactly the complaint that sends you
back: *"it shows as one entry with no obvious link to the episodes"* — the series card has nothing
to navigate into and `NextUp` is empty, so it reads as a failed migration when the files are fine.

Diagnose against the API, not the UI — a direct-children query returning 0 while a recursive one
finds the seasons is the signature:

```
GET /Items?ParentId=<seriesId>&userId=<uid>                          -> 0 items   (broken)
GET /Items?ParentId=<libId>&Recursive=true&IncludeItemTypes=Season   -> seasons DO exist
GET /Users/<uid>/Items/<seriesId>                                    -> ChildCount: 0
```

Fix — force a full refresh of the SERIES itself. No rescan, no DB edit, no re-import:

```
POST /Items/{seriesId}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh
     &replaceAllMetadata=false&replaceAllImages=false                -> 204
```

It rebuilds within seconds; poll `ChildCount`/`RecursiveItemCount` while it settles. Success =
`ChildCount` equals the season count, `RecursiveItemCount` equals total episodes, the
direct-children query returns the Season rows, and `NextUp` lists the first episode.

**Run this check on every Shows-library ingest, not just a migration.** A clean scan report says
nothing about whether the result is navigable, and the two failures look identical from the UI.

### Mapping parts to episodes, and when the metadata actually shows up

- **Validate the part→episode mapping by DURATION against the provider's episode list, not by
  filename order.** A folder of `part 1..6` files can hold fewer episodes than parts: a 4-episode
  PBS series arrived as six numbered files whose parts 5 and 6 ran 187 s and 94 s — fragments of
  the same episodes, not episodes. Pull the season list, compare each held file's runtime against
  the episode runtimes (a feature-length finale is normal and the provider shows it), and leave
  anything far outside the episode range behind rather than renaming it into a slot. Duration is
  the only reliable key when the filenames are generic.
- **TV-form names tolerate episode gaps — do NOT renumber to close one.** With `S01E0N` naming the
  episode number lives in the name, so a missing episode simply leaves a hole
  (`E01, E03..E08`) that Jellyfin renders correctly and that fills itself when the file arrives.
  This is the OPPOSITE of the part-stack rule earlier in this file, where missing middles must be
  renumbered because stacking is positional. Renaming TV-form files to close a gap mislabels every
  later episode.
- **Per-part stills sitting beside the source files become episode images.** When the source folder
  carries a `.jpg` per part (a fan-made set), move each to `<episode basename>-thumb.jpg` in the
  season folder — that is the sidecar name the scanner reads for the episode still, so no
  frame-grabbing is needed.
- **Episode metadata arrives AFTER the file is indexed — an empty-looking card is a timing
  artifact, not a defect.** Immediately after a scan the first episode can read `Overview: 0` with
  no air date; minutes later the same row carries its full synopsis, air date and still, and
  `GET /Shows/NextUp?UserId=<uid>&SeriesId=<id>&Fields=Overview` returns it complete. Re-read
  before diagnosing: "the Next Up card shows no metadata" has been reported mid-pass and was fully
  populated when checked again. There is no per-episode synopsis setting to enable — the remedy is
  patience plus a client refresh, and the data a client renders from that card is the client's own
  layout choice.
- **Do not infer that provider metadata is switched off from the library's
  `EnableInternetProviders` flag.** On this Shows library the flag reads `false` while episodes
  still acquire provider titles, air dates, overviews and stills. Read the actual episode fields
  (`Fields=Overview,PremiereDate,ImageTags`) before concluding metadata cannot arrive, and audit
  coverage per series rather than assuming: on a 23-series library the newly built series came back
  complete while older ones held gaps (6/11 episodes with no overview in one, 10/15 in another) —
  the per-series count is what tells you where a repair pass is worth running.

### API notes

- `GET /Items/{id}/People` 404s. Request people via `Fields=People` on the item query instead.
- An item query returns ONLY the fields named in `Fields`; a summary computed without
  `Overview` in `Fields` reports 0 overviews and looks like a data defect. Request every field
  the verification asserts on.
- Creating a library: `POST /Library/VirtualFolders?name=…&collectionType=tvshows&paths=/data/…&refreshLibrary=true`.
- A pre-existing "TV" library may well be `type=movies` — check `CollectionType` rather than
  assuming from the name before reasoning about what a library can hold.

## Two structures the user will ask for and Jellyfin cannot express

Both requests are natural, both have no native answer. Know the limit BEFORE promising the result
and give the closest equivalent instead of quietly delivering something else:

- **"A separate description for each part."** A stacked part-series carries exactly ONE overview
  for the whole item — there is no per-part description, synopsis or image field, and the parts are
  not addressable objects. Closest equivalent: label each part inside the single `<plot>`/overview
  (`PART 1 - <title> (<broadcast>): <synopsis>`, then PART 2 …), which is what the user reads
  anyway, and say plainly that true per-part fields would require the Shows/episode structure —
  separate rows — which contradicts "combine them in one entry". Source each part's synopsis from
  the provider or the broadcaster's programme page; never invent one.
- **"Skip the season level — there's only one series."** A Shows library's Series → Season →
  Episodes path is structural. Read the library's `LibraryOptions` before claiming otherwise (its
  ~40 keys include `EnableAutomaticSeriesGrouping`, `SeasonZeroDisplayName` and `TypeOptions` — and
  nothing that collapses, flattens or bypasses a season). With one season it is exactly one extra
  click, identical on every client. The only working mitigations are client-side: Continue Watching
  and Next Up deep-link straight to the episode, which is how a single-series show gets watched
  anyway. Report this as a structural limit, not as an outstanding to-do.

The same limit decides **library placement**, and the two goals can conflict: an episodic series
placed in a Movies-type library loses every per-episode field, so "file it with the other
documentaries" and "give it per-episode metadata" cannot both be satisfied. Surface the trade-off
with the libraries named and let the user pick which one wins.

## Step 3 — Tell deliberate stacking from accidental collapse

`MediaSources` and `PartCount` answer different questions and are easy to confuse:

- `MediaSources > 1` with **`PartCount = None`** → the scanner **version-grouped** files that
  merely shared a folder and near-identical names. It *looks* combined in the UI, but there is
  no episode structure and the parts are not individually addressable. Treat this as **un-done
  work, not a successful combine** — it is what an accidental collapse looks like.
- `PartCount > 1` → a deliberate stack built from **spaced** `Title (Year) - partN.mkv` names
  inside a single `Title (Year)/` folder. Only this form carries real part structure.

`MediaSources` stays **1** even for a correctly stacked item, so it can never confirm a stack.

**`RunTimeTicks` on a stacked item is the FIRST part's runtime, not the sum — and this reads as
missing content.** A healthy `PartCount=2` item whose parts run 113 and 95 minutes reports
`RunTimeTicks` = 113 minutes, because the client labels the entry from part 1 while playback
continues straight into part 2. The user-visible symptom is a complaint that only half the film is
there ("it is 3h 28m and only 1 episode half that length is there. did you delete some?"), which is
a presentation artifact, not a deletion. Before accepting any content-loss report on a stacked item:

1. Read `PartCount` on the item — `> 1` means every part is registered.
2. `ffprobe` each file on disk and SUM the durations; compare the total against the figure the user
   quoted. A match proves nothing was lost.
3. Report the displayed runtime as the cause, and state plainly that the runtime label never
   reflects the sum for multi-part items.

The alternative the user may then choose is splitting the stack into separate rows named for each
part — honest runtimes and direct access to part 2, at the cost of one entry per part. Say which
trade-off each option buys rather than presenting it as a fix for a fault.

## Step 4 — Filter download debris before counting episodes

Download artifacts inflate the episode count and get mistaken for parts:

- **yt-dlp fragments** of ONE video share a stem with `.fNNN` / `.temp` suffixes
  (`Title.f251.webm`, `Title.f616.mp4`, `Title.temp.mp4`) — three apparent "episodes" that are
  one incomplete file.
- **Exact duplicate NAMEs** across the library surface as separate entries for the same episode.

Report these as defects to clean up, not as episodes to combine.

## Hand-built .nfo files for series with no metadata provider

When a series has no TMDB entry, a Shows library still gives full per-episode structure if you
write the metadata yourself. Two failure modes both present as "the metadata didn't work":

- **An unescaped `&` makes the .nfo silently invalid.** A plot containing "Crosby, Stills, Nash
  & Young" is not well-formed XML, and Jellyfin ignores the file with NOTHING in the log — the
  episode keeps its title but shows no overview, which reads like a matching failure. Escape `&`
  to `&amp;` (and `<`/`>` likewise), then parse-test the whole generated set with
  `xml.etree.ElementTree.parse` before refreshing. Verify a batch as a set: one bad file and the
  rest look fine.
- **A per-episode .nfo must be named on the episode basename** and sit beside the video
  (`<episode basename>.nfo`), with `<episodedetails>` carrying `<title>`, `<season>`, `<episode>`,
  `<aired>` and `<plot>`. The series-level `tvshow.nfo` is separate.

Episode stills for a provider-less series: frame-grab at ~12% into the episode with ffmpeg
(`-ss` before `-i` for a fast seek) and save as `<episode basename>-thumb.jpg`; capture the
series `poster.jpg` at the series-folder root. Do the grabbing on the host that holds the array
natively (proxmox3 has ffmpeg on PATH) — the Jellyfin container has no `python3` at all, so a
Python generator script must run on the host and only the RESULT needs to be visible to Jellyfin.

**Key batch sidecar generation on the FULL on-disk folder name.** A series folder carries the
`[tmdbid-NNNN]` suffix, so a lookup table keyed on the bare `Title (Year)` matches nothing and
generates zero thumbnails — silently, because "this series is excluded" and "no such key" are
indistinguishable in the output. Key on the actual folder name (or match by prefix), and have the
script print a per-series generated/skipped count so a miss shows up as a line of zeroes instead of
an absence. Re-read the counts after the run: a summary total hides which series produced nothing.

## Curated series metadata: prefer the API write path over refreshes

On rows that already carry provider ids (Tvdb/TMDB attached at scan), local episode
.nfo files are IGNORED — repeated FullRefresh+ReplaceAllMetadata rounds change nothing
(verified across four rounds with zero movement). The reliable path is a direct write:
GET the user-scoped DTO, set `Name`/`Overview`/`IndexNumber`/`ParentIndexNumber`/`PremiereDate`
plus `LockedFields: ["Name", "Overview"]`, POST it back to `/Items/{id}`, and read the
fields back to prove it. Locks survive later refreshes; unlocked API-set fields do not.

Three rules attach to this path:

- **Never run series-level ReplaceAllMetadata on a curated series.** A series Refresh with
  `ReplaceAllMetadata=true` re-fetches provider data and CLOSES OVER episode-level curation —
  hand-set names/overviews revert to provider values (or blanks) on every row. Refresh series
  with both ReplaceAll flags false, or refresh episodes individually.
- **A provider-less episodic series scans its S01E0N files as S00E00 rows** - season 0, episode 0, Name equal to the series name, zero-length overview - however correct the filenames are, because there is no provider list to resolve them against. Force `IndexNumber`/`ParentIndexNumber`/`Name`/`Overview` in one DTO write right after the first scan and lock them; leave `PremiereDate` unset when no airdate is sourced (an invented date is worse than a blank). Do not wait for another refresh - with no provider the scanner never renumbers them.
- **Every version file needs its own same-basename FULL .nfo.** When an HD copy (`... - 720p.mkv`)
  joins an SD episode, the row's primary version can flip to the file with no matching .nfo, and
  the scanner may write back a title-less stub (BOM + `<plot>` only, `<lockdata>false</lockdata>`)
  that then loses to provider data. Copy the full SD .nfo (with `<title>`, `<season>`,
  `<episode>`) onto each version basename.
- **A provider row keeps provider NAMES even when your numbering is right.** When the provider's
  season list is itself wrong (duplicated/missing episodes, verified against two independent
  release orders), the row displays the wrong identity with full confidence. Correct it through
  the API path above and lock it — do not keep re-refreshing.

## Pitfalls

- **Never rename files to force a structure before the audit is finished.** Two series in one
  library can need opposite conventions, so a blanket rule applied early creates the very
  collapse the audit was meant to find.
- **Sanity-check the group count against the series' real episode count.** A strand with 60
  episodes reported as 24 items means the grouping missed spelling variants, or debris and real
  episodes are mixed in one bucket — resolve that before proposing anything.
- **The library root is never a series.** Excluding it is the difference between a useful
  inventory and one dominated by unrelated one-off titles.
- **When a user reports a library or series as invisible, check the browsing account's library
  SCOPE before investigating the library.** `GET /Users` → `Policy.EnableAllFolders` /
  `EnabledFolders`: a restricted account sees only the folders on that list, so a Shows library
  outside it is invisible to them and reads as a failed build. Confirm which account is browsing
  and compare against the library id, then look at the library itself.
- **A SeriesName count is not an episode count — pathless virtual placeholders inflate it.**
  A provider-listed series shows hundreds of rows carrying its SeriesName with EMPTY paths
  (virtual unaired/missing-episode entries); the playable holdings are the rows WITH paths
  under the series folder. Audit missing metadata against the series-scoped children
  (`ParentId=<seriesId>`), never against the SeriesName grouping, or a complete series reads
  as hundreds of gaps. Related: `searchTerm` episode queries return only a search-index
  subset — enumerate with `ParentId` pagination for counts that must be exact.
