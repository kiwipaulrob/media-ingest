# 19 Jul 2026 — Full Remediation & New Findings

## Root Cause: DNS SRV Infrastructure, Not MA Code

The `at1.api.radio-browser.info` server was decommissioned / went NXDOMAIN.
This was **not hardcoded** in MA — it was returned dynamically by a DNS SRV
lookup on `_api._tcp.radio-browser.info`. The SRV has since been cleaned up
and now only retur...[truncated]
```
This maps the dead `at1` subdomain to `de1.api.radio-browser.info` (which resolves). Confirmed working — RB API responds in ~0.8s.

## BBC Stations: Stale available=false Flags (Not Dead Streams)

The old investigation concluded BBC 320K lsn.lv streams were broken (HTTP 500). This was incorrect.

**Root cause:** MA's availability check uses the Radio Browser API to verify stations. When the RB API was unreachable (DNS failure on `at1.api.radio-browser.info`), MA couldn't verify any stations and set their `available` flag to `false`. This was NOT a stream issue — the streams themselves worked fine.

**Proof:** After the DNS fix was applied:
- All 8 BBC stations with `available=false` were updated via MA API to `available=true`
- Their lsn.lv URLs return HTTP 200 and serve audio data (tested with curl)
- BBC Radio 5 live sports extra and BBC Radio 2 show 500 only when `&uk=1` param is added (region check)

**Lesson:** When many stations show `available=false` simultaneously, check Radio Browser API health before investigating individual streams.

## KDHX Removed from MA Library

- Confirmed: station permanently ceased operations October 2025
- License sold to Double Helix Media; domain now redirects to doublehelixmedia.com
- Removed from MA library 19 Jul 2026 (was station ID 255)
- Removed from the health check script's known streams
- Do NOT re-add — no replacement exists

## 95bFM Stream URLs Fixed

- Both 95bFM instances now have proper stream URLs:
  - `95bFM`: `https://streams.95bfm.com/stream128` (MP3)
  - `95bFM - FLAC`: `https://streams.95bfm.com/stream112` (FLAC/OGG)
- Old UUIDs (unresolved MA provider IDs) replaced
- Both show `available=true`
- FLAC stream is slow (~8s to first byte) — server-side, not our issue

## Stale Available Flag Remediation Pattern

When a stream responds HTTP 200 but shows `available: false`:

```python
# 1. Get full station object
station = ma_api_call("music/radios/get", {
    "item_id": station_id,
    "provider_instance_id_or_domain": "library"
})

# 2. Set available=true in all provider_mappings
for mapping in station["provider_mappings"]:
    mapping["available"] = True

# 3. Update with overwrite=true
ma_api_call("music/radios/update", {
    "item_id": station_id,
    "update": station,
    "overwrite": True
})
```

## Stations Fixed on 19 Jul 2026

| Station | Issue | Fix |
|---------|-------|-----|
| BBC Radio 1 (320K) id=237 | Stale `available=false` | Set available=true |
| BBC Radio 1Xtra (320K) id=239 | Stale `available=false` | Set available=true |
| BBC Radio 2 (320K) id=241 | Stale `available=false` | Set available=true |
| BBC Radio 3 (320K) id=243 | Stale `available=false` | Set available=true |
| BBC Radio 4 (320K) id=245 | Stale `available=false` | Set available=true |
| BBC Radio 4 Extra (320K) id=247 | Stale `available=false` | Set available=true |
| BBC Radio 5 live sports extra (320K) id=248 | Stale `available=false` | Set available=true |
| BBC World Service (320K) id=250 | Stale `available=false` | Set available=true |
| KDHX 88.1 FM id=255 | Permanently dead | Removed from library |
| FIP Hip-Hop | Dead URL (radio-metadata.fr) | Already had replacement URL |
| Jazz24 | Dead URL (amperwave.net) | Already had replacement URL |
| MDR Klassik | Dead URL (cast.mdr.de) | Already had replacement URL |
| DR P8 Jazz (320K) | Dead HLS URL | Already had replacement URL |
| P1 (Sveriges Radio) | Dead topsy URL | Already had replacement URL |
| P2 (Sveriges Radio) | Dead http-live.sr.se URL | Already had replacement URL |
| SwissGroove | Dead IP URL | Already had replacement URL |
| PRM | Dead shoutcastunlimited URL | Already had replacement URL |
| 95bFM | UUID URL | Replaced with real stream URL |
| 95bFM - FLAC | UUID URL | Replaced with real stream URL |

Investigated 18 broken streams from the 1 Jul 2026 health check. 14 of 18 have confirmed working replacement URLs ready to apply.

## UUID Resolution Failures (MA Provider Issue)

These have unresolved provider UUIDs instead of real stream URLs in MA's config.

| Station | Current Issue | Replacement URL | Verification |
|---------|--------------|-----------------|--------------|
| 95bFM - FLAC | UUID in MA config | `https://streams.95bfm.com/stream112` | HTTP 200, audio/ogg, 1MB+ served |
| Radio One 91FM | UUID in MA config | `https://play.r1.co.nz/live` | HTTP 200, audio/mpeg, 192kbps MP3 |

**Cause:** Likely MA provider integration — TuneIn/RadioBrowser provider ID didn't resolve during import.

## FIP Sub-Genre Streams (radio-metadata.fr Proxy)

Four FIP stations used `radio-metadata.fr:8000/` proxy endpoints that returned 404. The proxy only has mounts for: fip, fipcultes, fipelectro, fipgroove, fipjazz, fipmonde, fipnouveaute, fipreggae, fiprock, fiproon. It never had hip-hop, metal, pop, or sacre francais endpoints.

| Station | Old URL | Replacement URL | Result |
|---------|---------|-----------------|--------|
| FIP Hip-Hop | radio-metadata.fr:8000/fiphiphop | `https://icecast.radiofrance.fr/fiphiphop-hifi.aac` | HTTP 200, audio/aac, 192kbps |
| FIP Metal | radio-metadata.fr:8000/fipmetal | `https://icecast.radiofrance.fr/fipmetal-hifi.aac` | HTTP 200, audio/aac, 192kbps |
| FIP Pop | radio-metadata.fr:8000/fippop | `https://icecast.radiofrance.fr/fippop-hifi.aac` | HTTP 200, audio/aac, 192kbps |
| FIP Sacre Francais | radio-metadata.fr:8000/fipsacre | `https://icecast.radiofrance.fr/fipsacrefrancais-hifi.aac` | HTTP 200, audio/aac, 192kbps |

**Note:** The correct name for Sacre Francais is `fipsacrefrancais` (all lowercase, no accent), not `fipsacre`. The `-hifi.aac` suffix gives 192kbps — same quality as the working FIP stations. Official Radio France icecast is preferred over the third-party radio-metadata.fr proxy.

## Dead/Relocated URLs — Replacements Found

| Station | Old URL Problem | Replacement URL | Details |
|---------|----------------|-----------------|---------|
| DR P8 Jazz (320K) | HLS Akamai URL returns 403 | `http://live-icy.gslb01.dr.dk/A/A22H.mp3` | 128kbps MP3 (320kbps HLS region-locked). Also: `http://live-icy.gslb01.dr.dk/A/A22H.mp3.m3u` (playlist wrapper) |
| frequence3 (FLAC) | `hd.stream.frequence3.net` (reported 404) | `https://frequence3.net-radio.fr/frequence3.flac` | HTTP 200, audio/ogg (FLAC). Old domain still works too (200). Also: `frequence3-256.mp3` (256kbps MP3) |
| Jazz24 | `live.amperwave.net/direct/ppm-jazz24aac256-ibc1` (404) | `https://knkx-live-a.edge.audiocdn.com/6285_256k` | HTTP 200, audio/aac, 256kbps. Station moved CDNs to knkx-live-a.edge.audiocdn.com. |
| MDR Klassik | `cast.mdr.de/.../stream.aac` (404) | `https://mdr-284350-0.sslcast.mdr.de/mdr/284350/0/mp3/high/stream.mp3` | HTTP 302 → HTTP 200, audio/mpeg, 128kbps via rndfnk.com CDN. Only MP3 available (AAC high returns 404). |

## BBC Radio 1 — No Replacement Available

- Old URL: `https://lsn.lv/bbcradio.m3u8?station=bbc_radio_one&bitrate=320000` → HTTP 500
- BBC's own HLS (`a.files.bbci.co.uk/media/live/manifesto/...`) → HTTP 410 Gone
- BBC restricted all public HLS/Shoutcast streams in January 2025
- The `lsn.lv` third-party proxy (also used by Radio 2-6, World Service, 1Xtra) is down with 500
- GarfNet (garfnet.org.uk) still lists lsn.lv URLs as of Jul 2026
- **No reliable working BBC stream available from NZ at this time**

## Sveriges Radio P1 & P2

| Station | Old URL Problem | Replacement URL | Details |
|---------|----------------|-----------------|---------|
| P1 | Timeout on `sverigesradio.se/topsy/direkt/132-hi-aac` | `https://ljud1-cdn.sr.se/lc/p1.m3u8` | HTTP 200, HLS with 32kbps + 128kbps variants. Verified playlist. |
| P2 | DNS failure on `http-live.sr.se/p2musik-aac-320` | `https://ljud1-cdn.sr.se/lc/p2.m3u8` | HTTP 200, same quality tiers. |

SR moved live streams to a new CDN infrastructure. The `ljud1-cdn.sr.se/lc/` path is the current correct endpoint.

## Timeout Failures — Stations Moved

| Station | Old URL Problem | Replacement URL | Details |
|---------|----------------|-----------------|---------|
| PRM - Prog Rock & Metal | `shoutcastunlimited.com:8512` timeout | `https://centova.radioservers.biz/proxy/progrock/stream` | HTTP 200, audio/mpeg, 400KB+ served |
| SwissGroove | `158.69.74.203:80/` timeout/connection refused | `http://relay1.swissgroove.ch:80` | HTTP 200, audio/mpeg. Confirmed by Volumio community (May 2026) |

## Station Ceased Operations

| Station | Status | Recommendation |
|---------|--------|---------------|
| KDHX 88.1 FM St. Louis | Ceased operations Oct 2025. Parent company Double Helix dissolved after bankruptcy. | Remove from MA. Digital reincarnation (CRSTL) planned late 2026/early 2027 — re-add then. |

Source: https://www.stlpr.org/arts/2025-10-03/kdhx-ceases-operations-as-former-djs-announce-home-for-online-station

## Transient (Already Recovered)

| Station | Issue | Status |
|---------|-------|--------|
| FluxFM - Sound of Berlin | 429 rate limit on health check | 302 → 200 working. Transient. |
| FluxFM - HipHop Classics | 429 rate limit on health check | 302 → 200 working. Transient. |

## How Streams Were Verified

For each candidate replacement URL, the actual stream was tested via curl:

```bash
curl -s -o /dev/null -w 'HTTP %{http_code} Type: %{content_type}' --max-time 10 'URL'
```

A stream is considered working when:
- HTTP status 200 (or 302 redirect → 200 when followed with -L)
- Content-Type is a recognized audio format: audio/mpeg, audio/aac, audio/ogg, audio/flac, application/vnd.apple.mpegurl (for HLS)
- Non-empty response body (audio data)