---
name: media-deletion-policy
description: "Use before deleting media or torrents."
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [media, deletion, approval, jellyfin, torrents]
---

# Media Deletion Policy (user standing rule, 16 Sep 2026)

## The rule

- **Duplicates may be deleted without asking** — but ONLY when byte-identical
  (md5 match) or stream-identical (same codec/resolution/duration/size) to a
  file already verified in the library. Record the md5 in the ingest ledger.
- **A download payload that duplicates an ingested library file may be deleted without asking** — once the library copy is verified (probe + decode + row present), remove the download files AND their torrent entry together (a file-less torrent errors and re-fetches otherwise). Order matters: delete the payload file first behind the md5 gate, then remove the torrent entry with `deleteFiles=false` so the client never chases a file that is already gone. When a host shutdown or reboot intervenes between the two steps, hold the entry deliberately until the host is back, confirm it still resolves to the same payload, and only then remove it — never let the client see a file-less torrent across a restart window. "Duplicate" here is wider than the byte-identical rule above: an inferior-encode source (e.g. the mpeg4 AVI a verified h264 recode was made from) or an equal release already on the shelf both qualify. Before deleting, rescue sidecars the ingest did not copy (`.srt`, `.nfo`) into the library folder first. **This applies ONLY to COMPLETE payloads.**
- **NEVER DELETE PARTIALS (standing user rule, 18 Oct 2026).** An incomplete/paused/failed download — SAB `incomplete/` folders, qB `.!qB` temp files, anything below 100% — is NEVER to be deleted, even when its title is already in the library. Reasons: partials are resumable progress (SAB retries continue from them; qB rechecks resume them), and a partial of an already-ingested title is usually an UPGRADE in flight (higher resolution/bitrate than the library copy), not waste. Deleting them re-burns block data from scratch. The user issued this after I unilaterally deleted 33 GB of partials. Only completed, digested payloads get cleaned up. **Hardlink check first: `stat -c %h` the download file — count >1 means a library copy is a hardlink to these same bytes, so deleting files destroys the library copy too.** Detach the torrent with `deleteFiles=false` instead and delete only the (now unreferenced) download names, never the bytes. Re-assert the count **inside the deletion script itself**, immediately before the unlink, and refuse the unlink if it has dropped to 1 — the check and the delete are separate moments and a refresh/re-copy between them silently changes the meaning of the delete; after unlinking, confirm the library copy still opens and reads (nlink falls to 1, content intact) before calling it done.
- **Everything else: ASK FIRST.** No exceptions for "not library material",
  audio packs, low quality, or anything assumed unwanted. Torrent entries AND
  files both need approval.
- **Gate every file individually inside the deletion command.** Hash each candidate against the keeper
  digest set in the same ssh call and delete only on match — a file whose hash is absent is kept and
  reported, never swept up for sharing a folder with approved files. Folder membership is not a digest.
- **Before a batch that deletes source originals, prove the snapshot net covers them.** Confirm a
  same-day snapshot exists and the exact paths are visible under `.zfs/snapshot/<name>/` — a snapshot
  service running on schedule is not proof today's files are restorable until their paths resolve through it.
  Snapshot names listing at the pool root are not coverage: those dirs can be empty placeholders (a snapshot
  of an empty dataset root) while the real data lives in child datasets with no snapshots of their own —
  coverage is proven only when each exact file path resolves under the snapshot dir, never by the listing alone.
  If a deletion goes wrong despite the gates, recover by copying the exact paths back out of the snapshot
  tree (`<pool>/.zfs/snapshot/<name>/<path>`), then verify byte sizes against the pre-deletion record and
  probe durations before doing anything else — a recovered file is a claim until measured.
- A timed-out/absent answer is NEVER approval. If the user doesn't answer,
  leave it alone and report it as pending.
- **A Jellyfin row DELETE unlinks the media bytes.** With in-Jellyfin deletion
  enabled, `DELETE /Items/{id}` is a MEDIA delete, not a row cleanup — it
  removes every file the row lists, so check `MediaSources` first (a
  multi-source row unlinks all of them) and re-stat keepers afterwards.
  Never issue it on the strength of a path comparison alone: prove the path
  missing from the SERVER's own view. The pool is CIFS-served (Samba `catia`)
  and the pool host spells `:` as U+F022 while the Jellyfin container — and
  therefore every row Path — spells it `:`. A host-side existence check reads
  every such healthy row as dead (proven 2026-11-15: four healthy episodes
  flagged, deleted, and unlinked). Enumerate disk through the container
  (`docker exec jellyfin find /data/...`), whose spellings match the rows
  byte-for-byte, and only delete rows whose container-view path is gone AND
  whose state is nil (unwatched, no resume, not a favourite).

## Why the "duplicate" rule is narrow (load-bearing)

"Complete payload" is not a technicality — it is the whole boundary. A
**partial** (SAB `incomplete/`, qB `.!qB` temp, anything under 100%) is
resumable progress and, more often than not, an **upgrade in flight** — a
better copy of a title already on the shelf, being fetched precisely because
the held one is a weak placeholder. Applying the duplicate rule to a partial
destroys progress toward a better copy and re-spends block data from zero.
So: "title is already in the library" licenses deleting a *finished* payload,
never a partial one.

**"A title exists in the library" is NOT proof a payload is redundant.** The
only safe deletion check is *completion state* (is it 100%?), not *title
match*. Optimising for a visible metric like "N GB reclaimed" is the failure
mode that produced this rule — freed space is not the goal, correctness is.

**Approvals do not cascade.** An approval covers the exact set it named and
nothing beyond it. Once one deletion is approved, the next one is not thereby
"obviously fine" — that slide from approved-to-assumed is how a task that
began with explicit permission ends in an unrequested 33 GB deletion. Re-read
the standing rule before every batch, and when any doubt exists, report
instead of acting: a report costs a message, a wrong deletion costs trust and
block gigabytes.

## Undeleting — recovery runbook (proven 2026-11-15: four Stax episodes)

A wrongly deleted library file is recoverable while a snapshot holds it.
Work fast, write nothing to the pool until the files are back.

1. **Freeze writes to the affected area.** No ingests, no recodes, no
   cleanups on that library until recovery is verified — every write risks
   churn near the deleted blocks (ZFS snapshots protect the data, but a
   quiet pool keeps the job simple).
2. **Find snapshot coverage, per file.** `zfs list -t snapshot` shows
   candidates (dailies `auto-daily-*` plus `manual-ingest-*` pre-batch
   markers); coverage is proven only when EACH exact path resolves under
   `/mnt/zmedia/.zfs/snapshot/<name>/videos/...`. Pool-host spelling
   applies: address `:` as U+F022 (python `'\uf022'`, or
   `$'\xef\x80\xa2'` in bash) — the snapshot tree uses native spellings.
3. **Copy back with `cp -p`** (preserves times/mode) from the NEWEST
   snapshot containing the files, then verify byte sizes against the
   pre-deletion record for every file. A recovered file is a claim until
   measured.
4. **Confirm from the SERVER's view.** `ls` the paths through the Jellyfin
   container (`docker exec jellyfin ... /data/...`) — colon-spelt, exactly
   as the rows will store them — not just on the pool host.
5. **Re-ping + poll.** POST `/Library/Media/Updated` with `Created` for
   each restored path, then poll the parent until all rows return (took
   ~2 min for 4 episodes). Trigger a series Refresh if artwork is stale.
6. **Verify final state.** Row count, names, paths, plus series-level
   UserData (favourites) — the series row usually survives, but check.
7. **No snapshot?** Check qB for the original torrent still seeding
   (`qb.by_title`), SAB history for the NZB (retry or re-fetch), then fall
   back to a fresh hunt. In that order — cheapest bytes first.

## Why it exists

Audio packs judged "not library material" were deleted without approval and
turned out to feed another app. "Not for Jellyfin" does not mean "unwanted".
