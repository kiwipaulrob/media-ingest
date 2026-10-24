# Handoff — media ingest session, 2026-02

Pick up here. Read this before touching the library.

## Completed and verified

Two titles placed and verified (probe, decode, av-sync, Jellyfin row):

- `musicdocos/White Riot (2019)/White Riot (2019).mkv` — 1920x1036, 4821.2s, sync ok, index ok
- `musicdocos-series/Later with Jools Holland (1992) [tmdbid-10959]/Season 50/Later with Jools Holland (1992) S50E05.mkv` — 1920x1080, 3656.0s, sync ok, index ok

Sources deleted after verification:
- White Riot payload (1370 MB) + torrent entry removed. Four subtitle tracks rescued into the
  library folder first: nor.srt, dan.srt, fin.srt, swe.srt.
- Jools S50E05 `.ts` was already absent — nothing to delete.

## OPEN — fix this first

White Riot EXISTS TWICE:

```
848 MB   musicdocos/white.riot.(2019).mkv          <- pre-existing, has .nfo, 80.5 min row
1372 MB  musicdocos/White Riot (2019)/...mkv       <- placed by this session, 80.4 min
```

My deletion guard refused and that was correct: the comparison ran a hand-built `ffprobe`
line that returned NOTHING for the old file (path parentheses mangled it), so "keep mine"
was decided on a failed measurement. Do not delete either until both are probed properly:

```
python3 -c "
import sys; sys.path.insert(0,'.')
from media_lib import probe
for f in ['/mnt/zmedia/videos/musicdocos/white.riot.(2019).mkv',
          '/mnt/zmedia/videos/musicdocos/White Riot (2019)/White Riot (2019).mkv']:
    print(f, probe.probe(f))"
```

Keep the higher resolution, delete the loser, refresh the library so the dead row clears.

## OPEN — the remaining plan (steps 2-6)

1. Re-scan WITH an existence check. `cli.scan`, then filter every `all_videos` path through
   `[-f "$p"]` before it can reach a spec. Prevents all 7 phantom rows from last run.
2. Ingest the packs that are real: S56, S58, S62. **S65 was never downloaded** — those rows
   were phantom.
3. Run the two genuine upgrades through `cli.apply`, NOT ingest: Urgh (464x336 -> 1280x720)
   and The Killers (640x352 -> 1920x1080). Killers must target the EXISTING folder or it
   becomes duplicate #2.
4. Four `VIDEO_TS` sources need the DVD chain (VOB -> MKV encode, `bwdif`, timestamps
   regenerated) — hours each, one at a time with a frame-count check. Not an ingest job.
5. Review the 15 planner-flagged rows: 6 PROBE-FAIL, 5 weak match, 4 multi-file.

## Corrected this session

- `probe.decode_check` no longer reports playable files as corrupt. It used `on_media()`,
  which raises on ffmpeg's non-zero exit after recovered warnings, and treated ANY output as
  failure. A 1080i HDTV capture emits `mmco: unref short failure` / `reference picture
  missing` on a mid-GOP seek — normal chatter. Now uses `query()` and ignores known-benign
  lines; still fails on `error while decoding`. Commit 718f471, 319 tests.
- Skill `music-docos-library-maintenance` now says: build specs from `plan.json` not
  `scan.json`. Commit 6b1c010.

## DO NOT DELETE — these look like orphans and are not

The `.part N` files in `musicdocos/` are the library's multi-part programme convention, each
with a Jellyfin row: Jimi Hendrix The Uncut Story (1,2,3), The Brian Epstein Story (part1,2),
Let the Music Play/Barry White (part1,2), Soul America (1,2,3), Laurel Canyon (1,2),
Disco Soundtrack of a Revolution (1,2,3). An earlier note flagged them as orphaned partials;
that was wrong.

## Root cause to remember

This session's mess came from hand-assembling an ingest spec out of `scan.all_videos`
instead of asking `cli.plan`, which had ALREADY classified every item (Killers = REPLACE,
15 rows = review). The shortcut produced the duplicate, the 7 phantom placements and the
release-junk folder names. The superiority gate added to `ingest_new` treats the symptom;
using the planner treats the cause.

Rule: when a held copy cannot be measured, stop. Unmeasurable is not worse, and not absent.

## UPDATE — later session state
Done and verified (each read back from disk, not trusted from a return code):
- White Riot duplicate resolved (kept 1920x1036, deleted 544x576, row 80.4min)
- Urgh upgraded 464x336 -> 1280x720 (verified decode/sync/index)
- Elvis Thats the Way It Is upgraded 534p -> 1920x800 (5698.7s, byte-identical to source)
- The Killers 1080p placed as third item (1920x1080, row 119.2min)
- 20 torrents removed with per-item evidence (~19.5GB payload + ~26GB empty packs)
- S56 torrent deleted - it actually contained S65 (already ingested, 6 eps in library), 0 video files left
- 4 code/skill fixes committed and CI-green: 718f471, 6b1c010, 9396d53, 9306911

### Open
- **S56** — genuinely missing - the S56-labelled torrent delivered S65. Needs fresh download.
- **Grateful_Dead_DVD** — /mnt/zmedia/downloads/Grateful Dead - Anthem To Beauty (Classic Albums)/VIDEO_TS - VOB chain then Tdarr, one trial first
- **Def_Leppard_DVD** — /mnt/zmedia/downloads/Def Leppard - Classic Albums. Hysteria/VIDEO_TS - 8 VOBs, kept deliberately
- **Lennon** — source deleted by error (dot-vs-space guard); library copy intact at 576; needs redownload for 576->720 upgrade
- **Urgh_source** — 1525MB still in downloads; new copy is 120.9min vs old 124.9min - awaiting decision

### Do not delete
- musicdocos/*part* multi-part programmes, not orphans

Partials: 476GB in qBittorrent left alone per instruction
