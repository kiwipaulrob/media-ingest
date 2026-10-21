---
name: ma-stream-health-checker
description: "Monthly Music Assistant stream health check."
version: 1.4.0
author: Hermes Agent
tags: [music-assistant, radio, health-check, monitoring]
---

# MA Stream Health Checker

## Overview

Runs monthly on the 1st at midday UTC via cron job `ma-stream-health-check`. Checks all ~251 radio stations in MA for:
- Stream connectivity (responds with audio data)
- Stale `available=false` flags (stream works but MA thinks it's dead)
- Radio Browser API health (`at1.api.radio-browser.info` DNS status)
- ICY metadata presence (`icy-metaint` header)
- HLS vs regular HTTP streams
- Higher bitrate alternatives
- New metadata sources on radio-metadata.fr

## Script

`~/.hermes/scripts/ma-stream-checker.py`

### Requirements
- `MA_TOKEN` env var set (MA API bearer token at 192.168.214.159:8095)
- Python 3 stdlib only (no external deps)
- `CF_DNS_TOKEN` and `CF_ZONE_ID` in env (for DNS updates if needed)

### How it works
1. Calls MA API `music/radios/library_items` to get all stations
2. Tests each stream URL concurrently (10 workers, 5s timeout)
3. For HLS: verifies valid `.m3u8` playlist, extracts bandwidth info
4. For HTTP: checks for `icy-metaint`, `icy-br`, `Content-Type` headers
5. Checks Radio Browser API health (tries `de1.api.radio-browser.info` as primary)
6. Checks for known higher-bitrate alternatives (ABC 56k→128k, BBC 96k→320k)
7. Remediates stale `available=false` flags on stations that respond HTTP 200
8. Generates report with broken streams, flags fixed, and RB API health

## Cron Job Details
- **Job ID:** `378cf22d059a`
- **Schedule:** `0 12 1 * *` (1st of every month, 12:00 NZ time)
- **Mode:** `no_agent` since 22 Aug 2026 — the script IS the job; its stdout is delivered verbatim, zero tokens. Exit code 2 = job could not run (MA_TOKEN missing / MA API error) → cron alerts; broken streams are report content (exit 0), not failures.
- **Deliver:** `origin` — results go to this chat
- **Script:** `ma-stream-checker.py` — loads MA_TOKEN from env or /root/.bashrc itself (unanchored grep — the bashrc line is `export MA_TOKEN="..."`); prints a human-readable summary + writes full JSON to `/root/.hermes/scripts/ma-stream-report-latest.json`

## Third Run Findings (1 Aug 2026) — MA HOST DOWN (prox2 incident)

**The full 251-station check could NOT run: the MA host (192.168.214.159, HA VM100 on prox2) has been unreachable since ~23:35 UTC 31 Jul (11:35 NZST 1 Aug).** Full details: `cron/output/ma-stream-report-20260801.md`.

- **prox2 (192.168.214.109, HP ProDesk 600 G6) partially down**: pings + port 3128 (spiceproxy) respond, but SSH (22) and PVE API (8006) refuse from everywhere (CT100, prox1, CT103, cross-connect 10.10.10.2). proxmox.robertsons.cloud → 502. Consistent with the 18 Jul 2026 OOM-kill pattern (HA VM OOM-killed, no auto-restart despite `onboot=1`).
- prox2 WAS fully working ~09:00 NZST 1 Aug (music-sync session ran `qm guest exec 100` on it). The DR-plan cron (4bebaddabec9, 12:00 UTC 1 Aug) confirmed the same and flagged CRITICAL: get console/KVM access to prox2, check boot/OOM, start VM100/CT108/CT103p2.
- **No remote recovery path exists from Hermes** — no SSH/API route to prox2. Requires physical/console access.
- **BBC stations: lsn.lv:8000 now Connection refused** (IPv4 185.151.30.152 + IPv6). lsn.lv main site still 302. This is NEW (proxy worked Jul 2026) and breaks ALL BBC stations in MA even after MA returns. radio-metadata.fr still serves BBC metadata (17 entries).
- **radio-metadata.fr was rebuilt — old API endpoints now 404.** New endpoints (verified 1 Aug 2026):
  - `https://radio-metadata.fr/apiserver/webradios` → 224 stations, 109 FLAC-enabled, 184 in song mode
  - `https://radio-metadata.fr/apiserver/system-stats`, `https://radio-metadata.fr/apiserver/icecast-stats`
  - Icecast proxy :8000 mostly healthy (fip, fipgroove, fipjazz, fiprock, fipmonde, fipreggae, fipcultes, fipnouveaute, francemusique OK). fiphiphop/fippop/fipmetal/fipsacrefrancais STILL 404 on the proxy — irrelevant since MA uses official `icecast.radiofrance.fr/*-hifi.aac` (all verified 200).
  - The checker script's radio-metadata scan must be updated to `/apiserver/webradios`.
- RB API healthy: de1 resolves (A=91.98.4.78) + returns 200; `/etc/hosts` override for at1 (91.98.4.78) in place; SRV → de1 only.
- Stream spot-checks (direct, no MA): 23/24 OK incl. all FIP hifi, 95bFM, MDR, Jazz24, DR P8, SR P1/P2, PRM, SwissGroove, frequence3 FLAC, Radio Paradise, SomaFM, FluxFM. Only failure = BBC lsn.lv.

## Known Issues

- **`music/playlists/get` requires TWO args on MA 2.9.x — omitting `provider_instance_id_or_domain` returns HTTP 500.** The old `music/playlists/get_playlist` name still exists as an alias of `get`, but the handler signature is `get(item_id: str, provider_instance_id_or_domain: str, ...)`. Calls that pass only `playlist_id`/`item_id` (the pre-2.9 arg shape) fail with "Internal server error" — this was misdiagnosed as an MA bug until the source (`music_assistant/controllers/media/base.py` @ tag 2.9.10) was checked. Correct calls (verified Aug 2026):
  - `music/playlists/get` args `{"item_id": "1", "provider_instance_id_or_domain": "library"}` → full playlist object
  - `music/playlists/playlist_tracks` args `{"item_id": "1", "provider_instance_id_or_domain": "library"}` → the playlist's track list (715 tracks for the builtin "All favorited tracks" playlist)
  - `music/tracks/library_items` args `{"favorite": true}` → server-side favorites filter works
  - `music/playlists/update` requires an admin-role token (registered with `required_role="admin"`) — a non-admin token fails the repair path
  Registered playlist commands in 2.9.10 (from `controllers/media/playlists.py`): `create_playlist`, `playlist_tracks`, `add_playlist_tracks`, `remove_playlist_tracks`, `export_playlist`, `import_playlist`, plus base-class `get`/`update`/`remove`/`count`/`library_items`.
- **MA_TOKEN extraction from `/root/.bashrc`**: the line is `export MA_TOKEN="..."` — a `^MA_TOKEN=` grep anchor misses it (returns empty → API answers 401 "Authentication required"). Use `grep 'MA_TOKEN='` (no anchor) or match `"MA_TOKEN=" in line`. The legacy envelope `{"message_id": "...", "command": "...", "args": {...}}` is still valid on MA 2.9.9/2.9.10 (only the old GET REST endpoints were removed).

- **Radio Browser API DNS**: MA uses the `python-radios` library which discovers API servers via DNS SRV (`_api._tcp.radio-browser.info`). In Jul 2026 the Toronto server (`at1`) was decommissioned (NXDOMAIN). The SRV record has since been cleaned up to only return `de1`. A `/etc/hosts` override is in place as insurance on both Hermes LXC and MA host.
  - To verify: `getent hosts at1.api.radio-browser.info` should return 91.98.4.78
  - To fix: `echo "91.98.4.78 at1.api.radio-browser.info" >> /etc/hosts` on this LXC AND 192.168.214.159
- **BBC 320K stations**: Worked via `lsn.lv` proxy through Jul 2026, but **lsn.lv:8000 was Connection-refused on 1 Aug 2026** — all BBC stations in MA will fail until the proxy recovers. The `available: false` flag issue was previously a stale flag caused by RB API being unreachable. Re-check `curl -s -o /dev/null -w '%{http_code}' --max-time 6 'http://lsn.lv:8000/bbc_radio_one'` before concluding BBC stations are dead.
- **KDHX 88.1 FM St. Louis**: Station permanently ceased operations October 2025 — license sold, domain now points to "Double Helix" (different org). **Removed from MA library 19 Jul 2026.** Do not re-add; no replacement exists.
- **95bFM**: Both instances (`95bFM - FLAC` and `95bFM`) now have proper stream URLs (`https://streams.95bfm.com/stream112` and `https://streams.95bfm.com/stream128`). Both show `available=true`. The FLAC stream is slow (~8s to connect) — that's server-side latency, not a problem on our end.
- **FluxFM**: Returns 429 (rate limit) on first-of-month — transient, usually recovers by next day. Add `sleep(1)` between connection retries if this persists.
- Some old Icecast/OEM URLs may 404 (Jazz24, MDR Klassik, DR P8 Jazz) — replacements applied 19 Jul 2026 (see references/broken-stream-investigation.md for full table).
- **Stale `available` flag remediation**: If a stream responds HTTP 200 but shows `available: false`, use the MA API to update it:
  ```bash
  # Get full station object
  curl -s -X POST $MA_API -H "Authorization: Bearer $MA_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message_id":"x","command":"music/radios/get",\
         "args":{"item_id":"STATION_ID","provider_instance_id_or_domain":"library"}}'
  # Set provider_mappings[].available = true, then update with overwrite
  curl -s -X POST $MA_API -H "Authorization: Bearer $MA_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message_id":"x","command":"music/radios/update",\
         "args":{"item_id":"STATION_ID","update":<FULL_OBJECT>,"overwrite":true}}'
  ```
- **5 stations still without logos** (as of 19 Jul 2026): 2BOB Radio 104.7 FM, 6forty Radio, Davide of MIMIC, JB Radio2 (320K), Mad Music Asylum. These are small independent stations with no easily findable logo URLs on Wikimedia Commons. (PRM logo was found since the 30 Jun report.)

## First Run Findings (30 Jun 2026)

### Broken Streams Found
- **95bFM - FLAC** + **Radio One 91FM**: Have GUIDs (UUIDs) instead of URLs — these are from local/UPnP providers, not real streamable HTTP URLs
- **FIP proxy streams (4)**: radio-metadata.fr returned 404 for some FIP channels (fiphiphop, fipmetal, fippop, fipsacrefrancais) — transient proxy downtime
- **KCMP 89.3 FM - The Current**: 404 (old URL, stream may have moved)
- **Jazz24**: 404 (old `live.amperwave.net` URL)
- **MDR Klassik**: 404 (old `cast.mdr.de` URL)
- **DR P8 Jazz (320K)**: 404 (HLS URL no longer valid)
- **KDHX**: Connection refused (transient)
- **P2 (Sweden)**: DNS error (`http-live.sr.se`)
- **PRM, SwissGroove**: Connection timeouts (transient)
- **BBC Radio 1**: 500 from lsn.lv proxy

## Second Run Findings (1 Jul 2026) — With Full Investigation & Replacement URLs

For every broken stream, verified working replacements were found. See `references/broken-stream-investigation.md` for the complete table.

### Stream Investigation Methodology

When a stream fails in the health check, follow this investigation order:

1. **Check radio-browser.info API** for crowd-sourced working URLs:
   ```bash
   curl -s 'https://de1.api.radio-browser.info/json/stations/byname/STATION_NAME' | jq '.[0:3] | .[] | {name, url, codec, bitrate}'
   ```

2. **Visit the station's website** and check for a "Listen" / "Streaming" page — many stations list their direct stream URLs there.

3. **Check community directories**: streamurl.link, radio.net, tunein.com, hiresaudio.online (for FLAC/HiFi streams)

4. **Check official CDN/icecast** endpoints:
   - Radio France stations: `https://icecast.radiofrance.fr/{channel}-hifi.aac` (AAC 192kbps, preferred over third-party proxies)
   - Sveriges Radio: `https://ljud1-cdn.sr.se/lc/{channel}.m3u8` (P1, P2, P3, etc.)
   - DR (Denmark): `http://live-icy.gslb01.dr.dk/A/A22H.mp3` (MP3, or per-channel URL)
   - BBC: No reliable public streams since Jan 2025 — only lsn.lv proxy
   - MDR: `https://mdr-284350-0.sslcast.mdr.de/mdr/284350/0/mp3/high/stream.mp3`

5. **Follow redirect chains**: Some streams 302-redirect to CDN-provisioned URLs (FluxFM → radiosphere.io, MDR → rndfnk.com). Use `curl -sL` to follow.

6. **Verify each candidate**:
   ```bash
   curl -s -o /dev/null -w 'HTTP %{http_code} Type: %{content_type}' --max-time 10 'URL'
   ```
   Must return 2xx with a recognized audio Content-Type (audio/mpeg, audio/aac, audio/ogg, audio/flac).

7. **For stations reported as "ceased operations"** (like KDHX): Verify with news sources rather than continuing to retry the dead stream.

### Bitrate Upgrades Investigated (none available)
- ABC Country/Jazz/Unearthed: The `mp3_128/` URL variant looked promising but redirects to the same 56k Streamguys endpoint — no actual upgrade
- BBC 320k: Exists as UK-restricted variants (`&uk=1`), worldwide 320k Akamai paths don't resolve
- Everything else already at optimal quality

### Logo Coverage
- **246/252 stations with logos** (97%)
- Sourced from: Wikimedia Commons (major broadcasters), TuneIn CDN (smaller stations), station website favicons, media.info, direct station CDNs (SomaFM, Radio Paradise)

