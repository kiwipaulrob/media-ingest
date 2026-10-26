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


libs = {f.get("Name"): f.get("ItemId") for f in (jellyfin.get_json("/Library/VirtualFolders") or [])}
groups = {}
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
        prov = i.get("ProviderIds") or {}
        key = None
        if prov.get("Imdb"):
            key = "imdb:" + prov["Imdb"]
        elif prov.get("Tmdb"):
            key = "tmdb:" + prov["Tmdb"]
        else:
            key = "name:" + norm(i.get("Name")) + ":" + str(i.get("ProductionYear") or "")
        groups.setdefault(key, []).append({
            "lib": lib, "name": i.get("Name"), "year": i.get("ProductionYear"),
            "path": i.get("Path"), "id": i.get("Id"),
        })

print(f"movie items scanned: {total}   distinct keys: {len(groups)}")
dups = []
for key, rows in groups.items():
    paths = {r["path"] for r in rows if r["path"]}
    if len(rows) > 1 and len(paths) > 1:
        dups.append({"key": key, "rows": rows})

print(f"duplicate groups (same film, >1 item row): {len(dups)}\n")
for d in sorted(dups, key=lambda x: x["rows"][0]["name"] or ""):
    print(f"{d['rows'][0]['name']} ({d['rows'][0]['year']})  [{d['key']}]")
    for r in d["rows"]:
        print(f"    {r['lib']:<12} {str(r['path']).replace('/data/', '')}")

with open(OUT, "w") as fh:
    json.dump(dups, fh, indent=1)
print(f"\nwrote {OUT}")
