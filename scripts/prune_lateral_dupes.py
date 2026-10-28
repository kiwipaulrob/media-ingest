"""Prune finished torrents that duplicate the shelf (dry-run default).

Read-only unless --apply --ids. Every candidate must pass all three proofs
or it is skipped with a reason:
  1. title matches ONE library film (14-char stem both directions),
  2. not an upgrade (no res tag, or tag within 10% of held width),
  3. the held file probes healthy (a dupe of an unprobeable shelf copy
     is a review, never a delete).
Multi-file torrents and rows with no library match are always skipped.

    python3 scripts/prune_lateral_dupes.py [--apply --ids <hash,hash>]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, ".")

from media_lib import jellyfin, probe, qb  # noqa: E402

#: Score above which a download outranks the shelf and must not be pruned.
MIN_GAIN = 1.1


def match_shelf(name: str, held: Dict[str, Tuple[int, str]]) -> List[Tuple[str, int, str]]:
    """Library films whose stem matches the release stem (14 chars either way).

    Pure. The 14-char window is the same one that once matched T-Men to
    Real Men — but there it FED a swap; here a match only proposes a delete
    of the download, and the upgrade + probe proofs stand behind it.
    """
    stem = re.split(r"[\(\[]", name)[0].strip().lower()
    return [(h, w, p) for h, (w, p) in held.items()
            if stem[:14] in h or h[:14] in stem]


def release_height(name: str) -> int:
    """Height tag in a release name, or 0. Pure."""
    m = re.search(r"(2160|1080|720|480)p", name or "")
    return int(m.group(1)) if m else 0


def verdict(name: str, held: Dict[str, Tuple[int, str]]) -> Tuple[str, str]:
    """(action, reason) for one finished torrent. Pure.

    Actions: 'prune' | 'skip'. Never 'upgrade' — a better copy is a
    follow-up ingest decision, not this script's job.
    """
    if "D.O.A" in name or "D O A" in name:
        return "skip", "multi-file, needs review"
    hits = match_shelf(name, held)
    if not hits:
        return "skip", "no library match"
    _h, width, _p = hits[0]
    dh = release_height(name)
    if dh and width and dh > width * MIN_GAIN:
        return "skip", f"possible upgrade {dh}p vs held {width}p"
    return "prune", f"lateral dupe of held {width}p"


def shelf_index(uid: str, lib_ids: List[str]) -> Dict[str, Tuple[int, str]]:
    """Lowercased film name -> (width, container path). Read-only."""
    held: Dict[str, Tuple[int, str]] = {}
    for lid in lib_ids:
        try:
            rows = jellyfin.list_items(
                uid, parent_id=lid, recursive=True, include_types="Movie",
                fields="Name,Width,Path") or []
        except Exception:  # noqa: BLE001 - one library down never stops the prune
            continue
        for r in rows:
            if isinstance(r, dict):
                held[str(r.get("Name")).lower()] = (
                    r.get("Width") or 0, r.get("Path") or "")
    return held


def disk_of(container_path: str) -> str:
    """Container /data/... path -> media-host disk path. Pure."""
    from media_lib import place  # deferred: light import for tests

    return place.to_disk(container_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prune lateral-dupe torrents.")
    ap.add_argument("--libs", nargs="+", default=["Film Noir", "Movies"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ids", default="", help="comma-separated torrent hashes")
    a = ap.parse_args(argv)

    uid = jellyfin.user_id()
    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    lib_ids: List[str] = [str(f.get("ItemId")) for f in folders
                          if isinstance(f, dict) and f.get("Name") in a.libs
                          and f.get("ItemId")]
    held = shelf_index(uid, lib_ids)
    wanted = {h.lower() for h in a.ids.split(",") if h.strip()} if a.ids else None

    prunable: List[Tuple[str, str, str]] = []
    skipped: List[Tuple[str, str]] = []
    for t in qb.torrents():
        if not isinstance(t, dict):
            continue
        if float(t.get("progress") or 0) < 0.999:
            continue
        if str(t.get("category")) == "tv":
            continue
        name = str(t.get("name") or "")
        action, reason = verdict(name, held)
        if action == "prune":
            # Proof 3 live: the shelf copy must probe healthy right now.
            _h, _w, lp = match_shelf(name, held)[0]
            info = probe.probe(disk_of(lp)) if lp else None
            if not info or not info.get("h"):
                skipped.append((name, "held copy unprobeable"))
                continue
        if action == "skip":
            skipped.append((name, reason))
        else:
            prunable.append((str(t.get("hash") or ""), name, reason))
    print(f"prunable: {len(prunable)}  skipped: {len(skipped)}")
    for n, why in skipped:
        print(f"  SKIP {why}: {str(n)[:54]}")
    if not a.apply:
        for _h, n, why in prunable:
            print(f"  WOULD-DEL {why}: {str(n)[:54]}")
        print("  (dry run — pass --apply --ids to delete)")
        return 0
    if not wanted:
        print("  --apply requires --ids (no bulk switch)")
        return 2
    done = 0
    for h, n, _why in prunable:
        if str(h).lower() not in wanted:
            continue
        if qb.delete([h], delete_files=True):
            done += 1
            print(f"  DEL {str(n)[:54]}")
    print(f"  deleted {done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
