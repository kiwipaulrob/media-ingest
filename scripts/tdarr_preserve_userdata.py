#!/usr/bin/env python3
"""Preserve Jellyfin watch state across Tdarr replacements.

Tdarr's flow outputs `.mkv`, so transcoding `X.avi` produces `X.mkv`. Jellyfin
treats that as a BRAND NEW file: it allocates a new item id, stamps a new
DateCreated and drops the old row — verified on 23 Oct 2026, where
`All The Yong Dudes` went from id d2b55207... to a0f9aaad... at the moment of
replacement. With realtime monitoring on, this happens within seconds. Played
flags and resume positions go with the old item.

The fix keys on something Tdarr does NOT change: the path without its extension.
`/data/musicdocos/Foo (2009)/Foo.avi` and `/data/musicdocos/Foo (2009)/Foo.mkv`
share the key `/data/musicdocos/Foo (2009)/Foo`. That also keeps part1/part2 in
the same folder distinct, which a folder-only key would not.

    snapshot   record Played / PlayCount / PlaybackPositionTicks for every item
    restore    re-apply that state to whatever item now carries the same key

Run `snapshot` BEFORE a transcode batch and `restore` after it. Restore only ever
writes state that is currently missing — it never resets an item you have since
watched, and never un-marks something played.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import jellyfin  # noqa: E402

PARENTS = {
    "musicdocos": ("5f95760bae4c07e31d840d70c3a46111", "Movie"),
    "musicdocos-series": ("18ce65a0ab9d7a771d5ac55e6024fe2a", "Episode"),
}
SNAPSHOT = Path(__file__).resolve().parent.parent / "state" / "userdata-snapshot.json"


def key_for(path: str) -> str:
    """Stable identity across an extension change: the path minus its extension."""
    return os.path.splitext(path)[0]


def iter_items(uid: str):
    """Yield (library, path, item) for every item with a file in both libraries."""
    for lib, (parent, kind) in PARENTS.items():
        start = 0
        while True:
            page = jellyfin.get_json(f"/Users/{uid}/Items", params={
                "ParentId": parent, "Recursive": "true", "IncludeItemTypes": kind,
                "Fields": "Path", "StartIndex": str(start), "Limit": "500",
            })
            rows = page.get("Items") or []
            for it in rows:
                path = it.get("Path")
                if path:
                    yield lib, path, it
            start += len(rows)
            total = page.get("TotalRecordCount") or 0
            if not rows or start >= total:
                break


def cmd_snapshot(_args) -> int:
    uid = jellyfin.user_id()
    items: Dict[str, Dict[str, Any]] = {}
    collisions: List[str] = []
    for lib, path, it in iter_items(uid):
        k = key_for(path)
        ud = it.get("UserData") or {}
        rec = {
            "library": lib, "item_id": it.get("Id"), "path": path,
            "played": bool(ud.get("Played")),
            "play_count": int(ud.get("PlayCount") or 0),
            "position_ticks": int(ud.get("PlaybackPositionTicks") or 0),
        }
        if k in items:
            collisions.append(k)
        items[k] = rec

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(items, indent=1, sort_keys=True))

    interesting = [r for r in items.values()
                   if r["played"] or r["play_count"] or r["position_ticks"]]
    print(f"snapshot: {len(items)} items -> {SNAPSHOT}")
    print(f"  with state worth preserving: {len(interesting)} "
          f"(played {sum(1 for r in items.values() if r['played'])}, "
          f"resume points {sum(1 for r in items.values() if r['position_ticks'])})")
    if collisions:
        print(f"  WARNING: {len(collisions)} duplicate keys (same path minus "
              f"extension): {collisions[:3]}")
    return 0


def cmd_restore(args) -> int:
    if not SNAPSHOT.exists():
        print(f"FAIL: no snapshot at {SNAPSHOT}; run 'snapshot' first")
        return 1
    saved: Dict[str, Dict[str, Any]] = json.loads(SNAPSHOT.read_text())
    uid = jellyfin.user_id()

    restored = already = missing_file = scanned = 0
    seen: set[str] = set()
    for _lib, path, it in iter_items(uid):
        k = key_for(path)
        snap = saved.get(k)
        if not snap:
            continue
        seen.add(k)
        ud = it.get("UserData") or {}
        cur_played = bool(ud.get("Played"))
        cur_count = int(ud.get("PlayCount") or 0)
        cur_pos = int(ud.get("PlaybackPositionTicks") or 0)
        # Trust the Played FLAG alone. Jellyfin can hold PlayCount>0 with
        # Played=False (a part-watched item), so deriving "played" from the count
        # would mark 144 untouched items played the moment this ran.
        want_played = bool(snap["played"])
        want_pos = int(snap["position_ticks"])

        changed = False
        label = os.path.basename(path)
        if args.dry_run:
            scanned += 1
            if (want_played and not cur_played) or (want_pos and not cur_pos):
                print(f"  would restore {label}: played={want_played} pos={want_pos}")
                restored += 1
            else:
                already += 1
            continue
        if want_played and not cur_played:
            jellyfin.set_played(uid, it["Id"], True)
            changed = True
            print(f"  played      <- {label}")
        if want_pos and not cur_pos:
            jellyfin.set_playback_position(uid, it["Id"], want_pos)
            changed = True
            mins = want_pos / 600_000_000
            print(f"  resume {mins:6.1f}m <- {label}")
        restored += 1 if changed else 0
        already += 0 if changed else 1

    # Snapshot entries with no current item: the file is gone and nothing took its place.
    for k, snap in saved.items():
        if k not in seen and (snap["played"] or snap["position_ticks"]):
            missing_file += 1
            print(f"  ORPHAN (no current item) {os.path.basename(snap['path'])}")

    print(f"restore: {scanned} matched, {restored} updated, {already} already correct, "
          f"{missing_file} orphaned with state at risk")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="record state before a transcode batch")
    r = sub.add_parser("restore", help="re-apply state after replacements")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return cmd_snapshot(args) if args.cmd == "snapshot" else cmd_restore(args)


if __name__ == "__main__":
    sys.exit(main())
