---
name: media-av-sync
description: "Use when media audio and video are out of sync."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [media, sync, audio, jellyfin, ingest]
---

# A/V sync: verify structurally, repair the audio only

## When to Use

- The user reports sound out of step with the picture on a film or episode.
- Auditing recently ingested media for files whose audio and video disagree.
- A file passes every other ingest gate (complete, right duration, decodes) but plays wrong.
- Choosing between repairing a file and replacing it.

For "the sound is out of sync on X", and for auditing a library for files whose audio and
picture disagree. Code lives in `media_lib/avsync.py`; library-wide audit is
`scripts/sync_audit.py`; ingest enforcement is `media_lib/place.place_new()`.

Commands, benchmark numbers and the repair matrix: `references/sync-commands.md`.

## Only container timestamps are measurable — never correlate

Two facts are obtainable: when each stream **starts**, and when each stream **ends**. That
is enough for every real fault. Correlating on-screen motion against the audio envelope was
attempted and peaked at **r≈0.06** over real content — noise. Do not invent an offset from
correlation; if the user reports audible desync that the timestamps pass, suspect the source
and say so.

## MKV reports no per-stream duration — derive it, or the check silently passes

`ffprobe -show_entries stream=duration` returns `N/A` on most Matroska files, so a check
written as `if v_dur and a_dur:` never fires and reports a badly desynced file as fine.
Derive each stream's end from its **last packet** (`pts_time + duration_time`).

Two traps when doing it:

- **`-select_streams` takes the audio ORDINAL, not the stream index.** A single-audio file is
  `a:0` even when that stream's `index` is `1`. Selecting `a:1` matches nothing, yields no
  duration, and the drift check passes silently — the exact failure the check exists to catch.
- **Never compute audio length from packet counts.** `packets × frame_samples / rate` assumes
  a fixed frame size: true for AAC (1024) and AC-3 (1536), false for mp2/mp3 and VBR. A
  packet-count pass flagged healthy files as **+46 s** and **−497 s** out of sync; last-packet
  measurement put the same files within 100 ms.

## Tier the work — truncation is cheap to test, drift is not

Disk I/O dominates (~25 s per full pass over CIFS), so split by question:

| Tier | Question | Signal | Cost | Finds |
|---|---|---|---|---|
| fast | does the picture reach the end? | `-sseof -10`, count decoded frames | 1–3 s | truncated picture |
| full | do both streams end together? | last-packet pts, v:0 and a:0 | ~25 s | drift, offsets |

**Gate the truncation test on the FRAME COUNT, never the exit code** — a null muxer exits 0
whether or not a frame was produced. `frame=0` means the stream stops early.

Run the fast tier routinely (a whole library in ~25 min at 6-way parallel) and the full tier
overnight (a few hours). Do not attempt drift detection with a fast probe: MKV omits the
durations that would make it possible.

## Repair before rejecting — and never re-encode the picture

Try, in order: shift the audio (`-itsoffset`, stream copy) → trim an over-long audio track
(`-c copy -shortest`) → regenerate only the audio timeline (`asetpts=N/SR/TB`, audio-only
re-encode). Only truncation is terminal: there are no frames to align to, so refuse it and
replace the file. Check truncation FIRST — a tail probe answers in seconds what a full packet
scan takes 90 s to reach, and never call the full check on that path just to fill a
diagnostic field.

Two repairs are silent-damage traps — both yield a file that looks repaired and passes a
naive check while having destroyed content:

- **Never use `-shortest` against SHORT audio.** It ends the output at whichever stream
  finishes first, so audio 3 s short silently cuts 3 s off the picture. Pad the audio
  (`apad`) and let `-shortest` trim only the padding.
- **A stream-copy trim cannot repair timestamps.** `-c copy` carries broken PTS through
  unchanged, so trimming a looping-timestamp file changes nothing. Re-measure the output and
  fall back to an audio retime when it still fails.
- **Map the input the offset was applied to.** `-itsoffset` shifts the input that follows it,
  so the repair opens the file twice and must map `1:a:0`; `-map 0:a:0` copies the unshifted
  audio, exits 0, and changes nothing. Pass `-y` as well — without it ffmpeg refuses a leftover
  temp, prints nothing under `-v error`, and a retry silently measures the stale file.

**A stubbed probe proves the branch, not the command.** The no-op above passed a suite that
stubbed the probe and asserted the branch. Build the fault with lavfi (`testsrc2` + `sine`,
offset or short/long audio), run the real repair, and compare the picture's end timestamp
before and after: a trim-to-audio repair also verifies clean, so only the video end proves no
frames were lost. Never trust a repair command's exit status — verify by re-measuring the
output. See `references/sync-commands.md`.

## Sign matters: drift vs trailing credits

- audio running **past** the picture → drift; repair or refuse (>750 ms).
- audio **starting** offset ≥250 ms → audible from the first line; repair or refuse.
- audio ending a second or two **short**, both streams starting at 0 → credits with no sound;
  warn, do not re-encode a feature over it.

Reference: ~34 ms reads as in sync; ~920 ms is audible; a couple of seconds of silence under
end credits is normal.

## A large shortfall needs evidence — the gap tells you which stream is short

A negative drift (audio ends before the picture) has two completely different causes, and
neither the size of the shortfall nor any duration probe distinguishes them: the audio may
simply stop at the credits, or the audio track may be truncated. What separates them is what
the **picture** does during the gap:

```
ffmpeg -v info -sseof -<gap+30> -i FILE -map 0:v:0 \
  -vf blackdetect=d=1:pix_th=0.10 -an -f null - 2>&1 | grep -o 'black_start:[0-9.]*'
```

- **black events across the gap** → the picture is end credits with no sound. Benign; do not
  re-encode. A 2.5-minute silent credits roll is normal for a documentary.
- **no black, picture continues** → the audio track is truncated mid-content. A real fault;
  replace the file, since nothing can regenerate missing audio.
- Boundary case: a shortfall of a few seconds is credits far more often than a fault. Reserve
  replacement for gaps measured in minutes, or for gaps with picture under them.

Classify before reporting. "N files out of sync" is not actionable; "3 pictures truncated,
1 audio truncated, 1 silent credits roll, 22 offsets under a second" tells the user what
to spend attention on, and keeps them from rebuilding files that are fine.

## Auditing a source class, not the whole library

When the ask is "check the DVD-sourced films", scope the sweep to that provenance rather
than running the full library:

- **Enumerate by path AND by known rebuilds.** `find -ipath '*DVD*' -o -ipath '*DVDRip*'
  -o -ipath '*PAL*' -o -ipath '*VIDEO_TS*'` finds the files whose names advertise their
  origin, and misses every programme rebuilt from VOBs into a clean library name — a rebuild
  carries no provenance in its filename. Add those explicitly.
- **Measure the subset completely, 6-way parallel.** A dozen files finish in minutes, so
  sampling is unnecessary once the subset is small.
- **Provenance is not a predictor of sync quality.** Measured over 18 DVD-derived files: 16
  within ±0.2 s, and both real faults came from one disc family with looping MPEG-2
  timestamps. Never re-encode or replace a whole source class on the assumption that its
  origin makes it suspect.
- **A sample-clip file measures as unmeasurable, not as broken.** A 33-second `SAMPLE`
  extract fails a tail probe because there is no tail worth seeking to; exclude non-features
  before counting failures.
- **Posters and sidecars are not media.** A `.jpg`, `.webp` or `.nfo` beside a film decodes
  as a one-frame "video", so a bare `find` over extensions drags them into the sweep and they
  surface as unmeasurable, inflating the failure count. Filter candidates by duration
  (≥60 s) at collection time, not in the report afterwards.

## Put the gate at ingest, and never delete the payload first

A file can pass completeness, size, height, duration and full decode and still have audio
running a second past the picture — sync is the only gate that sees it. Run the check as the
last gate in the placement path: repair, re-verify, place marked repaired; refuse and remove
the copy only if the repair fails. When the check lives in a download-ingest script, keep the
payload on failure — deleting it destroys the only thing left to re-mux from.

## "In sync at the start, drifts later" — endpoints agree, so measure the container

Matching stream ends does not mean the file plays in step. That fault is not a timestamp
fault, so the repair matrix above cannot touch it — work the ladder in
`references/progressive-drift-triage.md` (missing frames → decoded content length → join
check → index integrity) before re-encoding anything.

Three traps that produce confident wrong answers:

- **A second `RIFF` header in an AVI is OpenDML splitting, not a join.** Large AVIs carry
  `RIFF`+`AVIX` at 70–80% of the file by design; a real concatenation shows as a PTS
  discontinuity instead.
- **Never sum PTS gaps on a stream that reports `N/A` timestamps.** Deltas between
  consecutive *valid* packets span the missing ones and manufacture hundreds of phantom
  gaps — 543 gaps / 798 s was reported for a file that decodes to its correct 211,443
  frames. Decode both streams to null and trust that instead.
- **The decoder is the authority, not the container.** Read the `frame=`/`time=` line from a
  `-f null -` decode; the exit code is 0 whether or not anything was produced.

If most video packets report `N/A` pts the index is damaged: players estimate timestamps and
the estimate degrades over the runtime — exactly "fine, then drifts". Rebuild with
`setpts=N/FRAME_RATE/TB` plus `aresample=async=1:first_pts=0` and verify the output's
decoded frame count against the original's. If the drift survives a clean rebuild, the
source audio is genuinely offset and no amount of re-encoding will fix it — replace it.

Detect it by **sampling the middle, not the head** (command and thresholds in the reference).
Reading the first packets of a damaged file passes it, because the damage usually starts
further in.

Two rules keep such a gate honest:

- **An empty or unreadable probe is `unknown`, never `ok`.** A probe that returns nothing is
  not evidence of a clean file; collapsing "could not measure" into "measured fine" is the
  bug the gate exists to prevent.
- **The finding must reach the verdict.** A check whose result never raises a warning reports
  the file as healthy while the fault is already known — a damaged-index film was returned as
  `ok` by a check that measured it correctly and then discarded the result. When adding a
  probe, wire it into the verdict in the same edit, and make the placement gate carry it
  (`needs_rebuild`) so the fault is visible downstream.

## Sourcing a replacement when the indexers have nothing

Platform-only documentaries (single-episode video essays, channel uploads) are frequently
absent from every torrent and Usenet indexer. "Nothing found" there does not mean the file is
unrecoverable — check the platform before telling the user it is a dead end.

- **The held file's remaining stream is the specification.** When the picture is truncated and
the audio is complete, the replacement's total duration must match the **held audio**
duration. A match independently confirms you found the same cut, and it is far stronger
evidence than a title search.
- **Verify the download before it touches the library** — tail frame present, both stream ends
  agreeing, index clean. A platform copy is not trustworthy by default.
- Prefer a container that keeps both streams (`--merge-output-format mkv`) so the sync check
  can be run on the artifact you are about to place.

## Putting a rebuilt file back

A rebuild that is never swapped in is not a fix, and the swap has its own failure modes:

- **Resolve the target library by exact name or ItemId**, never a substring test — `Film`
  matches `Film Noir` and `Film Docos` and `Film Docos Series`, and the refresh returns 204
  either way, so the wrong section is refreshed while the old file keeps serving.
- **Quarantine the original, never delete it.** A rebuild that turns out to sound wrong leaves
  the original as the only fallback. `chmod` the replacement to match its siblings. Delete the
  quarantined original only when the replacement has been **re-verified** (sync verdict clean,
  index clean, correct duration) and the user has asked for it — a quarantine directory that
  is never emptied is just a second copy, and one that is emptied too early removes the
  fallback before the fix has proved itself.
- **A source that broke one file broke its siblings — sweep the series.** Two episodes of one
  documentary series were truncated in the same way (complete audio, parked picture) while a
  third from the same batch was fine. When one member of a batch fails, audit the rest of that
  batch immediately; provenance alone is not a predictor, but a specific bad download batch is.
- **A new path is a new item id, so watch state does not follow it.** Read `UserData`
  (`Played`, `PlaybackPositionTicks`) from the old id before the swap and re-apply it to the
  new one.
- **A new path has never been probed.** The row appears with `RunTimeTicks: 0` and stays that
  way through library-scoped refreshes; force a per-item `MetadataRefreshMode=FullRefresh` and
  re-read the duration. The row's presence is not proof it was probed.
- **Say what you verified and what you did not.** Frame count, container integrity and stream
  alignment are checkable; whether it *sounds* right is not. Ask the user to spot-check the
  timestamp where they noticed the fault, and do not report a clean rebuild as a fix.

## A clean audit is not proof of good sync

Both streams ending together means no *structural* fault; it cannot see progressive drift
through the middle, which no endpoint measurement can detect. Report it as "no structural
fault found", never as "sync verified".
