# qBittorrent: reviving "stalled" torrents (verified 12 Sep 2026)

A qB instance showing a large block of `forcedMetaDL` torrents at **0.00 GB size** looks like
dead swarms. It usually is NOT dead swarms. Work this list in order before hunting new sources.

## 1. Check the queue limits FIRST (biggest cause)

`GET /api/v2/app/preferences` — inspect `queueing_enabled`, `max_active_downloads`,
`max_active_torrents`. A box with `queueing_enabled: true` and `max_active_downloads: 3`
will park everything else in `queuedDL`, where it **never even attempts to fetch metadata** —
indistinguishable in a casual `torrents/info` glance from a dead swarm.

```bash
curl -s <QB>/api/v2/app/preferences | python3 -c "import json,sys; d=json.load(sys.stdin); print({k:d[k] for k in ('queueing_enabled','max_active_downloads','max_active_torrents')})"
```

Fix:

```bash
curl -s -X POST <QB>/api/v2/app/setPreferences --data-urlencode \
  'json={"max_active_downloads":12,"max_active_torrents":30}'
curl -s -X POST <QB>/api/v2/torrents/setForceStart \
  --data 'hashes=<h1|h2|...>&value=true'
```

Verified effect: 0 of 38 targeted torrents had metadata before; **17 of 38 after** raising the
limit and force-starting.

### A cap set TOO HIGH starves every torrent — lowering it is also a fix

The cap is not a one-way dial, and "raise the limit" is the wrong move when the limit is already
in the hundreds. `max_connec` is the client's **global** connection budget and is shared across
whatever is active, so a cap of 140 against `max_connec: 500` hands each torrent about three
connections: every entry shows `downloading`, none of them moves. Diagnose with the arithmetic —
`max_connec / max_active_downloads` — and read single-digit connections per torrent as
**contention**, not as dead swarms.

Fix by **concentrating** rather than widening: drop `max_active_downloads` to roughly the number
of torrents you actually want moving (low double digits at most), then move the batch you care
about to the front so the cap fills with those and not with the backlog.

```bash
curl -s -X POST <QB>/api/v2/app/setPreferences \
  --data-urlencode 'json={"max_active_downloads":10,"max_active_torrents":15}'
curl -s -X POST <QB>/api/v2/torrents/topPrio --data 'hashes=<h1|h2|...>'
```

`topPrio` takes pipe-separated `hashes` and moves that batch to the head of the queue. Prefer it
over `setForceStart` when the complaint is *"everything is slow"* — force-starting every torrent
under an already-huge cap reproduces exactly the starvation it was meant to cure. Report which knob
was turned and the new per-torrent connection share.

### Pausing is `stop`/`start`, not `pause`/`resume`

`POST /api/v2/torrents/pause` and `/resume` return **404** on current qBittorrent — the endpoints
are `/api/v2/torrents/stop` and `/api/v2/torrents/start`, both taking pipe-separated `hashes` and
returning 200. Suspiciously, a freeze step written against the old names does nothing but raise, so
"I paused the client before touching the disk" can be false without anyone noticing — always
re-read the state you think you changed.

### Before adding a batch, size the cap to the batch

Adding N candidates while `max_active_downloads` sits well below N parks the excess in `queuedDL`
the moment they are added — they never attempt a metadata fetch, and in the monitor they are
indistinguishable from dead swarms. Order matters:

1. `setPreferences` with a cap at least as large as the batch.
2. Add every candidate.
3. `torrents/setForceStart` for the whole batch in **one** call (`hashes` pipe-separated), then
   `reannounce`.

Adding first and raising the cap afterwards leaves the tail stuck in `queuedDL`. Re-run `setForceStart`
on anything still non-downloading on later checks — the odd entry slips back into `queuedDL`/`metaDL`.

### Report an alive/dead split, not a success count

A freshly added batch resolves in two waves: a minority reach `downloading` within minutes, and a
large remainder sit in `forcedMetaDL` at **0 seeds** for a long time. That second group is the dead
cohort — but do not call it dead before it has had a real window (metadata resolution over DHT is
slow, and entries do keep trickling out of `metaDL` an hour in). Give the split at each check:
downloading / stalled / still-no-metadata, plus bytes pulled. "Added 41 torrents" is not a result;
"41 added, 17 downloading, 1 complete, 19 still metadata-less at 0 seeds" is.

## 2. Refresh the tracker list (second cause)

Old torrents carry defunct trackers (`tracker.thepiratebay.org` no longer resolves;
moack/exodus frequently time out). `GET /api/v2/torrents/trackers?hash=<h>` shows the status —
`4`/`-1` = timed out / not working.

```bash
curl -s -X POST <QB>/api/v2/torrents/addTrackers \
  --data-urlencode 'hash=<single-hash>' --data-urlencode "urls=$(printf '%s\n' ${TRACKERS[@]})"
```

- **`hash` is SINGULAR.** Passing `hashes` returns `400 Missing required parameters: hash`.
- **Success is HTTP 204, not 200.** A check written as `status == 200` silently reports 0/N success.
- Multi-line (`\n`-separated) `urls` is accepted.

Current live tracker set used:
`tracker.opentrackr.org:1337`, `open.demonii.com:1337`, `open.stealth.si:80`,
`tracker.torrent.eu.org:451`, `exodus.desync.com:6969`, `opentracker.io:6969`,
`explodie.org:6969`, `tracker.tiny-vps.com:6969`, `tracker.moeking.me:6969`,
`tracker.tamersunion.org:443`, `tracker.dler.org:6969`. Then
`POST /api/v2/torrents/reannounce` with `hashes=<pipe-separated>`.

## 3. The metaDL wedge fix (delete + re-add, zero data risk)

Some 0-byte entries stay wedged even after 1 and 2, while a **different torrent from the same
release set downloads instantly** — proving the swarm is alive. Fix: remove the 0-byte entry and
re-add the same infohash as a magnet carrying the fresh tracker list.

```bash
# 0 bytes downloaded = nothing to lose. Confirm size==0 first.
curl -s -X POST <QB>/api/v2/torrents/delete --data 'hashes=<h>&deleteFiles=false'
curl -s -X POST <QB>/api/v2/torrents/add \
  --data-urlencode 'urls=magnet:?xt=urn:btih:<H>&dn=<name>&tr=<tracker1>&tr=<tracker2>...' \
  --data 'savepath=/downloads'
```

Verified: a wedged entry went from `forcedMetaDL / 0.00 GB / 0 peers` to
`downloading / 20.1% / 40.6 MB/s` within 45 seconds of the re-add.

Only ever do this on a **size == 0** entry — never on one with partial data unless the user approves.

## 4. Finding genuinely NEW sources

- **Dedupe by infohash.** Most "alternative sources" an indexer offers for an old release are the
  SAME infohash already in qB. Re-adding is a no-op. Only a different 40-hex infohash counts.
- LimeTorrents works without auth via HTML scrape; the RSS variant **omits seeder counts**, so use HTML:
  `https://www.limetorrents.lol/search/all/<url-encoded-query>/`
  Each row yields name, `/…-torrent-<id>.html` detail link, size, `<td class="tdseed">N</td>`,
  and an `itorrents.net/torrent/<40-HEX>.torrent` link carrying the **infohash**.
- **Treat indexer seeder counts as unreliable.** LimeTorrents advertised 66 seeds for an infohash
  whose qB entry showed 0 peers with a healthy DHT (362 nodes) and 14 trackers — the count is
  from the indexer's own stale cache. Trust qB's observed peers, not the indexer's number.
- `apibay.org` (The Pirate Bay API) now returns `No results returned` — do not rely on it.
- 1337x fails in the extract renderer (`CDP discovery failed`); LimeTorrents is the reliable path.

## 5. qB API gotchas

### The files are not where `save_path` says they are

`save_path` is the path **inside the downloader container** (`/downloads`), not a host path — and files that have not finished metadata/file-renaming sit in a sibling `temp/` dir on the real share with a `.!qB` suffix. Resolve the real location through `torrents/files` plus host-mount knowledge before scripting any copy. A `.!qB` file whose video stream is at 100% is usable once verified (strip the suffix on copy); the suffix alone only proves a tag-along file (`.nfo`, artwork) never finished. When direct scp between the downloader host and the media host is refused, check whether the destination share is already mounted on the source host and copy locally instead of routing bytes through a third machine.

### Per-file 100% progress does not mean a complete file

A file can report `progress: 1.0` while its head is zero-filled holes (seen: an 8.5 GB mkv whose first 16 bytes were `00`, failing EBML parse). Size match against the client's reported total is therefore not proof either. Before any ingest, probe the actual copy — `ffprobe` must return real streams, and the head bytes must match the container magic. If the probe fails, delete the partial copy, `POST /api/v2/torrents/recheck` the torrent, and let the remaining seed(s) fill the holes; only re-copy after the recheck completes. Never install a file on size alone.

- `torrents/info` returns **lowercase** hashes, while magnets carry the infohash **uppercase**. A
tracking file built from magnet URIs then silently fails to match and reports torrents as "not in qB"
while they are in fact downloading — a whole batch looks lost. Lower-case the hash **at write time**,
not just at comparison, so every later reader is trivially correct.
- `torrents/add` returns JSON with `added_torrent_ids`; **409 Conflict = infohash already present**
  (not an error to retry).
- `torrents/delete` needs `hashes` **plural** with **full 40-char hashes**; `addTrackers` needs `hash` **singular**. They differ. A truncated prefix to `torrents/delete` returns 200 yet deletes nothing — re-list afterwards and retry with the full hash when the row survives. The same silent no-op applies to `torrents/setForceStart`: a short prefix is accepted but the torrent stays parked in `queuedDL` — always pass the full hash, then re-read `state` to prove it actually moved to `forcedDL` before concluding the swarm is slow.
- **A magnet's declared `btih` is not necessarily the hash qB ends up holding.** Indexer- and
  agent-supplied magnets carry stale or mis-transcribed hashes, so a ledger built from the magnets you
  sent can fail to reconcile against `torrents/info`. The symptom is contradictory and easy to
  misread: re-adding the recorded magnet returns **409 Conflict** ("already present") while a lookup
  of that exact hash returns **empty**. When a tracked count comes up short, do not conclude the add
  failed — search `torrents/info` **by name**, confirm the release really is there, and rewrite the
  ledger with the hash qB actually reports.

## Root-cause summary

The "dead torrents" were almost entirely a **local configuration problem**: queue limits too low
for the backlog, plus stale tracker lists. Always diagnose locally (queue limits → trackers →
re-add) before spending effort hunting replacement releases.
