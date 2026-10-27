---
name: media-series-consolidation
description: "Use when consolidating multi-part media into series."
version: 1.0.0
author: media-maintainer
license: MIT
metadata:
  hermes:
    tags: [media, jellyfin, series, consolidation]
    related_skills: [jellyfin-administration, music-docos-library-maintenance]
---

# Consolidating loose multi-part media into series entries

A folder of `Title - partN.ext` files is usually a series, not a film and not N films. This skill covers
turning those folders into proper series entries — and the checks that stop a confident, wrong conversion.

Companion skills: `jellyfin-administration` (server, REST API, part-stacking, delete safety) and
`music-docos-library-maintenance` (music-domain specifics, ingest pipeline). This one carries the bulk
conversion procedure and its guard set.

## When to Use

- A library folder holds loose `part1`/`part2`/`pt`/`cd` files for one programme.
- One title appears as several separate movie rows that should be a single series.
- A bulk pass over "multi-part groups" is being planned — check for existing series first, most are converted.
- A conversion produced a wrong series entry and has to be undone without deleting media.
- Deciding which of the documentary libraries a series belongs in.
- Building or fixing the scan that locates multi-part groups in the first place.

## Finding the groups — ask the provider, not the filenames

A filename-based scan produces false negatives that look like a clean result, so it is the step that fails
quietly. Two conventions break it on their own:

- **one episode per folder** — `Title ep01.WnA/`, `Title ep02.WnA/` … A grouping key of
  `(folder, filename)` yields one file per group, and every group is discarded as "not multi-part". Group by
  TITLE across folders instead.
- **parts that are not `part N`** — `Part A`/`Part B`, `Inning 01`, `ep04`, `1of5`, plain `Title 1.mkv`.
  A regex covering only `part|pt|cd|disc` misses all of them.

The reliable detector ignores naming altogether and asks the provider:

1. derive a candidate title per file — folder name first, file stem as fallback — stripping episode/part
   markers (`ep`, `part`, `pt`, `cd`, `disc`, `vol`, `inning`, `chapter`), `N of M`, `SxxEyy`, broadcast
   dates, years in brackets, release tags and dotted separators;
2. group by that title ACROSS folders so per-episode folders reconverge;
3. query `/search/tv` and require an **exact normalised name match**;
4. compare `number_of_episodes` against the files present and label each group `FULL` (files ≥ episodes),
   `PART` (fewer) or `?` (provider lists zero episodes).

Being strict about the name match trades false matches for false negatives, which is the right way round:
`Gandhi ep01 The Making of the Mahatma` normalises to something that never equals `gandhi`, so it is missed
rather than bound to the wrong series. Report the misses; do not loosen the match to catch them.

Exclude generic stems. A folder named for `extras` matches a real TMDB series and sweeps up every
featurette folder in the library — require the candidate title to be specific before accepting a match.

Filename-shape matching has two exact rules. Dotted releases omit the `p` (`Show.e01`), so a bare
`e` + digits with `.`/`_`/`no` separator IS an episode marker. A bare `e` + digits with a SPACE
separator is NOT — it false-positives on ordinary titles (`Grade 8 Readers` reads as episode 8).
The p-form (`ep05`, `episode 2`) allows any separator. And the walk must run where the media lives:
a filename scan over a local path that is not mounted returns empty, which reads as "no series-shaped
files" — pipe the remote listing in (stdin) and treat an empty result as a failed mount until proven
otherwise.

## Procedure

1. **Check whether the series already exists** in the target library, with episodes. Roughly a third of
   multi-part groups are already converted; the remaining job is cleanup, not conversion (see below).
2. **Decide the library from the SUBJECT, not the source folder** (standing user direction):

   | Content | Library |
   |---|---|
   | music-subject series | **Music Docos Series** — `/data/musicdocos-series` |
   | film-subject series | **Film Documentary Series** — `/data/filmdocuseries` |
   | general factual series | **Documentary Series** — `/data/docuseries` |

   Decide before renaming anything. Series converted into the wrong library have to be moved a second time.

   **The target library must already exist as a real `tvshows` library.** Each library is its own bind
   mount in `/opt/jellyfin/docker-compose.yml` (`/mnt/zmedia/videos/<folder>:/data/<lib>`) — not one mount
   of the whole `videos` tree — so a new folder under `videos/` is invisible to the server until a mount
   line is added and the container recreated. Creating the library before that fails
   **400 Error processing request** against the path, and the request itself looks perfectly valid.

   Procedure, in order:

   1. create the folder on the share host with the exact case you will mount (`DocuSeries` and
      `filmdocuseries` differ only by case);
   2. back up the compose file, add the volume line, and `docker compose up -d` from `/opt/jellyfin` — check
      `GET /Sessions` for `NowPlayingItem` first, because a recreate disconnects clients;
   3. prove the path is visible: `pct exec 105 -- docker exec jellyfin ls -d /data/<lib>`;
   4. create it: `POST /Library/VirtualFolders?name=<Name>&collectionType=tvshows&paths=/data/<lib>&refreshLibrary=true`;
   5. confirm `ItemId` is populated and content enumerates.

   `collectionType` is immutable after creation — `LibraryOptions` accepts the field and changes nothing, so
   a different type means delete and recreate. The `mixed` type is a generic folder view that strips series
   metadata; never use it as a shortcut around a missing mount.

   **Renaming a library is structural, not cosmetic — get explicit approval first.**
   `POST /Library/VirtualFolders/Name` is the working endpoint (an `/Items` name write is accepted and
   ignored), and it **discards the library's `ItemId`**: the row then reads `ItemId=None` with `Locations`
   still populated, and every `Items?ParentId=<old id>` returns **400**. Recovery is a full
   `POST /Library/Refresh`, which recreates the library under a **new item id** and purges the
   deliberately-kept ghost rows a full scan is normally forbidden for. Abbreviating display names is not
   part of any consolidation procedure — if asked whether it is possible, answer and stop.

   Two further consequences to expect, both observed on a single four-library rename:

   - **Collections lose their members.** A BoxSet stores member ids, and members re-linked under new ids no
     longer match — 9 of 22 boxsets emptied silently. Repair with
     `POST /Collections/{id}/Items?ids=a,b,c` after finding the members, and verify with a USER-SCOPED
     `ParentId` read (the anonymous endpoint reports 0 children on a healthy BoxSet). Name-matching the
     collection to its media is unreliable in both directions: dotted release names defeat word matching
     (`Gary.Hustwits.Design.Trilogy`), and some collections share no token with their members at all (a
     "Harry Palmer" collection holds *The Ipcress File*). A broad match will over-fill a collection — say
     so instead of presenting it as restored.
   - **Ghost library views appear in the client sidebar** as libraries that open to nothing: listed by
     `/Users/{uid}/Views`, absent from `/Library/VirtualFolders`, and `GET /Items/{id}` on them returns 400.
     **A plain container restart clears them** — the view list is rebuilt at startup from the library config,
     which was correct all along. Restart and re-read `/Users/{uid}/Views` *before* considering any database
     work; assuming the state is persistent and reaching for SQLite is how a 20-second test becomes an hour
     of theory.
3. **Resolve the provider entry deliberately**: search, print the candidate list, then filter by exact
   `first_air_date` YEAR (then ±2 with a note). Generic titles (`Pandora's Box`, `The Trap`) match a
   recent unrelated production first, and taking the first name hit binds the wrong show.

### When TMDB has no record — the BBC fallback

Some BBC documentaries have no TMDB entry at all, or TMDB's only hit is a wrong-era namesake (a
2009 Matt Frei series colliding with a 2023 heist drama). Refusing the wrong binding leaves the
work unidentified; the BBC programme site is the second provider. `media_lib/bbc.py` implements it:
`programme(pid)` for title + synopsis, `episode_guide(pid)` for episode PIDs with n/m numbering,
`gate_with_fallback()` which runs the TMDB gate first and only consults the BBC on a clean
no-match (an ambiguous TMDB hit is never overridden). Programme PIDs come from a
`site:bbc.co.uk/programmes` web search — runtime search-engine scraping from the homelab is
bot-walled, so PID discovery is an interactive step, and the code consumes PIDs rather than
searching. A BBC-matched series gets the BBC overview verbatim as a locked synopsis with NO
provider ids — an empty id set is honest, a guessed one is a Metroland error. Broadcast year
comes from the episode page transmission date, never from the PID sequence or footer.

Distinguish brand PIDs from episode PIDs before mapping: a series brand page reads as a
description of the whole series ("Documentary series telling the story of Berlin") while an
episode page describes one part — binding the brand PID as an episode misplaces the file and
the mistake looks like a correct overview. Fetch each PID's title first. On pre-2010 series
the episode-guide page carries no episode links at all, so `episode_guide()` returns empty;
fall back to per-episode PIDs from `site:bbc.co.uk/programmes` search rather than reading
the empty guide as a one-part series.
4. **Read the provider's episode count and per-episode runtimes** for the SEASON you hold.
5. **Run the guards below.** Any failure aborts and moves nothing.
6. **Name episodes from provider data**, never from filenames — `Title (Year) SxxEyy <Episode Title>.ext`
   in `Season NN/`. When two held files both plausibly fit adjacent episodes, bind the one carrying
   the exact title token first and assign the remainder by elimination — never compress the numbering
   to dodge the ambiguity.
7. **Refresh the library, then refresh the series ITEM**, then enumerate episodes and compare distinct
   paths against the files on disk before reporting success.

`media-ops` carries this as `scripts/multipart_to_series.py`; drive it entirely by env vars
(`SRC`, `QUERY`, `YEAR`, `TITLE`, `SEASON`, plus `ALLOW_PARTIAL` / `ALLOW_RUNTIME_MISMATCH`) rather than
editing a copy per title:

```
SRC="/mnt/zmedia/videos/Documentaries/<Title (Year)>" QUERY="<provider title>" YEAR=<year> \
TITLE="<Title (Year)>" [SEASON=6] python3 -u scripts/multipart_to_series.py
```

## The guards, and the failure each one exists for

| Guard | Refuses when | Why it exists |
|---|---|---|
| series already exists | target library holds the title with episodes | re-converting manufactures the duplicate that was just deleted |
| count | files ≠ provider episodes | four parts against six episodes is missing media |
| **runtime** | any file/episode ratio outside **0.75–1.33** | **a count match is not a mapping** — six 59-min files mapped onto six two-hour episodes is half a series, and every count check passes it |
| year | no candidate within the year window | a wrong binding blames the files for the provider's error |
| season | (not a guard — a required input) | without it every folder becomes Season 01 |

`ALLOW_RUNTIME_MISMATCH=1` is the only override, and it must ALSO require the **total** runtime to match
within 5% — a set whose parts are unevenly split still totals correctly, a half-series does not.
`ALLOW_PARTIAL=1` maps by part number and leaves a known gap visible.

When the provider publishes no per-episode runtimes the runtime guard cannot run: say so explicitly and
treat the conversion as unverified rather than silently passing it.

## Cleanup is the common case, and it is not a conversion

If the series already exists, the source folder is residue. Compare it to the episodes:

- `stat -c "%h links %s %n"` on both sets. **`links >= 2` with equal sizes means one copy, two paths** —
  delete the redundant PATH (`rm` of one name), keep the bytes. Never treat a hardlink as wasted space.
- Equal sizes but `links=1` on both is a genuine second copy — verify with `cmp` before removing either.
- Then refresh; the emptied folder's rows drop on their own.
- **Different sizes are not duplicates.** Equal size with `links=1` on both sides is a second copy of the
  same content — confirm with `cmp`. Different sizes are two different encodes, and the larger is usually
  the better one. A consolidation that pulled from the wrong sub-folder can leave the series holding the
  weaker set while the better rips sit in the movies library; compare sizes per episode and swap the
  better file in rather than deleting either blindly.
- **A "successful" conversion can leave the source in place.** The symptom is the movies library holding
  phantom Movie rows for the same titles the series covers (eleven `Baseball - Inning NN` movies beside an
  eleven-episode series; four `Century of the Self` movies beside a four-episode series) plus duplicate
  disk. Detect it by comparing Movie item paths against Series episode paths, and by counting what is still
  in the source folder after the run — a converter that moves leaves an empty folder behind, so residue
  means either the folder was never emptied or the copy predates the run.

The same title can also be in the wrong library entirely (a film-history series sitting in Music Docos
Series, with unused parts still in Film Documentaries). Re-file it; do not convert a second copy.

A wrongly-identified series leaves a folder behind whose `.mkv` are usually **hardlinks** (`links=2`) to the
corrected files, alongside `links=1` `.nfo`/`-thumb.jpg` from the bad match. Removing that whole folder is
safe and does not free space — the data survives under the new name and the counts drop to 1. Verify with
`stat -c "%h"` on both sides before removing, and re-list the keeper folder afterwards.

## Pitfalls

- **When the slot is occupied, keep the library's numbering and de-collide the newcomer.** Provider DVD order and broadcast order disagree on some long-running series, so a correctly-identified file can land on an episode number the library already uses for another episode. Do not renumber the established row and do not overwrite it: place the newcomer in the same season folder WITHOUT an `SxxEyy` number so it indexes as its own row, and let a later re-map decide. The collision itself is the signal — it means two orderings are in play, and picking one silently manufactures either a duplicate or a loss.

- **NEVER call `jellyfin.delete_item()` on a Series/Season/Folder row.** It unlinks the item's files, and
  for a container it takes every file beneath — that is how 5.3 GB of media was destroyed while "clearing
  a stale row". Move the files out and let the scan drop the row; the row is cheap, the media may be the
  only copy. The wrapper refuses container types unless `allow_container=True` is passed deliberately.
- **A stale/wrong identification is fixed by moving files, never by deleting the row.** For a re-do: move
  files back to the source folder, remove the now-empty season/folder, and let the scan drop the row.
- **Snapshot before a bulk destructive pass.** ZFS checksum repair cannot recover a logical unlink — scrubs
  and mirror healing fix bit rot, not a wrong `rm`. Confirm a recent snapshot exists on the media dataset
  before moving or deleting at scale; daily snapshots with 28-day retention are installed, and a restore is
  a copy out of `.zfs/snapshot/<name>/`, so a mistake costs minutes instead of a re-download.
- **Never put a colon in a generated filename.** `:` is illegal on this SMB share: the file is reachable
  only through an 8.3 alias (`CP5SS6~2.MKV`), and Jellyfin indexes that alias, so the episode comes back
  unnamed and unnumbered while the file on disk looks perfectly correct. Keep the sanitiser whitelist to
  `- ' . , & ( )`, map `:` to `-`, and expect provider titles to contain them constantly
  (`Trouble in the Family: 1337-1360`).
- **`mv … && echo OK` is not evidence.** It reported three failures on this CIFS share for moves that had
  succeeded; acting on it invites a retry that moves files twice. Confirm on disk — list the destination
  and `stat` the sizes — before treating any reported failure as real.
- **Renaming inside a scanned library needs an ITEM refresh.** Old stems persist as rows pointing at
  deleted paths while new names create fresh ones (nine files became eighteen episode rows), and renamed
  episodes sat at `SNoneENone` / 0.0 min until `jellyfin.refresh_item(series_id)` was called — a
  library-scoped refresh was not enough.
- **Parameterising a working script leaves its assumptions behind.** Env-var conversion left the old title
  hardcoded in the existing-series guard, the post-conversion lookup and the provider filter; the same
  script globbed only `.mkv`/`.mp4` (a folder of `.avi` parts read as "1 file"), forced `.mkv` onto `.avi`
  sources, and sorted by the FIRST number in the filename — the year. After parameterising, grep for the
  old literal and require zero hits, and exercise it on a folder whose extensions and naming differ.
- **Season, not just title, comes from the folder name.** A folder holding "Series 6" placed as Season 01
  passes count, runtime and title checks because the season was never a variable.
- **`- partN` sequential parts are NOT joined into one movie** on this server; `<primary> - <label>`
  alternate encodes DO group. Three or more parts of one programme → series style; exactly two → film
  style (one Movies entry).
- **Provider listings can split a programme in half.** If six files total the provider's stated series
  length but the provider lists twelve episodes, the provider is split and the set is complete — check the
  TOTAL runtime against the broadcaster's published total before concluding files are missing.
