"""Stage 3 — apply.

Executes ONLY the explicitly approved plan rows. There is no 'all' switch on
purpose: an approval is a decision about specific items, and a bare --all would
let a stale plan act on titles nobody looked at.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from media_lib import config, jellyfin, qb, safety, swap


def _refresh(lib_ids, item_ids=()):
    """Re-probe swapped items, then refresh their libraries.

    The swap replaced the bytes under an EXISTING row, and a library-scoped Refresh
    re-resolves metadata for known rows — it does not re-read the media. Only a per-item
    FullRefresh picks up the new streams. The old code also issued this as a GET
    (jellyfin.get_json), while /Items/{id}/Refresh is POST-only, so it never ran at all.
    """
    for item_id in item_ids:
        try:
            jellyfin.refresh_item(item_id)
        except Exception as exc:  # noqa: BLE001 - report, never abort
            print(f"  refresh item {item_id} failed: {str(exc)[:100]}")
    for lib_id in lib_ids:
        try:
            jellyfin.refresh_library(lib_id)
        except Exception as exc:  # noqa: BLE001 - report, never abort
            print(f"  refresh {lib_id} failed: {str(exc)[:100]}")


def _watch_snapshot(uid: str, item_id: str) -> Dict[str, Any]:
    """Played + position before a swap. Never raises (empty on any failure)."""
    try:
        ud = jellyfin.user_data(uid, item_id) or {}
        return {"played": bool(ud.get("Played")),
                "ticks": int(ud.get("PlaybackPositionTicks") or 0)}
    except Exception:  # noqa: BLE001 - best effort; no snapshot, no restore
        return {}


def _row_id_by_path(uid: str, container_path: str) -> Optional[str]:
    """Current row id for a container path, or None. Never raises."""
    try:
        rows = jellyfin.list_items(uid, recursive=True, include_types="Movie",
                                   fields="Path") or []
        for it in rows:
            if isinstance(it, dict) and it.get("Path") == container_path:
                return str(it.get("Id"))
    except Exception:  # noqa: BLE001 - best effort
        pass
    return None


def _restore_watch_state(uid: str, new_id: str, snap: Dict[str, Any]) -> None:
    """Re-apply Played + position after an id-changing swap. Never raises."""
    try:
        if snap.get("played"):
            jellyfin.set_played(uid, new_id, True)
        if snap.get("ticks"):
            jellyfin.set_playback_position(uid, new_id, int(snap["ticks"]))
    except Exception:  # noqa: BLE001 - report, never abort
        print(f"  watch-state restore failed for {new_id}", flush=True)


def _hardlinked_hashes(hashes: list) -> set:
    """Torrent hashes whose payload shares bytes with the library (nlink>1).

    Best effort; any lookup failure excludes the hash (fail-open toward
    keeping files — deleting the entry only). Never raises.
    """
    from media_lib import place  # deferred: light import for tests

    linked = set()
    try:
        live = {str(t.get("hash")): t for t in qb.torrents() if isinstance(t, dict)}
        for h in hashes:
            t = live.get(str(h))
            if not t:
                continue
            try:
                root = qb.save_to_remote(str(t.get("save_path") or "/downloads"))
            except Exception:  # noqa: BLE001 - unknown root, keep files
                linked.add(h)
                continue
            try:
                files = qb.files(str(h)) or []
            except Exception:  # noqa: BLE001 - unreadable, keep files
                linked.add(h)
                continue
            for f in files:
                if not isinstance(f, dict):
                    continue
                disk = root + "/" + str(f.get("name") or "")
                try:
                    n = place.remote_nlink(disk)
                except Exception:  # noqa: BLE001 - unstatable, keep files
                    n = 2
                if n and n > 1:
                    linked.add(h)
                    break
    except Exception:  # noqa: BLE001 - whole check failed, keep everything
        return set(hashes)
    return linked


def _lib_id(uid, name) -> str | None:

    rows: Any = jellyfin.get_json("/Library/VirtualFolders")
    items: list[Any] = rows if isinstance(rows, list) else []
    for r in items:
        if r.get("Name") == name:
            item_id = r.get("ItemId")
            return str(item_id) if item_id else None
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply approved plan rows")
    ap.add_argument(
        "--ids", required=True, help='comma-separated plan ids, e.g. 1,3,5 (no "all")'
    )
    ap.add_argument("--plan", default=str(config.STATE_DIR / "plan.json"))
    ap.add_argument(
        "--delete-source",
        action="store_true",
        help="after a verified swap, delete the torrent AND its files",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        wanted = [int(x) for x in args.ids.split(",") if x.strip()]
    except ValueError:
        print("--ids must be a comma-separated list of integers", file=sys.stderr)
        return 2
    if not wanted:
        print("refusing: no ids supplied", file=sys.stderr)
        return 2

    plan = json.loads(Path(args.plan).read_text())
    by_id = {r["id"]: r for r in plan}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        print(f"refusing: ids not in plan: {missing}", file=sys.stderr)
        return 2
    rows = [by_id[i] for i in wanted]
    # A row missing a field makes the summary line raise KeyError mid-run, after work may
    # already have happened. Check the shape up front and refuse the whole batch instead.
    REQUIRED = ("id", "src", "dest_disk", "old_disk")
    malformed = [(r.get("id"), [k for k in REQUIRED if k not in r]) for r in rows]
    malformed = [(i, m) for i, m in malformed if m]
    if malformed:
        for i, m in malformed:
            print(f"  row {i}: missing {m}", file=sys.stderr)
        print(f"refusing: {len(malformed)} malformed row(s) in {args.plan}", file=sys.stderr)
        return 2
    bad = [r["id"] for r in rows if r.get("recommend") != "REPLACE"]
    if bad:
        print(f"refusing: not REPLACE rows: {bad}", file=sys.stderr)
        return 2

    if args.dry_run:
        for r in rows:
            print(
                f"[{r['id']}] would swap {r['old_res']} -> {r['new_res']}: {r['title']}"
            )
        return 0

    with safety.acquire():
        done, failed = [], []
        snaps: Dict[Any, Dict[str, Any]] = {}
        for r in rows:
            print(
                f"[{r['id']}] {r['old_res']} -> {r['new_res']}  "
            f"{(r.get('title') or r.get('old_name') or '?')[:50]}",
                flush=True,
            )
            try:
                # Review-8: never swap bytes under an active viewer. old_id is
                # the live Jellyfin row being replaced; skip (don't fail) when
                # it is currently playing — the next apply run retries it.
                if r.get("old_id") and jellyfin.is_playing(r["old_id"]):
                    print("    SKIPPED: client is playing this item right now", flush=True)
                    failed.append((r, "playing — retry on next apply run"))
                    continue
                # Review-3 P1.4: snapshot watch state BEFORE the swap. An
                # extension change mints a new ItemId and wipes it otherwise.
                snap: Dict[str, Any] = {}
                try:
                    snap = _watch_snapshot(jellyfin.user_id(), r["old_id"]) if r.get("old_id") else {}
                except Exception:  # noqa: BLE001 - uid unavailable, skip snapshot
                    pass
                status, detail = swap.replace(
                    r["src"],
                    r["dest_disk"],
                    r["old_disk"],
                    old_name=r.get("old_name") or "",
                )
            except swap.SwapError as exc:
                print(f"    FAILED: {exc}", flush=True)
                failed.append((r, str(exc)))
                continue
            # journal IMMEDIATELY: a crash must not forget a completed swap
            safety.record(
                hash=r["hash"],
                title=r.get("title"),
                status=status,
                detail=detail,
                item=r.get("old_id"),
            )
            done.append(r)
            if snap:
                snaps[r["id"]] = snap
            print(f"    {status}: {detail}", flush=True)

        libs = {r.get("library") for r in done if r.get("library")}
        uid = None
        try:
            uid = jellyfin.user_id()
        except Exception:
            print("  (JELLYFIN_USER_ID unset - skipping library refresh)")
        if uid and libs:
            ids = [i for i in (_lib_id(uid, n) for n in libs) if i]
            _refresh(ids, [r["old_id"] for r in done if r.get("old_id")])
            # Review-3 P1.4: an extension change mints a new ItemId, wiping
            # Played/position. The bytes are at dest; if the row id differs
            # from old_id, re-apply the pre-swap snapshot.
            from media_lib import place as _place

            for r in done:
                kept = snaps.get(r["id"])
                if not kept or not (kept.get("played") or kept.get("ticks")):
                    continue
                try:
                    jf_path = _place.to_jf(str(r["dest_disk"]))
                    new_id = _row_id_by_path(uid, jf_path)
                except Exception:  # noqa: BLE001 - best effort
                    new_id = None
                if new_id and new_id != r.get("old_id"):
                    _restore_watch_state(uid, new_id, kept)
                    print(f"  watch state restored onto {new_id}", flush=True)

        if args.delete_source and done:
            hashes = [r["hash"] for r in done]
            # Review-3 P1.1: hardlink guard. If the payload shares bytes with
            # the library (nlink>1), drop the torrent entry but keep the files.
            linked = _hardlinked_hashes(hashes)
            plain = [h for h in hashes if h not in linked]
            if plain:
                qb.delete(list(plain), delete_files=True)
            if linked:
                qb.delete(list(linked), delete_files=False)
                print(f"  kept files for hardlinked: {sorted(linked)}")
            after = {str(t.get("hash")) for t in qb.torrents()}
            left = [h for h in hashes if h in after]
            print(f"  deleted {len(hashes)} torrent(s); still present: {left}")

    print(f"done: {len(done)} replaced, {len(failed)} failed")
    for r, why in failed:
        print(f"  [{r['id']}] {why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
