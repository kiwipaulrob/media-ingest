# Playback faults & item repair

Symptom → cause → fix for playback failures, judder, odd containers, lost artwork, and stuck resume state — plus the audio/subtitle default model and the forensic DB access pattern.

## Transcode "fatal player error" — three known causes

Full AV1/N150 incident, capability table and probe recipe: `references/vaapi-codec-troubleshooting.md`.

**Cause A — VAAPI encode profile.** Playback dies instantly; the ffmpeg-transcode log ends in `No usable encoding entrypoint found for profile ...` then `Conversion failed!`. Root cause on this install: `encoding.xml` had `AllowAv1Encoding=true`, but the Intel N150 (Alder Lake-N) iGPU CANNOT hardware-ENCODE AV1 — AV1 is decode-only on that generation, so every transcode that selected AV1 died at encoder open.

**Cause B — container/extension mismatch** (mkv bytes named `.avi`). Same player error, but the log ends in `Error opening input: Invalid data found when processing input` — a forced `-f avi` demuxer on EBML bytes `1a 45 df a3`. Jellyfin picks the input demuxer from the file EXTENSION, so a Matroska file wearing a DB-expected release name like `Kiss.Me.Deadly.1955.BRRip.XviD.MP3-XVID.avi` always fails; encoder fixes will not help.

> Rule: the on-disk extension MUST match the real container (magic bytes — mkv = `1a 45 df a3`, avi = `RIFF`). When the DB-expected name lies about the container, rename the file to its true extension (native write on the SMB server, never via the read-only CIFS mount) — do not keep a fake extension to satisfy the DB row. The rename alone is NOT sufficient: the DB row still points at the old name (Cause C).

**Cause C — stale DB path after the container fix.** The identical player error persists AFTER the on-disk rename; the newest `FFmpeg.Transcode-*_<itemid>_*.log` now ends in `Error opening input: No such file or directory` on the OLD filename. Diagnosis: `SELECT Path, Size FROM BaseItems WHERE Path LIKE '%<Film>%'` still returns the old path while `docker exec jellyfin ls` shows only the new file.

Fix — a staged single-row DB swap, the one sanctioned exception to the never-rewrite-the-DB rule (that rule assumed the DB name was truthful; when it lies about the container, the row must be corrected, not the disk):

1. Stop the jellyfin container.
2. Timestamp-backup `jellyfin.db` — keep a copy in `/tmp` AND one in `config/data/SQLiteBackups/`.
3. Copy to a staging file.
4. `UPDATE BaseItems SET Path='<new>', Size=<bytes>, DateLastSaved=<now>, Data=json_replace(Data,'$.Container','mkv','$.Size',<bytes>) WHERE Id='<ITEMID>'`
5. `DELETE FROM MediaStreamInfos WHERE ItemId='<ITEMID>'`
6. Re-INSERT fresh stream rows from container-side ffprobe: `/usr/lib/jellyfin-ffmpeg/ffprobe -show_entries stream=index,codec_type,codec_name,width,height,sample_rate,channels:stream_tags=title,language` — video StreamType=1, audio=0, subtitle=2. The table has NO `Type`/`DisplayTitle` columns; it uses `StreamType`/`Title`.
7. `PRAGMA integrity_check;` must return `ok`.
8. Swap the staged file over live `jellyfin.db`, delete `-shm`/`-wal`, restart the container, retry playback.

Rollback = restore the backup + restart (~1 min downtime). Never rescan — posters, backdrops and userdata are untouched. **Always get explicit user approval before the live swap.**

### Post-swap "still failing" triage — prove the retry reached the server

`ls -lat` the log dir: a fresh `FFmpeg.Transcode-*_<itemid>_*.log` must appear for the new attempt, and `grep <itemid>` in the main log should show new TranscodeManager lines. If NOTHING new appears (newest files predate the restart, only old `[ERR]` lines remain), the attempt never left the client — it is holding stale stream URLs for the dead path. Remedy is client-side: hard-refresh the web tab (Ctrl+Shift+R), back out to the film page, press Play fresh (**never resume the stalled player**), and ask which client (browser/app/TV) plus the exact symptom. Do NOT re-edit the DB or encoder settings on a no-new-logs failure. nginx `connect() failed (111: Connection refused)` lines during the container-restart window are expected noise, not a proxy fault.

### Encoder enablement rule

Enable hardware codecs in Jellyfin only for what the GPU can actually encode/decode — **decode support does NOT imply encode support**. N150: encode = h264 + hevc only; decode = h264 + hevc + av1. Set `AllowAv1Encoding=false`, `AllowHevcEncoding=true`, keep av1 in `HardwareDecodingCodecs`. Probe with `scripts/vaapi-encode-probe.sh` (inside the container) before trusting any UI toggle — do not trust a checkbox.

### Transcode temp on tmpfs

Encoding settings live in `config/config/encoding.xml` — the `/System/Configuration` API does not expose them, so edit the file directly (back it up first). For a RAM transcode dir: add a compose `tmpfs:` mount (`- /transcodes:size=1G,mode=1777`, sized for one stream on a small host), set `TranscodingTempPath=/transcodes`, and bound RAM use with `EnableThrottling=true` plus `EnableSegmentDeletion=true`. Read `HardwareAccelerationType`/`EnableHardwareEncoding` in the same file before changing anything — hardware encode may already be on, in which case there is nothing to enable.

## "It won't play" with a healthy server-side log — Cause D: MPEG-4 Part 2 DirectStream

Distinct from causes A–C because the transcode log is **clean**. The give-away is the codec choice:
Jellyfin picked **DirectStream** (`-codec:v:0 copy`) for an `.avi`, so the server reports success and
streams bytes while the client renders nothing — a black screen, or a spinner that never clears.
DirectStream is worse than a failed transcode precisely because nothing errors anywhere.

Root cause: the video stream is **MPEG-4 Part 2 (DivX/XviD, `Advanced Simple Profile`)** — a legacy
codec that browsers, smart-TV apps and mobile clients generally cannot decode. The server does not
transcode it because the client's declared profile claims support.

Confirm from the FILE, not the client:

```
/usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,width,height,sample_aspect_ratio,display_aspect_ratio \
  -of default=noprint_wrappers=1 "<file>"
```

`codec_name=mpeg4` + `profile=Advanced Simple Profile` in an `avi` container is this cause. Probe
EVERY file in the set: a mixed set is normal (one 10-part documentary here had 3 parts in h264/mp4
that direct-played fine and 7 in mpeg4/avi that did not), so "some episodes play" does not rule it out.

**Jellyfin's choice is not stable** — for the same source it may transcode one attempt and
DirectStream the next, so one successful playback proves nothing and one failure is not a client
bug. Read the log for the specific attempt: `-codec:v:0 copy` on an mpeg4 source is the broken case,
`-codec:v:0 h264_vaapi` the working one. Do not conclude "works, therefore fixed".

**Fix — re-encode to h264** (the house rule anyway: encode h264, never *to* mpeg4). Two things to
get right beyond the codec:

- **Aspect ratio.** Never infer DAR from frame size — a 720x480 NTSC frame is ambiguously 4:3 OR 16:9 anamorphic, and the 4:3 example below bakes in one guess. Probe `display_aspect_ratio` on the source BEFORE the source is retired or deleted — once the VIDEO_TS is gone the flags go with it, and the only fallback is the retail disc spec (blu-ray.com lists the encoding format per DVD). AND vision-check one extracted frame (faces, circular objects) before committing any batch to square-pixel dimensions — but ask the check a NEUTRAL question (`describe the proportions of faces and round objects`), never a leading one (`does this still look squished?`): a led verdict agrees with whatever distortion was suggested, even against the measured flags. 16:9 anamorphic widescreen scales to 854x480 (`scale=854:480:flags=lanczos,setsar=1`); DVD-era SD rips also often carry `SAR 1:1` with a wrong `DAR` (688x464 at `43:29` is
  not 4:3). Scale to the correct frame AND force square pixels, or the recode inherits a stretched
  picture: `-vf "scale=720:540:flags=lanczos,setsar=1"` yields a true 4:3.
- **Sample-encode before committing.** Measure throughput and output size on a ~3-minute slice
  FIRST — a full-set recode is hours, and the encoder/preset choice is the entire cost:

```
ffmpeg -v error -ss 600 -i src.avi -t 180 -c:v libx264 -preset veryfast -crf 20 \
  -pix_fmt yuv420p -vf "scale=720:540:flags=lanczos,setsar=1" \
  -c:a aac -b:a 160k -movflags +faststart /tmp/probe.mp4
```

Measured on this install's 2-vCPU N150 container, 688x464 mpeg4/avi -> 720x540 h264:

| Encoder | Speed | Output vs source |
|---|---|---|
| `libx264 -preset veryfast -crf 20` | ~0.9x realtime | smaller (1.26 vs 1.42 Mbps) |
| `h264_vaapi` (`-vaapi_device /dev/dri/renderD128`) | ~3.5x realtime | larger at matched quality |

Software CRF is the better quality-per-bit and lands a SMALLER file than the source; hardware is
~4x faster for a multi-hour batch. The hardware quality gap narrows substantially in constant-QP mode (`-rc_mode CQP -qp 18`, QP 18-20 plus `-bf 3` where the driver accepts it) — visually near-indistinguishable from CRF 20 on SD material at normal viewing distance, at the cost of ~30-50% LARGER files than the software equivalent. So the choice is disk space vs calendar time, not quality vs speed: software for smallest files, CQP hardware for bulk throughput, software reserved for frequently-rewatched titles. Decode stays on the CPU either way — this iGPU cannot
hardware-decode MPEG-4 Part 2, so the source must be software-decoded regardless.

Verify the recode as with any other (container ffprobe shows `h264` and the intended
`display_aspect_ratio`, mid-file decode exits 0), keep the originals until playback is confirmed,
and replace the files in place so no DB edit or rescan is needed.

**Keep the extension when you keep the path.** Renaming `.avi` to `.mkv` during a recode creates a
NEW row and loses watch state and provider links for no benefit — an AVI container holding h264 is
perfectly truthful, so write `-f avi` back to the original name. Set the original aside under a
NON-video extension (`.avi.orig`, not `.orig.avi`) so the scanner does not briefly see two copies
of the episode, and delete it only after the in-place file re-verifies.

**A batch runs detached; poll the log, never the process.** Run it inside the container via
`docker exec -d … setsid nohup sh script.sh` — the container has no `python3` and no `ps`, so a
shell script and a log file are the only reliable instrumentation. Measured on this install,
~59-minute 688x464 mpeg4/avi episodes take 10–26 min each with `h264_vaapi` — a 2x spread driven
by CIFS/spindle contention, not by the encoder, so size the ETA from the running log rather than
from the first file. Also weigh re-encoding against
sourcing a proper h264 release — for a low-resolution SD set the recode preserves low quality, so
treat it as making the set *watchable now*, not as the end state.

## Row exists but was never probed — PlaybackInfo errors with zero streams

When a freshly-added row will not play and NO transcode log spawns for the attempt: read the item with `Fields=MediaSources` and call `PlaybackInfo`. One MediaSource carrying zero MediaStreams plus a `PlaybackInfo` error means the server has not probed the file yet — not that the file is damaged. This is distinct from causes A–D above (all of those have streams and either a dirty log or a DirectStream decision); here there is nothing to transcode because the server knows no streams.

Prove the file before touching the server: container-side ffprobe must show real video+audio streams, a mid-file `-xerror` decode must exit 0, and a `dd` read through the `/data/...` path must succeed. When all three pass, do NOT re-encode, remux, rename, or re-copy — every one of those resets the probe queue position and restarts the wait.

Prove queue position instead: grep `will be refreshed` in `config/log/log_YYYYMMDD.log` for the exact file path. Read that log via `pct exec` into the container host — the config path lives in CT filesystem space and does not exist at the same path on the host. A hit means the surgical `POST /Library/Media/Updated` (`Created`) already landed and the backlog (drained sequentially, and lengthened by any concurrent batch) has not reached it yet; re-firing while queued is a no-op. Wait for the drain, then re-read `MediaSources` for streams before writing IDs — the user-scoped DTO endpoint 400s until streams exist, so refresh-first-then-write.

## Slow start with no transcode log — measure the chain before changing anything

When playback starts slowly but no ffmpeg log spawns, the encoder is not the problem — work the delivery chain in order and stop at the first slow hop: (1) time `GET /Items/{id}/PlaybackInfo?UserId=<uid>` — instant plus `SupportsDirectPlay=true` exonerates the server decision (repeated start attempts / `MediaInfoHelper` lines with no new ffmpeg log mean the user is retrying a client-side stall, not hitting the server); (2) `dd`-read the file on the storage host; (3) `dd`-read through the container's actual mount path (CIFS); (4) read fps off the newest `FFmpeg.Transcode-*` log for encoder speed. If all four are fast, the stall is client-side (player build, client link) — offer a stream-copy remux to a clean-index MKV as the cheap server-side experiment, and do not touch encoder settings.

**Remux tool selection for timestamp-broken AVIs.** When `ffmpeg -c copy` fails with `Can't write packet with unknown timestamp`, the source timestamps are broken — do not re-encode to work around it. `mkvmerge -o fixed.mkv src.avi` tolerates the breakage and rebuilds valid Matroska timing from the same streams; verify by duration match against the source, then swap in under the same basename and park the original. Never write the temp output with a double extension (`fixed.mkv.new`) — ffmpeg chooses the muxer from the extension and fails `Invalid argument`; use a proper `.mkv` temp name and rename after.

**Salvage one corrupt stream, not the whole file.** When the remuxed file's VIDEO decodes clean at multiple seeks but decode errors isolate to one audio packet, rebuild audio only — `ffmpeg -err_detect ignore_err -i fixed.mkv -c:v copy -c:a ac3 -b:a 192k fixed2.mkv` — instead of re-encoding gigabytes of good video. Re-verify all seeks on the result, then park the pre-fix file as suspect.

**Scope a remux batch by detection, not by extension.** Every path change (including `.avi` → `.mkv`) creates a new library row, losing watch state and flooding Recently Added — so test first and remux only the broken ones: `ffmpeg -v error -i src.avi -c copy -f null -` exits non-zero on timestamp-broken files and clean on healthy ones. A read-only pass over the whole set finds the slow-start candidates; healthy files keep their rows untouched.

## Judder / jumpy direct-play with no transcode log — check the timebase

When playback stutters but no ffmpeg transcode spawns, the defect is in the file or the delivery path, not the encoder — direct-play passes file timestamps through bit-for-bit. Probe the video stream (`ffprobe -select_streams v:0 -show_entries stream=r_frame_rate,avg_frame_rate`) and compare against a sibling copy when one exists: an off-spec rate (e.g. `23.975986` instead of `24000/1001`) causes even-cadence micro-stutter as the player periodically re-locks, most visible in long smooth pans. **Even cadence = file defect; bursty smooth-then-stutter clusters = delivery** (CIFS/network read stalls on a contended spindle). Browser clients add their own 23.976-to-60 Hz pulldown judder — compare other 24p titles in the same client before blaming the file.

**Fix 1 — timebase remux** (stream-copy, on the SMB server itself, native write, never via the read-only CIFS mount; run detached with a log for multi-GB files, since a foreground remux can saturate a small source host and stall SSH/CIFS):

```
ffmpeg -i src.mkv -map 0 -c copy -video_track_timescale 24000 -r:v 24000/1001 fixed.mkv
```

Verify the fixed file: container ffprobe shows exactly `24000/1001` on BOTH rate fields, all streams carried, and a mid-file decode probe (`ffmpeg -ss <mid> -i fixed.mkv -t 20 -f null -`) exits 0. Then swap it into the exact DB-expected basename — move the original aside as `.orig.mkv` first, rename the fixed file over it — so no rescan or DB edit is needed. Confirm through the container path (ffprobe + a small `dd` read under `/data/...`) before handover; keep the `.orig` backup until smooth playback is confirmed, then delete it.

**Fix 2 — full re-encode** when the remux still judders (the source has uneven packet durations baked in; only a re-encode rebuilds even frame pacing). Run on the SMB server itself, native write:

```
nice -n 10 ffmpeg -y -i src.orig.mkv -map 0 -c:v libx264 -preset medium -crf 20 \
  -pix_fmt yuv420p -r 24000/1001 -c:a copy -c:s copy dst.recode.mkv
```

Run detached with a log — a 1080p feature takes ~10h on a small Gen8 host, so schedule overnight away from viewing windows. Verify the same way (exact `24000/1001` on both rate fields, mid-file decode exits 0), then swap it into the DB-expected basename — move the live file aside as `.remux-backup.mkv`, rename the recode over it. A DB Size field still showing a mid-encode value after the swap is cosmetic (the scanner read the growing file); it self-corrects on the next validation pass and never affects playback.

## DVD-folder (VIDEO_TS) rows play as a raw VOB chain, not a film

A Movie row whose Path is the FOLDER itself (not a file), whose Size equals the first VOB's byte size, and whose stream rows lead with `dvd_nav_packet` is a DVD-folder ingest — playback serves split 1 GB VOBs with no IFO navigation, so it stutters or stops at file boundaries. That is the file layout failing, not the encoder: do not chase transcode settings for it.

- Confirm with per-VOB durations (`ffprobe -show_entries format=duration` per `VTS_*.VOB`) and a mid-file decode probe of any sibling playable.
- Absent ffmpeg logs for the item Id AND absent mentions in the main log means the attempt never reached the server (client-side chain break) — the same triage as a no-new-logs post-swap failure.
- A folder that gained playables after its `DateLastRefreshed` is blind to them until a library-scoped Refresh of that library only. Orphan Video rows whose Path folder no longer exists are dead entries that Refresh correctly purges.
- If the library-scoped pass leaves the row's `DateLastRefreshed` untouched, follow with a **per-item Refresh** on the Movie Id (Recursive=true, MetadataRefreshMode=FullRefresh, both ReplaceAll flags false) — the narrower call re-reads the folder when the broad one skips it.

**Completing a partial VIDEO_TS from a pristine second copy:** diff the file lists, copy ONLY the missing files host-to-host with a native write on the SMB server, and md5-verify each arrival against the rip's `.md5` manifest when one ships alongside (scene DVD rips usually carry one in an extras dir with a lineage `.txt` that doubles as a metadata synopsis). Byte-identical shared files mean the copy is incomplete, not corrupt — the signature is missing title-set IFOs (`VTS_0N_0.IFO/BUP` = no navigation for that part) plus missing tail VOBs (truncated episodes). Complete the canonical library copy; **never mount the rescue image as a second library source** (duplicate rows for the same film, plus loop-mount fragility).

**Recode policy for DVD-era material — fix on complaint, not blanket recodes:** remux a sibling `.m4v`/`.mp4` to `.mkv` by stream-copy (dropping `mov_text`/`bin_data` streams per the intake rule) so the watchable version is a clean seekable file; recode VOBs (mpeg2 → h264) only when the DVD cut is actually watched; leave superseded-era XviD rips as alternates untouched. Probe `field_order` first: `progressive` takes NO deinterlace filter at all — only interlaced material needs `yadif`/`bwdif`, and then pass parity explicitly (`bb` → `yadif=mode=0:parity=1`) since auto parity detection fails `EINVAL` on some DVD streams in this build (proven on both) and writes nothing; prove the filter on a 30 s slice before the full encode. Join split 1 GB VOBs with the concat DEMUXER (`-f concat -safe 0 -i chain.txt`), never the `concat:` protocol — the protocol chokes at VOB cell boundaries (`Invalid frame dimensions 0x0`) and exits rc=0 on a kilobytes-sized file, which reads as success; a sample output two orders of magnitude under the expected bitrate is broken stream mapping, re-map, never a pass. When the demuxer-chain encode wedges AT a boundary (output mtime frozen, encoder at 0% CPU — reads as merely slow unless process CPU is checked), abandon the chain: encode each VOB standalone with byte-identical parameters, then join the recoded segments — the stall is corrupt cells at the split, not a slow encoder. Keep discrete 5.1 AC3 with `-c:a copy` (lossless, direct-plays everywhere) rather than downmixing music material to stereo AAC.

## Re-created item loses its images — refresh per item, never rescan

Renaming a file to its true container (or any path change the scanner notices) can make the server DELETE the old row — image rows and orphan poster dir with it — and CREATE a fresh row (new Id, empty `DateLastRefreshed`, no provider IDs, zero images). A single-row path/stream edit never writes image rows or metadata files itself, but expect the item to go imageless afterward and plan a targeted refresh.

**Diagnose before blaming the edit.** Compare a pre-edit DB backup against live: count `Movies.Movie` rows with no `BaseItemImageInfos` row in each, then diff the name sets to isolate newly-imageless from always-imageless (ghost entries without provider IDs never had posters). **Compare by Path, not Name** — the same title can be two different rows with two different Ids. Confirm stale pointers by mapping each image Path (`%MetadataPath%` to the config metadata dir, `/config/` to the config root) and checking disk existence; rows pointing at long-gone dirs or stale nested `/data/...` segments predate the session. Repeated `Could not find file ... folder.jpg/backdrop.jpg` lines for such paths are cosmetic artwork 404s, not a hang cause.

**Remedy — per-item, via the REST API.** Full metadata refresh with image fetch enabled, on the affected Ids only; posters re-download without touching anything else. **Never run a full rescan across all libraries** — it purges deliberately-kept ghost entries. A library-scoped Refresh (`POST /Items/{libId}/Refresh?Recursive=true` with both ReplaceAll flags false) of the single affected library is safe: it picks up new files while other libraries' ghosts stay untouched. Use it for all new-file intake. TV episodes misfiled as Movie rows inherit the series poster — never Apply movie posters to them, so refresh films only.

### Poster/art recovery after a re-creation event

Mine the pre-event DB backup for **provider IDs, not images** — old image rows point at dead `%MetadataPath%` hash dirs and stale nested `/data/...` segments, while the old `ProviderIds` (Tmdb/Imdb) are still valid.

1. Re-attach the IDs with the direct DTO write — `GET /Users/{uid}/Items/{itemId}` → set `ProviderIds` → `POST /Items/{itemId}` with the full object. (This is the working path; see `references/ingest-and-intake.md`.)
2. Re-fire art with a per-item Refresh carrying `ReplaceAllImages=true` (and `ReplaceAllMetadata=false`) — the image-refresh flag is what re-downloads artwork; the plain metadata refresh does not replace existing art.
3. Verify through the parent listing — `GET /Items?ParentId={libId}&Recursive=true&IncludeItemTypes=Movie&Fields=Path,ProviderIds,ImageTags`, reading `ImageTags.Primary`. The per-item detail call can 400 on field combinations the listing accepts. Apply once more after a few seconds when the first pass lands empty; the image download can race the DB write. **Trust the re-read, not the status code** — these endpoints return an empty 200/204 even on failure.

> **Superseded:** an earlier version of this skill drove poster recovery with `POST /Items/RemoteSearch/Apply/{id}?ReplaceAllImages=true`. That endpoint does NOT persist provider links here (204 + `. {}` in the log, zero `BaseItemProviders` rows) and 400s for manual title/overview edits — it is not a recovery path. Use the two steps above.

Keep the old-to-live ID map as a JSON file — re-running the batch from it takes minutes if re-creation recurs. For titles with no old IDs, run read-only `RemoteSearch` trials by name plus year and show the hits for a human pick before applying anything; series-type items are invisible to movie search and need a series-type query instead.

## Clearing a stuck Resume position (UserData)

One item can hold MULTIPLE `UserData` rows under different `CustomDataKey` formats (legacy numeric key, lowercase item id, external id) — all pointing at the same `PlaybackPositionTicks`. Clearing one row leaves Resume alive.

**Prefer the live REST API** — one call clears all key formats for that user+item with no container restart: `POST /Users/{userId}/Items/{itemId}/UserData` with body `{"PlaybackPositionTicks":0}`, authenticated by API key. Verify with `GET /Users/{userId}/Items/{itemId}` plus `SELECT ItemId,CustomDataKey,PlaybackPositionTicks,PlayCount FROM UserData WHERE ItemId='<ITEMID>'` showing 0 on every row. PlayCount and Played flags survive the call — watch history stays.

**Fall back to SQLite only when the server is down:** stop container → timestamp-backup `jellyfin.db` → `UPDATE UserData SET PlaybackPositionTicks=0 WHERE ItemId='<ITEMID>'` → `PRAGMA integrity_check` must return ok → delete `-shm`/`-wal` → restart. **Never DELETE the rows — zero the ticks** so Played state survives.

If the client still offers Resume after the server is clean, the browser cached the position — hard-refresh the web tab and start via Play fresh, never resume the stalled player.

## Audio/subtitle defaults — container flags first, user prefs second

How Jellyfin picks the audio track: the per-USER language preference (`Users.AudioLanguagePreference`, e.g. 'eng') wins when set; when EMPTY (all 5 users on this install), it follows the **container's default-track flag**.

- mkv default flags are header bits — flip them without a remux via `mkvpropedit` on proxmox3 (installed 6 Sep 2026, v92.0): `mkvpropedit "<file>.mkv" --edit track:a1 --set flag-default=0 --edit track:a3 --set flag-default=1`.
- Track numbering: `track:aN` = the Nth AUDIO track, 1-based among audio only — `a3` = 3rd audio, which is index 3 including video.
- Verify with `mkvinfo | grep -E "Track number|Language|Default track"` and container-side ffprobe (`.../ffprobe -show_entries stream=index,codec_type:stream_disposition=default`).
- **Rule for every multi-audio replacement: audit default flags BEFORE handing over** — dubs (rus MVO/AVO) often ship flagged default.
- If a user still gets the wrong track despite correct flags, set their `AudioLanguagePreference`/`SubtitleLanguagePreference` instead of re-flagging.

**Subtitles.** There is no server-global subtitle switch and no trailer exception — subtitle display is per-USER `SubtitleMode` (`Default` follows container default flags, `No Subtitles` never auto-shows, `Only Forced` shows forced foreign-language lines only) plus per-device player overrides that can diverge from the user default. Trailers/extras are ordinary video items, so a releaser-set default sub flag auto-shows under `Default` mode. Prefer `Only Forced` for everyday users; for trailer-only complaints clear the default/forced flags on the extras files (`mkvpropedit --edit track:s1 --set flag-default=0 --set flag-forced=0`) instead of changing film behaviour.

- **Bulk mode change is per user:** `GET /Users/{id}` first, then `POST /Users/{id}/Configuration` (**this build accepts POST, not PUT** — returns 204) carrying back the FULL Configuration object with only `SubtitleMode` flipped (to `OnlyForced`), preserving every other key — **never POST a partial object** — then re-GET each user to verify the mode stuck before handover. Send `X-Emby-Authorization` (`MediaBrowser Client="x", Device="x", DeviceId="x", Version="1.0"`) alongside the API key on all `/Users/*` calls.

## Forensic DB access

- Query SQLite from the **host rootfs** path over a `file:...?mode=ro` URI with Python's `sqlite3` rather than shell-quoted `sqlite3` one-liners (built-in function support varies by build and quoting breaks).
- Never `python3 -c` through SSH either — nested quotes die with `unexpected EOF`. Write the probe to a local `.py` file and feed it over stdin: `ssh host 'python3' < /tmp/probe.py`.
- **Do not filter library membership by `ParentId`** — items link to their library through the `AncestorIds` table, so a `ParentId='<libId>'` predicate returns zero rows. Filter by `Path LIKE '/data/<lib>/%'` plus `Type` instead (the CollectionFolder Id can differ in case from the API library id).
- When the container host is CPU-starved, `pct exec` hangs — probe from the host instead.
