"""Stage 2 — plan.

Turns a scan into a decision table. Nothing is changed by this command, and the
output is byte-stable so a monitor can gate on it. Only rows whose decision is
REPLACE are actionable; everything else is written for a human.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from media_lib import config, library, matcher, safety, tmdb


def _ext(path: str) -> str:
    i = path.rfind('.')
    return path[i:] if i > 0 else ''


def _dest_for(old_disk: str, src: str) -> str:
    """Keep the row's filename stem, take the NEW file's extension.

    Keeping the stem is what preserves the Jellyfin row, its provider ids and
    its poster — a rename makes the scanner drop the row and take the art.
    """
    stem = os.path.splitext(old_disk)[0]
    return stem + _ext(src)


def _pack_like(name: str, n_videos: int) -> bool:
    """Season pack / multi-file torrent: only its biggest file is visible."""
    if n_videos > 1:
        return True
    return bool(re.search(r'(?i)\bs\d{1,2}(?!\s*e\d)', name)) and \
        not re.search(r'(?i)s\d{1,2}\s*e\d{1,2}', name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Build an ingest plan (read-only)')
    ap.add_argument('--scan', default=str(config.STATE_DIR / 'scan.json'))
    ap.add_argument('--out', default=str(config.STATE_DIR / 'plan.json'))
    ap.add_argument('--no-tmdb', action='store_true', help='skip NEW-row TMDB annotation')
    args = ap.parse_args(argv)

    scan = json.loads(Path(args.scan).read_text())
    dump = library.load()
    plan, check = [], []

    for e in scan.get('entries') or []:
        base = {
            'hash': e.get('hash'),
            'title': (e.get('name') or '')[:90],
            'size': e.get('size'),
            'src': e.get('src'),
            'videos_in_torrent': e.get('videos_in_torrent'),
        }
        p = e.get('probe') or {}
        base['new_res'] = (f"{p.get('w')}x{p.get('h')} {p.get('codec')}"
                           if p.get('h') else 'PROBE-FAIL')
        base['new_h'] = p.get('h')
        base['new_w'] = p.get('w')
        base['new_codec'] = p.get('codec')

        # 1. poisoned payload: all-zero head. Never ingest.
        if str(e.get('head', '')).startswith('poison') or e.get('head') == 'poisoned':
            base.update(recommend='poisoned-payload',
                        reason='head is all zeros - swarm delivered garbage')
            check.append(base)
            continue
        if not p.get('h'):
            base.update(recommend='PROBE-FAIL', reason='could not probe the source')
            check.append(base)
            continue

        # 2. multi-file torrent: the scanner sees only the biggest file.
        if _pack_like(e.get('name') or '', e.get('videos_in_torrent') or 1):
            base.update(recommend='multi-file-torrent',
                        reason='multi-file torrent - single-file mapping would pick an arbitrary episode')
            check.append(base)
            continue

        v = matcher.match(e.get('name') or '', dump)
        item = v.get('item') or {}
        base.update(
            library=item.get('lib'),
            score=v.get('score'),
            decision=v.get('decision'),
            reason=v.get('reason'),
            old_res=(f"{item.get('w')}x{item.get('h')} {item.get('codec')}"
                     if item.get('h') else '-'),
            old_h=item.get('h'),
            old_name=item.get('Name'),
            old_disk=item.get('disk'),
            old_id=item.get('id'),
        )

        if v['decision'] != matcher.MATCH or not item:
            if item and base.get('new_h') and item.get('h'):
                if base['new_h'] == item['h']:
                    base['recommend'] = 'REVIEW-equal-res'
                elif base['new_h'] < item['h']:
                    base['recommend'] = 'REVIEW-lower-res'
                else:
                    base['recommend'] = 'REVIEW-weak-match'
            else:
                base['recommend'] = 'NEW'
            if base['recommend'] == 'NEW' and not args.no_tmdb:
                base['tmdb'] = tmdb.gate(e.get('name') or '')
            check.append(base)
            continue

        # 3. folder-shaped target (DVD/VIDEO_TS): an in-place swap cannot express it.
        if not _ext(item.get('disk') or ''):
            base.update(recommend='folder-based-item',
                        reason='target path is a folder; a sibling-file swap leaves the old folder')
            check.append(base)
            continue

        if (item.get('nsrc') or 1) > 1:
            base.update(recommend='multi-source-item',
                        reason='target row is version-grouped; swapping one source changes the entry')
            check.append(base)
            continue

        if not base.get('new_h') or not base.get('old_h') or base['new_h'] <= base['old_h']:
            if base.get('new_h') and base.get('old_h') and base['new_h'] == base['old_h']:
                base['recommend'] = 'REVIEW-equal-res'
            elif base.get('new_h') and base.get('old_h') and base['new_h'] < base['old_h']:
                base['recommend'] = 'REVIEW-lower-res'
            else:
                base['recommend'] = 'REVIEW-weak-match'
            check.append(base)
            continue

        base['recommend'] = 'REPLACE'
        base['dest_disk'] = _dest_for(item['disk'], e.get('src') or '')
        plan.append(base)

    # Deterministic ordering: actionable rows by title, then the check list.
    plan.sort(key=lambda r: (str(r.get('title')), str(r.get('hash'))))
    for i, r in enumerate(plan, 1):
        r['id'] = i

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(plan, indent=1, sort_keys=True))
    safety.write_check_list(check)

    print(f'plan: {len(plan)} REPLACE candidate(s); check list: {len(check)}')
    for r in plan:
        print(f"  [{r['id']}] {r['old_res']:<22} -> {r['new_res']:<22} "
              f"{r['title'][:46]}")
    if check:
        from collections import Counter
        print('  check list:', dict(Counter(r['recommend'] for r in check)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
