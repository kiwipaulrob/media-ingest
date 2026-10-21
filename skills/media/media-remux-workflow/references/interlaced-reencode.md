# Interlaced broadcast re-encode (1080i → 1080i)

Cleaning a 1080i50/60 broadcast capture without changing its scanning:
stale/legacy codec, non-conformant level, mildly damaged reference
structure (decoder prints `mmco: unref short failure` / `number of reference
frames (0+5) exceeds max`). Interlacing is preserved deliberately — a
broadcast master that was transmitted as 1080i carries 50 distinct fields a
second, and deinterlacing to 25p throws half of them away permanently.

## Encode recipe (software libx264, house CRF 20)

```bash
ffmpeg -i "$SRC" \
  -map 0:v:0 -map 0:a -map 0:s -map_chapters 0 \
  -c:v libx264 -preset medium -crf 20 -profile:v high -level:v 4.2 \
  -flags +ildct+ilme -x264-params "tff=1:weightp=0" \
  -c:a copy -c:s copy -map_metadata 0 \
  -metadata encoder="libx264 CRF20 recode" \
  -f matroska "$OUT"
```

- `-flags +ildct+ilme` + `tff=1` = interlaced encode, top field first. Drop
  `tff=1` for bottom-first sources (`field_order=bb`).
- Always pass `weightp=0`: x264 refuses weightp on interlaced content, prints
  `interlace + weightp is not implemented`, and silently disables it — set it
  explicitly so the log is clean and the run reproducible.
- `-level:v 4.2` is the lowest level that contains 1920×1080 at 50 fields/s
  (408000 MB/s vs Level 4.0's 245760). A source tagged L4.0 is out of spec;
  re-tagging to 4.2 is a correctness fix, not a compatibility gamble.
- `-c:s copy` carries DVB subtitle (bitmap) and SubRip tracks into MKV
  together with `-c:a copy` for AC3/E-AC3 — no re-encode of either.
- Keep `-map_chapters 0`: broadcast packers often write a single
  whole-file chapter, and dropping it changes metadata for no benefit.

## Calibration (measured, 46 min 1080i50 gig recording)

| | source | recode |
|---|---|---|
| size | 1.78 GB (5.15 Mbps total) | 2.03 GB (5.86 Mbps, **+14%**) |
| video | h264 High L4.0 (non-conformant) | h264 High L4.2 |
| decoder error lines / 10 min | 6 | 0 |

`-preset medium` ran ~1.28x realtime on 6 cores → 36 min wall clock for a
46 min programme. Interlaced CRF 20 does not shrink a broadcast capture;
report the projected growth before starting so 'the file got bigger' is not
a surprise afterwards.

## Verification gates that actually mean something

1. **Field order — use frames, not stream metadata.**
   `ffprobe -select_streams v:0 -show_entries stream=field_order` reports
   `tb` on interlaced libx264 output that is genuinely top-field-first;
   every flag variant (`tff=1`, `-field_order tt`, both) reads back `tb`.
   The source may read `tt`. Both readings are noise. Prove it with:

   ```bash
   # decoded frames of source and output must both say interlaced + TFF
   ffprobe -v quiet -read_intervals 600%+2 -select_streams v:0 \
     -show_entries frame=interlaced_frame,top_field_first -of csv=p=0 "$F"
   # and the Matroska Track Video element must carry FieldOrder=9 (TFF)
   ```

   `FieldOrder` values in Matroska: 9 = top field first, 6 = bottom field
   first, 1 = interlaced but unspecified. Verify by parsing the Track
   `Video` element (`9A` FlagInterlaced, `9D` FieldOrder) out of the first
   ~40 KB — if the element is ABSENT the source never declared its field
   order at all, and writing it is part of the fix.
2. **Frame cadence — compare pts sequences, not `r_frame_rate`.**
   A 1080i50 file decodes 25 frames/s at 0.04 s spacing while reporting
   `r_frame_rate=50/1` (nominal field rate). Count decoded frames inside a
   fixed window on both files — a 2 s window yields 50 frames in each — and
   diff the first few `pts_time` values. An encoder that halved the field
   rate still produces a matching container duration, so duration alone
   cannot catch it.
3. **Duration and streams:** container duration within ±2 s of the source
   and identical stream counts before swapping.
4. **Decode integrity:** 20 s spans at ~5%, ~50% and near-end with
   `-xerror`; then a 10-minute error-line count for the before/after claim.
5. **Seek index:** run the seekability audit
   (`jellyfin-administration` → `scripts/mkv-seekindex-audit.py`); ffmpeg
   writes a valid leading SeekHead+Cues, but prove it rather than assume,
   because an unseekable recode is worse than the original.

## Operational notes

- The `-stats` line's final `time=` can read ~12 s short (it tracks the last
  muxed frame, not the longest stream). Gate on the probed container
  duration; ignore it.
- Launch detached inside the media LXC — `nohup setsid /root/<script>.sh >
  /root/<script>.log 2>&1 < /dev/null &` — because a foreground
  `ssh pct exec` run dies with the session, mid-encode, leaving a partial file
  that must be thrown away. Poll the remote log (`tail` the last ~200 bytes)
  for `fps=`, `speed=` and a final `FFMPEG_RC=0` line.
- Swap onto the SAME filename: the Jellyfin row survives, watch state and
  provider metadata are untouched, and no phantom duplicate appears. Park
  the original under `_replaced/<label>-<date>/<lib>/<folder>/`.
- Materialise the temp output OUTSIDE the library tree (share-root
  `_staging/`), never beside the media — a partial `.mkv` inside a library
  folder is scanned as a separate row.
- After the swap, fire a per-item refresh
  (`POST /Items/{id}/Refresh?Recursive=true&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh`,
  both ReplaceAll flags false) and re-read `MediaSources`: size, bitrate and
  level updated within ~10 s, confirming the server re-probed the new bytes.
