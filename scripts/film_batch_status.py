"""Status for a queued hunt batch (the batch file hunt_films.py writes).

Prints one row per wanted release with the queue's real state, progress and
speed, so a batch is judged on the downloader's numbers rather than on the
adds' HTTP statuses. `--force` re-force-starts anything still queued — a batch
larger than `max_active_downloads` otherwise parks in queuedDL forever.

  python3 scripts/film_batch_status.py --batch state/film-queued.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_lib import qb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="state/film-queued.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="totals only")
    ap.add_argument(
        "--remaining",
        action="store_true",
        help='print only "LEFT MOVING" for a shell loop to read',
    )
    a = ap.parse_args()

    wanted = json.loads(Path(a.batch).read_text())
    by_hash = {w["hash"]: w["name"] for w in wanted}
    ts = qb.torrents()
    films = [t for t in ts if t["hash"] in by_hash]

    if a.remaining:
        left = [t for t in films if (t.get("progress") or 0) < 0.999]
        moving = [t for t in left if (t.get("dlspeed") or 0) > 0]
        print(f"{len(left)} {len(moving)}")
        return 0

    if a.force:
        for t in films:
            if not t["state"].startswith("forced") and (t.get("progress") or 0) < 0.999:
                qb.set_force([t["hash"]], True)

    done = [t for t in films if (t.get("progress") or 0) >= 0.999]
    speed = sum(t.get("dlspeed") or 0 for t in films)
    meta = [t for t in films if t["state"] == "forcedMetaDL"]
    if not a.quiet:
        for t in sorted(films, key=lambda x: -(x.get("dlspeed") or 0)):
            print(
                f"{t.get('dlspeed', 0) / 1e3:9.1f}KB/s {t['state']:<13}"
                f"{t.get('progress', 0) * 100:6.1f}%  {t['name'][:66]}"
            )
    print(
        f"\nqueued {len(films)}/{len(wanted)} complete {len(done)} "
        f"meta-pending {len(meta)} aggregate {speed / 1e6:.2f} MB/s"
    )
    missing = [n for h, n in by_hash.items() if h not in {t["hash"] for t in films}]
    if missing:
        print(f"NOT IN QUEUE ({len(missing)}): " + "; ".join(m[:48] for m in missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
