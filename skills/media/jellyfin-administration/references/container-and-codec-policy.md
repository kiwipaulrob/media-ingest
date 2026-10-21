# Container & codec policy — what to standardise on, and when to remux

Answers two recurring questions: *"should I remux this to MP4?"* and *"what container should the
library use?"* Both are decided by the codec far more than by the container.

## The rule that settles most of it: the container is a MINOR lever, the codec is the MAJOR one

A remux is a stream copy — free, lossless, seconds per file, no re-encode. But it changes only the
box, never whether the video decodes. A browser will not play MPEG-4 Part 2 in **any** container, so
remuxing a DivX/XviD `.avi` to `.mp4` yields an `.mp4` that still will not play. Check the codec
first; if it is legacy, the container is irrelevant and the answer is recode or replace.

## Remux to MP4 only when ALL THREE hold

| Condition | Why |
|---|---|
| Video is `h264`, `vp9` or `av1` | the only video codecs browsers decode natively |
| Audio is `aac`, `mp3`, `opus`, `vorbis` or `flac` | browser-verified playable — see the probe table below |
| **No image-based subtitle track — unless the media is English** | MP4 physically cannot store bitmaps — see below |

If any condition fails, leave the container alone and fix the codec (or replace the file).

**AC3, EAC3 and DTS are NOT browser-safe**, even though MP4 can physically carry them. Container
capability and client capability are different questions, and conflating them is the commonest way
this advice goes wrong: an `.mp4` carrying AC3 audio is a perfectly valid file that still will not
play in a browser. Never list an audio codec as MP4-safe on the strength of the container spec
alone — probe it.

## What MP4 gives up — the real downside list

| Downside | Mechanism |
|---|---|
| **Cannot hold image-based subtitles at all** | `PGSSUB`, `DVDSUB`, `DVBSUB`, `DVBTXT` are bitmaps; MP4's only subtitle slot is `mov_text` (text). MKV carries any subtitle format |
| **ASS/SSA loses all styling** | converting to `mov_text` strips positioning, fonts and karaoke timing |
| **Multi-track audio is patchier** | MKV carries AC3/DTS/EAC3/FLAC/Opus unconditionally; MP4 client support varies |
| **Needs `+faststart`** | without it the index sits at the end of the file and starts/seeks suffer |
| **Renaming resets the Jellyfin item** | a new extension creates a NEW row: watch state, resume position and provider links are lost. On titles that have never been played this is free; elsewhere it is a real cost |

**Browser-native containers** (MDN's list): MP4 and WebM. **MKV and AVI are not browser containers** —
content inside them must be remuxed on the fly before a browser can play it.

## Probe the client instead of reasoning about it

Codec support is per-ENGINE and per-BUILD, and published tables lag reality, so settle it
empirically before advising on a batch. A real browser answers in one call — `canPlayType` returns
`"probably"` / `"maybe"` / `""`, where **empty means unsupported**:

```javascript
const v = document.createElement('video');
v.canPlayType('video/mp4; codecs="avc1.42E01E"');   // h264
v.canPlayType('video/mp4; codecs="mp4v.20.9"');    // MPEG-4 Part 2 — the Cause D codec
v.canPlayType('video/webm; codecs="vp9"');
v.canPlayType('audio/mp4; codecs="mp4a.40.2"');    // aac
v.canPlayType('audio/mp4; codecs="ac-3"');         // ac3
```

Measured on Chrome 152 (Linux, headless) — treat as the shape to re-verify, not as eternal truth:

| Asked | Answer |
|---|---|
| h264 in mp4 | `probably` |
| vp9 in webm | `probably` |
| **MPEG-4 Part 2 in mp4 (`mp4v`)** | **`""` — unsupported** |
| **AVI container** | **`""` — unsupported** |
| MKV container | `maybe` |
| hevc in mp4 | `""` on this build |
| aac in mp4 / mp3 | `probably` |
| **ac3 / dts in mp4** | **`""` — unsupported** |

Note what the MPEG-4 Part 2 row proves: **the same codec is refused in MP4 exactly as in AVI.** That
single result invalidates any plan to "fix" a DivX library by remuxing it — re-run the equivalent
probe before proposing a container migration, and re-check HEVC separately since support there is
platform- and hardware-dependent rather than universal.

## Subtitles: only load-bearing for non-English media

**Standing preference: subtitle compatibility is not a priority unless the media is NOT made in
English.** English-language material keeps its value without subs, so a bitmap subtitle track
downloads not block a remux there — what is lost is SDH or forced-foreign-dialogue, which is
acceptable. Non-English media is the opposite: the subtitle track is the only way to follow it, so
those items must stay in MKV (or have their subs converted to text first).

Decide per item on the AUDIO language / `OriginalLanguage`, not on the subtitle's own language tag.
Applying this rule collapsed a "must stay MKV" bucket from 17 items to 4 — mostly one director's
German/Russian-language films — so check it before reporting a large blocked set.

## Recommended default

**Keep MKV as the archive default.** It is the only container that holds everything this library
actually contains — image-based subtitles, ASS, multi-track AC3/DTS/FLAC, HEVC, AV1. Standardising
on MP4 trades real capability for a compatibility win that only applies to the subset that passes
the three conditions above.

Levers in order of effect:

1. **Video codec** — h264, or HEVC for 1080p+ per the house rules. Most of "will it play".
2. **Audio codec** — AAC/MP3/Opus are broadly safe; **AC3/EAC3/DTS are not** (probe, never assume).
3. **Container** — MKV and MP4 are both fine. **AVI is the one genuinely worth eliminating** (no
   modern features, poor seeking, 2 GB limits) — but converting AVI only helps when the codec inside
   is already good.

Jellyfin remuxes an eligible container on the fly cheaply (DirectStream), which is why MKV is not a
playback barrier inside Jellyfin's own apps — the barrier is browsers, and the cost of a container
mismatch is a transcode session per play rather than a failure.

## Measuring the library rather than assuming

One REST pass over `Fields=Path,MediaSources` gives container, video codec, audio codecs and subtitle
codecs for every item (`Fields=MediaStreams` returns nothing — see
`references/library-quality-sweep.md`). Then bucket:

- **needs a video recode** — video codec not in {h264, vp9, av1}: a container change does NOTHING here
- **safe to remux** — passes all three conditions and is not already mp4/webm
- **must stay MKV** — has an image-based subtitle track
- **already browser-friendly** — mp4/webm already

Report the first bucket as the dominant cost. On this library it is the large majority: the dominant
legacy codec is **MPEG-4 Part 2 in AVI**, i.e. exactly the Cause D playback failure in
`references/playback-and-repair.md`. Size the container question honestly — a plan that remuxes the
eligible subset and calls the library "MP4-standardised" would be misleading if the recode bucket is
untouched.

## Count the codec∩resolution overlap BEFORE proposing a recode batch

Bucket by codec AND resolution together, then intersect. When the legacy-codec set and the
low-resolution set mostly coincide, **replacement beats recoding**: a recode makes the file playable
but permanently locks in the low resolution, so it spends hours of encoding to make a bad picture
*viewable* rather than *better*. Recoding is the fallback for material with no better source, not the
plan.

Measured shape on this library (music documentaries): of 170 `mpeg4` items, **167 were also <=576p**;
and in the series library 82 of 111 episodes were `mpeg4` at 360p and below. Meanwhile **zero** items
at 1080p+ carried a legacy codec — the HD tier was already healthy and needed no work at all.

So report three things before recommending any encode:

1. the codec bucket, and the resolution bucket, and **how far they overlap**;
2. how much of the target list an already-running replacement pipeline (the intake job) is likely to
   clear for free — clear that backlog FIRST, because each REPLACE fixes both defects at once and
   shrinks the recode list;
3. the resolution ceiling recoding would preserve, stated plainly. "Recode these" and "these are
   360p forever" are the same sentence and the user is entitled to both halves of it.

## Remux-then-delete confidence gate

A probe-readable remux is not proof it plays — gate deletions on decode evidence, never on
container validity alone. Ladder per file: (1) ffprobe returns real video + audio streams and
audio stream count matches the source; (2) duration within ~2 s of the source; (3) **decode
sampling**: `ffmpeg -xerror -ss <t> -i file -t 20 -f null -` at 5%, 50% and near-end spans —
exits non-zero on truncation or mid-file corruption, the actual "won't play" cases, at a fraction
of full-decode cost. Delete the parked original only for files passing all three; failures keep
their originals and join the re-encode list. When swapping under the same basename, carry
same-stem sidecars (`.nfo`, `.srt`, `.jpg`) to the new stem so metadata and subs stay attached.

## Recoding is decided per complaint, not as a blanket project

The existing policy stands: fix on complaint, not blanket recodes. A remux only pays off when the
file is already browser-playable *and* browser direct-play is the goal — otherwise it is churn that
resets rows for no playback gain.
