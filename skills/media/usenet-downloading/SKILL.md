---
name: usenet-downloading
description: Use when downloading from Usenet via SABnzbd and Prowlarr.
version: 1.0.0
---

# Downloading from Usenet (SABnzbd + Prowlarr)

## When to Use

- Filling stuck/dead torrent queue items with Usenet twins, or hunting
  upgrades the torrent indexers don't carry.
- Setting up or reconfiguring the SABnzbd client, provider servers, or
  Usenet indexers in Prowlarr.
- Judging whether a Usenet posting will complete (age/grabs) before
  spending block gigabytes.

SABnzbd is the download client (fetch side, alongside qBittorrent); Usenet
indexers plug into the existing Prowlarr as `usenet`-protocol indexers.
Secrets (indexer API keys, provider logins, SAB API key) live in `.env` only.

## Provider: blocks, not subscriptions, for gap-filling

Sporadic upgrade/gap work never justifies a recurring unlimited plan. Buy
block accounts (no expiry) on two different backbones for completion cover.
Before debugging auth, verify the provider hostname resolves **from the
downloader host** — a retired endpoint returns NXDOMAIN everywhere and looks
like a credential failure. Switch to the provider's alternate endpoint
(e.g. EU host) rather than fighting the dead name.

Verify a new server login at the NNTP level (`AUTHINFO USER/PASS` over
`s_client` to port 563, expect `281 Authentication accepted`) — this
separates credential problems from client-config problems. SABnzbd has no
working `test_server` API mode; use `mode=warnings` (empty is healthy) and
`mode=server_stats` after a real grab instead — it breaks down
articles_tried/articles_success per server per day, so a non-zero success
count on the fill server is the proof its login works and it carried blocks.

## Adding a Usenet indexer to Prowlarr

Build the POST `/api/v1/indexer` body from the `/api/v1/indexer/schema`
entry (same pattern as torrent indexers) with these usenet-specific fields:

- `protocol: usenet`, `definitionName` matching the preset (e.g. `NZBgeek`),
  `appProfileId` of a real profile (schema default 0 is rejected — `1`/
  `Standard` normally exists), `priority` 1-50.
- `redirect: true` — required for Usenet indexers, refused otherwise.
- `baseUrl` WITH trailing slash, e.g. `https://api.nzbgeek.info/`.
- Always print the 400 response body: each missing field is reported
  precisely (`AppProfileId`, then `Redirect`), converging in two rounds.
- Creation runs the indexer's connection test as the gate. A credentials
  failure surfaces as `Trial Account Only` / error 101 — trial API keys
  cannot search; the account needs the paid tier before Prowlarr will save it.
  Confirm with a direct `?t=caps` / `?t=search` call against the indexer's
  own API endpoint.

## Searching Usenet through Prowlarr

- Scope with `indexerIds` to the Usenet indexer, or torrent-indexer echoes
  pollute the results and read as Usenet hits. Pass it as a plain string
  (`indexerIds=17`) — a JSON array 400s.
- Indexer token matching is strict: a narrow multi-token query returns
  nothing while the title IS indexed. Search BROAD (bare title) and filter
  by tokens in code — same discipline as the torrent side.
- Usenet rows carry no seeders. Judge completion odds by post **age** versus
  provider retention plus **grabs** (high grabs = recently completable).
  Treat posts older than ~3,500 days as gambles: try the cheapest one first
  to calibrate real retention before spending block space on the rest.
- A zero is genuine only after controls pass: same-indexer search for a
  popular title must return ~100 hits, else the indexer (not the subject)
  is broken.
- Never invent a magnet/hash — Usenet results hand over a `downloadUrl`
  (Prowlarr download link, key already embedded) for the `addurl` step.

## Queueing in SABnzbd

- `mode=addurl&name=<downloadUrl>&nzbname=<title>&cat=<cat>&priority=1`
  fetches the NZB server-side; expect `{"status":true,"nzo_ids":[...]}`.
- Sleep ~15-20s then read `mode=queue` and confirm the filename appears
  with a climbing percentage before reporting success.
- Read `mode=history` for prior failed attempts at the same titles BEFORE
  queueing a batch — a recent identical failure is a verdict on the post,
  not a stale result, and re-queueing it just re-spends block gigabytes.
- With no fill server configured, a first-pull `cannot be completed` is
  final: move the title to the torrent-side wanted list rather than
  retrying the same backbone.
- Retry failed history jobs with `mode=retry&value=<nzo_id>` before
  abandoning them: close repair-block shorts and transient write errors
  (disk is fine) often clear on a second pull, while `cannot be completed`
  aborts are backbone gaps no retry will fix. **`fail_message` is sometimes
  absent from the history XML entirely** — then diagnose from the two signals
  that remain: the `incomplete/` payload size against the NZB's expected
  total (a stall near ~10% is a backbone gap, not a repair short), and the
  failure count (a second identical failure on the same post is a verdict —
  stop re-queueing it on that backbone). Never retry "to see what happens":
  each attempt re-spends block data for the same missing articles. When
  retrying a batch, fire
  smallest-first so the cheap wins land early and a dead post burns minimal
  block space before you read its second verdict. Once a fill server exists,
  compare the `N blocks short` count across retries: a SHRINKING short-count
  proves the fill server is engaged (it contributed blocks even though the job
  still failed); a STATIC count after a fill-server retry means the post is
  dead on every carried backbone — stop spending block space and move the
  title to the torrent-side wanted list.
- Set `download_dir`/`complete_dir` to paths on the writable share via
  `mode=set_config&section=misc` (SABnzbd creates the dirs itself).
- **SAB JSON responses carry raw control characters** (release names with
  embedded newlines/quotes) — `json.loads` raises `Invalid control character`
  or `Expecting ':' delimiter`; parse with `strict=False`, and for
  `mode=history` fall back to `output=xml` (regex the `<slot>` blocks) when
  even tolerant parsing fails. In `mode=get_config&output=json` the folder
  settings (`download_dir`, `complete_dir`) live under `config.misc` — there
  is no `folders` section to read.
- A `Completed` history entry is a **log, not disk truth**: intake moves
  payloads to the library, so history titles may have no files anywhere while
  failed jobs leave partials in `incomplete/`. The audit that matters is the
  post-ingest sweep in `music-docos-library-maintenance` (three sinks:
  qBittorrent entries, SAB `incomplete/`+`complete/`, `_FAILED_*` markers).
- Every grab spends block gigabytes: batch approvals per title (grab/skip)
  with size, age, and grabs stated inline, exactly like torrent picks.
- Verify completions like any download (ffprobe streams + decode) before
  ingesting; a platform-grade post never outranks a real release.

## Completion cover: second backbone as fill server + paused-job diagnosis

- Run two providers on different backbones: main at priority 0, fill at
  priority 1 (`mode=set_config&section=servers&name=<n>&host=<h>&port=563
  &username=<u>&password=<p>&connections=20&ssl=1&priority=1&enable=1`).
  SABnzbd pulls missing blocks from the fill server automatically — no
  per-job action needed once it exists.
- A job that instantly re-pauses after resume is SABnzbd's verdict, not a
  glitch: resume is per-job (`mode=resume&value=<nzo_id>`), and a queue-level
  resume does not clear a job-level pause. Read `mode=warnings` to learn WHY
  it paused — the two causes need opposite fixes:
  - `cannot be completed` = backbone gaps (missing articles). Retries only
    help close repair-block shorts and transient write errors; a hard abort
    needs the fill server, not another pull from the same backbone.
  - `paused ... because of encrypted RAR file` = password-locked, not
    missing data. Hunt the password (indexer release comments, NZB meta)
    before spending more block space — but distrust an NZB-embedded password
    once SAB reports all-supplied-passwords-tried, and abandon the post when
    no board carries it rather than re-downloading identical bytes.

### Never queue a replacement without proving it is an upgrade

Got this wrong on 24 Oct 2026: four replacement releases were queued for content already
in the library, and **three were the same quality** — PDTV/x264 rips carrying no
resolution tag, chosen because the search sorted on seeders and treated "untagged" as
"low". That costs bandwidth, disk and transcode hours to arrive back where we started.

`media_lib/quality.py` decides:

| candidate | held | verdict | queue? |
|---|---|---|---|
| `... 720p WEBRip` | 464p | upgrade | **yes** |
| `... PDTV x264` (no height) | 832p | unknown | **no** |
| `... 1080p WEB-DL` | 1080p | same-or-worse | **no** |
| anything | nothing held | no-local-copy | **yes** |

```python
from media_lib import quality
quality.decide(candidate_title, search_term)   # probes the real held file
quality.verdict(title, held_height)            # pure decision
```

An untagged SD release is **not** a proven upgrade — report it for a human decision.
`scripts/usenet_triage.py --search --queue-upgrades` applies the gate and labels every
candidate QUEUE / skip with the reason.

Sort candidates by **resolution first, seeds second** — seeders alone is how a PDTV rip
outranks a genuine 1080p release.

## "Not enough repair blocks" is NOT a credit problem

Cost real time on 24 Oct 2026: 32 failed downloads were retried after a block account
was topped up, and **every one failed identically**. The shortfall number is the whole
diagnosis — it counts articles missing from EVERY configured provider, and no amount of
credit buys articles the backbone does not hold.

| Shortfall | Meaning | Action |
|---|---|---|
| **`bytes == downloaded`** | SAB fetched everything the providers would serve, then repair failed — the articles do **not exist upstream** | **never retry** — replace the release |
| **<= 10 blocks** | near-complete, a fill server may cover it (only when the download did NOT fetch everything) | one retry |
| **11–100** | usually dead | one attempt, then replace the release |
| **> 100** | hopeless | replace the release, never retry |
| `Corrupt RAR` / `Bad archive` / `RAR files failed to verify` | damaged post | replace |
| `Not on your server(s)` / `Aborted, cannot be completed` | absent from provider | replace |

Use `scripts/usenet_triage.py`, which classifies from SAB history:

```bash
python3 scripts/usenet_triage.py                # bucket every failure
python3 scripts/usenet_triage.py --retry-small  # requeue only <=10-block jobs
python3 scripts/usenet_triage.py --search       # find replacement releases
```

`media_lib/sab.classify_failure()` holds the same thresholds for code paths.

**Two traps around retries:**

* A **paused job or paused queue silently swallows retries** — `sab.retry()` returns
  True and nothing runs. Check `queue.paused` and resume: `sab._call("resume")`, and
  resume an individually paused slot with `sab._call("queue", name="resume", value=nzo_id)`.
* Retrying costs block data. Classify FIRST so the data goes to jobs that can finish.

When a post is hopeless, replace rather than retry: search for the same title from a
different release group. Confirmed on 24 Oct — `BBC Arena 1998 The Brian Epstein Story
1080p HDTV x265 MVGroup` failed 72 blocks short on both providers, so the release had to
change, not the credit.
