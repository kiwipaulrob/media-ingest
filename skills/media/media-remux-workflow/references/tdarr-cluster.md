# Tdarr distributed transcoding cluster

Server + nodes to federate the re-encode backlog across hosts (validated:
server on CT114, node on proxmox4 registered and scanning; trial library of
734 files indexed monitor-only). Use for multi-host encode work; use the
script drivers in this skill for single-host batches.

## Deploy

- Images are `haveagitgat/tdarr` (server) and `haveagitgat/tdarr_node`
  (workers) — not the similarly-named lookalikes; a wrong name fails at
  pull time with an auth-shaped error, so verify the name first.
- Server: `-p 8265:8265 -p 8266:8266`, data in `/opt/tdarr/{server,configs,logs}`.
- Node env: `serverIP`, `serverPort=8266`, `nodeName`, `apiKey` (the seeded
  key, below). Cap CPUs with `--cpus` (leave headroom on shared hosts).
- Mount media at the SAME container path on server and every node
  (e.g. `/mnt/zmedia` → `/media` everywhere) — mismatched paths are the
  commonest silent failure (jobs queue, workers idle, no error).
- On Proxmox VE hosts, AppArmor blocks the s6 supervisor in these images
  (`s6-ipcserver-socketbinder: Permission denied`, app never starts).
  Fix: `--security-opt apparmor=unconfined` on the node container.
- Run node containers with `PUID=0 PGID=0` when library files are
  root/nobody-owned and mode-locked — a uid-1000 node reads fine but
  fails the write half of replace-original.
- Measured software pace (8-thread Ivy Bridge Xeon, SD mpeg4→x264 CRF20
  fast): ~60–70 MB/min at ~700% CPU, output roughly parity with source.
  Size overnight windows from that, not from optimism.

## Auth without the WebUI

- Set `seededApiKey` env on the server container. It MUST start with
  `tapi_` (anything else logs `Invalid seeded API key` and issues nothing).
  On valid startup the server logs `Created seeded API key` and creates an
  `admin` user with the key as password.
- Nodes authenticate with `apiKey=<seeded key>` env. `401 Unauthorized`
  from the node means key mismatch — recheck, don't rebuild.
- API calls: `x-api-key` header, bodies wrapped as `{data:{...}}`.
  Key endpoints: `cruddb` (collections include `LibrarySettingsJSONDB`,
  `FlowsJSONDB`, `FileJSONDB`; modes getAll/getById/insert/update),
  `scan-files` (`scanConfig` needs `dbID` + `mode` + `arrayOrPath`),
  `get-filescanner-status`, `client/file` (needs `start` + `pageSize`).
- Route discovery when docs fall short: the WebUI JS bundle
  (`/static/js/main.*.js`) contains every `/api/v2/...` route — fetch it
  and grep. The live OpenAPI doc is at `/api/v2/public/api-docs/json`.

## Trial library (monitor-only first)

- Insert into `LibrarySettingsJSONDB`, then update with the full template:
  `folder` (server-side container path), `processLibrary:true`,
  `processTranscodes:false`, `processHealthChecks:false`, `scanOnStart:false`,
  plus a night-only `schedule` array (`Day:HH-HH` ids, 11pm–5am).
- Trigger `scan-files`/`scanFresh` manually and confirm file count against
  the filesystem before attaching any flow. Transcodes stay off until the
  operator approves the flow after reviewing it in the UI graph.

## Flow (software-only house standard)

- Clone the on-disk `basicVideo` community template structure; override
  node inputs. A flow doc is `{name, description, tags, flowPlugins:[...],
  flowEdges:[...]}` with per-node overrides in an `inputs:{...}` key on each
  flowPlugins entry (names from the plugin `inputs:` block, e.g. check node
  `codec:h264`, encoder `outputCodec:h264`,
  `ffmpegPreset:fast`, `ffmpegQuality:20`, `hardwareEncoding:false`,
  `forceEncoding:false` (the codec-check node already routes h264 away —
  belt and braces against re-encoding compliant files).
  Never attach an unreviewed hand-authored flow to a library — one bad
  graph touches every file.
- Read plugin input schemas from the plugin sources on disk
  (`.../FlowPlugins/CommunityFlowPlugins/<area>/<plugin>/1.0.0/index.js`,
  the `inputs:` block) rather than guessing names.
