#!/usr/bin/env python3
"""Find Jellyfin items whose file no longer exists — and files Jellyfin never indexed.

Tdarr renames files when its flow changes the container (`.avi` -> `.mkv`). The
new file is picked up by realtime monitoring, but anything that fails to re-resolve
leaves a row pointing at a path that is gone: Jellyfin shows the item, plays
nothing. That is what this checks, in both directions:

  DEAD ROW        item in Jellyfin, no file on disk      -> refresh or remove it
  UNINDEXED       file on disk, no item in Jellyfin      -> scan missed it

Paths: Jellyfin sees `/data/<lib>/...` where the host has `/mnt/zmedia/videos/<lib>/...`,
so `/data` maps to `/mnt/zmedia/videos`. The disk listing is taken from a host that
actually mounts the pool (prox3 or prox4), not from this machine, which has no mount.

Usage:
    jellyfin_dead_rows.py [--host root@192.168.214.131] [--show N]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import config, jellyfin  # noqa: E402

PARENTS = {
    "musicdocos": ("5f95760bae4c07e31d840d70c3a46111", "Movie"),
    "musicdocos-series": ("18ce65a0ab9d7a771d5ac55e6024fe2a", "Episode"),
}
REMOTE_ROOT = f"{config.MEDIA_VOLUME}/videos"
JF_ROOT = "/data"
VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".webm", ".mpg", ".wmv")
# Names Jellyfin falls back to when it cannot parse a title. They hide an item from
# every name search, which is how an unnamed feature survived unnoticed: searching
# "Joy Division" missed it because the item was literally called "Video".
GENERIC_NAMES = {"video", "movie", "feature", "untitled", "title", "clip", "tv",
                 "episode", "unknown", "dvd", "main"}
DVD_EXTS = (".vob", ".ifo", ".bup")


def disk_files(host: str) -> Set[str]:
    """Every media file under the two libraries, as host paths."""
    roots = " ".join(f"{REMOTE_ROOT}/{lib}" for lib in PARENTS)
    expr = " -o ".join(f'-iname "*{e}"' for e in VIDEO_EXT)
    cmd = f'find {roots} -type f \\( {expr} \\) 2>/dev/null'
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=15", host, cmd],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit(f"find failed on {host}: {out.stderr.strip()[:200]}")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def jf_items() -> Dict[str, Dict]:
    """path (host form) -> item, for every item with a file."""
    uid = jellyfin.user_id()
    found: Dict[str, Dict] = {}
    for lib, (parent, kind) in PARENTS.items():
        start = 0
        while True:
            page = jellyfin.get_json(f"/Users/{uid}/Items", params={
                "ParentId": parent, "Recursive": "true", "IncludeItemTypes": kind,
                "Fields": "Path", "StartIndex": str(start), "Limit": "500",
            })
            rows = page.get("Items") or []
            for it in rows:
                p = it.get("Path")
                if p:
                    found[p.replace(JF_ROOT, REMOTE_ROOT, 1)] = {
                        "id": it.get("Id"), "name": it.get("Name"), "lib": lib,
                        "jf_path": p,
                    }
            start += len(rows)
            if not rows or start >= (page.get("TotalRecordCount") or 0):
                break
    return found


def remote_existing(host: str, paths: List[str]) -> Set[str]:
    """Ask the host which of these paths exist (file OR directory).

    Necessary because a DVD-folder item's Path is a DIRECTORY (VIDEO_TS style),
    so a file-only `find` would report every one of them as a dead row. Test,
    don't infer.
    """
    if not paths:
        return set()
    script = "while IFS= read -r p; do [ -e \"$p\" ] && printf '%s\\n' \"$p\"; done"
    # Trailing newline matters: `while read` returns non-zero on EOF for a final
    # line with no newline and never runs the body for it, so the last candidate
    # is silently reported as missing. That produced a false "dead row".
    payload = "\n".join(paths) + "\n"
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=15", host, script],
                         input=payload, capture_output=True, text=True,
                         timeout=600)
    if out.returncode != 0:
        raise SystemExit(f"existence check failed: {out.stderr.strip()[:200]}")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="root@192.168.214.131",
                    help="host that mounts the pool (default prox3)")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    disk = disk_files(args.host)
    items = jf_items()
    print(f"disk files: {len(disk)}   jellyfin items: {len(items)}")

    # Candidates are only items whose path is not a known file; confirm on disk,
    # because folder-items (DVD/VIDEO_TS) legitimately have directory paths.
    candidates = [p for p in items if p not in disk]
    exists = remote_existing(args.host, candidates)
    dead = [(p, i) for p, i in items.items() if p not in disk and p not in exists]

    # Files inside a folder-item belong to that item; never call them unindexed.
    folder_items = sorted(exists)

    def claimed(f: str) -> bool:
        return any(f.startswith(d.rstrip("/") + "/") for d in folder_items)

    unindexed = sorted(f for f in (disk - set(items)) if not claimed(f))

    print(f"\nDEAD ROWS (item exists, path missing on disk): {len(dead)}")
    for p, i in dead[:args.show]:
        print(f"   [{i['lib']}] {i['name'][:60]}")
        print(f"        {i['jf_path']}")

    # A rename is the usual cause: same stem, different extension, still on disk.
    stems = {os.path.splitext(f)[0]: f for f in disk}
    renamed = [(p, i, stems[os.path.splitext(p)[0]])
               for p, i in dead if os.path.splitext(p)[0] in stems]
    if renamed:
        print(f"\n  of which RENAMED (same stem present with another extension): {len(renamed)}")
        for p, i, actual in renamed[:args.show]:
            print(f"     {os.path.basename(p)}  ->  {os.path.basename(actual)}")

    print(f"\nfolder-items (directory paths, all present): {len(folder_items)}")

    # Items that are hard to find and easy to misjudge: generic names, and files
    # parked inside a VIDEO_TS tree that are not actually DVD files.
    junk = [(p, i) for p, i in items.items()
            if str(i.get("name", "")).strip().lower() in GENERIC_NAMES]
    fake_dvd = [(p, i) for p, i in items.items()
                if "/VIDEO_TS/" in p and not p.lower().endswith(DVD_EXTS)]
    print(f"\nGENERIC NAMES (invisible to name search): {len(junk)}")
    for p, i in junk[:args.show]:
        print(f"   [{i['lib']}] {i['name']!r}  {os.path.basename(p)}")
    print(f"\nMIS-NESTED IN VIDEO_TS (not a DVD file): {len(fake_dvd)}")
    for p, i in fake_dvd[:args.show]:
        print(f"   [{i['lib']}] {i['name'][:50]}  {os.path.basename(p)}")

    print(f"\nUNINDEXED (file on disk, no item): {len(unindexed)}")
    for f in unindexed[:args.show]:
        print(f"   {f.replace(REMOTE_ROOT + '/', '')}")
    if len(unindexed) > args.show:
        print(f"   ... {len(unindexed) - args.show} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
