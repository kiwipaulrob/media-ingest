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

from media_lib import config, jellyfin, place


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Place completed downloads as new items')
    ap.add_argument('--spec', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--refresh-lib', default=None,
                    help='library id to refresh after placing')
    args = ap.parse_args(argv)

    config.ensure_state_dir()
    placements = json.loads(Path(args.spec).read_text())
    journal = config.STATE_DIR / 'ingest-new.jsonl'
    done = set()
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get('ok'):
                done.add(rec.get('dest'))

    results = []
    for p in placements:
        dest = p['dest']
        if dest in done:
            print(f'SKIP   (already placed) {Path(dest).name}'[:110], flush=True)
            continue
        if args.dry_run:
            print(f'WOULD  {p["src"][-58:]}  ->  {Path(dest).name}', flush=True)
            continue
        res = place.place_new(p['src'], dest,
                              min_height=int(p.get('min_height') or 0),
                              expect_duration=p.get('expect_duration'))
        res['src'] = p['src']
        res['at'] = int(time.time())
        with journal.open('a') as fh:
            fh.write(json.dumps(res) + '\n')
        results.append(res)
        flag = 'PLACED' if res['ok'] else 'FAILED'
        extra = res.get('res') or res.get('detail') or res.get('stage')
        print(f'{flag:<7}{Path(dest).name[:58]:<59} {str(extra)[:40]}', flush=True)

    placed = [r for r in results if r.get('ok')]
    if placed and args.refresh_lib:
        jellyfin.refresh_library(args.refresh_lib)
        print(f'refreshed library {args.refresh_lib}', flush=True)

    print(f'\nplaced: {len(placed)}  failed: {len(results) - len(placed)}', flush=True)
    for r in results:
        if not r.get('ok'):
            print(f"  FAILED {Path(r['dest']).name}: {r.get('stage')} {r.get('detail','')}", flush=True)
    return 0 if len(placed) == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
