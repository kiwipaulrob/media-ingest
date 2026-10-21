# Jellyfin REST API — agent control surface

Verified 11 Sep 2026 against the live server (10.11.11 @ http://192.168.214.20:8096) and cross-checked against the OpenAPI stable spec (`https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json`, which reports version 12.0.0). Where a popular tutorial disagrees with the spec + a live probe, the spec and probe win — see *Corrections* below.

## Connection & auth

Credentials for this homelab live in `/root/.hermes/.env` as `JELLYFIN_URL` and `JELLYFIN_API_KEY` (32-char hex). Read them into shell variables, never echo the key (and note `read_file` refuses the credential store by design — `grep` in a shell var is the working path):

```bash
KEY=$(grep -m1 '^JELLYFIN_API_KEY=' /root/.hermes/.env | cut -d= -f2- | tr -d '"')
URL=$(grep -m1 '^JELLYFIN_URL=' /root/.hermes/.env | cut -d= -f2- | tr -d '"')
```

Two header forms, both accepted:

```
Authorization: MediaBrowser Token="<API_KEY>"        # verified 200 on GET /Sessions
X-Emby-Authorization: MediaBrowser Client="AgentSkill", Device="HeadlessAgent", DeviceId="unique-agent-id", Version="1.0.0", Token="<API_KEY>"
```

- `X-Emby-Authorization` is MANDATORY on `/Users/AuthenticateByName` — without it the endpoint throws a bare stack trace instead of returning "invalid username or password". Sending it alongside the API key on `/Users/*` calls is harmless and is this skill's habit.
- API keys CANNOT hit `/Users/Me` (returns **400**) — always pass an explicit `userId`.
- `Content-Type: application/json` on POSTs; GETs need nothing.

## Endpoint map

Health / server (first two need no auth):

| Call | Notes |
|---|---|
| `GET /health` | returns the plain-text `Healthy`, HTTP 200. NOT in the OpenAPI spec (registered outside the controllers) but real, and the cheapest liveness probe. |
| `GET /System/Info/Public` | no auth; `ServerName`, `Version`, `StartupWizardCompleted`, `Id` |
| `GET /System/Info` | authenticated; full config/options |
| `GET /System/Info/Storage` | library storage roots |

Discovery:

| Call | Notes |
|---|---|
| `GET /Sessions` | active clients — `Id`, `DeviceName`, `Client`, `UserName`, `PlayState`, `Capabilities`, `NowPlayingItem` |
| `GET /Items?searchTerm=<q>&includeItemTypes=Movie,Series,Audio&recursive=true` | works with an API key; returns the lowercase-dashed item `Id` the write endpoints want |
| `GET /Users/{userId}/Items` | per-user view (favourites, resume, played) |
| `GET /Library/VirtualFolders` | library definitions (`DELETE`/`POST` on the same path manage them) |
| `GET /Items?...&Fields=Path` | `Path` comes back **null unless explicitly requested** — a search without `Fields=Path` cannot be matched against disk, and the miss reads as 'no such item' rather than 'wrong query shape' |

Collections (BoxSets):

| Call | Notes |
|---|---|
| `POST /Collections?Name=<n>&IsLocked=false` | creates the collection, returns its id (plain string or `{"Id"}`); look it up first (`IncludeItemTypes=BoxSet&searchTerm=`) and cache the id — creating blind duplicates the collection |
| `POST /Collections/{id}/Items?Ids=<id,...>` | adds members (204; idempotent — re-adding is a no-op, so a retry-safe sync just re-posts) |
| `DELETE /Collections/{id}/Items?Ids=<id>` | removes a member (204) without touching the item or its file |
| resolving members | match by **name + path-suffix**, never name alone — a bare-name search matched a Kevin Spacey episode instead of the Clapton/Buddy Guy recode sharing the series name. If the row was renamed since (NFO refresh), fall back to an exact-basename sweep (`IncludeItemTypes=Movie,Episode&Fields=Path&Limit=5000` — `Limit=0` returns zero rows, never use it as 'unlimited') |

Playback control — the target session must advertise `SupportsMediaControl: true` and list the command in `SupportedCommands`; both are visible in `GET /Sessions`.

| Call | Notes |
|---|---|
| `POST /Sessions/{sessionId}/Playing?playCommand=PlayNow&itemIds=<uuid>` | `playCommand` enum: PlayNow, PlayNext, PlayLast, PlayInstantMix, PlayShuffle. **`playCommand` and `itemIds` are QUERY parameters, not a JSON body.** Optional `startPositionTicks`, `mediaSourceId`, `audioStreamIndex`, `subtitleStreamIndex`. |
| `POST /Sessions/{sessionId}/Playing/{command}` | playstate command; enum: Stop, Pause, Unpause, NextTrack, PreviousTrack, Seek, Rewind, FastForward, PlayPause |
| `POST /Sessions/{sessionId}/Playing/Seek?seekPositionTicks=<ticks>` | the same template with `command=Seek`; ticks = seconds x 10,000,000 |
| `POST /Sessions/{sessionId}/Command` | full general command, body `{"Name":"SetVolume","Arguments":{"Volume":"50"}}` — **the only volume path** |
| `POST /Sessions/{sessionId}/Command/{command}` | general command by name; the enum includes VolumeUp/VolumeDown/Mute/Unmute/ToggleMute/SetVolume, GoHome, GoToSettings, SendKey, ToggleOsd, ... but this route takes NO `volume` parameter |
| `POST /Sessions/{sessionId}/System/{command}` | device-level system commands (same name enum; GoHome, TakeScreenshot, ...) |
| `POST /Sessions/{sessionId}/Message` | on-screen toast: body `{"Text":"...","Header":"...","TimeoutMs":5000}` |

`GeneralCommand.Arguments` is a string-to-string map, so numeric values are quoted strings.

## Corrections to commonly-circulated snippets

These forms fail on this server:

- Wrong: `POST /Sessions/{id}/Playing` with JSON body `{"ItemIds":[...],"PlayCommand":"PlayNow"}`.
  Right: `POST /Sessions/{id}/Playing?playCommand=PlayNow&itemIds=<uuid>` — query params.
- Wrong: `POST /Sessions/{id}/Command/VolumeLevel?volume=<0-100>`.
  Right: `POST /Sessions/{id}/Command` with `{"Name":"SetVolume","Arguments":{"Volume":"50"}}`. There is no `VolumeLevel` route, and `/Command/{command}` accepts no `volume` parameter.
- Correct as-is: `POST /Sessions/{id}/Playing/Seek?seekPositionTicks=<ticks>` (it is the `{command}` template with `command=Seek`).
- Wrong: `POST /Items/Refresh` (or `/Items/{libId}/Refresh`) with a JSON body carrying the refresh options.
  Right: per-item `POST /Items/{itemId}/Refresh?MetadataRefreshMode=FullRefresh&ImageRefreshMode=Default&ReplaceAllMetadata=false&ReplaceAllImages=false` — options ride the query string; a JSON body 400s. For single-path discovery or purge signalling prefer `POST /Library/Media/Updated` with `{Updates:[{Path, UpdateType:"Created"|"Deleted"}]}`.

## Session id vs item id

`GET /Sessions` returns `Id` as a 32-char lowercase hex string (live example: `97b207f43a689e071f02faf07a8a796d`), while library item `Id`s are UUID-shaped (dashed, sometimes lowercase). Different namespaces — read a session id live from `/Sessions`, never infer one or reuse an item id as a session id.

## Working with sessions safely

1. `GET /Sessions` FIRST, every time. Enumerate `Id` + `DeviceName` + `UserName` and pick the target explicitly; there is normally a `Chrome` / `Jellyfin Web` session for the browsing user.
2. Only issue control commands when the session advertises `SupportsMediaControl: true` and lists the command in `SupportedCommands` — otherwise the POST can return 204 yet do nothing.
3. `PlayState.CanSeek` tells you whether a seek will land (false when the client is idle).
4. Ticks everywhere: 1 s = 10,000,000 ticks (a 1 h 42 min film is ~6.12e10 ticks).
5. Status-code discipline (verified table below): `404` on a session call = stale id, re-read the list; `401` = a key problem, and API keys have NO refresh path, so do not loop; `400` from `/Users/Me` is EXPECTED under API-key auth; `204` = accepted, not confirmed.
6. This is a live server with real viewers — verify state before acting, and never blind-fire playback commands at a session whose device/user you have not identified.

## Status codes & error handling (all verified live)

| Response | Meaning | Correct reaction |
|---|---|---|
| `200` from `GET /health` / `GET /System/Info/Public` with NO auth | unauthenticated in practice | use them as reachability probes before touching anything credential-related |
| `401` with no `Authorization` header at all | endpoint is protected | add the key; not a credential fault |
| `401` with a key present | key is wrong, revoked, or rotated | **there is NO refresh flow for API keys** — re-read `JELLYFIN_API_KEY` from `/root/.hermes/.env`; if it still 401s the key must be regenerated in the dashboard. Do NOT blind-retry the same key in a loop. |
| `404` from any `/Sessions/{id}/...` call | that session id is stale/gone (client closed, reconnected, or the id was fabricated) | re-read `GET /Sessions` and target the fresh id; never retry the old one |
| `400` from `GET /Users/Me` | EXPECTED under API-key auth | pass an explicit `userId` |
| `400` from `GET /Items/{id}` | this build 400s on item detail lookups rather than 404ing | fetch the full DTO via `GET /Users/{userId}/Items/{itemId}` instead |
| `204` from a control call | accepted — but NOT proof the client acted | confirm via `GET /Sessions` that `PlayState`/`NowPlayingItem` actually changed |

**On the generic "401 -> refresh token -> retry" pattern:** that applies to Bearer-token/expiry flows, not to Jellyfin API keys. If this instance is ever driven with username/password auth instead, the equivalent is `POST /Users/AuthenticateByName` (requires the `X-Emby-Authorization` header) to mint a fresh `AccessToken`, then retry once; escalate to the user after a second 401 rather than looping.

## Python client (optional, NOT installed here)

`jellyfin-apiclient-python` provides `import jellyfin; api = jellyfin.api(url, key)`. It is not installed on this host (`ModuleNotFoundError: No module named 'jellyfin'`); `pip install jellyfin-apiclient-python` in a venv if you want it. The proven, zero-dependency path for this homelab is `curl`, or `requests`/`httpx` if scripting in Python — which is what every recipe in this skill assumes.
