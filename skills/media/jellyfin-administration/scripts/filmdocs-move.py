#!/usr/bin/env python3
"""Hardlink a batch of library files from one library root to another.
Same filesystem -> hardlinks (no extra disk). Prints per-item verdict.
Plan JSON: list of {"name", "path"} where path is the CONTAINER path
(e.g. /data/documentaries/...); src-root/dst-root map it to disk.
Usage: filmdocs-move.py <plan.json> <src-root> <dst-root>
"""
import json, os, sys

plan_path, SRC, DST = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.load(open(plan_path))

done, skip, err = [], [], []
for p in plan:
    rel = p["path"]
    for prefix in ("/data/documentaries", "/data/filmdocos",
                   "/data/musicdocos", "/data/movies"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):].lstrip("/")
            break
    src = os.path.join(SRC, rel)
    if not os.path.isfile(src):
        err.append((p["name"], "SRC MISSING: " + src))
        continue
    parent = os.path.basename(os.path.dirname(src))
    # A file sitting directly in the library root groups under the
    # library's own name -> explicit title folder instead (see
    # references/ingest-and-intake.md "bulk (hardlink move)").
    if os.path.dirname(src) == SRC.rstrip("/"):
        parent = os.path.splitext(os.path.basename(src))[0][:90]
    dstdir = os.path.join(DST, parent)
    os.makedirs(dstdir, exist_ok=True)
    dst = os.path.join(dstdir, os.path.basename(src))
    if os.path.exists(dst):
        skip.append((p["name"], "already there"))
        continue
    try:
        os.link(src, dst)
        done.append((p["name"], dst))
    except Exception as e:
        err.append((p["name"], str(e)[:80]))
print(f"linked={len(done)} skipped={len(skip)} errors={len(err)}")
for name, d in done:
    print(f"  + {name[:55]}")
for name, why in skip + err:
    print(f"  ! {name[:55]} :: {why[:70]}")
