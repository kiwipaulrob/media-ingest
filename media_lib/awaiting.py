"""Row discovery: wait for placed files to become library rows.

Split out of `cli/ingest_new.py` after a real failure. The placement stage used
to poll per item for a fixed budget, which had two faults:

  * **It blocked the run.** Two placements each waiting their own budget kept one
    CLI invocation alive past the caller's timeout, so the placements looked
    failed when they had actually succeeded (they were journaled and on disk).
  * **The budget was far too small.** A row appeared **nine minutes** after its
    file landed while the scan backlog drained. A 90-second poll reports
    "missing" on a healthy placement, which is worse than not checking at all.

So discovery is a separate, explicitly-budgeted step: placements report their
own outcome, and looking for the rows is a second command that can be run later
or by a monitor.

Measured on this install: notify → row is typically 30–90 s, but under a heavy
refresh backlog it has taken ~9 minutes. Budget accordingly, and treat a miss as
"not yet", never as "failed".
"""
from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

from . import jellyfin

#: Item types a placed file can legitimately become.
ROW_TYPES = 'Episode,Movie,Video'


def find_row_by_path(path: str, *, uid: Optional[str] = None,
                     types: str = ROW_TYPES, limit: int = 20) -> str:
    """Return the item id whose Path is exactly `path`, or ''.

    Exact-path equality is deliberate: a name search matches a same-titled row
    from an earlier ingest (or a ghost), which reads as success and hides the
    real state.
    """
    uid = uid or jellyfin.user_id()
    stem = path.rsplit('/', 1)[-1][:36]
    data = jellyfin.get_json(f'/Users/{uid}/Items',
                             params={'Recursive': 'true', 'SearchTerm': stem,
                                     'IncludeItemTypes': types,
                                     'Fields': 'Path', 'limit': limit})
    for it in data.get('Items') or []:
        if (it.get('Path') or '') == path:
            return it.get('Id') or ''
    return ''


def await_rows(specs: Iterable[Dict], *, budget: int = 600, interval: int = 20,
               notify_each: int = 2) -> Dict[str, Dict]:
    """Poll for rows with ONE global deadline shared by every item.

    Returns {dest: {'found': bool, 'item_id': str}}. Never raises: a missing row
    is information, not an error.
    """
    specs = [s for s in specs if s.get('dest')]
    deadline = time.time() + max(0, budget)
    found: Dict[str, Dict] = {s['dest']: {'found': False, 'item_id': ''} for s in specs}
    notifies = 0
    while specs and time.time() < deadline:
        still: List[Dict] = []
        for s in specs:
            dest = s['dest']
            if not found[dest]['found']:
                iid = find_row_by_path(dest)
                if iid:
                    found[dest] = {'found': True, 'item_id': iid}
                    continue
                still.append(s)
        specs = still
        if not specs:
            break
        # A notify can be dropped while the scan queue is busy; re-send a bounded
        # number of times rather than trusting one 204.
        if notifies < notify_each:
            try:
                jellyfin.notify_media([s['dest'] for s in specs])
            except Exception:                              # noqa: BLE001
                pass
            notifies += 1
        time.sleep(min(interval, max(1, deadline - time.time())))
    return found
