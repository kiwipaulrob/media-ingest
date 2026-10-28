"""Series-level 4-field audit (the inline sweep, promoted to a script).

Read-only. Lists every Series row in a library missing Tmdb / Imdb /
Overview>=50c / Primary image, so the enrich pass has a shopping list.
Writes nothing — enrichment stays explicit per row.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from media_lib import jellyfin  # noqa: E402


def flags(row: Dict[str, Any]) -> List[str]:
    """Missing-field flags for one Series row. Pure.

    Identity is satisfied by EITHER Tmdb or Imdb: many UK strands exist in
    IMDb with no TMDB series entry (Folk America, Soul Britannia, the
    Decline trilogy). Flagging those as gaps buried real ones under noise.
    """
    prov = row.get("ProviderIds") or {}
    ov = row.get("Overview") or ""
    tags = row.get("ImageTags") or {}
    out = []
    if not prov.get("Tmdb") and not prov.get("Imdb"):
        out.append("no-ids")
    if len(ov) < 50:
        out.append(f"ov{len(ov)}c")
    if not tags.get("Primary"):
        out.append("no-img")
    return out


def audit(library: str) -> List[Dict[str, Any]]:
    """Flagged Series rows for a library. Read-only, never raises."""
    uid = jellyfin.user_id()
    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    libs = {lf.get("Name"): lf.get("ItemId") for lf in folders if isinstance(lf, dict)}
    if library not in libs:
        return [{"error": f"unknown library {library!r}"}]
    rows = list(jellyfin.list_items(
        uid, parent_id=libs[library], include_types="Series",
        fields="Overview,ProviderIds,ImageTags") or [])
    out = []
    for r in sorted(rows, key=lambda x: str(x.get("Name"))):
        if not isinstance(r, dict):
            continue
        f = flags(r)
        if f:
            out.append({"id": r.get("Id"), "name": r.get("Name"),
                        "tmdb": (r.get("ProviderIds") or {}).get("Tmdb"),
                        "flags": f})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit series metadata (read-only).")
    ap.add_argument("--library", required=True)
    a = ap.parse_args(argv)
    rows = audit(a.library)
    print(f"flagged: {len(rows)}")
    for r in rows:
        if "error" in r:
            print(f"  ERROR {r['error']}")
            return 2
        print(f"  {str(r['name'])[:52]:<54} {','.join(r['flags'])} tmdb={r['tmdb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
