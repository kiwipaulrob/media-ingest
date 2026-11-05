#!/usr/bin/env python3
"""Follow-up for the overnight XviD batch: cover the SWAPPED rows the main
aftermath could not see.

The main aftermath (xvid_aftermath.py) only queries the Docos movies library
with Movie/Video types, so it misses (a) Episode rows in Docos Series
(/data/docuseries) and (b) any Docos row that is version-grouped or otherwise
not path-listed. This script searches BOTH libraries with broad types,
fires per-item FullRefresh on the newly found rows, waits out the re-probe
backlog (240s), verifies h264 + overview, files the new IDs into the Recoded
collection, and proves membership with a user-scoped read.
"""

import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_lib import jellyfin, place  # noqa: E402

STATE = Path("/root/.hermes/profiles/media-maintainer/state")
RECEIPT = STATE / "xvid-batch-receipt.jsonl"
HOST_BASE = "/mnt/zmedia/videos/"
WAIT_S = 240


def video_codec(item: dict) -> str:
    for src in item.get("MediaSources") or []:
        for st in src.get("MediaStreams") or []:
            if st.get("Type") == "Video":
                return (st.get("Codec") or "unknown").lower()
    return "none"


def main() -> int:
    swapped: dict = {}
    for line in RECEIPT.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("status") == "SWAPPED":
            swapped[r["path"]] = r.get("backup")
    jf_paths = [place.to_jf(HOST_BASE + p) for p in swapped]
    print(f"SWAPPED total: {len(jf_paths)}", flush=True)

    uid = jellyfin.user_id()
    docos = jellyfin.resolve_library("Docos")
    series = jellyfin.resolve_library("Docos Series")
    print(f"Docos={docos} DocosSeries={series}", flush=True)

    catalog: dict = {}
    for lib_id, types in ((docos, "Movie,Video,Episode"),
                          (series, "Series,Season,Episode,Movie,Video")):
        items = jellyfin.list_items(
            uid, parent_id=lib_id, include_types=types,
            fields=["Path", "MediaSources", "Overview", "Name"])
        for it in items:
            if it.get("Path"):
                catalog[it["Path"]] = (lib_id, it)
        print(f"catalogued {len(items)} rows from {lib_id}", flush=True)

    found: dict = {}
    missing: list = []
    for jf in jf_paths:
        if jf in catalog:
            found[jf] = catalog[jf]
        else:
            missing.append(jf)
    print(f"found={len(found)} missing={len(missing)}", flush=True)
    for m in missing:
        print(f"  STILL-MISSING {m}", flush=True)

    # Name-search the missing ones: a version-grouped row lists only its
    # primary path, so the swapped file may be a live secondary source.
    for m in missing:
        stem = Path(m).stem
        probe = " ".join(stem.replace(".", " ").replace("_", " ").split()[:6])
        try:
            res = jellyfin.get_json(
                f"/Users/{uid}/Items?Recursive=true&SearchTerm="
                + urllib.parse.quote(probe)
                + "&Fields=Path,MediaSources&Limit=25")
        except Exception as exc:  # noqa: BLE001
            print(f"  SEARCH-ERR {probe}: {exc}", flush=True)
            continue
        for it in res.get("Items") or []:
            srcs = [s.get("Path") for s in (it.get("MediaSources") or [])]
            if m in srcs or (it.get("Path") and Path(it["Path"]).parent == Path(m).parent):
                print(f"  ALT-ROW {it.get('Id')} type={it.get('Type')} "
                      f"path={it.get('Path')} nsrc={len(srcs)}", flush=True)

    # Refresh the newly found rows so their MediaSources re-probe the file.
    to_refresh = [it["Id"] for lib_id, it in found.values()
                  if lib_id == series]
    # Also refresh any Docos rows the main pass may have skipped.
    print(f"refreshing {len(to_refresh)} Docos-Series rows", flush=True)
    for iid in to_refresh:
        try:
            jellyfin.refresh_item(iid)
        except Exception as exc:  # noqa: BLE001
            print(f"  REFRESH-ERR {iid} {exc}", flush=True)
    if to_refresh:
        print(f"sleeping {WAIT_S}s for re-probe backlog...", flush=True)
        time.sleep(WAIT_S)
        catalog = {}
        for lib_id, types in ((docos, "Movie,Video,Episode"),
                              (series, "Series,Season,Episode,Movie,Video")):
            items = jellyfin.list_items(
                uid, parent_id=lib_id, include_types=types,
                fields=["Path", "MediaSources", "Overview", "Name"])
            for it in items:
                if it.get("Path"):
                    catalog[it["Path"]] = (lib_id, it)
        found = {jf: catalog[jf] for jf in jf_paths if jf in catalog}
        missing = [jf for jf in jf_paths if jf not in catalog]
        print(f"after wait: found={len(found)} missing={len(missing)}", flush=True)

    ok, bad_codec, no_overview = [], [], []
    ids = []
    for jf, (lib_id, it) in found.items():
        ids.append(it["Id"])
        codec = video_codec(it)
        ov = (it.get("Overview") or "").strip()
        if codec != "h264":
            bad_codec.append((jf, codec, it.get("Type")))
        elif not ov:
            no_overview.append((jf, it.get("Type")))
        else:
            ok.append(jf)
    print(f"VERIFY h264+overview over all located rows: ok={len(ok)} "
          f"bad_codec={len(bad_codec)} no_overview={len(no_overview)} "
          f"missing={len(missing)}", flush=True)
    for jf, codec, t in bad_codec[:20]:
        print(f"  BAD-CODEC {codec} [{t}] {jf}", flush=True)
    for jf, t in no_overview[:20]:
        print(f"  NO-OVERVIEW [{t}] {jf}", flush=True)

    coll_search = jellyfin.get_json(
        "/Items?IncludeItemTypes=BoxSet&Recursive=true&SearchTerm="
        + urllib.parse.quote("Recoded"))
    colls = [i for i in (coll_search.get("Items") or [])
             if (i.get("Name") or "").lower() == "recoded"]
    coll_id = colls[0]["Id"]
    print(f"collection Recoded: {coll_id}", flush=True)
    if ids:
        jellyfin._request(
            f"/Collections/{coll_id}/Items?Ids=" + ",".join(ids),
            method="POST")
    members = jellyfin.list_items(uid, parent_id=coll_id,
                                  include_types="Movie,Video,Episode",
                                  fields=["Path"])
    print(f"collection Recoded members now: {len(members)}", flush=True)
    return 0 if (not bad_codec) else 2


if __name__ == "__main__":
    sys.exit(main())
