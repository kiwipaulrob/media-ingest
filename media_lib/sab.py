"""SABnzbd Web API client — the Usenet half of the fetch role.

Kept deliberately small: the sourcing stage only needs to queue an NZB by URL,
then report queue/history health. Completion is proved by reading the history
slot's ``status`` and ``storage`` path, never by an accepted ``nzo_id`` — the
API returns one of those for a grab that later fails at unpack time.

Endpoint shapes verified against SABnzbd 5.1.3:
  * queue/history answer ``{"queue"|"history": {"slots": [...]}}``
  * ``mode=addurl`` takes ``name=<url>`` and returns ``{"nzo_ids": [...]}``
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import config


class SabError(RuntimeError):
    pass


def _base() -> str:
    if not config.SAB_URL:
        raise SabError('SABNZBD_URL is not set')
    return config.SAB_URL.rstrip('/')


def _call(mode: str, **params: Any) -> Dict[str, Any]:
    qs = {'mode': mode, 'output': 'json', 'apikey': config.SAB_KEY}
    qs.update({k: str(v) for k, v in params.items()})
    url = f'{_base()}/api?' + urllib.parse.urlencode(qs)
    try:
        with urllib.request.urlopen(url, timeout=90) as fh:
            return json.loads(fh.read() or b'{}')
    except Exception as exc:                            # noqa: BLE001
        raise SabError(f'{mode} failed: {type(exc).__name__}: {exc}') from exc


def queue() -> List[Dict[str, Any]]:
    return (_call('queue').get('queue') or {}).get('slots') or []


def history(limit: int = 20) -> List[Dict[str, Any]]:
    return (_call('history', limit=limit).get('history') or {}).get('slots') or []


def add_url(url: str, name: str = '', category: str = 'movies') -> List[str]:
    """Queue an NZB by URL. Returns the new nzo_ids (empty when refused)."""
    out = _call('addurl', name=url, nzbname=name[:80], cat=category)
    return out.get('nzo_ids') or []


def completed_slots(limit: int = 30) -> List[Dict[str, Any]]:
    """History slots that finished AND carry a storage path on disk."""
    return [s for s in history(limit)
            if s.get('status') == 'Completed' and s.get('storage')]


def find_completed(name_contains: str, limit: int = 30) -> Optional[Dict[str, Any]]:
    needle = name_contains.lower()
    for slot in completed_slots(limit):
        if needle in (slot.get('name') or '').lower():
            return slot
    return None
