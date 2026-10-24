"""Which media files are on disk but NOT indexed as a library row?

A count difference is not proof: two files can legitimately map to one row
(version-grouped `S02E02 x.avi` + `S02E02 x - 720p.mkv` = one item with two
sources), and sidecars (.nfo/.jpg) are not media at all. So compare the actual
basenames, per series, rather than the totals.

This is the check that answers "is the episode missing, or just unindexed?" —
the distinction the Jools investigation got wrong the first time.
"""
import argparse
import json
import subprocess
import sys

sys.path.insert(0, ".")
from media_lib import config, jellyfin  # noqa: E402

MOUNTS = config.JF_TO_DISK   # single source of truth


def ssh(cmd: str, timeout: int = 240) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", "root@192.168.214.109",
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def to_host(path: str) -> str:
    for container, host in MOUNTS.items():
        if path.startswith(container + "/"):
            return host + path[len(container):]
    return path


def series_folder(sample: str) -> str:
    """The SERIES folder — parent of the Season dir, i.e. 6 path components.

    Slicing to 5 returns the LIBRARY root instead, and `find` then enumerates every
    other series in the library: the first run of this script reported "248 files
    not indexed" for a 4-episode series, which was simply the whole library.
    """
    return "/".join(to_host(sample).split("/")[:6])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default="Music Docos Series")
    ap.add_argument("--series", nargs="*", default=None,
                    help="limit to these series names (default: all with gaps)")
    args = ap.parse_args()

    uid = jellyfin.user_id()
    views = jellyfin.get_json(f"/Users/{uid}/Views").get("Items") or []
    view = next((v for v in views if v.get("Name") == args.library), None)
    if not view:
        print(f"library {args.library!r} not found")
        return 2

    series = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "ParentId": view["Id"], "Recursive": "true", "IncludeItemTypes": "Series",
        "Limit": 500,
    }).get("Items") or []

    total_missing = 0
    for s in series:
        if args.series and s.get("Name") not in args.series:
            continue
        eps = jellyfin.get_json(f"/Shows/{s['Id']}/Episodes", params={
            "UserId": uid, "Fields": "Path", "Limit": 500,
        }).get("Items") or []
        indexed = {e["Path"].rsplit("/", 1)[-1] for e in eps if e.get("Path")}
        if not indexed:
            continue

        folder = series_folder(next(e["Path"] for e in eps if e.get("Path")))
        raw = ssh(f'find "{folder}" -type f 2>/dev/null '
                  f"| grep -iE '\\.(mkv|mp4|avi|m4v|ts|mov)$'")
        on_disk = [p.rsplit("/", 1)[-1] for p in raw.strip().splitlines() if p.strip()]

        # A file is accounted for if a row's Path basename matches exactly, or the
        # row's stem is a prefix of it (version-grouped: "... x - 720p.mkv").
        stems = {f.rsplit(".", 1)[0] for f in indexed}
        unindexed = []
        for f in on_disk:
            stem = f.rsplit(".", 1)[0]
            if f in indexed or stem in stems:
                continue
            if any(stem.startswith(x) for x in stems):
                continue
            unindexed.append(f)

        status = f"{len(unindexed)} NOT INDEXED" if unindexed else "complete"
        print(f"\n=== {s.get('Name')} — {len(indexed)} rows / {len(on_disk)} files on disk: {status}")
        for f in unindexed:
            print("    ", f)
        total_missing += len(unindexed)

    print(f"\nTOTAL files on disk with no library row: {total_missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
