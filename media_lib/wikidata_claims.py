"""Wikidata structured claims — the universal ID bridge (Review-P4.2).

`cli/bio.py` used wbsearchentities for a 1-line description only. The entity
claims behind that search are the single best source for:
  P345  IMDb id        (fills the 4th verify field via exact QID, no guessing)
  P569  date of birth  (structured — no opening-sentence regex)
  P570  date of death
  P106  occupation QIDs (deterministic profession check)
  P18   Commons portrait (exact file, not a search-result gamble)

All fetchers return "" / {} on failure: a missing claim is a gap, not a crash.
Portrait URLs are PRINTED for human approval — cmd_portrait attaches them.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List

UA = {"User-Agent": "hermes-media-maintainer/1.0 (media library maintenance)"}
API = "https://www.wikidata.org/w/api.php"


def _get(params: Dict[str, object]) -> Dict[str, Any]:
    q = urllib.parse.urlencode({"format": "json", **params})
    req = urllib.request.Request(API + "?" + q, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - a failed lookup is a gap
        return {}


def _names_match(name: str, label: str) -> bool:
    def norm(v: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (v or "").lower()).strip()

    a, b = norm(name), norm(label)
    return bool(a and b) and (a == b or a in b or b in a)


def search_qid(name: str) -> str:
    """Verified QID for a person name, or "". Label must match (no fuzzy)."""
    data = _get({"action": "wbsearchentities", "language": "en",
                 "search": name, "limit": 3})
    for hit in data.get("search") or []:
        if _names_match(name, hit.get("label") or ""):
            return hit.get("id") or ""
    return ""


def claims(qid: str) -> Dict[str, List[str]]:
    """Claim values by property: mainsnak datavalue strings. Pure-parseable."""
    if not qid:
        return {}
    data = _get({"action": "wbgetentities", "ids": qid, "props": "claims"})
    return parse_claims((data.get("entities") or {}).get(qid) or {})


def parse_claims(entity: Dict[str, Any]) -> Dict[str, List[str]]:
    """Entity JSON -> {pid: [values]}. Pure (tested with fixtures)."""
    out: Dict[str, List[str]] = {}
    for pid, stmts in (entity.get("claims") or {}).items():
        vals = []
        for s in stmts or []:
            dv = ((s.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(dv, dict):
                if dv.get("id"):
                    vals.append(dv["id"])
                elif dv.get("time"):
                    vals.append(dv["time"])
                elif dv.get("text"):
                    vals.append(dv["text"])
            elif isinstance(dv, str):
                vals.append(dv)
        if vals:
            out[pid] = vals
    return out


def imdb_id(name_or_qid: str) -> str:
    """P345 for a name (via verified search) or QID. "" when unknown."""
    qid = name_or_qid if re.fullmatch(r"Q\d+", name_or_qid or "") else search_qid(name_or_qid)
    vals = claims(qid).get("P345") or []
    return vals[0] if vals else ""


def birth_year(name_or_qid: str) -> str:
    """P569 year (structured — no prose regex). "" when unknown."""
    qid = name_or_qid if re.fullmatch(r"Q\d+", name_or_qid or "") else search_qid(name_or_qid)
    vals = claims(qid).get("P569") or []
    m = re.search(r"(\d{4})", vals[0]) if vals else None
    return m.group(1) if m else ""


def portrait_url(name_or_qid: str, width: int = 800) -> str:
    """Commons P18 file as a direct URL. "" when unknown."""
    qid = name_or_qid if re.fullmatch(r"Q\d+", name_or_qid or "") else search_qid(name_or_qid)
    vals = claims(qid).get("P18") or []
    if not vals:
        return ""
    return (f"https://commons.wikimedia.org/wiki/Special:FilePath/"
            f"{urllib.parse.quote(vals[0])}?width={width}")


def qid_for_wikipedia_title(title: str) -> str:
    """QID via enwiki pageprops (exact page, no search fuzz)."""
    data = _get({"action": "query", "titles": title, "prop": "pageprops"})
    pages = (data.get("query") or {}).get("pages") or {}
    for p in pages.values():
        qid = (p.get("pageprops") or {}).get("wikibase_item") or ""
        if qid:
            return qid
    return ""


def imdb_for_tmdb_movie(tmdb_id: int) -> str:
    """P345 via TMDB-movie-id reverse lookup (P4947). Fills film Imdb gaps."""
    data = _get({"action": "query", "list": "search",
                 "srsearch": f"haswbstatement:P4947={tmdb_id}", "srlimit": 1})
    hits = (data.get("query") or {}).get("search") or []
    if not hits:
        return ""
    title = hits[0].get("title") or ""
    m = re.match(r"(Q\d+)", title)
    qid = m.group(1) if m else qid_for_wikipedia_title(title)
    return (claims(qid).get("P345") or [""])[0] if qid else ""
