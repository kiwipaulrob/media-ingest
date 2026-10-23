# Tdarr distributed transcoding cluster

Server + nodes to federate the re-encode backlog across hosts. **This is the
default recoder (standing order, 23 Oct 2026).** Check its live state before
every use; "installed" does not mean "working".

## Live configuration (proven 23 Oct 2026)

| Role | Host | Address |
|---|---|---|
| Server | CT114 on proxmox2 | `192.168.214.23` — 8265 web, 8266 API, 8267 node |
| Node | proxmox4 (`.132`) | container `tdarr-node`, `--cpus 6`, `apparmor=unconfined`, `PUID/PGID=0`, `/mnt/zmedia -> /media`, `/tmp/tdarr-cache -> /temp` |

Tdarr 3.00.00 (`haveagitgat/tdarr:latest`). Node registers as `prox4`, live id
`rdH2eZP` (the `NodeJSONDB` doc `_id` is `prox4` — API calls like
`update-node`/`alter-worker-limit` want the LIVE `rdH2eZP`). Node schedule is
`scheduleEnabled:false` with `workerLimits` 2×transcodecpu + 1×healthcheckcpu;
the night policy lives on each LIBRARY's schedule (23:00–05:00), not the node.

| Library | id | folder | flow | transcodes |
|---|---|---|---|---|
| musicdocos | `d2mSD6V` | `/media/videos/musicdocos` | `b3VtKqt` | on, 23:00–05:00 |
| musicdocos-series | `pbfcK4h` | `/media/videos/musicdocos-series` | `b3VtKqt` | on, 23:00–05:00 |
| Documentaries | `QmFydFZ` | `/media/videos/Documentaries` | none | off (monitor only) |

Flow `b3VtKqt` "Legacy SD -> h264 CRF20 (software, house)":
inputFile → checkStreamProperty(video, codec_name, legacy whitelist) →
checkFileNameIncludes(`Sample,sample,SAMPLE, - original,Trailer,trailer`, skip
on output 1) → ffmpegCommandStart → checkStreamProperty(video, field_order,
`tt`) → [tt] ffmpegCommandCustomArguments `-vf bwdif=mode=send_frame:parity=tff`
→ ffmpegCommandSetVideoEncoder (h264, preset fast, quality 20,
hardwareEncoding false, forceEncoding false) → ffmpegCommandSetContainer(mkv)
→ ffmpegCommandExecute → replaceOriginalFile. No scaling plugin = never
upscales.

Legacy whitelist = `mpeg4,mpeg4v2,mpeg4v3,msmpeg4,msmpeg4v2,msmpeg4v3,mpeg1video,
mpeg2video,wmv1,wmv2,wmv3,vc1`. A whitelist (not a "skip h264" blacklist) is
deliberate: VP9/AV1/H.265 files are untouched automatically, which is the
standing policy for the 82 VP9/AV1 items.

Command actually produced (verified in a job report):
`-i in -map 0:0 -c:0 libx264 -crf 20 -preset fast -map 0:1 -c:1 copy [-vf bwdif=mode=send_frame:parity=tff] out.mkv`
— audio/subs stream-copied, `-vf` only on the `tt` branch.

## Diagnostics (inside CT114)

```bash
# the real Tdarr 3 DB is SQLite, not per-collection directories
docker exec tdarr python3 -c "import sqlite3;print(sqlite3.connect('/opt/tdarr/server/Tdarr/DB2/SQL/database.db').execute('select count(*) from filejsondb').fetchone())"
docker inspect tdarr --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -iE 'node|key'
tail -40 /opt/tdarr/logs/Tdarr_Server_Log.txt   # container TZ = Pacific/Auckland
docker logs --tail 60 tdarr-node               # node lives on proxmox4, not CT114
```

`ls /opt/tdarr/server/Tdarr/DB2/<Collection>` is a **trap**: Tdarr 3 keeps
nothing there (only `SQL/database.db`), so every collection counts 0 and an
"empty install" verdict can be wrong in both directions. Query the sqlite
tables (`filejsondb`, `flowsjsondb`, `librarysettingsjsondb`, `nodejsondb`)
instead.

## Media mount can go stale two ways (both seen)

1. The CIFS mount inside CT114 silently returns a partial view (only
   `_quarantine_*` dirs) while the export is full. `systemctl restart
   mnt-zmedia.mount` fixes it — the unit comes from `/proc/self/mountinfo`
   (`No files found for mnt-zmedia.mount`, no fstab entry), so the restart
   reports a failed unmount yet mounts cleanly. **Nothing persists across a CT
   reboot** — re-check and remount, or add an fstab entry.
2. After the remount the media is right on the CT host but `/media` in the
   container is still EMPTY: docker bound whatever directory sat under the
   mount point at container start. Always `docker restart tdarr` afterwards,
   then verify `docker exec tdarr ls /media/videos`.

## Auth without the WebUI

- `seededApiKey` env must start with `tapi_` (else `Invalid seeded API key`).
  Valid startup logs `Created seeded API key` and creates user `admin`.
- API: header `x-api-key`, bodies wrapped `{data:{...}}`. Endpoints used:
  `cruddb`, `scan-files` (`scanConfig{dbID,mode,arrayOrPath}`),
  `get-filescanner-status`, `client/file` (needs `filters`+`sorts`+`opts`),
  `bulk-update-files` (`{fileIds, updatedObj}`), `alter-worker-limit`,
  `poll-worker-limits`, `remove-library-files`.
- Live OpenAPI at `/api/v2/public/api-docs/json`; every route also sits in
  `/app/Tdarr_Server/public/static/js/main.*.js`.

## Wiring a library to a flow — the traps that return 200 and do nothing

1. **Node inputs live in `inputsDB`, not `inputs`.** A flow authored with an
   `inputs` key runs every plugin on DEFAULTS and the job report shows
   `Loaded plugin inputs: {}`; `checkStreamProperty` then defaults to
   `valuesToMatch: aac`, so every file is "not required". Allowed keys per
   flowPlugins entry: `id`, `version`, `pluginName`, `inputsDB`, `position`,
   `sourceRepo`, `name`, `fpEnabled`.
2. **The library links its flow via `flowId` AND
   `decisionMaker.settingsFlows:true`.** `decisionMaker:{settingsFlows:false}`
   (even with a valid `flowId`) makes the node abort every job with
   `No library transcode settings selected` → file status `Transcode error`,
   file untouched. Set the whole decisionMaker block explicitly, then RE-READ
   it from the server.
3. **`cruddb` update needs `docID` separate from `obj`.** Passing the whole
   document (with `_id` inside) and no `docID` returns 200 and changes
   nothing.
4. **Library `cache` must be `/temp`** (the node's cache mount). Unset, the
   node refuses every flow with `Transcode Cache is not set. Set it in the
   library settings before running a flow.` — visible only in the node log on
   proxmox4.
5. **Worker/schedule limits can be zero at two levels.** `get-nodes` showing
   `workers: {}` with files queued means either `workerLimits` is 0 or the
   node's 24-hour schedule is all zeros. Fix with
   `alter-worker-limit {nodeID, process:'increase', workerType:'transcodecpu'}`
   and `scheduleEnabled:false` on the node.

Requeue a file stuck at `Transcode error` / `Not required` with
`bulk-update-files {updatedObj:{TranscodeDecisionMaker:'Queued', bumped:true,
lastTranscodeDate:0, holdUntil:0}}`; `bumped` is what makes the queue builder
pick it up promptly. `holdUntil` (epoch ms) parks individual files without
disabling the library.

## Scanning: never run two libraries at once

Two concurrent `scan-files` calls cross-assign rows — a `musicdocos` scan and a
`musicdocos-series` scan left Films/TV/filmnoir rows filed under BOTH
libraries, and per-library totals of 2,260 / 2,665 that look plausible but are
wrong (the real folders hold 670 and 265). Recovery: `remove-library-files`
per library, then `scanFresh` with the explicit library path, one library at a
time, and assert every row's `_id` starts with that library's folder.

## Verify before trusting the instance

- `docker restart tdarr` right after any mount change, then compare a known
  file byte-for-byte with the Jellyfin host.
- Per-file verdicts live in
  `/opt/tdarr/server/Tdarr/DB2/JobReports/<footprintId>/*.txt` — read the
  `spawnArgs` block for the real ffmpeg command and the `File routed to
  output N` lines for branch decisions.
- Proven paces (proxmox4 Xeon E3-1265L V2, software x264): 512x384 mpeg4 →
  ~115 fps (~4x realtime); 720x576 mpeg2 `tt` with bwdif → ~30 fps (~1.2x).

## Tdarr replaces files in place — park the originals first

`replaceOriginalFile` renames the source to `<name>.partial.old`, moves the
encoded file in, then removes the old one; it does NOT create the
`<stem> - original<ext>` alternate the house convention wants. Before enabling
transcodes on a library, copy the qualifying files to
`videos/_replaced/tdarr-<YYYYMMDD>/<relative path>` (size-verified; spot
`cmp`'d) so A/B and rollback survive. AVI sources come back as `.mkv` — new
Jellyfin items, so a library refresh is required afterwards.

## Trial library (monitor-only first)

- Insert `LibrarySettingsJSONDB` with `processLibrary:true`,
  `processTranscodes:false`, `processHealthChecks:false`, `scanOnStart:false`,
  `cache:'/temp'`, a full `decisionMaker` block with `settingsFlows:true` +
  `flowId`, and a `containerFilter` WITHOUT `vob,evo,iso,m2ts,ts` (VIDEO_TS
  trees belong to the VOB-chain route, not Tdarr).
- Prove the flow on copies in a throwaway folder (`videos/_tdarr-test`) at
  24/7, including one `field_order=tt` sample, before pointing it at a real
  library; delete the folder and its library afterwards.
- Sample and `- original` files must be excluded by the flow
  (`checkFileNameIncludes`); a library-wide run otherwise recodes the very
  alternates the house convention preserves.
- Two agents editing this instance at once is a real hazard (it happened on 23
  Oct: a parallel run flipped `settingsFlows` back to false and mis-read a
  contaminated scan as success). Re-read library/flow state immediately before
  and after any change, and treat another worker's "verified" comment as
  unverified until you check it yourself.
