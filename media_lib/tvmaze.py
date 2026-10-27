"""TVmaze episode fallback for UK broadcast strands (Review-P3.1).

TMDB is weakest exactly where this library is strongest: Arena, Omnibus,
Storyville, Later... with Jools Holland, Classic Albums S11+. TVmaze is
community-curated around broadcast TV and carries complete episode titles,
airdates, and summaries where TMDB has empty shells or 404s.

Keyless, single-response embedding: one call returns the show plus its
ENTIRE episode list, so season-by-season 404-walking is unnecessary.
Used ONLY when tmdb.find_episode_by_name misses — never overriding a TMDB
hit, and never inventing: no match returns None.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API = "https://api.tvmaze.com"
UA = {"User-Agent": "hermes-media-maintainer/1.0 (media library maintenance)"}


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        import json

        return json.load(r)


def show_episodes(show: str) -> List[Dict[str, Any]]:
    """Every episode of the best-matching show, or []. Never raises."""
    try:
        data = _get_json(
            f"{API}/singlesearch/shows?q={urllib.parse.quote(show)}&embed=episodes"
        )
        return (data.get("_embedded") or {}).get("episodes") or []
    except Exception:  # noqa: BLE001 - fallback source, never a gate
        return []


def strip_html(summary: Optional[str]) -> str:
    """TVmaze summaries are <p>-wrapped HTML. Pure."""
    if not summary:
        return ""
    return re.sub(r"<[^>]+>", "", summary).strip()


def by_number(show: str, season: int, ep: int) -> Optional[Dict[str, Any]]:
    """Episode by broadcast (season, number). None when absent."""
    for e in show_episodes(show):
        if e.get("season") == season and e.get("number") == ep:
            return {
                "name": e.get("name") or "",
                "overview": strip_html(e.get("summary")),
                "air_date": e.get("airdate") or "",
                "season_number": season,
                "episode_number": ep,
            }
    return None


def by_name(show: str, wanted: str) -> Optional[Dict[str, Any]]:
    """Episode whose name shares distinctive words with `wanted`. Pure match."""
    want = {w for w in re.findall(r"[a-z]{3,}", (wanted or "").lower())}
    if not want:
        return None
    best = None
    for e in show_episodes(show):
        have = {w for w in re.findall(r"[a-z]{3,}", str(e.get("name") or "").lower())}
        overlap = len(want & have)
        if overlap >= max(1, min(2, len(want))) and (best is None or overlap > best[0]):
            best = (overlap, e)
    if not best:
        return None
    e = best[1]
    return {
        "name": e.get("name") or "",
        "overview": strip_html(e.get("summary")),
        "air_date": e.get("airdate") or "",
        "season_number": e.get("season"),
        "episode_number": e.get("number"),
    }
