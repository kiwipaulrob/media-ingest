"""Make sure every placed film folder is actually INDEXED with a runtime.

Placing a file is not the same as having it in the library: Jellyfin discovers
new files on its own schedule, and a freshly discovered row can arrive with
RunTimeTicks = 0 (it looks empty/unplayable in clients until refreshed). This
reconciles the two, idempotently:

  * a media file with NO row  -> POST /Library/Media/Updated (Created)
  * a row with 0 runtime      -> per-item FullRefresh via jellyfin.ensure_row_runtime

  python3 scripts/film_rows_reconcile.py [--library "Film Noir"] [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_lib import jellyfin, place, probe  # noqa: E402

MEDIA_EXT = (".mkv", ".mp4", ".avi", ".m4v")


def lib_entry(name: str) -> dict:
    for entry in jellyfin.get_json("/Library/VirtualFolders") or []:
        if isinstance(entry, dict) and entry.get("Name") == name:
            return entry
    raise SystemExit(f"library not found: {name}")


def rows_by_path(library_id: str) -> dict:
    """jellyfin container path -> item row (paged, retried)."""
    out, start = {}, 0
    while True:
        d = jellyfin.get_json(
            f"/Items?ParentId={library_id}&Recursive=true&IncludeItemTypes=Movie,Video"
            f"&Fields=Path,RunTimeTicks&Limit=500&StartIndex={start}"
        ) or {}
        items = d.get("Items") or []
        for i in items:
            out[i.get("Path") or ""] = i
        if len(items) < 500:
            return out
        start += 500


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default="Film Noir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--only",
        default="",
        help="only paths containing this substring (e.g. 'tmdbid-'), so a whole-library "
             "run does not flag Extras/ and featurettes that carry no rows by design",
    )
    a = ap.parse_args()

    entry = lib_entry(a.library)
    lid = entry["ItemId"]
    disk_root = place.to_disk((entry.get("Locations") or [""])[0])
    rows = rows_by_path(lid)
    # Ask the media host what is on disk (the library mount, container path view).
    listing = probe.on_media(
        f"find {disk_root} -type f -iregex '.*\\.\\(mkv\\|mp4\\|avi\\|m4v\\)$' 2>/dev/null"
    )
    files = [place.to_jf(p.strip()) for p in listing.splitlines() if p.strip()]
    if a.only:
        files = [f for f in files if a.only in f]

    missing, flat = [], []
    for f in files:
        row = rows.get(f)
        if not row:
            missing.append(f)
        elif not (row.get("RunTimeTicks") or 0):
            flat.append(f)

    print(f"on disk {len(files)} | rows {len(rows)} | no row {len(missing)} | 0-runtime {len(flat)}")
    for f in missing:
        print("  NO-ROW   ", f)
    for f in flat:
        print("  NO-RUNTIME", f)

    if not a.apply:
        print("\n(dry run — pass --apply to notify and refresh)")
        return 0

    if missing:
        jellyfin.notify_media(missing)
        print(f"notified {len(missing)} new path(s)")
    for f in flat:
        mins = jellyfin.ensure_row_runtime(f, lid)
        print(f"  refreshed {Path(f).name}: runtime={mins}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
