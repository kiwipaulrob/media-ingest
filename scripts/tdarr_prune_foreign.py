"""Remove Tdarr file records that do not belong to their library.

A `scan-files` call takes `arrayOrPath`, and passing a PARENT directory indexes every
file beneath it into the library being scanned. Scanning with `/media/videos` therefore
pulled Films, TV, DocuSeries, Documentaries and even `_staging-*` folders into the two
music libraries — 1,267 records that were then queued for transcoding on a flow built
for music docs.

`remove-library-files` is library-wide (it would drop the legitimate records too) and
`bulk-delete-files` deletes media from DISK. Neither is usable here. `cruddb` with
`removeOne` drops a single DB record and leaves the file alone — verified: the file
survived at full size while its record vanished.

Usage:
    tdarr_prune_foreign.py            # report only
    tdarr_prune_foreign.py --apply    # remove the records
"""
from __future__ import annotations

import argparse
import collections
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tdarr_kick import LIBS, crud  # noqa: E402

PROBE_HOST = "root@192.168.214.131"


def foreign(libs: dict[str, str]) -> list[dict]:
    docs = crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    out = []
    for name, db in libs.items():
        root = f"/media/videos/{name}/"
        out += [d for d in docs
                if d.get("DB") == db and not str(d.get("file", "")).startswith(root)]
    return out


def disk_exists(paths: list[str]) -> dict[str, bool]:
    """Confirm the media itself is untouched before and after."""
    host = [p.replace("/media/", "/mnt/zmedia/", 1) for p in paths]
    script = 'while IFS= read -r p; do [ -e "$p" ] && echo E || echo M; done'
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", PROBE_HOST, script],
                       input="\n".join(host) + "\n", capture_output=True,
                       text=True, timeout=900)
    flags = [line for line in r.stdout.splitlines() if line.strip()]
    return {p: (f == "E") for p, f in zip(paths, flags)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually remove the records")
    args = ap.parse_args()

    bad = foreign(LIBS)
    if not bad:
        print("no foreign records in the target libraries")
        return 0
    by_lib = collections.Counter(d.get("DB") for d in bad)
    names = {v: k for k, v in LIBS.items()}
    print(f"foreign records: {len(bad)}  " +
          ", ".join(f"{names.get(db, db)}={n}" for db, n in by_lib.items()))
    tops = collections.Counter(str(d.get("file", "")).split("/media/videos/")[-1].split("/")[0]
                               for d in bad)
    print("  by top folder:", dict(tops.most_common(8)))

    paths = [str(d.get("file")) for d in bad]
    before = disk_exists(paths)
    print(f"  on disk before: {sum(before.values())}/{len(paths)}")

    if not args.apply:
        print("\nreport only — re-run with --apply to remove the records")
        return 0

    removed = 0
    for i, p in enumerate(paths, 1):
        crud("FileJSONDB", "removeOne", doc_id=p)
        removed += 1
        if i % 200 == 0:
            print(f"  removed {i}/{len(paths)}")
    print(f"  removed {removed} record(s)")

    left = foreign(LIBS)
    print(f"verify: foreign records remaining = {len(left)}")
    after = disk_exists(paths)
    print(f"verify: media still on disk = {sum(after.values())}/{len(paths)} "
          f"(must equal the before count)")
    if sum(after.values()) != sum(before.values()):
        print("FAIL: disk contents changed — investigate before proceeding")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
