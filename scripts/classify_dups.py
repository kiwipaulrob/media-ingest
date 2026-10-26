#!/usr/bin/env python3
"""Classify the duplicate candidates: hardlink (free), same-size copy (real cost), or different version."""
import json
import os
import subprocess
import shlex

NODE = "root@192.168.214.132"

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


def display_name(row):
    """find_dups.py writes API-shape keys ("Name"); older candidate files use
    lowercase ("name"). Crashing with KeyError on either shape is the bug."""
    return row.get("Name") or row.get("name")


def display_year(row):
    return row.get("ProductionYear") or row.get("year")


def verdict_for(links, sizes):
    if all(v >= 2 for v in links.values()):
        return "HARDLINK (no disk cost)"
    if len(set(sizes.values())) == 1:
        return "COPY (same size)"
    return "DIFFERENT VERSION/SIZE"


def main():
    data = json.load(open("/tmp/dup_candidates.json"))
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
        rows.append({
            "name": display_name(g["rows"][0]), "year": display_year(g["rows"][0]),
            "key": g["key"],
            "paths": paths, "links": links, "sizes": sizes,
            "verdict": verdict_for(links, sizes),
        })

    order = {"COPY (same size)": 0, "DIFFERENT VERSION/SIZE": 1, "HARDLINK (no disk cost)": 2}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), str(r["name"])))

    print(f"candidates with >1 folder: {len(rows)}\n")
    for r in rows:
        print(f"[{r['verdict']}] {r['name']} ({r['year']})")
        for p in r["paths"]:
            nbytes = r["sizes"].get(p, 0)
            nlinks = r["links"].get(p, 0)
            print(f"    {nlinks} links  {nbytes/1e6:>8.0f}MB  {str(p).replace('/data/', '')}")
    print("\nwrote /tmp/dup_classified.json")
    json.dump(rows, open("/tmp/dup_classified.json", "w"), indent=1)


if __name__ == "__main__":
    main()
