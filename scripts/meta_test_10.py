#!/usr/bin/env python3
"""Compare the metadata chain against what Music Docos already holds.

Read-only. For a reproducible random sample of Music Docos titles:
  * record the doc's CURRENT metadata (overview length, ids, poster, credits)
  * resolve up to N credited people through the provider chain
  * report, per person: current bio/portrait vs chain findings

Writes JSON to state/meta-test-10.json and prints a table.

Run: python3 -m scripts.meta_test_10  (env: JELLYFIN_*, TMDB_API_KEY, LASTFM_API_KEY)
"""
from __future__ import annotations

import json
import random
import sys
import time
from typing import Any, Dict, List
from pathlib import Path

from media_lib import config, jellyfin
from media_lib.providers import chain

MUSIC_DOCOS = '5f95760bae4c07e31d840d70c3a46111'
SAMPLE = 10
PEOPLE_PER_DOC = 3
SEED = 20261020


def _args():
    import argparse
    ap = argparse.ArgumentParser(description='Compare chain output vs library metadata')
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--sample', type=int, default=SAMPLE)
    ap.add_argument('--people', type=int, default=PEOPLE_PER_DOC)
    ap.add_argument('--out', default=None)
    return ap.parse_args()


def docs(uid: str) -> List[Dict[str, Any]]:
    return jellyfin.list_items(
        uid, recursive=True, include_types='Movie,Video', parent_id=MUSIC_DOCOS,
        fields='Overview,ProviderIds,ImageTags,People,ProductionYear')


def current(row: Dict[str, Any]) -> Dict[str, Any]:
    prov = row.get('ProviderIds') or {}
    ov = (row.get('Overview') or '').strip()
    return {
        'name': row.get('Name'),
        'year': row.get('ProductionYear'),
        'overview_len': len(ov),
        'tmdb': prov.get('Tmdb'), 'imdb': prov.get('Imdb'),
        'poster': bool((row.get('ImageTags') or {}).get('Primary')),
        'people': len(row.get('People') or []),
        'path': row.get('Path'),
    }


def resolve_person(name: str, prefer: str = 'artist') -> Dict[str, Any]:
    """Try the preferred kind, then the other one if nothing came back."""
    out = chain.resolve(name, prefer)
    if out['bio_len'] == 0:
        alt = chain.resolve(name, 'person' if prefer == 'artist' else 'artist')
        if alt['bio_len'] > out['bio_len']:
            alt['kind_tried'] = alt['kind']
            return alt
    out['kind_tried'] = prefer
    return out


def main() -> int:
    args = _args()
    uid = jellyfin.user_id()
    rows = docs(uid)
    rnd = random.Random(args.seed)
    sample = rnd.sample(rows, min(args.sample, len(rows)))
    print(f'musicdocos items: {len(rows)} | sample: {len(sample)} (seed {args.seed})', flush=True)
    per_doc = args.people

    report: List[Dict[str, Any]] = []
    for i, row in enumerate(sample, 1):
        cur = current(row)
        print(f'[{i}/{len(sample)}] {cur["name"]}', flush=True)
        people = list(row.get('People') or [])
        # Prefer people with NO bio (the real gaps), then fill up with anyone.
        unbiographed = [p for p in people
                        if not (p.get('Overview') or '').strip()]
        picks = unbiographed[:per_doc]
        if len(picks) < per_doc:
            rest = [p for p in people if p not in picks]
            picks += rest[:per_doc - len(picks)]

        entries = []
        for p in picks:
            name = p.get('Name') or ''
            if not name:
                continue
            try:
                res = resolve_person(name)
            except Exception as exc:                    # noqa: BLE001
                entries.append({'person': name, 'error': str(exc)[:120]})
                continue
            existing_bio = (p.get('Overview') or '').strip()
            entries.append({
                'person': name,
                'role': p.get('Role'),
                'existing_bio_len': len(existing_bio),
                'existing_poster': bool(p.get('ImageTags') or {}),
                'chain_bio_len': res['bio_len'],
                'chain_bio_source': res['provenance'].get('biography'),
                'chain_portrait': bool(res['fields'].get('portrait')),
                'chain_portrait_source': res['provenance'].get('portrait'),
                'chain_bio_head': str(res['fields'].get('biography') or '')[:160],
                'conflicts': list(res['conflicts']),
                'rejected': res['rejected'],
                'mbid': res['identity'].get('mbid'),
                'country': res['identity'].get('country'),
                'type': res['identity'].get('type'),
                'aliases': res['identity'].get('aliases'),
                'errors': res['errors'],
                'kind': res.get('kind_tried'),
            })
            time.sleep(0.2)
        report.append({'doc': cur, 'people': entries,
                       'gaps': [e for e in entries
                                if e.get('existing_bio_len', 0) == 0
                                and e.get('chain_bio_len', 0) > 0]})

    out = {'seed': args.seed, 'sampled': len(sample), 'library': 'Music Docos',
           'docs': report}
    path = Path(args.out) if args.out else (config.STATE_DIR / f'meta-test-{args.seed}.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(f'\nwrote {path}', flush=True)

    total_people = sum(len(d['people']) for d in report)
    fillable = sum(len(d['gaps']) for d in report)
    print(f'SUMMARY: {total_people} people checked | {fillable} bio gaps the chain can fill')
    for d in report:
        c = d['doc']
        print(f"\n{c['name']} ({c['year']}) ov={c['overview_len']} "
              f"tmdb={c['tmdb'] or '-'} imdb={c['imdb'] or '-'} poster={'Y' if c['poster'] else '-'}")
        for e in d['people']:
            if 'error' in e:
                print(f"   {e['person']}: ERROR {e['error']}")
                continue
            print(f"   {e['person']:<26} existing={e['existing_bio_len']:>4} "
                  f"chain={e['chain_bio_len']:>4} via={e.get('chain_bio_source') or '-':<12} "
                  f"portrait={'Y' if e['chain_portrait'] else '-'}"
                  f"{'  ⚠conflict' if e['conflicts'] else ''}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
