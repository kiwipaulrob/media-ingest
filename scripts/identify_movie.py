"""Identify a single-film row: find by path fragment, apply TMDB match, verify.

Same contract as identify_series: exit 0 only if the read-back shows the
requested Tmdb id. For overview-only entries (no provider ids exist) use
set_overview directly — identifying with a wrong id is worse than no id.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, ".")

from media_lib import jellyfin


def identify_movie(
    library: str,
    frag: str,
    title: str,
    year: int,
    tmdb_id: str,
    replace_images: bool = False,
    settle: int = 60,
) -> dict:
    """Run the full identify flow. Returns the verified read-back row."""
    from typing import Any

    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    libs = {
        str(f["Name"]): str(f["ItemId"])
        for f in folders
        if isinstance(f, dict) and f.get("Name") and f.get("ItemId")
    }
    lib = libs.get(library) or jellyfin.resolve_library(library)
    rows = jellyfin.get_json(
        "/Items",
        params={
            "ParentId": lib,
            "Recursive": "true",
            "IncludeItemTypes": "Movie",
            "Fields": "Path",
            "Limit": "2000",
        },
    )
    target = next(
        (i for i in (rows.get("Items") or []) if frag in str(i.get("Path", ""))),
        None,
    )
    if not target:
        return {"ok": False, "error": f"movie row not found for {frag!r}"}
    res = jellyfin.remote_search(
        target["Id"], title, year, {"Tmdb": tmdb_id}, media_type="Movie"
    )
    best = next(
        (x for x in res if x.get("ProviderIds", {}).get("Tmdb") == tmdb_id),
        None,
    )
    if not best:
        cands = [(x.get("Name"), x.get("ProductionYear")) for x in res[:3]]
        return {"ok": False, "error": f"no TMDB {tmdb_id} candidate", "candidates": cands}
    code = jellyfin.apply_identification(
        target["Id"], best, replace_images=replace_images
    )
    jellyfin.refresh_item(target["Id"])
    time.sleep(settle)
    read = jellyfin.get_json(
        f"/Users/{jellyfin.user_id()}/Items/{target['Id']}",
        params={"Fields": "ProviderIds,Overview,ImageTags,Name,ProductionYear,RunTimeTicks"},
    )
    prov = read.get("ProviderIds") or {}
    ok = prov.get("Tmdb") == tmdb_id
    return {
        "ok": ok,
        "apply": code,
        "name": read.get("Name"),
        "year": read.get("ProductionYear"),
        "providers": sorted(prov.keys()),
        "overview_chars": len(read.get("Overview") or ""),
        "images": sorted((read.get("ImageTags") or {}).keys()),
        "runtime_min": round((read.get("RunTimeTicks") or 0) / 600_000_000, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Identify a movie row via TMDB.")
    ap.add_argument("--library", required=True)
    ap.add_argument("--frag", required=True, help="path fragment of the film")
    ap.add_argument("--title", required=True)
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--tmdb-id", required=True)
    ap.add_argument("--images", action="store_true", help="replace images too")
    ap.add_argument("--settle", type=int, default=60)
    a = ap.parse_args(argv)
    try:
        out = identify_movie(
            a.library, a.frag, a.title, a.year, a.tmdb_id,
            replace_images=a.images, settle=a.settle,
        )
    except Exception as exc:  # noqa: BLE001 - CLI surface: report, don't trace
        print({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        return 1
    print(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
