"""Episode-aware sourcing hunts — the tested shape of fifteen ad hoc loops.

Every hand-rolled hunt this session did the same four things: Prowlarr
search, protocol/seed/title filtering, seed-ranked pick, queue. The ad hoc
versions each invented their own filters and three of them silently returned
nothing on typos (`want in title` after the wrong normalization, seed key
`seeds` vs `seeders`). This module fixes the shape once.

Two stages, split at the destructive line:

* ``plan()`` — read-only: search, filter, rank. Returns candidates.
* ``queue()`` — spends bandwidth / Usenet blocks: queues ONE explicit
  candidate. Never bulk-queues; the caller picks.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_EP = re.compile(r"[Ss]?(\d{1,2})[EeXx](\d{1,2})")


def title_keys(title: str) -> set:
    """Lowercase alphanumeric tokens — the only normalization allowed.

    Earlier loops normalized with `.replace(".", " ")` in one place and
    forgot it in another, so `e01 in title` silently missed `S01E01`.
    Token sets make the match order-independent.
    """
    return set(re.findall(r"[a-z0-9]+", title.lower()))


def episode_of(title: str) -> Optional[tuple]:
    """(season, episode) from SxxEyy / NxN markers, else None."""
    m = _EP.search(title)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def relevant(result: Dict[str, Any], keys: List[str]) -> bool:
    """Every key must appear as a token in the title. All-or-nothing."""
    toks = title_keys(str(result.get("title", "")))
    return all(k.lower() in toks for k in keys)


def plan(
    results: List[Dict[str, Any]],
    keys: Optional[List[str]] = None,
    protocol: Optional[str] = None,
    min_seeders: int = 1,
    episode: Optional[tuple] = None,
) -> List[Dict[str, Any]]:
    """Filter + rank raw Prowlarr rows. Pure — no network, no side effects.

    * protocol: "torrent" / "usenet" / None (either).
    * min_seeders: torrents below this are dropped; Usenet rows carry no
      seeder count, so grabs (or mere presence) count instead.
    * episode: (season, ep) the title must contain, when known.
    Rank: seeders + grabs, descending.
    """
    out = []
    for r in results:
        if protocol and str(r.get("protocol", "")).lower() != protocol:
            continue
        if keys and not relevant(r, keys):
            continue
        if episode is not None:
            if episode_of(str(r.get("title", ""))) != episode:
                continue
        seeds = r.get("seeders") or 0
        grabs = r.get("grabs") or 0
        if str(r.get("protocol", "")).lower() == "torrent" and seeds < min_seeders:
            continue
        out.append((seeds + grabs, r))
    out.sort(key=lambda t: -t[0])
    return [r for _, r in out]


def queue(
    candidate: Dict[str, Any],
    category: str = "tv",
) -> Dict[str, Any]:
    """Queue ONE explicit candidate. The only bandwidth-spending call here."""
    from . import qb, sab  # deferred: keeps import light for tests

    proto = str(candidate.get("protocol", "")).lower()
    title = str(candidate.get("title", ""))
    if proto == "usenet":
        url = candidate.get("downloadUrl")
        if not url:
            return {"ok": False, "error": "usenet candidate has no downloadUrl"}
        return {"ok": True, "nzo": sab.add_url(url, title, category=category)}
    url = candidate.get("magnetUrl") or candidate.get("guid")
    if not url:
        return {"ok": False, "error": "torrent candidate has no magnet/guid"}
    return {"ok": True, "qb": qb.add_verified(url, title, category=category)}
