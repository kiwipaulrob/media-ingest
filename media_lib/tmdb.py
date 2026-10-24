"""TMDB search and the identity gate.

Two traps encoded here:

* TMDB search is spelling-sensitive — British 's' vs American 'z' returns zero
  hits on one and the full set on the other, so retry the alternate spelling
  before concluding there is no match.
* Movie and TV ids are SEPARATE namespaces. A TV title's id can collide with an
  unrelated movie id, so the gate reports which namespace produced the hit and
  never silently cross-assigns.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from . import jellyfin, matcher

_TMDB = "https://api.themoviedb.org/3"
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


class TmdbError(RuntimeError):
    pass


def api_key() -> str:
    key = jellyfin.env("TMDB_API_KEY")
    if not key:
        raise TmdbError("TMDB_API_KEY is not set")
    return key


def title_year(name: str) -> tuple[str, Optional[str]]:
    """'Some.Film.2005.1080p.BluRay.x264-GRP' -> ('Some Film', '2005'). Pure."""
    cleaned = matcher.clean_release(name)
    m = _YEAR.search(cleaned)
    year = m.group(1) if m else None
    title = _YEAR.sub(" ", cleaned)
    return re.sub(r"\s+", " ", title).strip(), year


def _get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(params, api_key=api_key())
    url = f"{_TMDB}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except Exception as exc:  # noqa: BLE001 - network shape is not ours
        raise TmdbError(f"TMDB {path} failed: {str(exc)[:160]}") from exc


def search(
    title: str, year: Optional[str] = None, media_type: str = "movie"
) -> Optional[Dict[str, Any]]:
    """Top search hit for a title, or None.

    Retries with the other British/American spelling when the first pass is
    empty — zero hits on one spelling is not evidence of absence.
    """
    # Swap the British/American SUFFIX only. Substituting every s/z turned
    # "The Stories of Us" into "The Ztoriez of Uz" and poisoned the search.
    variants = [title]
    uk = re.sub(r"([a-z]{2,})z(e[ds]?|ing|ation|er|ers)\b", r"\1s\2", title, flags=re.I)
    if uk != title:
        variants.append(uk)
    us = re.sub(r"([a-z]{2,})s(e[ds]?|ing|ation|er|ers)\b", r"\1z\2", title, flags=re.I)
    if us != title and us not in variants:
        variants.append(us)

    for q in variants:
        params: Dict[str, Any] = {"query": q}
        if year:
            params["year"] = year
        data = _get(f"/search/{media_type}", params)
        results = data.get("results") or []
        if results:
            hit = results[0]
            return {
                "id": hit.get("id"),
                "title": hit.get("title") or hit.get("name"),
                "year": (hit.get("release_date") or hit.get("first_air_date") or "")[:4]
                or None,
                "media": media_type,
                "overview": (hit.get("overview") or "")[:200],
            }
    return None


def gate(name: str) -> Dict[str, Any]:
    """Annotate a release name with its TMDB candidate. Never writes anything."""
    title, year = title_year(name)
    if not title:
        return {"status": "no-match", "query": "", "year": year}
    try:
        hit = search(title, year, "movie") or search(title, year, "tv")
    except TmdbError as exc:
        return {
            "status": "error",
            "query": title,
            "year": year,
            "error": str(exc)[:120],
        }
    if not hit:
        return {"status": "no-match", "query": title, "year": year}
    # A year mismatch beyond 1 year means the hit is a different work.
    if year and hit.get("year") and abs(int(year) - int(hit["year"])) > 1:
        return {
            "status": "ambiguous",
            "query": title,
            "year": year,
            "candidate": hit,
            "reason": "year-mismatch",
        }
    return {"status": "candidate", "query": title, "year": year, "candidate": hit}


def find_episode_by_name(series_id: int, wanted: str, max_season: int = 20):
    """The TMDB episode whose NAME matches `wanted`, or None.

    Never look an episode up by number: TMDB's per-season numbering does not
    match a hand-built library's, so episode 1 of season 4 returned the
    Def Leppard *Hysteria* episode for a Sex Pistols slot, and season 9's
    episode 2 returned Tom Petty for John Lennon. Both wrote the wrong synopsis
    and the wrong still onto the row.

    Match on distinctive words, ignoring series boilerplate.
    """
    stop = {
        "classic",
        "albums",
        "album",
        "the",
        "and",
        "of",
        "a",
        "in",
        "on",
        "making",
        "never",
        "mind",
        "here",
        "is",
    }
    want = {
        w for w in re.findall(r"[a-z]{3,}", (wanted or "").lower()) if w not in stop
    }
    if not want:
        return None
    best = None
    for season in range(0, max_season + 1):
        try:
            data = _get(f"/tv/{series_id}/season/{season}", {})
        except Exception:  # noqa: BLE001 - a missing season 404s
            continue
        for ep in data.get("episodes") or []:
            have = {
                w
                for w in re.findall(r"[a-z]{3,}", (ep.get("name") or "").lower())
                if w not in stop
            }
            overlap = len(want & have)
            if overlap >= max(1, min(2, len(want))) and overlap >= 1:
                if best is None or overlap > best[0]:
                    best = (overlap, season, ep)
    return best[2] if best else None
