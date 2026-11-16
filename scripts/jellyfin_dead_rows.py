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
    jellyfin_dead_rows.py [--host root@192.168.214.132] [--show N]
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
    # Extended 2026-11-15: all file-backed libraries. Keys are the CONTAINER
    # dir names from `docker inspect jellyfin` mounts (pool spellings differ:
    # Films, Documentaries, ...). TV holds loose episodes indexed as Movie.
    "documentaries": ("fd1a953b7d62de4b14994039ca9a7b07", "Movie"),
    "docuseries": ("640aaafd53a8ce69b310ecd52ebb3c74", "Episode"),
    "filmdocos": ("4205a0fb59c3c25b817a6842754a6049", "Movie"),
    "filmdocuseries": ("ee3ce99311f3c30e90b9e8416cf0e5c4", "Episode"),
    "filmnoir": ("756e47c21cb82fde09bf4a0fcd4a3512", "Movie"),
    "movies": ("f137a2dd21bbc1b99aa5c0f6bf02a805", "Movie"),
    "tvshows": ("4514ec850e5ad0c47b58444e17b6346c", "Movie"),
}
REMOTE_ROOT = f"{config.MEDIA_VOLUME}/videos"
JF_ROOT = "/data"
# Pool spellings differ in case/name from the container dirs; display mapping.
POOL_NAME = {
    "musicdocos": "musicdocos", "musicdocos-series": "musicdocos-series",
    "documentaries": "Documentaries", "docuseries": "DocuSeries",
    "filmdocos": "FilmDocumentaries", "filmdocuseries": "filmdocuseries",
    "filmnoir": "filmnoir", "movies": "Films", "tvshows": "TV",
}


def to_host(jf_path: str) -> str:
    """Container path -> real pool path for display."""
    rest = jf_path[len(JF_ROOT) + 1:].split("/", 1)
    if rest and rest[0] in POOL_NAME:
        return f"{REMOTE_ROOT}/{POOL_NAME[rest[0]]}" + (f"/{rest[1]}" if len(rest) > 1 else "")
    return jf_path.replace(JF_ROOT, REMOTE_ROOT, 1)


def to_jf(host_path: str) -> str:
    """Real pool path (or mapped form) -> container path for comparison."""
    rest = host_path[len(REMOTE_ROOT) + 1:].split("/", 1)
    for cont, pool in POOL_NAME.items():
        if rest and rest[0].lower() == pool.lower():
            return f"{JF_ROOT}/{cont}" + (f"/{rest[1]}" if len(rest) > 1 else "")
    return host_path.replace(REMOTE_ROOT, JF_ROOT, 1)
# The pool is CIFS-served (Samba `catia`): a colon in a name is stored on disk
# as U+F022, so the SAME file has two spellings — `/data/.../Stax: ...` inside
# the Jellyfin container, `/mnt/zmedia/videos/.../Stax<U+F022> ...` on the pool
# host. Comparing one view against the other reads every such row as a dead
# row (proven 2026-11-15: four healthy Stax episodes flagged dead, then
# unlinked by DELETE). Disk truth therefore comes from the CONTAINER's view,
# which is byte-identical to what Jellyfin stores — never the pool host's.
CT_HOST = "root@192.168.214.109"
CT_ID = "105"
CONTAINER = "jellyfin"
VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".webm", ".mpg", ".wmv")
# Names Jellyfin falls back to when it cannot parse a title. They hide an item from
# every name search, which is how an unnamed feature survived unnoticed: searching
# "Joy Division" missed it because the item was literally called "Video".
GENERIC_NAMES = {"video", "movie", "feature", "untitled", "title", "clip", "tv",
                 "episode", "unknown", "dvd", "main"}
DVD_EXTS = (".vob", ".ifo", ".bup")


def disk_files(host: str) -> Set[str]:
    """Every media file under the libraries, as CONTAINER paths (`/data/...`).

    Enumerated through the Jellyfin CONTAINER (`/data/...`), which is the
    same CIFS-decoded view the server stores in its rows — then mapped to
    host form for readable output. Never enumerate the pool host here: its
    native spelling (U+F022 for ':') mismatches every row and fabricates
    dead rows. The `host` arg is kept for CLI compatibility (folder-item
    checks below still use it via remote_existing).
    """
    roots = " ".join(f"{JF_ROOT}/{lib}" for lib in PARENTS)
    expr = " -o ".join(f'-iname "*{e}"' for e in VIDEO_EXT)
    cmd = (f"pct exec {CT_ID} -- docker exec {CONTAINER} "
           f"find {roots} -type f \\( {expr} \\) 2>/dev/null")
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=15", CT_HOST, cmd],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit(f"find failed in container: {out.stderr.strip()[:200]}")
    # Canonical key is the CONTAINER path (exactly what Jellyfin stores).
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
                    found[p] = {
                        "id": it.get("Id"), "name": it.get("Name"), "lib": lib,
                        "jf_path": p,
                    }
            start += len(rows)
            if not rows or start >= (page.get("TotalRecordCount") or 0):
                break
    return found


def remote_existing(host: str, paths: List[str]) -> Set[str]:
    """Which of these container-form paths exist — answered from the CONTAINER view.

    `paths` arrive in container form (`/data/...`); they are tested against ONE
    container enumeration (files and directories). The pool host is never asked:
    its native spelling of CIFS-mangled names (U+F022 for ':') disagrees with
    the server's rows, and that disagreement previously fabricated dead rows
    out of healthy items. Returns the subset of `paths` that exist.
    """
    if not paths:
        return set()
    roots = " ".join(f"{JF_ROOT}/{lib}" for lib in PARENTS)
    cmd = (f"pct exec {CT_ID} -- docker exec {CONTAINER} "
           f"find {roots} 2>/dev/null")
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=15", CT_HOST, cmd],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit(f"container find failed: {out.stderr.strip()[:200]}")
    present = {line.strip() for line in out.stdout.splitlines() if line.strip()}
    return {p for p in paths if p in present}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="root@192.168.214.132",
                    help="host that mounts the pool (default prox4)")
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
        print(f"   {to_host(f)}")
    if len(unindexed) > args.show:
        print(f"   ... {len(unindexed) - args.show} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
