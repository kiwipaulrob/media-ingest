#!/usr/bin/env python3
"""Aftermath for the overnight XviD->h264 batch (direct ffmpeg, NOT Tdarr).

Reads state/xvid-batch-receipt.jsonl, notifies Jellyfin of every SWAPPED
file, waits 4 minutes for the re-probe backlog, then verifies each SWAPPED
row reads h264 with its overview intact, files all recoded item IDs into
the Jellyfin Collection named "Recoded" (created if missing), and confirms
every .orig-xvid backup is still on disk (originals are NEVER deleted).

Read-only against media (API reads + one ssh existence check); the only
write is collection membership.
"""

import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_lib import jellyfin, place  # noqa: E402

STATE = Path("/root/.hermes/profiles/media-maintainer/state")
RECEIPT = STATE / "xvid-batch-receipt.jsonl"
HOST = "root@192.168.214.132"
BASE = "/mnt/zmedia/videos/"
WAIT_S = 240


def video_codec(item: dict) -> str:
    for src in item.get("MediaSources") or []:
        for st in src.get("MediaStreams") or []:
            if st.get("Type") == "Video":
                return (st.get("Codec") or "unknown").lower()
    return "none"


def main() -> int:
    swapped: dict = {}
    counts: dict = {}
    for line in RECEIPT.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
        if r.get("status") == "SWAPPED":
            swapped[r["path"]] = r.get("backup")
    print(f"receipt statuses: {counts}", flush=True)
    print(f"SWAPPED rows: {len(swapped)}", flush=True)
    if not swapped:
        print("nothing swapped; nothing to verify", flush=True)
        return 0

    jf_paths = [place.to_jf(BASE + p) for p in swapped]
    unmapped = [j for j in jf_paths if not j.startswith("/data/")]
    if unmapped:
        print(f"FATAL: {len(unmapped)} paths did not map to /data/: {unmapped[:3]}",
              flush=True)
        return 1

    uid = jellyfin.user_id()
    lib_id = jellyfin.resolve_library("Docos")

    # 1. notify Jellyfin of the swapped files (same path, new bytes)
    jellyfin.notify_media(jf_paths)
    print(f"notified Jellyfin of {len(jf_paths)} swapped files", flush=True)

    # 2. resolve rows by exact path; per-item FullRefresh forces re-probe
    #    so MediaSources stops reporting the stale mpeg4 stream info.
    items = jellyfin.list_items(
        uid, parent_id=lib_id, include_types="Movie,Video",
        fields=["Path", "MediaSources", "Overview"])
    by_path = {i.get("Path"): i for i in items if i.get("Path")}
    matched = [j for j in jf_paths if j in by_path]
    missing = [j for j in jf_paths if j not in by_path]
    print(f"rows matched by exact path: {len(matched)}/{len(jf_paths)}", flush=True)
    for m in missing[:10]:
        print(f"  NO-ROW {m}", flush=True)
    for j in matched:
        try:
            jellyfin.refresh_item(by_path[j]["Id"])
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the pass
            print(f"  REFRESH-ERR {by_path[j]['Id']} {exc}", flush=True)
    print(f"per-item FullRefresh fired for {len(matched)} rows", flush=True)

    # 3. mandated wait for the re-probe backlog
    print(f"sleeping {WAIT_S}s for re-probe backlog...", flush=True)
    time.sleep(WAIT_S)

    # 4. verify: h264 + overview intact
    items2 = jellyfin.list_items(
        uid, parent_id=lib_id, include_types="Movie,Video",
        fields=["Path", "MediaSources", "Overview"])
    by_path2 = {i.get("Path"): i for i in items2 if i.get("Path")}
    ok, bad_codec, no_overview, gone = [], [], [], []
    ids = []
    for rel, jf in zip(swapped, jf_paths):
        it = by_path2.get(jf)
        if it is None:
            gone.append(jf)
            continue
        ids.append(it["Id"])
        codec = video_codec(it)
        ov = (it.get("Overview") or "").strip()
        if codec != "h264":
            bad_codec.append((jf, codec))
        elif not ov:
            no_overview.append(jf)
        else:
            ok.append(jf)
    print(f"VERIFY h264+overview: ok={len(ok)} bad_codec={len(bad_codec)} "
          f"no_overview={len(no_overview)} gone={len(gone)}", flush=True)
    for jf, codec in bad_codec[:15]:
        print(f"  BAD-CODEC {codec} {jf}", flush=True)
    for jf in no_overview[:15]:
        print(f"  NO-OVERVIEW {jf}", flush=True)
    for jf in gone[:15]:
        print(f"  GONE {jf}", flush=True)

    # 5. Recoded collection: create or reuse, add all resolved ids, prove membership
    coll_search = jellyfin.get_json(
        "/Items?IncludeItemTypes=BoxSet&Recursive=true&SearchTerm="
        + urllib.parse.quote("Recoded"))
    colls = [i for i in (coll_search.get("Items") or [])
             if (i.get("Name") or "").lower() == "recoded"]
    if colls:
        coll_id = colls[0]["Id"]
        print(f"collection Recoded exists: {coll_id}", flush=True)
    else:
        raw = jellyfin._request(
            "/Collections?Name=" + urllib.parse.quote("Recoded")
            + "&IsLocked=false", method="POST")
        try:
            coll_id = json.loads(raw).get("Id", raw.strip().strip('"'))
        except ValueError:
            coll_id = raw.strip().strip('"')
        print(f"collection Recoded created: {coll_id}", flush=True)
    if ids:
        jellyfin._request(
            f"/Collections/{coll_id}/Items?Ids=" + ",".join(ids), method="POST")
    members = jellyfin.list_items(uid, parent_id=coll_id,
                                  include_types="Movie,Video",
                                  fields=["Path"])
    print(f"collection Recoded members now: {len(members)}", flush=True)

    # 6. originals KEPT: every .orig-xvid backup still on disk (one ssh pass,
    #    list fed over stdin so apostrophes in titles need no quoting).
    backup_paths = [BASE + b for b in swapped.values() if b]
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", HOST,
         "n=0; m=0; while IFS= read -r f; do "
         "if [ -f \"$f\" ]; then n=$((n+1)); else m=$((m+1)); echo \"MISSING: $f\"; fi; "
         "done; echo \"KEPT=$n MISSING=$m\""],
        input="\n".join(backup_paths) + "\n",
        capture_output=True, text=True, timeout=300)
    print(proc.stdout.strip().splitlines()[-5:], flush=True)
    kept_line = [l for l in proc.stdout.splitlines() if l.startswith("KEPT=")]
    print((kept_line or ["KEPT=?"])[0], flush=True)

    # 7. remaining work
    done_paths = set()
    for line in RECEIPT.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get("status") in ("SWAPPED", "SKIP-DONE"):
                done_paths.add(r.get("path"))
        except ValueError:
            pass
    audit = STATE / "xvid-full.txt"
    total = len([l for l in audit.read_text().splitlines() if "|" in l])
    remaining = total - len(done_paths)
    print(f"SUMMARY swapped_total={len(swapped)} verify_ok={len(ok)} "
          f"collection_members={len(members)} remaining={remaining}/{total}",
          flush=True)
    if remaining == 0:
        print("JOB COMPLETE: no remaining files in xvid-full.txt", flush=True)
    return 0 if (not bad_codec and not gone) else 2


if __name__ == "__main__":
    sys.exit(main())
