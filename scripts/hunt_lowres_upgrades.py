#!/usr/bin/env python3
"""Hunt higher-resolution replacements for low-res music documentaries.

Two phases, deliberately separate so the candidate list can be reviewed before anything
is downloaded:

    --scan              probe both music libraries and record every item under the
                        threshold to state/lowres.json
    --hunt [--apply]    search for each item and apply the quality gate; only a proven
                        upgrade is queued, and only with --apply

The gate (media_lib.quality) is the point of the exercise: a replacement must be
MEASURABLY better than the copy on disk, and must clearly be the same programme. Both
rules exist because breaking them queued unrelated films and same-quality rips.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import jellyfin, place, quality  # noqa: E402

LIBRARIES = {
    "Music Docos": "5f95760bae4c07e31d840d70c3a46111",
    "Music Docos Series": "18ce65a0ab9d7a771d5ac55e6024fe2a",
}
STATE = Path(__file__).resolve().parent.parent / "state" / "lowres.json"


def library_items(parent_id: str) -> List[Dict[str, Any]]:
    """Every Movie/Episode under a library, with its path."""
    uid = jellyfin.user_id()
    out, start = [], 0
    while True:
        rows = jellyfin.get_json(f"/Users/{uid}/Items", params={
            "ParentId": parent_id, "Recursive": "true", "IncludeItemTypes": "Movie,Episode",
            "Fields": "Path,ProductionYear", "Limit": "500", "StartIndex": str(start)})
        batch = rows.get("Items") or []
        out.extend(batch)
        start += len(batch)
        if not batch or start >= (rows.get("TotalRecordCount") or 0):
            break
    return [i for i in out if i.get("Path")]


#: Host and container that can ffprobe the media share quickly (the Tdarr node).
PROBE_HOST = os.environ.get("TDAAR_HOST", "root@192.168.214.131")
PROBE_CT = os.environ.get("TDAAR_CT", "tdarr-node")
PROBE_MAP = ("/mnt/zmedia/", "/media/")


def _container_path(disk_path: str) -> str:
    src, dst = PROBE_MAP
    return disk_path.replace(src, dst, 1) if disk_path.startswith(src) else disk_path


def probe_heights(disk_paths: List[str], chunk: int = 60) -> Dict[str, int]:
    """ffprobe many files in a few remote calls; keys are DISK paths.

    Runs inside the Tdarr node, which already has the media share mounted — the CT105
    ffprobe is a docker wrapper and needs one container per file, which is far too slow
    for a whole library.
    """
    heights: Dict[str, int] = {}
    for i in range(0, len(disk_paths), chunk):
        batch = disk_paths[i:i + chunk]
        inner = ("for f in " + " ".join(shlex.quote(_container_path(p)) for p in batch) +
                 '; do h=$(ffprobe -v error -select_streams v:0 -show_entries stream=height'
                 ' -of csv=p=0 "$f" 2>/dev/null | head -1); printf "%s\\t%s\\n" "${h:-0}" "$f"; done')
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", PROBE_HOST,
               f"docker exec {PROBE_CT} sh -c {shlex.quote(inner)}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as exc:
            print(f"  probe batch failed: {exc}")
            continue
        for line in (proc.stdout or "").splitlines():
            if "\t" not in line:
                continue
            h, cp = line.split("\t", 1)
            try:
                heights[cp.replace(PROBE_MAP[1], PROBE_MAP[0], 1)] = int(h)
            except ValueError:
                continue
    return heights


def do_scan(threshold: int) -> int:
    rows: List[Dict[str, Any]] = []
    for lib, pid in LIBRARIES.items():
        items = library_items(pid)
        print(f"  {lib}: {len(items)} items")
        disk = {}
        for it in items:
            try:
                disk[it["Id"]] = place.to_disk(it["Path"])
            except Exception:
                pass
        heights = probe_heights(list(disk.values()))
        for it in items:
            d = disk.get(it["Id"])
            if not d:
                continue
            h = heights.get(d, 0)
            if h and h < threshold:
                rows.append({"id": it["Id"], "library": lib, "name": it.get("Name"),
                             "path": d, "height": h, "year": it.get("ProductionYear")})
        print(f"    under {threshold}p: {sum(1 for r in rows if r['library'] == lib)}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"threshold": threshold, "generated": int(time.time()),
                                 "items": rows}, indent=1))
    print(f"\nwrote {len(rows)} low-res items -> {STATE}")
    return 0


def prowlarr_search(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    import os
    import urllib.request
    base = os.environ["PROWLARR_URL"].rstrip("/")
    key = os.environ["PROWLARR_API_KEY"]
    url = f"{base}/api/v1/search?query={urllib.parse.quote(query)}&type=search&limit={limit}"
    try:
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"X-Api-Key": key}), timeout=240).read())
    except Exception:
        return []


def queue_torrent(result: Dict[str, Any]) -> bool:
    from media_lib import qb
    url = result.get("downloadUrl") or ""
    if not url:
        return False
    hdr = subprocess.run(["curl", "-s", "-m", "60", "-D", "-", "-o", "/dev/null", url],
                         capture_output=True, text=True, timeout=90).stdout
    mag = next((ln.split(": ", 1)[1].strip() for ln in hdr.splitlines()
                if ln.lower().startswith("location:")), "")
    try:
        return qb.add(mag if mag.startswith("magnet:") else url, category="musicdocos") in (200, 202, 409)
    except Exception:
        return False


def do_hunt(apply: bool, cap: int) -> int:
    if not STATE.exists():
        print("run --scan first")
        return 2
    data = json.loads(STATE.read_text())
    items = data.get("items") or []
    queueable, report = 0, []
    partial = STATE.with_name("lowres-hunt.json")
    for it in items:
        if queueable >= cap:
            report.append({**it, "verdict": "cap-reached"})
            continue
        cands = [r for r in prowlarr_search(it["name"] or "")
                 if r.get("protocol") == "torrent" and (r.get("seeders") or 0) > 0]
        # resolution first, seeds second — the inversion that caused the original mess
        cands.sort(key=lambda r: (quality.release_height(str(r.get("title"))) or 0,
                                  r.get("seeders") or 0), reverse=True)
        best = None
        for r in cands[:5]:
            v = quality.verdict(str(r.get("title", "")), it["height"],
                                wanted_title=it["name"] or "",
                                wanted_year=it.get("year"))
            if v["queue"]:
                best = (r, v)
                break
        if not best:
            report.append({**it, "verdict": "no-upgrade-found", "seen": len(cands)})
            partial.write_text(json.dumps(report, indent=1))
            continue
        r, v = best
        acted = queue_torrent(r) if apply else False
        queueable += 1
        report.append({**it, "verdict": v["verdict"], "candidate": r.get("title"),
                       "cand_height": v["candidate_height"], "seeds": r.get("seeders"),
                       "queued": acted})
        print(f"  {'QUEUED' if acted else 'would queue'}: {it['name'][:44]:<46} "
              f"{it['height']}p -> {v['candidate_height']}p  {str(r.get('title'))[:40]}",
              flush=True)
        partial.write_text(json.dumps(report, indent=1))
    out = partial
    out.write_text(json.dumps(report, indent=1))
    ups = sum(1 for r in report if r["verdict"] == "upgrade")
    print(f"\n{len(report)} items: {ups} upgrade(s) found, "
          f"{sum(1 for r in report if r.get('queued'))} queued -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--hunt", action="store_true")
    ap.add_argument("--apply", action="store_true", help="actually queue (default: report only)")
    ap.add_argument("--threshold", type=int, default=720)
    ap.add_argument("--cap", type=int, default=25, help="max downloads to queue in one run")
    args = ap.parse_args()
    if args.scan:
        return do_scan(args.threshold)
    if args.hunt:
        return do_hunt(args.apply, args.cap)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
