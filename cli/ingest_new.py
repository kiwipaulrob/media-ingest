"""Stage 5 CLI — place completed downloads as NEW library items.

  python3 -m cli.ingest_new --spec state/new-placement.json [--dry-run]

The spec is a list of placements::

    {"src": "/downloads-zmedia/Some.Release.mkv",
     "dest": "/mnt/zmedia/videos/.../Season 02/Show S02E03 Name.mkv",
     "min_height": 700}

Every placement passes the same gates as a swap (copy, probe, duration, decode)
and a failure removes the staged copy. Journal entries land in
`state/ingest-new.jsonl` so a re-run is idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from media_lib import config, jellyfin, place, qb, safety


def _await_row(path: str, budget: int) -> str:
    """Poll for the item whose Path is exactly this file. Returns its id or ''."""
    uid = jellyfin.user_id()
    stem = path.rsplit("/", 1)[-1][:36]
    deadline = time.time() + budget
    while time.time() < deadline:
        data = jellyfin.get_json(
            f"/Users/{uid}/Items",
            params={
                "Recursive": "true",
                "SearchTerm": stem,
                "IncludeItemTypes": "Episode,Movie,Video",
                "Fields": "Path",
                "Limit": 10,
            },
        )
        for it in data.get("Items") or []:
            if (it.get("Path") or "") == path:
                return it.get("Id") or ""
        time.sleep(10)
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Place completed downloads as new items")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--refresh-lib", default=None, help="library id to refresh after placing"
    )
    ap.add_argument(
        "--wait-rows",
        type=int,
        default=0,
        metavar="SECS",
        help="poll this long for each placed file to appear as a row",
    )
    args = ap.parse_args(argv)

    config.ensure_state_dir()
    placements = json.loads(Path(args.spec).read_text())
    journal = config.STATE_DIR / "ingest-new.jsonl"
    done = set()
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok"):
                done.add(rec.get("dest"))

    results = []
    for p in placements:
        dest = p["dest"]
        if dest in done:
            print(f"SKIP   (already placed) {Path(dest).name}"[:110], flush=True)
            continue
        if args.dry_run:
            print(f'WOULD  {p["src"][-58:]}  ->  {Path(dest).name}', flush=True)
            continue
        src = p["src"]
        torrent = None
        if p.get("hash"):
            src = qb.resolve_media_path(src, p["hash"]) or src
        else:
            # Look the torrent up by path: without a hash the ledger entry below
            # has no key, and `media_scan` re-offers an already-ingested release.
            try:
                torrent = qb.torrent_for_path(src)
            except Exception:  # noqa: BLE001
                torrent = None
            if torrent:
                src = qb.resolve_media_path(src, torrent["hash"]) or src
        res = place.place_new(
            src,
            dest,
            min_height=int(p.get("min_height") or 0),
            expect_duration=p.get("expect_duration"),
        )
        res["src"] = src
        res["at"] = int(time.time())
        with journal.open("a") as fh:
            fh.write(json.dumps(res) + "\n")
        results.append(res)
        if res["ok"]:
            # Journal AND ledger immediately: a killed run must not forget a
            # placement, and `scan` keys its exclusions off this ledger.
            rec_hash = p.get("hash") or (torrent or {}).get("hash")
            if rec_hash:
                try:
                    safety.record(hash=rec_hash, action="place", dest=dest, src=src)
                except Exception as exc:  # noqa: BLE001
                    print(f"  LEDGER-WARN {type(exc).__name__}", flush=True)
        flag = "PLACED" if res["ok"] else "FAILED"
        extra = res.get("res") or res.get("detail") or res.get("stage")
        print(f"{flag:<7}{Path(dest).name[:58]:<59} {str(extra)[:40]}", flush=True)

    placed = [r for r in results if r.get("ok")]
    if placed:
        # A library Refresh re-resolves KNOWN rows; it does NOT discover new
        # files. Without this call a placement stays invisible until the next
        # full discovery — proven the hard way on two episodes.
        try:
            jellyfin.notify_media([r["dest"] for r in placed])
            print(f"notified scanner of {len(placed)} new file(s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"NOTIFY-FAIL {type(exc).__name__}: {exc}", flush=True)
        if args.wait_rows:
            # Notifies are dropped while the scan queue is busy. Measured: rows
            # appeared after the 1st, 2nd and 3rd notify on different runs, so
            # poll and re-notify up to twice rather than trusting one 204.
            for r in placed:
                for _ in range(2):
                    if _await_row(r["dest"], args.wait_rows // 2 or 60):
                        break
                    try:
                        jellyfin.notify_media([r["dest"]])
                    except Exception:  # noqa: BLE001
                        pass
        if args.wait_rows:
            for r in placed:
                r["item_id"] = _await_row(r["dest"], args.wait_rows)
                print(
                    f"  row {'found' if r['item_id'] else 'MISSING'} {Path(r['dest']).name[:52]}",
                    flush=True,
                )
            with journal.open("a") as fh:
                for r in placed:
                    fh.write(json.dumps({**r, "discovered": int(time.time())}) + "\n")
    if placed and args.refresh_lib:
        jellyfin.refresh_library(args.refresh_lib)
        print(f"refreshed library {args.refresh_lib}", flush=True)

    print(f"\nplaced: {len(placed)}  failed: {len(results) - len(placed)}", flush=True)
    for r in results:
        if not r.get("ok"):
            print(
                f"  FAILED {Path(r['dest']).name}: {r.get('stage')} {r.get('detail', '')}",
                flush=True,
            )
    return 0 if len(placed) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
