"""Repair wrong-person bios found by the pre-1900 audit.

A repair is the one case where an existing Overview may be REPLACED, so it is
guarded in both directions:

* **signature guard** — the current bio must still contain the flagged pre-1900
  year. If it no longer does, someone (or another pass) already fixed it and the
  write is refused.
* **never a guess** — a repair either writes a bio from a provider that passed
  the identity + birth-year gates, or CLEARS the wrong bio so the row is an
  honest gap. No invented text.

Every repair is journalled with the old and new text.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import config, jellyfin

REPAIR_LOG = config.STATE_DIR / 'bio-repairs.jsonl'


def _journal(rec: Dict[str, Any]) -> None:
    REPAIR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REPAIR_LOG.open('a') as fh:
        fh.write(json.dumps(rec) + '\n')


def repair(person_id: str, name: str, *, new_bio: Optional[str],
           source: str, expect_year: int) -> Dict[str, Any]:
    """Replace (or clear) a wrong bio, guarded by the flagged year."""
    uid = jellyfin.user_id()
    row = jellyfin.item(uid, person_id, fields='Overview,LockedFields')
    cur = (row.get('Overview') or '').strip()
    if str(expect_year) not in cur:
        return {'status': 'SKIP-CHANGED', 'name': name,
                'reason': f'bio no longer mentions {expect_year}'}
    before = cur
    row['Overview'] = new_bio or ''
    if new_bio:
        row['LockedFields'] = sorted(set((row.get('LockedFields') or [])
                                         + ['Name', 'Overview']))
    row.pop('Genres', None)
    try:
        jellyfin.post_item(person_id, row)
    except Exception:
        jellyfin.post_item(person_id, {'Name': row.get('Name'),
                                       'Overview': new_bio or '',
                                       'LockedFields': row.get('LockedFields', [])})
    time.sleep(0.6)
    # Jellyfin's read-after-write can lag: a first attempt was reported FAILED
    # while the write had in fact succeeded. Poll before calling it a failure.
    got = ''
    for delay in (0, 1.0, 2.0, 3.0):
        if delay:
            time.sleep(delay)
        back = jellyfin.item(uid, person_id, fields='Overview')
        got = (back.get('Overview') or '').strip()
        if got == (new_bio or '').strip():
            break
    ok = (got == (new_bio or '').strip())
    rec = {'id': person_id, 'name': name, 'action': 'replace' if new_bio else 'clear',
           'source': source, 'old_len': len(before), 'new_len': len(got),
           'ok': ok, 'old_head': before[:120], 'new_head': got[:120]}
    _journal(rec)
    return {'status': 'REPLACED' if (ok and new_bio) else ('CLEARED' if ok else 'FAILED'),
            'name': name, 'old_len': len(before), 'new_len': len(got)}
