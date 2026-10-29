"""Exhaustive metadata resolution (the never-stop-at-TMDB rule).

Every resolver before this one stopped at the first source that said
nothing: no TMDB hit meant "unidentified", and the row sat flag-less for
months (The Devil's Music, Folk America). The rule now: try EVERY source
before giving up, and record which source each field came from so a later
pass knows what is still missing.

Order (cheapest + most structured first):
  1. TMDB (ids, overview, poster, episodes)
  2. TVmaze (episode names/overviews where TMDB shells or 404s)
  3. BBC pid catalogue (synopsis, first broadcast, artwork)
  4. Local evidence (tvshow.nfo imdb_id/plot, container tags)
A field keeps the FIRST source that provides it; sources never overwrite.

IMDb-direct has no API and is not suite-able — that step stays an
agent-level web search (skill rule), not code.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import re


def _words(s: str) -> List[str]:
    """Lowercase filler-free words for title agreement. Pure."""
    from . import config  # deferred: light import for tests

    return [t for t in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()
            if t not in config.STOPWORDS and len(t) >= 3]


def resolve_series(title: str, year: Optional[str] = None,
                   local_nfo: Optional[Dict[str, str]] = None,
                   wiki_page: Optional[str] = None) -> Dict[str, Any]:
    """Best metadata from every source. Never raises, never writes."""
    from . import bbc, tmdb, tvmaze  # deferred: light import for tests

    out: Dict[str, Any] = {"title": title, "sources": [], "episodes": []}
    # 1. TMDB
    try:
        hit = (tmdb.search(title, year, "tv") or tmdb.search(title, None, "tv"))
    except Exception:  # noqa: BLE001 - one source down never stops the chain
        hit = None
    if hit:
        out["sources"].append("tmdb")
        out["tmdb_id"] = hit.get("id")
        if (hit.get("overview") or ""):
            out["overview"] = hit["overview"]
            out["overview_from"] = "tmdb"
        try:
            det = tmdb._get(f"/tv/{hit['id']}", {"append_to_response": "external_ids"}) or {}
            imdb = ((det.get("external_ids") or {}).get("imdb_id") or "")
            if imdb:
                out["imdb_id"] = imdb
                out["imdb_from"] = "tmdb"
            if det.get("poster_path"):
                out["poster_path"] = det["poster_path"]
        except Exception:  # noqa: BLE001
            pass
    # 2. TVmaze (names + overviews, especially where TMDB shelled)
    try:
        eps = tvmaze.show_episodes(title)
    except Exception:  # noqa: BLE001
        eps = []
    if eps:
        out["sources"].append("tvmaze")
        out["episodes"] = [
            {"season": e.get("season"), "number": e.get("number"),
             "name": e.get("name") or "",
             "overview": tvmaze.strip_html(e.get("summary"))}
            for e in eps
        ]
        if "imdb_id" not in out:
            ext = tvmaze.show_externals(title)
            if ext.get("imdb"):
                out["imdb_id"] = ext["imdb"]
                out["imdb_from"] = "tvmaze"
        if "overview" not in out:
            summ = tvmaze.show_summary(title)
            if summ:
                out["overview"] = summ
                out["overview_from"] = "tvmaze"
    # 3. BBC catalogue
    if "overview" not in out:
        try:
            hits = bbc.search_programmes(f"{title} BBC", limit=4)
            pid = (hits[0].get("pid") if hits else "")
            if pid:
                prog = bbc.programme(pid)
                if (prog.get("synopsis") or ""):
                    out["sources"].append("bbc")
                    out["overview"] = prog["synopsis"]
                    out["overview_from"] = f"bbc:{pid}"
                    if prog.get("image"):
                        out["image_url"] = prog["image"]
        except Exception:  # noqa: BLE001
            pass
    # 4. Local evidence (sidecar truth outranks nothing, but beats a gap)
    if local_nfo:
        if local_nfo.get("imdb_id") and "imdb_id" not in out:
            out["sources"].append("nfo")
            out["imdb_id"] = local_nfo["imdb_id"]
            out["imdb_from"] = "nfo"
        if local_nfo.get("plot") and "overview" not in out:
            if "nfo" not in out["sources"]:
                out["sources"].append("nfo")
            out["overview"] = local_nfo["plot"]
            out["overview_from"] = "nfo"
    # 5. Search-engine IMDb discovery (last resort, verified before trust).
    # The engine returns evidence (Bing's top IMDb hit); TVmaze's imdb lookup
    # confirms it resolves to the wanted title. Soul Britannia proved the
    # loop matters: the engine's first hit was Soul 2020 (tt2948372), which
    # verification refused.
    if "imdb_id" not in out:
        try:
            from media_lib import imdb_search  # deferred: slow, network-heavy

            yr = int(year) if year and str(year).isdigit() else 0
            found = imdb_search.discover_imdb(title, yr)
            tt = found.get("imdb_id", "")
            if tt:
                back = tvmaze.by_imdb(tt)
                want = _words(title)
                got = set(_words(str(back.get("name") or "")))
                # The longest distinctive word must agree: "Soul" alone must
                # never license "Soul Britannia" (that pins Soul 2020).
                anchor = max(want, key=len) if want else ""
                ok = bool(anchor) and anchor in got
                if ok and "search" not in out["sources"]:
                    out["sources"].append("search")
                if ok:
                    out["imdb_id"] = tt
                    out["imdb_from"] = "search"
        except Exception:  # noqa: BLE001
            pass
    # 6. Wikipedia episode tables (keyless; First Cut proved the need: TMDB
    # covered the wrong years, TVmaze had titles with 0-char overviews).
    if "overview" not in out and wiki_page:
        try:
            from media_lib import wikipedia as _wiki  # deferred: network-heavy

            found = _wiki.episode_overview(wiki_page, title)
            if found.get("overview"):
                out["overview"] = found["overview"]
                out["overview_from"] = found["overview_from"]
                out["sources"].append("wikipedia")
        except Exception:  # noqa: BLE001
            pass
    return out


def missing_fields(resolved: Dict[str, Any]) -> List[str]:
    """What the chain still could not find. Pure."""
    return [f for f in ("tmdb_id", "imdb_id", "overview", "episodes")
            if not resolved.get(f)]
