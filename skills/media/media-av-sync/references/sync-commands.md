# Sync check: commands, timings, repair matrix

Everything here was measured on a CIFS-mounted 11 TB pool. Cost is I/O, not CPU.

## Measuring each stream's end (the only reliable signal)

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries packet=pts_time,duration_time -of csv=p=0 "$f" | tail -1
# 2738.720000,0.040000  ->  end = 2738.720 + 0.040 = 2738.76
```

Run once for `v:0` and once for `a:0`; the difference between the two ends is the drift.

Why not cheaper probes — all measured on the same 74-minute file:

| Method | Time | Verdict |
|---|---|---|
| last packet pts | 21 s | correct |
| packet count × frame size | 19 s | **wrong for mp2/mp3** (+46 s, −497 s false alarms) |
| `ffmpeg -c copy -f null` | 21 s | no usable end timestamp |
| `ffprobe -read_intervals 99%` | 21 s | correct but no faster |
| `-sseof` + `-copyts` + `-progress` | 1 s | reports **0** (output-relative) |
| `-sseof -10` + frame count | **0.7–2.8 s** | truncation only, not drift |

`-copyts` does not carry absolute time into `-stats`/`-progress`, and ffprobe has no
`-sseof`. The tail frame count is the only usable signal from a seek-to-end.

## Truncation test (fast tier)

```bash
ffmpeg -v error -stats -sseof -10 -i "$f" -map 0:v:0 -frames:v 1 -f null - 2>&1 \
  | tr '\r' '\n' | grep -o 'frame=[ ]*[0-9]*' | tail -1
# frame=    1  -> picture reaches the tail
# frame=    0  -> picture stops early: truncated
```

Exit status is 0 either way — the null muxer does not fail on zero frames. Parse the count.

## Repair matrix (`avsync.resync`)

Two of these rows are traps: each produces a file that *looks* repaired and passes a naive
check while having destroyed content. Read the picture column, not just the command.

| Fault | Command shape | Picture |
|---|---|---|
| start offset ≥250 ms | two inputs: `-i in -itsoffset <shift> -i in -map 0:v:0 -map 1:a:0 -c copy`, `shift = -start_delta_s` | untouched |
| audio past video | `-map 0 -c copy -shortest`, then **verify and fall back** | untouched |
| audio short of picture | `-map 0:v:0 -map 0:a:0 -c:v copy -af "asetpts=N/SR/TB,apad" -c:a aac -b:a 192k -shortest` | stream-copied |
| video ends >5 s early | refuse: `video-truncated` | unrepairable |

**Never reach for `-shortest` when the audio is SHORT.** `-shortest` ends the output at the
first stream that finishes, so a 3-second-short audio track silently truncates the picture by
3 seconds. Pad the audio (`apad`) and let `-shortest` cut only the padding.

**A stream-copy trim cannot repair timestamps.** `-c copy` carries broken PTS through
unchanged, so `-c copy -shortest` on a looping-timestamp file trims nothing meaningful and the
file stays desynced. Always re-measure the output; if it still fails, retry as an audio retime.

**Sign of `-itsoffset`:** it shifts the timestamps of the input that FOLLOWS it, so the file is
opened twice — input 0 is the picture, input 1 is the audio with the shift applied. Audio
starting late by +400 ms needs `shift = -0.400` (pulled earlier) and `-map 1:a:0`.

**Map the input the shift was applied to, not input 0.** `-map 0:a:0` copies the *unshifted*
audio from the first input: ffmpeg exits 0, writes a complete file, and nothing changes.
Measured on a 400 ms-delayed clip — still 400 ms after that "repair", and 0 ms with
`-map 1:a:0`. A no-op that reports success is the worst failure shape here, because the
caller records the file as repaired.

**Pass `-y` on every repair command.** Without it ffmpeg refuses to overwrite an existing
output, prints nothing under `-v error`, and the previous temp file is left in place — so a
retry "succeeds" while you measure the stale file. This is what hid the mapping bug above.

`asetpts=N/SR/TB` rebuilds the audio timeline from the sample count — the audio twin of
`setpts=N/FRAME_RATE/TB`. For a DVD/VOB-sourced file with looping MPEG-2 timestamps both are
needed: video drift and audio drift are independent faults from the same cause.

After any repair, re-run the end-timestamp measurement on the OUTPUT; a repair that does not
move the numbers did nothing.

## Proving a repair actually ran

A unit test with a stubbed `on_media` proves which branch was chosen, never that the command
does anything — the no-op above passed exactly such a test. Build the fault with lavfi and
repair it for real:

```bash
# constant offset: audio delayed 400 ms
ffmpeg -y -v error -f lavfi -i testsrc2=size=320x240:rate=25:duration=30 \
  -itsoffset 0.4 -f lavfi -i sine=frequency=440:duration=30 \
  -map 0:v:0 -map 1:a:0 -c:v libx264 -preset ultrafast -c:a aac -shortest offset.mkv
# short audio (-4 s) / long audio (+4 s): vary the sine duration and drop -shortest
```

Assert three things per branch: the reported `method`, the output's start/duration delta, and
— for the pad branch — that the **video end is unchanged**. A trim-to-audio repair also
verifies clean, so only the picture's own end timestamp proves no frames were lost. Measured:
400 ms → 0 ms; 26 s of audio padded to 29.95 s with video still 29.96 s.

## Library-wide audit

```bash
python3 scripts/sync_audit.py --fast --days 30 --parallel 6   # truncation, minutes
python3 scripts/sync_audit.py --full --days 3650 --parallel 6 # drift, hours
python3 scripts/sync_audit.py --report                        # read the last results
python3 scripts/sync_audit.py --classify                      # label every flagged row
```

Writes `/media/sync-audit-<mode>.tsv` on the media node, so a long run is resumable and the
results can be re-read without re-scanning. Measured: 84 files in 2.5 min fast (1.8 s/file
effective at 6-way); full tier ~25 s/file, so ~800 files ≈ 5–6 h.

Serial runs are painful: 7 files in 10 minutes at -P 1. Parallelism, not a faster probe, is
what makes a whole-library sweep practical.

A 12-file random sample of a healthy library came back all within ±0.4 s — treat a whole-file
sample as a cheap health signal before committing to a full sweep.

## Subset audit by provenance

For "check the DVD-sourced films", do not sweep the library — scope it:

```bash
find /media/videos/<lib> -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' \) \
  \( -ipath '*VIDEO_TS*' -o -ipath '*DVD*' -o -ipath '*PAL*' -o -ipath '*dvd*' \) | sort -u
```

Then **append the files you know were rebuilt from VOBs** — a re-encode carries no DVD
marker in its name, so the path filter undercounts by exactly the programmes that were most
likely to be damaged at source.

Measure every file in the subset (no sampling needed at this size), 6-way parallel:

```bash
xargs -d '\n' -P 6 -I{} sh -c '
  f="$1"
  v=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time -of csv=p=0 "$f" | tail -1)
  a=$(ffprobe -v error -select_streams a:0 -show_entries packet=pts_time,duration_time -of csv=p=0 "$f" | tail -1)
  printf "%s\t%s\t%s\n" "$f" "$v" "$a" >> /media/sync-subset.tsv
' sh {} < /tmp/subset.list
```

Measured over 18 DVD-derived files: 16 within ±0.2 s, one truncated (audio +1965 s past a
picture that stopped at 20:44), one −1.9 s tail gap. Both faults came from the same disc
family; the VOB rebuilds measured +0.0/+0.2/+0.0 s. **Provenance does not predict sync
quality** — judge each file, not its origin.

A short `SAMPLE` extract reports no measurement (a 33-second file has no meaningful tail) —
exclude non-features rather than counting them as failures.

## Index integrity (damaged container)

Both stream ends and the frame count can be correct while the file still plays out of step: if
most video packets carry no timestamp, the player estimates them and the estimate degrades
over the runtime. Sample the **middle** — damage usually starts further in, so the opening
packets pass.

```bash
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 \
  -read_intervals '50%+2' "$f" | grep -c N/A
```

Measured over a 2-second window at the midpoint:

| File | untimed / sampled | ratio | verdict |
|---|---|---|---|
| damaged AVI | 53 / 60 | 0.88 | unreliable |
| healthy MKV | 0 / 49 | 0.00 | ok |

Thresholds: ≥0.10 → unreliable; >0 but under → partial; **an empty sample is `unknown`, never
`ok`** — a probe that returned nothing is not evidence of a clean file.

Repair is a rebuild, not a remux: `-vf setpts=N/FRAME_RATE/TB` plus
`-af aresample=async=1:first_pts=0`, then compare the decoded frame count against the
original (211,443 in → 211,443 out).

## Classifying a shortfall: credits or truncated audio

A negative drift has two causes and its size does not distinguish them. Ask what the
**picture** does during the gap:

```bash
# window = |gap| + 30 s
ffmpeg -v info -sseof -<window> -i "$f" -map 0:v:0 \
  -vf blackdetect=d=1:pix_th=0.10 -an -f null - 2>&1 | grep -o 'black_start:[0-9.]*'
```

- black events across the gap → silent end credits. Benign; never re-encode a feature for it.
  Measured: −154 s shortfall, picture black through the tail.
- no black, picture continues → the audio track is truncated mid-content. Replace the file;
  nothing regenerates missing audio. Measured: audio stops at 16.1 min of a 29.4 min file,
  with no black anywhere in the last 200 s.

**Grep `black_start:`, not `blackdetect`.** The filter name also appears on ffmpeg's progress
lines, so matching it returns `frame=` counters and reads as "black found" for every file.

## Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `START_WARN_MS` / `START_FAIL_MS` | 100 / 250 | stream start offset |
| `DRIFT_WARN_MS` | 500 | end mismatch worth reporting |
| ingest refuse | >750 ms audio past video, or start ≥250 ms | |
| `AVSYNC_TRUNCATION_TOLERANCE_S` | 5.0 | beyond this the picture is missing, not out of step |
| ITU-R BT.1359 | +45 / −125 ms | audibility reference |

## Verdict wording

Report "no structural fault found" rather than "sync verified" — endpoint agreement cannot
prove the middle of the programme is aligned, and trimming fixes endpoints, not progressive
drift.
