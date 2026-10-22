"""Inventory of the music-doco libraries: which series exist, and how many of
their episode rows have no file.

"Missing episodes" has two very different meanings on this server, and conflating
them has already caused a wrong conclusion once:

  * a GHOST ROW — a provider-listed episode with no file (browsable, unplayable);
  * a MISSING FILE — media on disk that the scanner never indexed (invisible).

This script counts the first, then a companion check compares disk against rows to
find the second.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from media_lib import jellyfin  # noqa: E402
from media_lib.jellyfin import JellyfinError  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "state" / "musicdoco-inventory.json"


def series_in(library_id: str, uid: str):
    """Every series row in a library, paginated (Limit=0 returns zero items)."""
    items, start = [], 0
    while True:
        page = jellyfin.get_json(f"/Users/{uid}/Items", params={
            "ParentId": library_id, "Recursive": "true",
            "IncludeItemTypes": "Series", "Fields": "Path",
            "Limit": 500, "StartIndex": start,
        })
        batch = page.get("Items") or []
        items.extend(batch)
        if not batch or len(items) >= (page.get("TotalRecordCount") or 0):
            return items
        start += len(batch)


def episodes_of(series_id: str, uid: str):
    items, start = [], 0
    while True:
        page = jellyfin.get_json(f"/Shows/{series_id}/Episodes", params={
            "UserId": uid, "Fields": "Path,Overview",
            "Limit": 500, "StartIndex": start,
        })
        batch = page.get("Items") or []
        items.extend(batch)
        if not batch or len(items) >= (page.get("TotalRecordCount") or 0):
            return items
        start += len(batch)


def main() -> int:
    uid = jellyfin.user_id()
    views = jellyfin.get_json(f"/Users/{uid}/Views").get("Items") or []
    print(f"libraries: {len(views)}")
    report = {"libraries": [], "generated": None}
    for view in views:
        lib_id, name = view.get("Id"), view.get("Name")
        print(f"  {name}  ({view.get('CollectionType')})  id={lib_id}")

    for view in views:
        lib_id, name = view.get("Id"), view.get("Name")
        series = series_in(lib_id, uid)
        if not series:
            continue
        entry = {"library": name, "id": lib_id, "series": []}
        for s in series:
            try:
                eps = episodes_of(s["Id"], uid)
            except JellyfinError as exc:
                # A series row the listing returns but /Episodes rejects: a
                # dangling row, not a metadata gap. Record it rather than
                # aborting the whole inventory.
                entry["series"].append({
                    "name": s.get("Name"), "id": s["Id"], "rows": 0,
                    "with_file": 0, "ghosts": 0, "ghost_names": [],
                    "ghost_slots": [], "unreachable": str(exc)[:90],
                })
                continue
            with_file = [e for e in eps if e.get("Path")]
            ghosts = [e for e in eps if not e.get("Path")]
            if not ghosts:
                continue
            entry["series"].append({
                "name": s.get("Name"), "id": s["Id"],
                "rows": len(eps), "with_file": len(with_file),
                "ghosts": len(ghosts),
                "ghost_names": [e.get("Name") for e in ghosts],
                "ghost_slots": sorted(
                    f"S{(e.get('ParentIndexNumber') or 0):02d}E{(e.get('IndexNumber') or 0):02d}"
                    for e in ghosts),
            })
        if entry["series"]:
            report["libraries"].append(entry)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {OUT}")
    total_ghosts = sum(s["ghosts"] for lib in report["libraries"] for s in lib["series"])
    total_rows = sum(s["rows"] for lib in report["libraries"] for s in lib["series"])
    print(f"series with gaps: {sum(len(lib['series']) for lib in report['libraries'])}")
    print(f"episode rows: {total_rows}  ghost rows: {total_ghosts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
