#!/usr/bin/env python3
"""Classify the duplicate candidates: hardlink (free), same-size copy (real cost), or different version."""
import json
import os
import subprocess
import shlex

NODE = "root@192.168.214.132"
data = json.load(open("/tmp/dup_candidates.json"))

# paths in the API are /data/... ; host paths live under /mnt/zmedia/videos/
# Jellyfin /data/<lib> does NOT map 1:1 to the folder name on disk
DATAMAP = {
    "documentaries": "Documentaries",
    "movies": "Films",
    "filmnoir": "filmnoir",
    "tvshows": "TV",
    "musicdocos": "musicdocos",
    "musicdocos-series": "musicdocos-series",
    "filmdocos": "FilmDocumentaries",
    "docuseries": "DocuSeries",
    "filmdocuseries": "filmdocuseries",
}


def hostpath(p):
    s = str(p)
    if not s.startswith("/data/"):
        return s
    rest = s[len("/data/"):]
    head, _, tail = rest.partition("/")
    return "/mnt/zmedia/videos/" + DATAMAP.get(head, head) + ("/" + tail if tail else "")


rows = []
for g in data:
    paths = [r["path"] for r in g["rows"] if r["path"]]
    folders = {os.path.dirname(p) for p in paths}
    if len(folders) < 2:
        continue                       # same folder => episode siblings, not a duplicate
    sizes, links = {}, {}
    cmd = "; ".join(
        "stat -c '%h %s' {p} 2>/dev/null || echo 'MISSING 0'".format(p=shlex.quote(hostpath(p)))
        for p in paths)
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=12", NODE, cmd],
                         capture_output=True, text=True, timeout=200).stdout or ""
    stats = [ln.split() for ln in out.strip().splitlines() if ln.strip()]
    for p, st in zip(paths, stats):
        if len(st) == 2 and st[0].isdigit() and st[1].isdigit():
            links[p], sizes[p] = int(st[0]), int(st[1])
        else:
            links[p], sizes[p] = -1, 0          # unreadable path (stale row)
    if not sizes:
        continue
    hardlinked = all(v >= 2 for v in links.values())
    same_size = len(set(sizes.values())) == 1
    rows.append({
        "name": g["rows"][0]["name"], "year": g["rows"][0]["year"], "key": g["key"],
        "paths": paths, "links": links, "sizes": sizes,
        "verdict": "HARDLINK (no disk cost)" if hardlinked
                   else ("COPY (same size)" if same_size else "DIFFERENT VERSION/SIZE"),
    })

order = {"COPY (same size)": 0, "DIFFERENT VERSION/SIZE": 1, "HARDLINK (no disk cost)": 2}
rows.sort(key=lambda r: (order.get(r["verdict"], 9), str(r["name"])))

print(f"candidates with >1 folder: {len(rows)}\n")
for r in rows:
    print(f"[{r['verdict']}] {r['name']} ({r['year']})")
    for p in r["paths"]:
        s = r["sizes"].get(p, 0)
        l = r["links"].get(p, 0)
        print(f"    {l} links  {s/1e6:>8.0f}MB  {str(p).replace('/data/', '')}")
print("\nwrote /tmp/dup_classified.json")
json.dump(rows, open("/tmp/dup_classified.json", "w"), indent=1)
