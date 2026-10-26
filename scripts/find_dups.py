#!/usr/bin/env python3
"""Find duplicate movie items across/within libraries: same film, more than one item row.

Groups by provider id first (Imdb, then Tmdb), falling back to normalised name+year. A group is reported
only when it has more than one distinct Path, so genuine multi-version items (one row, several sources)
are not flagged.
"""
import json
import re
import sys

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin  # noqa: E402

MOVIE_LIBS = ["Docos", "Film Docos", "Music Docos", "Movies", "Film Noir", "TV"]
OUT = "/tmp/dup_candidates.json"


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def group_key(item):
    """Identity key for one Movie row: provider id wins, name+year is the fallback.

    Provider-first matters because two different films can normalise to one string
    (Metroland 1998 vs Metro-Land 1973); name+year is only reached when no ids exist.
    """
    prov = item.get("ProviderIds") or {}
    if prov.get("Imdb"):
        return "imdb:" + prov["Imdb"]
    if prov.get("Tmdb"):
        return "tmdb:" + prov["Tmdb"]
    return "name:" + norm(item.get("Name")) + ":" + str(item.get("ProductionYear") or "")


def find_duplicate_groups(rows):
    """Return groups with >1 row on >1 distinct path.

    The distinct-path rule is what keeps multi-version items (one row, sources=2)
    and episode siblings out of the report — grouping by id or title alone flags both.
    """
    groups = {}
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)
    return [
        {"key": key, "rows": members}
        for key, members in groups.items()
        if len(members) > 1 and len({m.get("path") for m in members if m.get("path")}) > 1
    ]


def main():
    libs = {f.get("Name"): f.get("ItemId") for f in (jellyfin.get_json("/Library/VirtualFolders") or [])}
    rows = []
    total = 0

    for lib in MOVIE_LIBS:
        iid = libs.get(lib)
        if not iid:
            continue
        r = jellyfin.get_json("/Items", params={"ParentId": iid, "Recursive": "true",
                                                "IncludeItemTypes": "Movie", "Limit": "2000",
                                                "Fields": "Path,ProviderIds,ProductionYear"})
        items = r.get("Items") or []
        total += len(items)
        for i in items:
            rows.append({
                "lib": lib, "Name": i.get("Name"), "ProductionYear": i.get("ProductionYear"),
                "path": i.get("Path"), "id": i.get("Id"),
                "ProviderIds": i.get("ProviderIds") or {},
            })

    print(f"movie items scanned: {total}   distinct keys: {len({group_key(r) for r in rows})}")
    dups = find_duplicate_groups(rows)

    print(f"duplicate groups (same film, >1 item row): {len(dups)}\n")
    for d in sorted(dups, key=lambda x: x["rows"][0]["Name"] or ""):
        print(f"{d['rows'][0]['Name']} ({d['rows'][0]['ProductionYear']})  [{d['key']}]")
        for r in d["rows"]:
            print(f"    {r['lib']:<12} {str(r['path']).replace('/data/', '')}")

    with open(OUT, "w") as fh:
        json.dump(dups, fh, indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
