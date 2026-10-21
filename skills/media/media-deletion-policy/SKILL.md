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
- **A download payload that duplicates an ingested library file may be deleted without asking** — once the library copy is verified (probe + decode + row present), remove the download files AND their torrent entry together (a file-less torrent errors and re-fetches otherwise). "Duplicate" here is wider than the byte-identical rule above: an inferior-encode source (e.g. the mpeg4 AVI a verified h264 recode was made from) or an equal release already on the shelf both qualify. Before deleting, rescue sidecars the ingest did not copy (`.srt`, `.nfo`) into the library folder first. **This applies ONLY to COMPLETE payloads.**
- **NEVER DELETE PARTIALS (standing user rule, 18 Oct 2026).** An incomplete/paused/failed download — SAB `incomplete/` folders, qB `.!qB` temp files, anything below 100% — is NEVER to be deleted, even when its title is already in the library. Reasons: partials are resumable progress (SAB retries continue from them; qB rechecks resume them), and a partial of an already-ingested title is usually an UPGRADE in flight (higher resolution/bitrate than the library copy), not waste. Deleting them re-burns block data from scratch. The user issued this after I unilaterally deleted 33 GB of partials. Only completed, digested payloads get cleaned up. **Hardlink check first: `stat -c %h` the download file — count >1 means a library copy is a hardlink to these same bytes, so deleting files destroys the library copy too.** Detach the torrent with `deleteFiles=false` instead and delete only the (now unreferenced) download names, never the bytes. Re-assert the count **inside the deletion script itself**, immediately before the unlink, and refuse the unlink if it has dropped to 1 — the check and the delete are separate moments and a refresh/re-copy between them silently changes the meaning of the delete; after unlinking, confirm the library copy still opens and reads (nlink falls to 1, content intact) before calling it done.
- **Everything else: ASK FIRST.** No exceptions for "not library material",
  audio packs, low quality, or anything assumed unwanted. Torrent entries AND
  files both need approval.
- A timed-out/absent answer is NEVER approval. If the user doesn't answer,
  leave it alone and report it as pending.

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

## Why it exists

Audio packs judged "not library material" were deleted without approval and
turned out to feed another app. "Not for Jellyfin" does not mean "unwanted".
