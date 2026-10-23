"""Scan Tdarr libraries SAFELY: one at a time, each with its own folder.

`scan-files` has two traps that both look like success:

  * A parent `arrayOrPath` indexes everything beneath it into the library being
    scanned. Scanning with `/media/videos` queued 1,266 films, TV episodes and
    `_staging-*` files onto a music-docs flow.
  * Two concurrent scans cross-assign rows, filing the same media under both
    libraries. Per-library totals of ~2,260 / 2,665 look plausible and are wrong.

This script removes both by construction: the path is derived from the library name
(never supplied by the caller), a parent path is refused, and multiple libraries are
scanned SEQUENTIALLY with the previous scan allowed to finish first.

    tdarr_scan.py musicdocos
    tdarr_scan.py --all
    tdarr_scan.py --all --verify      # also report any rows outside their library
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tdarr_kick import LIBS, api, crud  # noqa: E402

MEDIA_ROOT = "/media/videos"
SCAN_TIMEOUT = 3600          # an initial scan of 2,000+ files probes each one
POLL = 10


def library_path(name: str) -> str:
    """The ONLY path a library may be scanned with."""
    if name not in LIBS:
        raise SystemExit(f"unknown library {name!r}; known: {sorted(LIBS)}")
    path = f"{MEDIA_ROOT}/{name}"
    if path.rstrip("/") == MEDIA_ROOT:
        raise SystemExit("refusing to scan the parent media root")
    return path


def scanner_progress(db: str) -> tuple[int, int] | None:
    """(done, total) from the scanner's text status, or None if unparseable.

    The endpoint returns PLAIN TEXT like "Extracting info:2259/2259 Scanner mem:
    138.4MB/2715.8MB" — word matching would call "Checking:0" busy forever, and the
    memory figures contain their own "x/y" pair, so anchor on the label.
    """
    try:
        st = api("get-filescanner-status", {"data": {"dbID": db}})
    except urllib.error.HTTPError:
        return None
    text = str(st.get("raw", st)) if isinstance(st, dict) else str(st)
    # Only anchor on the labels: the memory figures carry their own "x/y" pair.
    m = re.search(r"Extracting info:\s*(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"Checking:\s*(\d+)", text)
    if m and int(m.group(1)) > 0:
        return 0, 1                     # actively checking: busy
    # "Found: N Checking:0" is the transient state between enumerating and
    # extracting. Reported as busy, because treating it as idle starts a second
    # scan on top of a running one — the cross-assignment bug.
    if re.search(r"Found:\s*\d+", text):
        return 0, 1
    return None


def scanner_busy(db: str) -> bool:
    prog = scanner_progress(db)
    return bool(prog and prog[0] < prog[1])


def any_scanner_busy() -> str | None:
    """Which library (if any) is scanning right now.

    Checking only the target's own scanner is not enough: a scan started for the
    OTHER library — by an earlier run, a cron, or a person in the UI — still
    cross-assigns rows. Both libraries are polled before starting.
    """
    for name, db in LIBS.items():
        if scanner_busy(db):
            return name
    return None


def scan_one(name: str, verify: bool) -> dict:
    db = LIBS[name]
    path = library_path(name)

    # Never start a scan while ANY library is scanning: concurrent scans
    # cross-assign rows between libraries.
    waited = 0
    while (busy := any_scanner_busy()) and waited < SCAN_TIMEOUT:
        if waited == 0:
            print(f"  waiting: {busy} is still scanning")
        time.sleep(POLL)
        waited += POLL
    if waited:
        print(f"  waited {waited}s for the previous scan to finish")

    print(f"  scanning {name} at {path}")
    api("scan-files", {"data": {"scanConfig": {
        "dbID": db, "mode": "scanFindNew", "arrayOrPath": path}}})

    # The scan does not register instantly; checking immediately would report
    # "finished" before it began. Give it a moment, then poll.
    time.sleep(5)

    waited = 0
    while scanner_busy(db) and waited < SCAN_TIMEOUT:
        time.sleep(POLL)
        waited += POLL
        if waited % 300 == 0:
            print(f"    still scanning ({waited}s)")

    docs = crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    mine = [d for d in docs if d.get("DB") == db]
    foreign = [d for d in mine
               if not str(d.get("file", "")).startswith(f"{path}/")]
    if verify:
        print(f"  {name}: {len(mine)} rows, {len(foreign)} outside {path}")
        for d in foreign[:5]:
            print("     FOREIGN:", d.get("file"))
    return {"library": name, "path": path, "rows": len(mine),
            "foreign": len(foreign), "took": waited}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("library", nargs="?", choices=sorted(LIBS))
    ap.add_argument("--all", action="store_true", help="every library, sequentially")
    ap.add_argument("--verify", action="store_true",
                    help="report rows that fall outside their library folder")
    args = ap.parse_args()

    if not args.all and not args.library:
        ap.error("give a library name or --all")

    targets = list(LIBS) if args.all else [args.library]
    bad = 0
    for name in targets:            # sequential, never parallel
        res = scan_one(name, args.verify)
        bad += res["foreign"]
        print(f"  done: {res['rows']} rows in {res['took']}s")
    if bad:
        print(f"\n{bad} row(s) sit outside their library — run "
              f"scripts/tdarr_prune_foreign.py --apply")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
