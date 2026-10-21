# VAAPI transcode troubleshooting + ghost-item audit — full case (Sep 2026)

## Incident: "Playback failed due to a fatal player error"

User reported instant playback failure on the freshly migrated 10.11 install (prox1 CT105,
N150 iGPU VAAPI). The per-playback log (`/config/log/ffmpeg-transcode-*.log`) showed:

```
Stream mapping:
  mpeg2video (native) -> av1 (av1_vaapi)
...
No usable encoding entrypoint found for profile VAProfileAV1Profile0
Conversion failed!
```

Root cause: `encoding.xml` (dashboard Transcoding profile, file lives in `/config` next to
`system.xml`) had `<AllowAv1Encoding>true</AllowAv1Encoding>` from the VAAPI setup. The
Intel N150 iGPU **has no AV1 hardware-encode engine** — AV1 decode only. The driver exposes
no `VAProfileAV1Profile0` *encode* entrypoint, so every transcode that picked AV1 died at
encoder open and the player surfaced the generic fatal error.

### Fix (encoding.xml, backup first)

```xml
<AllowAv1Encoding>false</AllowAv1Encoding>
<AllowHevcEncoding>true</AllowHevcEncoding>
<HardwareDecodingCodecs>h264,hevc,av1</HardwareDecodingCodecs>  <!-- decode all three -->
<HardwareEncodingCodecs>h264,hevc</HardwareEncodingCodecs>      <!-- encode only these -->
```

Restart the container (`docker restart jellyfin`) and retry.

## N150 (Alder Lake-N) VAAPI capability table

| direction | codecs |
|---|---|
| hardware encode | **h264, hevc only** |
| hardware decode | h264, hevc, av1 |

Decode support NEVER implies encode support. Verify a GPU's actual encode entrypoints before
enabling any codec in Jellyfin. (N150 also lacks AV1 encode on other driver stacks — it is a
hardware limitation of the 24-EU Alder Lake-N die, not a driver quirk.)

## Direct ffmpeg VAAPI probe — ground truth without the UI

Run INSIDE the container (needs ffmpeg in PATH):

```bash
docker exec jellyfin bash -c 'bash -s' < /path/to/vaapi-encode-probe.sh
# or: pct exec 105 -- docker exec jellyfin ffmpeg ... for a one-off
```

Probe per codec with a synthetic source — do NOT depend on library filenames (guessed file
names fail and muddy the signal):

```bash
ffmpeg -v error -vaapi_device /dev/dri/renderD128 -f lavfi \
  -i testsrc=size=640x360:rate=30:duration=2 \
  -vf 'format=nv12,hwupload' -c:v h264_vaapi -f mp4 /tmp/probe_h264.mp4
```

Swap `h264_vaapi` -> `hevc_vaapi` / `av1_vaapi`. Expected on N150: h264 OK, hevc OK, av1
FAIL with the same "no usable encoding entrypoint" error Jellyfin hit. A non-zero sized
output file proves the encoder works. This probe is saved as
`scripts/vaapi-encode-probe.sh`.

## API-driven transcode observation (valid credentials required)

To force a real Jellyfin transcode end-to-end: authenticate (`POST /Users/AuthenticateByName`
— requires the `X-Emby-Authorization` header, see password-recovery.md), pick a movie that
EXISTS on disk (`GET /Items?Recursive=true&IncludeItemTypes=Movie&SearchTerm=...`), then
request `GET /Videos/{id}/master.m3u8?MediaSourceId={id}` and follow the playlist chain to a
`.ts` segment, tailing a fresh `FFmpeg.Transcode-*.log`. Completed successfully on this
install after the user supplied the new admin password (they changed it post-handover — ask
for it rather than re-resetting; see the takeover pitfall in SKILL.md). The direct ffmpeg
probe remains the reliable verification when credentials are in flux.

### HLS request chain + URL-construction pitfalls (each cost a 400/500 round-trip)

Working chain, mirroring what jellyfin-web sends:

1. `GET /Videos/{id}/master.m3u8?MediaSourceId={id}` -> a `#EXT-X-STREAM-INF` line with
   `CODECS="avc1.640029"` proves the encoder choice changed (was av1 before the fix) without
   any ffmpeg run.
2. Resolve the relative playlist link `main.m3u8?...` -> full VOD segment list
   (`hls1/main/0.ts?...runtimeTicks=0&actualSegmentLengthTicks=60000000` per line).
3. Fetch a segment **with explicit codec + bitrate params on the segment URL**. Pitfalls:
   - **Never echo the master playlist's `AudioCodec=m3u8` query value into the segment
     request.** The server passes it to ffmpeg verbatim as the audio encoder name ->
     `Unknown encoder 'm3u8'`, HTTP 500 "Error processing request". That value is playlist
     bookkeeping, not a client codec choice.
   - **`runtimeTicks` + `actualSegmentLengthTicks` are required** on direct `hls1/main/N.ts`
     requests -> omit them and you get HTTP 400 validation errors.
   - **`VideoBitrate`/`AudioBitrate` are required for VAAPI encode** -> without them
     `h264_vaapi` fails `Bitrate must be set for VBR RC mode`, HTTP 500. Real clients always
     send them; hand-rolled test URLs must too.
   - `POST /Items/{id}/PlaybackInfo` returns empty `MediaSources` without a valid UserId in
     the body — the Items+master.m3u8 chain is the more robust scripted path.

Success signature: HTTP 200 with ~70KB MPEG-TS body (`file` says "data", sync bytes `G@021`)
and the ffmpeg log shows `Stream #0:0: Video: h264 (High), vaapi(...)` with
`encoder: Lavc... h264_vaapi` and live `frame=`/`speed=` lines (observed ~70-79 fps,
2-3x real-time on N150).

## Ghost-item audit — DB paths vs physical files

After a move/merge, some library items point at files that do not exist:

- **Renamed files**: physical files carry clean titles (`Alphaville (1965).mp4`) while the DB
  (from the old install) holds the original release names
  (`Alphaville.1965.FRENCH.1080p.BluRay.x265-VXT.mp4`). Same directory, different filename.
- **Never copied**: whole directories empty on BOTH the source tree (db1) and destination
  (dguh) — content was lost/never migrated off the old NAS years ago and only the DB rows
  survived. Empty-dir-on-both-trees is the signature.

This install: 34 of ~1,935 movie items were missing — ALL in `filmnoir` (30 dirs, only 15
with files; ~19 titles ghosts + 15 rename-mismatches). The other libraries (movies, TV,
documentaries, musicdocos) matched 100%. A ~2% ghost rate can hide entirely inside one
category folder.

### Audit method

Compare every DB media Path against the physical tree. In the 10.11 `jellyfin.db` the item
paths live in `BaseItems.Path` (10.9: `TypedBaseItems.Path`). Map the old container root
(`/data/filmnoir/...`) to the host SMB mount (`/srv/files/dguh/videos/filmnoir/...`) and run
a file-existence check. Do the audit from the host side — the media bind mounts exist only
inside the docker namespace (`docker exec`), NOT in the CT namespace (`pct exec` sees the CT
rootfs only; the container's `/data` maps are a docker-level bind).

Ghosts surface as "cannot play" regardless of encoder health — diagnose them separately from
transcode errors before blaming the GPU.

### Chosen remediation (user decision, Sep 2026)

**Rename the physical files to the DB-expected release names; keep ghost rows; never rescan.**
The user treats ghost entries as a visible "films to replace" shopping list, so purging them
via library rescan is explicitly unwanted. Renames happen on the SMB server natively
(`/mnt/dguh/videos/filmnoir/...` on proxmox3): prox1's `/srv/files/dguh` is a **read-only
CIFS mount** and `mv` there fails. Take the expected basename from `BaseItems.Path`
(`sqlite3 ... "SELECT Path FROM BaseItems WHERE Path LIKE '/data/filmnoir/%' AND Type LIKE
'%Movie%'"` on a host-side copy of jellyfin.db), `mv` the single disk file per dir to that
exact name. Oddities seen: FileBrowser download timestamps (`2025-03-16-05-39_265132508.mp4`)
and a two-DB-row dir where one disk file must satisfy the main movie row while the
`The Killers Extra.avi` row stays ghosted. Result on this install: 15 renamed clean, 16 rows
left missing (15 ghost films + 1 extra) as the user's replacement list. Verify afterwards:
DB-path existence re-check from the host mount, then the API transcode chain above on one
renamed title.

## Direct-play judder with zero transcode logs — off-spec frame-rate timebase

Signature: user reports regular micro-stutter ("jumpy") on one title; the item direct-plays
(no `FFmpeg.Transcode-*` log spawns for it) and the DB Path/Size match the file byte-for-byte,
so the server is passing file timestamps through untouched — a transcode would actually hide
the defect by re-stamping output timestamps.

Diagnosis recipe (container-side ffprobe, read-only):

```bash
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,width,height,r_frame_rate,avg_frame_rate,bit_rate \
  -of default=noprint_wrappers=1 "/data/movies/<Title>/<file>"
```

Compute the decimal (`python3 -c "print(13978/583)"`). A result like 23.975986 against the
correct NTSC-film 24000/1001 (23.976023) is a mux-stamped timebase error — ~37 microseconds
per frame of drift against every 23.976-locked clock (display, audio, subtitles), forcing
periodic duplicate/drop re-locks. Cross-check a sibling copy in the same folder when one
exists (a clean 24000/1001 there proves the film is fine and the one encode is at fault).

Separate file from delivery before fixing: even cadence across the whole runtime = timebase
defect; smooth-for-minutes then stutter clusters = CIFS/network read stalls (check source-host
load: remuxes, dedup scans, full-tree walks sharing the spindle). Browser playback adds
inherent 24p-to-60 Hz pulldown judder — require the "other 24p films look smooth in this same
client" control before blaming the file.

Fix: stream-copy remux normalizing the timescale (minutes, no quality loss), then a mid-file
decode probe (`ffmpeg -ss <midpoint> -i <fixed> -t 10 -f null -`, must exit 0) — a completed
copy alone is not proof of playability. Full re-encode only if the remux still judders
(packet durations themselves uneven, not just the header).
