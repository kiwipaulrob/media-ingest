"""Stage 0 — source. Find releases and queue the approved ones.

  python3 -m cli.source plan  --targets state/targets.json [--protocol usenet]
  python3 -m cli.source queue --ids 1,4 [--protocol usenet] [--category musicdocos]
  python3 -m cli.source pool  (debug: how many postings the broad sweep returned)

`plan` is read-only and writes a numbered shortlist so a human can answer with
numbers. `queue` takes explicit ids — there is no "all" switch, for the same
reason `apply` has none: an unread plan must not be able to download anything.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from media_lib import config, source

PLAN_PATH = config.STATE_DIR / 'source-plan.json'
QUEUE_LOG = config.STATE_DIR / 'source-queue.jsonl'


def _load_targets(path: str) -> list:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise SystemExit('targets must be a JSON list of {"artist","album"}')
    return data


def cmd_plan(args) -> int:
    targets = _load_targets(args.targets)
    pool = source.fetch_pool()
    print(f'pool: {len(pool)} postings '
          f'({sum(1 for r in pool.values() if r.get("protocol") == "usenet")} usenet)', flush=True)

    rows, n = [], 0
    for t in targets:
        picks = source.candidates_for(pool, t['artist'], t['album'],
                                      protocol=args.protocol, min_seeds=args.min_seeds)
        if not picks and not args.no_augment:
            # The broad pool and a per-title query miss different postings, so a
            # miss costs ONE targeted query before it is called absent.
            source.augment_for(pool, t['artist'], t['album'])
            picks = source.candidates_for(pool, t['artist'], t['album'],
                                          protocol=args.protocol, min_seeds=args.min_seeds)
        if not picks:
            print(f'  MISS  {t["artist"]} — {t["album"]}', flush=True)
            rows.append({'artist': t['artist'], 'album': t['album'], 'picks': []})
            continue
        best = picks[0]
        n += 1
        flag = '' if best['complete'] else '  [INCOMPLETE SET]'
        print(f'  [{n:>2}] {best["protocol"]:<8}{best["gb"]:>6}GB '
              f'seeds={str(best.get("seeds")):<4} {best["title"][:52]}{flag}', flush=True)
        rows.append({'id': n, 'artist': t['artist'], 'album': t['album'], 'picks': picks[:5]})

    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps({'at': int(time.time()), 'protocol': args.protocol,
                                     'rows': rows}, indent=1))
    found = sum(1 for r in rows if r['picks'])
    print(f'\nfound {found}/{len(rows)} — plan written to {PLAN_PATH}', flush=True)
    return 0 if found else 1


def cmd_queue(args) -> int:
    plan = json.loads(PLAN_PATH.read_text())
    wanted = {int(x) for x in str(args.ids).split(',') if x.strip().isdigit()}
    if not wanted:
        raise SystemExit('--ids must be a comma-separated list of plan numbers')

    by_id = {r['id']: r for r in plan['rows'] if r.get('picks')}
    missing = wanted - set(by_id)
    if missing:
        raise SystemExit(f'not in the plan: {sorted(missing)}')

    QUEUE_LOG.parent.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i in sorted(wanted):
        row = by_id[i]
        pick = row['picks'][0]
        if not pick.get('complete') and not args.allow_incomplete:
            print(f'  SKIP [{i}] incomplete set: {pick["title"][:56]}', flush=True)
            continue
        res = source.queue(pick, category=args.category)
        res.update({'id': i, 'artist': row['artist'], 'album': row['album'], 'at': int(time.time())})
        with QUEUE_LOG.open('a') as fh:
            fh.write(json.dumps(res) + '\n')
        status = 'QUEUED' if res.get('ok') else 'FAILED'
        ok += 1 if res.get('ok') else 0
        print(f'  {status:<7}[{i}] {pick["protocol"]:<8}{res.get("reason", "")} '
              f'{pick["title"][:50]}', flush=True)
        time.sleep(1.2)
    print(f'\nqueued {ok}/{len(wanted)}', flush=True)
    return 0 if ok == len(wanted) else 1


def cmd_pool(args) -> int:
    pool = source.fetch_pool()
    us = [r for r in pool.values() if r.get('protocol') == 'usenet']
    print(f'postings: {len(pool)} | usenet {len(us)} | torrents {len(pool)-len(us)}')
    for title in sorted(pool)[:20]:
        print('  ', title[:90])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Sourcing stage (Prowlarr → qB/SAB)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('plan', help='read-only: build a numbered shortlist')
    p.add_argument('--targets', required=True, help='JSON list of {artist, album}')
    p.add_argument('--protocol', choices=['torrent', 'usenet'], default=None)
    p.add_argument('--min-seeds', type=int, default=None)
    p.add_argument('--no-augment', action='store_true',
                   help='skip the per-target fallback query for pool misses')
    p.set_defaults(func=cmd_plan)

    q = sub.add_parser('queue', help='queue explicit plan ids')
    q.add_argument('--ids', required=True)
    q.add_argument('--category', default='')
    q.add_argument('--allow-incomplete', action='store_true',
                   help='permit 1ofN postings (default: refuse)')
    q.set_defaults(func=cmd_queue)

    d = sub.add_parser('pool', help='debug: show the broad sweep')
    d.set_defaults(func=cmd_pool)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
