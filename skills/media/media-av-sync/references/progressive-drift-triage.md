# Triaging "it drifts after N minutes" when the endpoints agree

A file whose streams start together and end together can still play progressively out of
step. Nothing in the endpoint check can see that, and the repair matrix (shift / trim /
retime) does not apply — those fix a *timeline* fault, and here the timeline is already
consistent. Work this ladder cheapest-first before re-encoding anything.

## 1. Are frames missing? (content shorter than the timeline claims)

```bash
ffprobe -v error -select_streams v:0 -count_packets \
  -show_entries stream=nb_read_packets -of csv=p=0 FILE
expected = container_duration * fps
```

A large shortfall means the picture advances more slowly than its timestamps, so it falls
further behind the audio the longer it plays — the classic "fine, then drifts" signature.
A shortfall of tens of frames over two hours is noise, not a fault.

## 2. What do the streams actually decode to?

Timestamps and container duration can both be wrong; the decoder is the authority.

```bash
ffmpeg -v error -stats -i FILE -map 0:v:0 -f null - 2>&1 | tr '\r' '\n' | grep 'frame=' | tail -1
ffmpeg -v error -stats -i FILE -map 0:a:0 -f null - 2>&1 | tr '\r' '\n' | grep 'time='  | tail -1
```

Read the `frame=` count and `time=` it prints — not the exit code, which is 0 either way.
Both streams reaching the container duration means the content is complete and the fault is
not truncation or frame loss.

## 3. Is there a join? (and what is NOT a join)

```bash
grep -abo 'RIFF' FILE | head
```

**A second `RIFF` header in a large AVI is normal.** OpenDML splits at ~1 GB, so a 1.4 GB
file legitimately shows `RIFF` at offset 0 and another at ~74% of the file — look for `AVIX`
next to it and it is the index extension, not two programmes glued together. A genuine
concatenation shows up as a **PTS discontinuity**, not as a RIFF boundary.

## 4. Is the index damaged?

Sample the **middle**, not the head — 2 s is enough and costs seconds:

```bash
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 \
  -read_intervals "50%+2" FILE | awk '{t++; if ($0 ~ /N\/A/) n++} END {print n"/"t}'
```

Measured separation is unambiguous: damaged AVI **53/60 untimed (0.88)**, healthy MKV
**0/49 (0.0)**. Treat **≥10% untimed** as a damaged index.

**Do not sample with `| head`.** The first packets of a damaged file often still carry
timestamps; the damage begins further in, so a head-only read passes the exact files this
step exists to catch. Midpoint sampling is what turns this from an anecdote into a gate.

Most packets reporting `N/A` means the index is broken. Players then *estimate* timestamps
from the frame rate, and the estimate degrades over the runtime — which produces exactly
"in sync at the start, drifts later" with no timestamp fault to find. Rebuild the timeline
(step 6) rather than hunting for an offset.

**Never sum PTS gaps on such a stream.** Deltas between consecutive *valid* packets span the
`N/A` ones, manufacturing phantom gaps: a file that decodes to the correct 211,443 frames
and 7056 s reported "543 gaps, 798 s of missing time" from that arithmetic. The measurement
was pure artifact — the decode in step 2 disproved it in one line.

## 5. Content offset — the case that is not repairable

If the frames are complete, the streams decode to the same length, and the index is clean,
the audio content itself is offset or speed-changed relative to the picture. No timestamp
work fixes that, because the timestamps were never wrong.

Correlation is the only method that measures it, and it is unreliable on this material: a
talking-head documentary has no coupling between picture activity and audio energy, and the
best peak lands at **r≈0.06 — noise**. It only works on strong transients shared by both
streams (claps, drum hits, slams, gunshots). Never fabricate an offset from a weak peak.

## 6. Rebuild the timeline

For a damaged index with content assumed aligned, regenerate both timelines and let the
frame count define the video:

```bash
ffmpeg -y -v error -stats -i FILE -map 0:v:0 -map 0:a:0 \
  -vf "setpts=N/FRAME_RATE/TB" -c:v libx264 -crf 20 -preset veryfast \
  -af "aresample=async=1:first_pts=0" -c:a aac -b:a 192k \
  -movflags +faststart OUT.mkv
```

Verify the output against the **original's decode**, not its metadata: frame count and
decoded duration must match (211,443 frames / 7056.02 s), and the endpoint drift must fall
inside tolerance. A rebuild that normalises the timeline but leaves the drift unchanged
proves the fault is content-level — stop re-encoding and replace the release.

At ~5x realtime this is a background job (two hours of SD ≈ 25 min); launch it detached and
check progress from its log rather than holding a session open.
