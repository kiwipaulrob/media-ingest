#!/usr/bin/env python3
"""Read-only aftermath-lite for a pool-suspended night.

No notify, no refresh (freeze rule: zmedia pool SUSPENDED). All DB-side:
- read cached codec+overview for every SWAPPED row, report split
- top up the Recoded collection with any recoded IDs missing from it
"""
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_lib import jellyfin, place  # noqa: E402

STATE = Path("/root/.hermes/profiles/media-maintainer/state")
RECEIPT = STATE / "xvid-batch-receipt.jsonl"
BASE = "/mnt/zmedia/videos/"


def video_codec(item):
    for src in item.get("MediaSources") or []:
        for st in src.get("MediaStreams") or []:
            if st.get("Type") == "Video":
                return (st.get("Codec") or "unknown").lower()
    return "none"


def main():
    swapped = {}
    counts = {}
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
        return 0

    jf_paths = [place.to_jf(BASE + p) for p in swapped]
    unmapped = [j for j in jf_paths if not j.startswith("/data/")]
    if unmapped:
        print(f"FATAL unmapped: {unmapped[:3]}", flush=True)
        return 1

    uid = jellyfin.user_id()
    lib_id = jellyfin.resolve_library("Docos")
    items = jellyfin.list_items(
        uid, parent_id=lib_id, include_types="Movie,Video",
        fields=["Path", "MediaSources", "Overview"])
    by_path = {i.get("Path"): i for i in items if i.get("Path")}

    ok, stale, noov, gone, ids = [], [], [], [], []
    for jf in jf_paths:
        it = by_path.get(jf)
        if it is None:
            gone.append(jf)
            continue
        ids.append(it["Id"])
        codec = video_codec(it)
        ov = (it.get("Overview") or "").strip()
        if codec != "h264":
            stale.append((jf, codec))
        elif not ov:
            noov.append(jf)
        else:
            ok.append(jf)
    print(f"CACHED-VERIFY h264+overview: ok={len(ok)} stale_codec={len(stale)} "
          f"no_overview={len(noov)} gone={len(gone)}", flush=True)
    for jf, codec in stale[:15]:
        print(f"  STALE-CODEC {codec} {jf}", flush=True)
    for jf in gone[:15]:
        print(f"  GONE {jf}", flush=True)

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
    members = jellyfin.list_items(uid, parent_id=coll_id,
                                  include_types="Movie,Video",
                                  fields=["Path"])
    mem_ids = {m["Id"] for m in members if m.get("Id")}
    print(f"collection Recoded members before: {len(members)}", flush=True)
    missing_ids = [i for i in ids if i not in mem_ids]
    print(f"recoded ids missing from collection: {len(missing_ids)}", flush=True)
    if missing_ids:
        jellyfin._request(
            f"/Collections/{coll_id}/Items?Ids=" + ",".join(missing_ids),
            method="POST")
    members2 = jellyfin.list_items(uid, parent_id=coll_id,
                                   include_types="Movie,Video",
                                   fields=["Path"])
    print(f"collection Recoded members after: {len(members2)}", flush=True)
    print(f"SUMMARY swapped_total={len(swapped)} cached_ok={len(ok)} "
          f"collection_members={len(members2)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
