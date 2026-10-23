---
name: media-remux-workflow
description: "Use when remuxing or re-encoding library video."
version: 1.1.0
---

# Library remux / re-encode workflow

Converting aging containers (AVI/DivX-era) to MKV for fast playback start
and seeking, plus H.264 re-encodes for sources too damaged to remux.
Stream-copy changes no pixels — the win is index behavior, not space.

## STOP — recoding is Tdarr's job (standing order, Paul, 23 Oct 2026)

**Never hand-roll an ffmpeg recode batch.** Tdarr is installed and is the only
sanctioned transcoder: CT114 on proxmox2, `http://192.168.214.23:8265`, media
mounted `/mnt/zmedia -> /media` (same CIFS share Jellyfin reads — verified
identical bytes+mtime, not the stale view the older topology note warns about).
Procedure, API and pitfalls: `references/tdarr-cluster.md`.

Before any recode work, in this order: **load this skill and the Tdarr
reference → check Tdarr's state → configure or queue there.** Hand-rolling a
batch instead is a process failure even when the encode is correct: on 23 Oct
2026 I wrote `recode.sh` for 14 files while the cluster had been sitting idle
for 11 days, and was corrected. The scripts below (`reencode-sweep.py`,
`reencode-batch.py`) are fallbacks for when Tdarr is genuinely unavailable —
not the default, and never to race it on the same files.

Tdarr's own state is worth checking every time, because "installed" is not
"working": on 23 Oct every DB was empty (`LibrarySettings`, `Flows`, `Nodes`,
`Files`, `Jobs`, `ApiKeys` all 0), `NODE_LINK` was unset and only ports
8265/8266 were published — an install that had never processed a single file.

## Remux pass (mkvmerge first)

- Prefer `mkvmerge -q -o <dest> <src>` over `ffmpeg -c copy` — sources
  with broken timestamps fail the ffmpeg copy path but mux fine.
- Any mux failure or stream/duration mismatch is a re-encode candidate,
  not a retry — record it and move on.
- Per file, verify before swapping: probe-readable, video stream count
  matches, audio stream count identical, duration within ~2s. If the
  container reports no duration at all, fall back to stream-level duration
  plus the decode samples rather than rejecting — damaged-source muxes
  often omit it while decoding fine. On any fail,
  delete the temp output so the next run starts clean (stale
  `*.remux-tmp.mkv` files otherwise surface as ghost duplicates in Jellyfin).
- Carry sidecars (`.nfo`, `.srt`, `.jpg`, `.png`, `.tbn`) to the new
  basename; snapshot art-file inventory per folder before/after and report
  any folder that lost one.
- **A filename extension is a claim, not evidence — probe before acting.**
  A mislabelled container (MP4 bytes named `.mkv`, the usual yt-dlp/repack
  artefact) still probes fine: ffprobe reports the REAL container (`mov`)
  and both ffmpeg and ExoPlayer sniff content, so it often direct-plays. That
  makes "corrupt file" the wrong diagnosis and a re-encode the wrong fix.
  The real findings are usually (a) a duplicate — prove it by identical size
  plus identical head AND tail MD5s on DIFFERENT inodes, then decide which row
  survives per `media-deletion-policy` — and (b) a metadata split, one copy
  provider-matched and one ID-less. Choose between remux to a real container
  (keeps the name, zero pixel loss), rename + repoint the single DB row
  (approval required), or delete the duplicate. Never re-encode to fix a
  container label.

## Playback-confidence gate (before deleting ANY original)

- Probe + duration only proves the container is well-formed. Decode-sample
  each remux: 20s spans at ~5%, ~50%, and near-end with errors fatal.
- Full-file decode of a whole library costs hours — sampling is the gate;
  reserve full decode for one-off re-encodes where it is affordable.
- Park originals to `_replaced/remux-<date>/`; delete parked originals
  only for gate-passers. Anything failing either check stays parked and
  joins the re-encode list (see media-deletion-policy before any delete).

## Re-encode pass (damaged sources)

- Recipe for SD sources: `libx264 CRF 20 preset medium`, audio
  stream-copied, `-err_detect ignore_err` + `+genpts` so damaged sections
  encode with glitches instead of aborting. H.264 over HEVC for SD — the
  size saving is trivial and H.264 direct-plays everywhere.
- Gate on a full decode with `-xerror`; if that fails, fall back to a
  lenient error-line count and accept sparse glitches (single-digit lines
  ≈ brief flickers) while rejecting heavily damaged files (>20 lines).
- Beyond salvage (thousands of error lines, or encode itself times out on
  a huge file): stop spending CPU and hunt a replacement copy instead.
- Audio-only damage on an otherwise good file: `-c:v copy -c:a <codec>`
  rebuilds the track in minutes — never re-encode the video for an audio fault.
  YouTube AV1+Opus merges in MP4 are a special case of this: the streams show
  zero container offset yet clients (notably Samsung/Tizen, some Jellyfin
  clients) play them out of sync, because Opus-in-MP4 is poorly direct-played.
  Fix is video-copy + Opus→AAC (`-c:v copy -c:a aac -b:a 192k -movflags
  +faststart`), mid-file decode-probe the result, swap it onto the same
  filename so the Jellyfin row survives, then fire a per-item FullRefresh so
  the server re-probes the streams (the row's `MediaSources` codec reading
  stays stale until then — ffprobe on disk is ground truth only after that
  refresh). Keep the Opus original in downloads for A/B.

## DVD-folder recode (VIDEO_TS → single file)

Raw DVD folders play with no navigation and stall some clients — recode to
one file per title (proven: three titles, same recipe).

- Aspect comes from the DISC FLAGS, never the frame size: ffprobe each main
  VOB for `sample_aspect_ratio` / `display_aspect_ratio` (`32:27` = 16:9
  anamorphic, `8:9` = 4:3). 720x480 pixels can be either — assuming 4:3 cost
  a full wasted re-encode. Cross-check retail specs (e.g. blu-ray.com disc
  page lists `Encoding format: 16:9`) when flags look suspect.
- Scan the field structure BEFORE choosing filters: 120 s `idet` sample on a main VOB, read `Multi frame detection` (TFF+BFF vs Progressive). A fully interlaced disc encoded progressive bakes visible combing into the library file and costs a full re-encode to fix — prepend `bwdif=mode=send_frame` (25p, correct for film-originated PAL) when fields win, and record the verdict in the encode log.
- Targets: 16:9 → `scale=854:480:flags=lanczos,setsar=1`; 4:3 NTSC →
  `scale=640:480`; 4:3 PAL → `scale=768:576`. `libx264 veryfast CRF 20`,
  audio stream-copied, into per-VOB `segN.mkv` files (see next point).
- NEVER encode the VOB chain via the concat demuxer in one ffmpeg run: it
  wedges at VOB boundaries (frozen progress, 0% CPU, looks merely slow).
  Encode each main-feature VOB standalone with identical settings, then
  `ffmpeg -f concat -i join.txt -c copy` the segments — a clean join with
  no re-encode. Identify main-feature VOBs by size (~1 GB each); menu/extras
  VOBs are small and excluded.
- Inspect VOB AUDIO streams before mapping: DVDs carry phantom/placeholder
  audio tracks (`sample_rate=0, channels=0`) and `-map 0:a?` grabs them,
  killing the mux at header-write (`sample rate not set`). But a zero-param
  track is not proof of empty audio — broken headers can hide a decodable
  stream, so before discarding one, attempt a full decode with
  `-err_detect ignore_err` and transcribe the result; stream-copy fails
  where decode succeeds. Do NOT dodge a phantom by blindly taking the
  next track index — when two or more VALID tracks exist, transcribe EACH
  one first (25 s sample + faster-whisper, per the language rule below):
  track order is not language order, and the dub can sit on any index.
  Track layouts can also differ between VOBs of one title set, so repeat
  the language check per distinct stream layout, not once per disc.
- Verify the audio LANGUAGE before swapping, not just its presence: a disc
  can carry a single valid track in the wrong language (dub) with no IFO
  language tag to warn you. Proven recipe (Sep 2026): extract 25 s mono
  16 kHz samples at ~15/45/75% runtime, transcribe with faster-whisper
  `tiny` on CT110 (`python3 -m pip install --break-system-packages
  faster-whisper`; CPU int8, ~2 min per sample), read the words — language
  ID alone is not enough, tiny-model output is noisy but English vs dub is
  unambiguous. On dub, check the disc's OTHER audio tracks before hunting
  a new source — multi-track DVDs routinely pair a dub with the original,
  and if the video encode is already good the fix is an audio-only remux
  (`-c:v copy` + the verified track), never a full re-encode. Log the
  verdict per title in the encode log.
- Gate the join like any re-encode (decode samples across every segment
  span), plus a frame-grab proportion check — and phrase the verdict
  neutrally ("natural proportions, or squeezed/stretched?"): a leading
  question gets a sycophantic answer, and the disc flags plus retail specs
  outrank any model looking at a grainy still.
- Verify AUDIO SYNC after any per-VOB audio join, not just duration: a
  damaged/odd stream (e.g. a mislabeled track decoded with error
  concealment) can come out seconds short, shifting everything after it
  early with total duration looking nearly right. Proven method (Sep
  2026): cross-correlate the new audio against a known-in-sync reference
  (parked previous version) at 2+ points — 8 kHz mono envelopes +
  numpy correlate gives millisecond offsets; a constant ~9.5 s offset
  means one short segment, fix by padding that segment to its true span
  (derived: total video minus the good segments) and re-measuring until
  residuals are sub-second. VOB container duration probes lie on damaged
  discs — never size segments from them.

## Hardware-encode pass (Quick Sync on prox1)

- For BULK legacy-codec backlogs (dozens of files), prefer prox1's N150
  Quick Sync over proxmox3's software x264: ffmpeg runs inside the Jellyfin
  container (`pct exec 105 -- docker exec jellyfin ... /usr/lib/jellyfin-ffmpeg/ffmpeg`),
  which carries `h264_qsv` + iHD driver, ~5x realtime on SD vs ~1x for
  x264/medium. The jellyfin image has no python3, so any batch driver runs
  on the prox1 HOST and shells ffmpeg/ffprobe calls into the container.
  prox1 reads the library over CIFS (fine for SD) — write temps to
  container-local `/tmp`, never encode across the share.
- Translate paths at the driver boundary: host `/mnt/zmedia/videos/<lib>/...`
  is container `/data/<lib>/...` (verify against the jellyfin process
  mountinfo, not the compose file). The share root is NOT mounted in the
  container, so park dirs (`_replaced/...`) and file moves happen host-side.
  Move finished encodes out of container `/tmp` with
  `docker exec <name> cat /tmp/<out> > <host dest>` piped from the host —
  never `docker cp`, whose fchmod is denied on CIFS mounts
  (`operation not permitted`) and writes nothing.
- Let `h264_qsv` auto-init: plain `-c:v h264_qsv -preset <p>
  -global_quality <q>` with software decode. Never pass explicit
  `-init_hw_device qsv` / `-filter_hw_device` / `hwupload` filter chains
  in that container — only `renderD128` is exposed and manual hw init
  segfaults (0-byte outputs + core dumps).
- Proven SD-Xvid recipe: `-preset medium -global_quality 20 -c:a copy
  -c:s copy -err_detect ignore_err -fflags +genpts`. Calibration: q22/veryfast
  is the quality floor (~40% smaller than source), q18/slow just re-encodes
  source noise (~source size); starved sources (<1.5 Mbps) bloat at q20 —
  harmless for direct-play, cap with maxrate only if it bothers the operator.
- Pilot new recipes test-only: 2 files × candidate settings to container
  `/tmp`, full-decode `-xerror` gate + size compare, no swaps until the
  winner is chosen. Clean container `/tmp` afterwards — core dumps land
  there and are hundreds of MB each.
- DVD/interlaced sources: probe `field_order` first and pass it EXPLICITLY
  to the deinterlacer (`yadif=parity=1` / `bwdif=parity=1` for bottom-field).
  Auto parity detection aborts with `-22 Invalid argument` and writes
  nothing on some MPEG-2 streams — and a failed filter leaves only a stub
  file, so gate every new filter chain on a 30 s slice (`-t 30` +
  ffprobe the output) before committing to a multi-hour full encode.
  Deinterlacing is for film-originated DVD/PAL sources being taken to
  progressive — a 1080i BROADCAST master stays interlaced (next section);
  deinterlacing it discards half the temporal resolution for good.

## Interlaced broadcast re-encode (1080i in → 1080i out)

For a 1080i50/60 broadcast capture that must be cleaned up (stale codec,
non-conformant level, damaged reference structure) but kept interlaced.
Full recipe, calibration numbers and the verification gates that actually
prove field order and cadence: `references/interlaced-reencode.md`.

- Recipe: `-c:v libx264 -preset medium -crf 20 -flags +ildct+ilme
  -x264-params "tff=1:weightp=0"` with `-c:a copy -c:s copy`, full map
  (`-map 0:v:0 -map 0:a -map 0:s -map_chapters 0 -map_metadata 0`). Motion
  stays interlaced; weightp must be off (x264 warns `interlace + weightp is
  not implemented` and changes it under you).
- Set `-level:v 4.2`. 1920×1080 at 50 fields/s is 408000 macroblocks/s
  against Level 4.0's 245760 limit, so x264 warns `MB rate > level limit`
  and a source labelled L4.0 is itself out of spec — 4.2 is the lowest
  level that truthfully contains this raster.
- **Never verify field order with `ffprobe -show_entries stream=field_order`:
  interlaced libx264 output reads back as `tb` even when it is correct.**
  Trust the decoded frames (`-show_frames` → `top_field_first=1`) and the
  Matroska Track Video `FieldOrder=9` (top-field-first) element; chasing
  the `tb` reading burns variants that all produce the same correct stream.
  The same reading also underlies the `r_frame_rate=50/1` label: a 1080i50
  file decodes 25 frames/s at 0.04 s spacing, so compare source and output
  by counting decoded frames in a fixed pts window, never by the nominal rate.
- Expect the file to GROW at CRF 20 — measured 4.7 → 5.86 Mbps (+14%) on a
  noisy 1080i gig recording, ~1.3x realtime on 6 cores (36 min for a 46 min
  source). State the projected size before encoding, and do not 'fix' a
  soft source by dropping CRF.
- The `-stats` progress line's `time=` can read ~12 s short of the real
  duration on the final line; gate on the probed container duration, not it.
- Launch detached inside the media LXC (`nohup setsid <script> > <log> &
  < /dev/null`) so a dropped ssh/pct session cannot kill a 30-40 min encode;
  poll the remote log, never a local copy.
- Quantify the defect before and after, don't assert improvement: count
  decoder error lines (`mmco|reference frames|corrupt`) over a fixed
  10-minute window on source and output (proven 6 → 0), and confirm frame
  cadence by identical frame pts sequences.
- Check progress on the media host, never from a local log copy — a scp'd
  `/tmp/qsv-batch.log` goes stale within minutes. Read the remote log over
  ssh for counts (`grep -c "OK swapped"`, `grep -c "VERIFY-FAIL\|STAGE-FAIL\|ENCODE-FAIL"`,
  `tail` for the current `[N/M]` line) and confirm liveness with
  `ps aux | grep qsv-driver` plus an active ffmpeg line — an old timestamp
  with no ffmpeg process means the batch stalled, not that it is slow.
- Run ONE CPU-heavy container job at a time — `ps` the host for already-running
  ffmpeg/verify jobs before launching another. Two concurrent full-decodes
  saturate the N150 (load 17+) and hang `pct exec`, which reads as a dead job;
  kill the stale duplicate, never the newest, and re-queue the gate behind
  the running encode.
- Classify failures from the log BEFORE retrying anything: `STAGE-FAIL`
  (the copy into the share failed, `Bad address`) is a transient
  share-write fault — the identical recipe works on a rerun; a
  `VERIFY-FAIL` / `ENCODE-FAIL` means the OUTPUT failed its own gates, i.e.
  the SOURCE is damaged, and a blind rerun reproduces it — those go to the
  lenient error-line gate or a replacement hunt. Map each verdict to its
  file via the path line immediately BEFORE it (the driver prints the path
  line, then the verdict).
- Size per-file timeouts to the LARGEST file and log-and-continue on expiry —
  never let a timeout propagate and kill the driver. A fixed timeout tuned for
  SD encodes murders a multi-GB file at 90% done and the whole batch ends with
  a traceback instead of a final count; an `ENCODE-FAIL` line keeps the run
  alive and routes that file to the retry set.
- Wait for completion without polling: launch
  `ssh <host> 'tail -f --pid=<driver pid> /tmp/<job>.log >/dev/null 2>&1;
  <print final counts>'` as a background process with completion
  notification — it exits exactly when the driver does and delivers the
  final counts in the wake-up message. No sleep loop, and no reliance on a
  stale local log copy.

## Run the encode on the pool-local host

- Launch encode batches on the host that serves the share natively (local
  pool reads), never over CIFS from another host: a degraded CIFS path
  (~hundreds of kB/s) stalls an encode mid-file for hours while looking
  merely slow — the process sits in `DLl` state with near-zero CPU
  accumulation (tens of CPU-minutes over a dozen wall-clock hours on file
  #1) and the batch log stays empty because nothing has finished yet.
- Tell stalled from slow by dividing accumulated CPU time by wall-clock
  elapsed: a healthy software encode burns cores continuously, so a large
  quotient gap plus an unchanging log means I/O starvation, not a big file.
  Confirm with a `dd` read through the same mount before killing anything.
- Fix: kill the stuck ffmpeg, `scp` the driver script to the pool host,
  relaunch detached (`setsid nohup ... &`), and require the first per-file
  completion line in the remote log as proof of liveness — then leave it
  alone until the final count.

## Distributed transcoding (Tdarr cluster — the default tool)

- This is the DEFAULT for all recoding, not just multi-host work (see the STOP
  gate at the top). Full procedure, API patterns and pitfalls in
  `references/tdarr-cluster.md`. A freshly-built flow stays monitor-only
  (scan + report, transcodes off) until the operator approves the graph, and a
  new flow is proven on ONE file before it is pointed at a library — it
  replaces files in place, so a bad graph damages everything it touches.

- `scripts/reencode-sweep.py --scan <libdir>` — read-only triage: walks the
  tree, ffprobes every video file (skips `*.reenc-tmp.mkv` / `*.remux-tmp.mkv`
  leftovers), prints `FLAG <codecs> <size> <path>` per legacy-codec file
  (mpeg4/mpeg2/mpeg1/msmpeg4/wmv3/vp8), ends `SCAN-DONE total=N flagged=M`.
  No encodes, no writes — safe to run anytime.
- `scripts/reencode-batch.py --scan <libdir> --park-dir <dir> [--dry-run]
  [--limit N]` — unattended batch: the recipe above per file (libx264 CRF 20,
  audio copy, ignore_err+genpts), full-decode `-xerror` gate plus duration
  ±2s on the OUTPUT, then park-original + swap ONLY on pass; temp deleted
  and file listed `NEEDS-REPLACEMENT-HUNT` on any fail. Non-MKV sources take
  the `.mkv` name (extension swap = new Jellyfin item — report it). Exit 1 if
  any file failed. Always `--dry-run` first to review the candidate list.
- Run ON the media host (proxmox3 — ffmpeg and the files live there): `scp`
  the scripts over and run; then do the Jellyfin aftermath (library-scoped
  Refresh, per-item full refresh where overview/art did not carry, recount
  imageless). Proven: sweep + dry-run against
  `musicdocos/The Man Who Recorded America (2010)` (1/1 flagged correctly).
- Agent judgement still required for: batch sizing (`--limit`), replacement
  hunts for gate failures, and metadata verification after swaps. The scripts
  remove per-file babysitting, not the operator.
- Strip `*-Sample` / sample files and stray scraps from the candidate list before batching — the sweep flags every legacy-codec file including samples, and encoding one wastes CPU while adding a phantom row.
- Report flagged totals (files + GB) with a CPU-time estimate before proposing a full-library run — a hundred-plus GB of SD sources at software-x264 pace is days, so default to `--limit` overnight slices unless the operator asks for the whole set.

## Output convention: keep the source as a versioned alternate

Standing preference: a recode does NOT quietly replace the original — the original stays beside it
as `<stem> - original<ext>` so the household can A/B the recode against the source and judge
whether it is actually better. Park under `_replaced/` only when the alternate name is already
taken (never overwrite an existing alternate).

So the swap is: encode to a temp name → verify → `mv <file> <stem> - original<ext>` →
`mv temp <file>`, then chown BOTH from the host that owns the share natively (a container-side
chown is refused; the scanner cannot read what the container user cannot access).

The version-suffixed name makes Jellyfin **version-group** the pair into one row with two
`MediaSources` — which is what the user wants (one entry, both cuts, choose at play time). The cost
is that the scanner **removes and recreates** that row; the log line `Removing item ...` is this
rebuild, not damage. The rebuilt row keeps provider/NFO metadata but DROPS:

- collection / BoxSet membership, and
- anything written through the API that was not LOCKED.

Both consequences have to be coded around, because both look like success at the time:

- **File the recode into its collection AFTER the rebuild settles** — never immediately after the
  swap. Poll for the path-matching row to show 2+ `MediaSources` (up to ~10 min) and add THAT row's
  id; an add issued against the pre-rebuild row dies with it, silently leaving a curated collection
  empty. On timeout, add best-effort and flag the row for a re-check instead of reporting success.
- **Re-apply and LOCK Name + Overview** on the rebuilt row, or the next rebuild drops them again.

Verify collection membership with a USER-SCOPED read (`/Users/{uid}/Items?ParentId=<collId>`) — the
anonymous endpoint reports 0 children on a full BoxSet and reads as a failed add.

## Priority order for a backlog run

- **The order is part of the job, not a detail.** Standing order for the recode backlog:
  Documentaries first, then Music Documentaries, then anything else — and within a library, worst
  codec first (mpeg2 → msmpeg4/wmv3/vc1 → mpeg4 → rest), then lowest resolution. Put this in the
  driver's sort KEY rather than relying on the candidate CSV's existing row order, so the
  preference survives a re-scan that reshuffles the list.

## Jellyfin aftermath

- Every swapped file is a new item: watched/resume states reset and
  Recently Added churns. Remote (provider-fetched) art re-fetches on a
  library refresh + image refresh — run both before judging imageless
  results; local art files survive untouched. For same-path swaps the
  item's `MediaSources` codec reading stays stale until a per-item full
  refresh re-probes the file — ffprobe on disk is ground truth, re-read
  streams only after that refresh.
