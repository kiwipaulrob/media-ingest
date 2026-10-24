"""Disk-vs-library cross-check for a series: are there media files the scanner
never indexed?

A ghost row can mean two very different things, and the difference decides the
action:

  * the episode was never acquired (download it), or
  * the file is ON DISK and the scanner missed it (index it — no download).

Guessing between them has already produced a wrong conclusion once, so this counts
the actual video files on disk under a series folder and compares them with the
number of rows that carry a Path.
"""
import argparse
import json
import subprocess
import sys

sys.path.insert(0, ".")
from media_lib import config, jellyfin  # noqa: E402

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov")


def ssh(cmd: str, timeout: int = 240) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", "root@192.168.214.109",
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


#: The container sees `/data/...`; the CT shell sees the bind source. Verified with
#: `docker inspect jellyfin --format '{{range .Mounts}}...'` — composing the host
#: path from memory returns zero files and reads as "nothing on disk".
MOUNTS = config.JF_TO_DISK   # single source of truth


def library_root(sample_path: str) -> str:
    """`/data/musicdocos-series/Series (1997) [tmdbid-1]/Season 01/x.mkv` -> the
    series folder as the CT SHELL sees it (the bind source, not the container path)."""
    for container, host in MOUNTS.items():
        if sample_path.startswith(container + "/"):
            rest = sample_path[len(container) + 1:].split("/")
            return "/".join([host] + rest[:1])
    parts = sample_path.split("/")
    return "/".join(parts[:4]) if len(parts) >= 4 else sample_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default="Music Docos Series")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    uid = jellyfin.user_id()
    views = jellyfin.get_json(f"/Users/{uid}/Views").get("Items") or []
    view = next((v for v in views if v.get("Name") == args.library), None)
    if not view:
        print(f"library {args.library!r} not found")
        return 2

    series = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "ParentId": view["Id"], "Recursive": "true", "IncludeItemTypes": "Series",
        "Fields": "Path", "Limit": 500,
    }).get("Items") or []

    rows = []
    for s in series:
        eps = jellyfin.get_json(f"/Shows/{s['Id']}/Episodes", params={
            "UserId": uid, "Fields": "Path", "Limit": 500,
        }).get("Items") or []
        with_file = [e for e in eps if e.get("Path")]
        if not with_file:
            continue
        rows.append({
            "series": s.get("Name"),
            "rows_with_file": len(with_file),
            "ghosts": len(eps) - len(with_file),
            "folder": library_root(with_file[0]["Path"]),
        })
    rows.sort(key=lambda r: -r["ghosts"])

    print(f"{'series':<34} {'rows':>5} {'on disk':>8} {'unindexed':>10}")
    print("-" * 62)
    findings = []
    for r in rows[: args.top]:
        # count real video files under the series folder (the container path)
        out = ssh(f'find "{r["folder"]}" -type f 2>/dev/null '
                  f"| grep -icE '\\.(mkv|mp4|avi|m4v|ts|mov)$'")
        try:
            on_disk = int(out.strip().split()[-1])
        except (ValueError, IndexError):
            on_disk = -1
        unindexed = on_disk - r["rows_with_file"] if on_disk >= 0 else None
        flag = ""
        if unindexed is not None and unindexed > 0:
            flag = "  <-- FILES ON DISK, NOT INDEXED"
        elif unindexed == 0:
            flag = "  ok"
        print(f"{str(r['series'])[:33]:<34} {r['rows_with_file']:>5} {on_disk:>8} "
              f"{str(unindexed):>10}{flag}")
        findings.append({**r, "on_disk": on_disk, "unindexed": unindexed,
                         "sample": r["folder"]})

    # Where a series has unindexed files, name the first few so the next step is
    # a specific file, not another audit.
    for f in findings:
        if f["unindexed"] and f["unindexed"] > 0:
            print(f"\n{f['series']}: first unindexed-looking files under {f['sample']}")
            out = ssh(f'find "{f["sample"]}" -type f 2>/dev/null | sort | head -12')
            for line in out.strip().splitlines()[:12]:
                print("   ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
