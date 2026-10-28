"""Approved ids for a film batch, in the suite's own check-list numbering.

The pipeline already closes the film loop: `cli.scan` probes completed downloads,
`cli.plan` writes `needs-your-check.json` with a `recommend` per row (a film the
library does not hold comes back `NEW`, annotated with `tmdb.gate`), and

    python3 -m cli.ingest_new --from-plan state/needs-your-check.json \
        --ids N,M --library "Film Noir"

turns those approved ids into placements through `placement_for_new` — folder
naming, `[tmdbid-N]` tag, copy/probe/duration/decode gates, ledger. This script
only answers "which of these rows is my batch?", because the client also holds a
hundred unrelated torrents and "approve every NEW row" would ingest other work.

Matching, in order:

1. **infohash** — the row's source came from one of the batch's torrents.
2. **TMDB identity** — the row's gate candidate has the same normalised title and
   a year within one of a batch target. Needed because the same film is often
   held by a *different* release than the one the hunt queued (the batch's
   `The Set Up … AMZN` while the finished download was `The Set-Up … WAC`), and
   refusing it would download the same film twice.

  python3 scripts/film_batch_ids.py [--batch B] [--plan P] [--check C]

Prints a comma-separated id list on stdout (empty when nothing is ready) and a
per-row line on stderr. Read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_lib import config, matcher  # noqa: E402


def load_rows(path: str) -> list:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    if isinstance(data, dict):
        return data.get("rows") or data.get("check") or []
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="state/film-queued.json", help="queued {name,hash} rows")
    ap.add_argument("--plan", default="state/film-plan.json", help="picks with clean title+year")
    ap.add_argument("--check", default=str(config.CHECK_LIST))
    a = ap.parse_args()

    hashes = {str(r.get("hash")) for r in load_rows(a.batch) if r.get("hash")}
    targets = [
        (matcher.squash(str(r.get("title") or "")), int(r.get("year") or 0))
        for r in load_rows(a.plan)
        if r.get("title")
    ]

    ids = []
    for i, r in enumerate(load_rows(a.check), 1):
        if not isinstance(r, dict) or r.get("recommend") != "NEW" or not r.get("src"):
            continue
        gate = r.get("tmdb") or {}
        cand = gate.get("candidate") or {}
        why = ""
        if str(r.get("hash")) in hashes:
            why = "batch hash"
        elif gate.get("status") == "candidate" and cand.get("title"):
            c_t, c_y = matcher.squash(str(cand["title"])), int(cand.get("year") or 0)
            if any(t == c_t and (not y or not c_y or abs(y - c_y) <= 1) for t, y in targets):
                why = "same film, other release"
        if not why:
            continue
        print(
            f"  [{i}] APPROVE ({why})  {Path(str(r['src'])).name[:58]}"
            f"  -> {cand.get('title', '?')} ({cand.get('year', '?')})",
            file=sys.stderr,
        )
        ids.append(str(i))
    print(",".join(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
