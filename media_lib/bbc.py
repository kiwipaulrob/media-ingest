"""BBC programme matching — the fallback after TMDB.

Two traps encoded here:

* TMDB under-covers BBC-original documentaries (strand episodes, one-offs,
  pre-2000 archive). A TMDB no-match is not evidence of absence — the BBC
  programme site usually has the title, synopsis and episode guide.
* BBC pages are server-rendered enough to parse with stdlib: programme pages
  carry JSON-LD plus <title>/meta description, and episode guides list
  /programmes/<pid> links with n/m numbering. DuckDuckGo's HTML endpoint is
  the search path — no API key, but treat it as flaky and never fail hard.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import tmdb

_PID = re.compile(r"/programmes/([a-z0-9]{8})\b")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_META_DESC = re.compile(
    r'<meta\s+(?:name="description"|property="og:description")\s+content="([^"]*)"',
    re.I,
)
_JSONLD_NAME = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_JSONLD_DESC = re.compile(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"')
_EP_LINK = re.compile(
    r'<a[^>]+href="(/programmes/[a-z0-9]{8})"[^>]*>(.*?)</a>', re.S | re.I
)
_EP_COUNT = re.compile(r"(\d+)\s*/\s*(\d+)")
_TAG = re.compile(r"<[^>]+>")


class BbcError(RuntimeError):
    pass


def _fetch(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "media-ops/1.0 (library matching)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - network shape is not ours
        raise BbcError(f"BBC fetch failed: {str(exc)[:160]}") from exc


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", text))).strip()


def search_programmes(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Programme PIDs for a title via DuckDuckGo site: search. Never raises."""
    try:
        q = urllib.parse.urlencode(
            {"q": f"site:bbc.co.uk/programmes {query}"}
        )
        page = _fetch(f"https://html.duckduckgo.com/html/?{q}")
    except BbcError:
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        re.S | re.I,
    ):
        href, anchor = m.group(1), _clean(m.group(2))
        pm = _PID.search(urllib.parse.unquote(href))
        if not pm or pm.group(1) in seen:
            continue
        seen.add(pm.group(1))
        out.append({"pid": pm.group(1), "title": anchor})
        if len(out) >= limit:
            break
    return out


def programme(pid: str) -> Dict[str, Any]:
    """Title + synopsis for a programme PID. Raises BbcError on failure."""
    page = _fetch(f"https://www.bbc.co.uk/programmes/{pid}")
    title_m = _TITLE.search(page)
    title = _clean(title_m.group(1)).split(" - ")[-1] if title_m else ""
    # Prefer JSON-LD description; fall back to meta description.
    desc_m = _JSONLD_DESC.search(page) or _META_DESC.search(page)
    desc = _clean(desc_m.group(1)) if desc_m else ""
    return {"pid": pid, "title": title, "synopsis": desc[:600]}


def episode_guide(pid: str) -> List[Dict[str, Any]]:
    """Episodes from a programme's episode guide. Raises BbcError."""
    page = _fetch(f"https://www.bbc.co.uk/programmes/{pid}/episodes/guide")
    eps: List[Dict[str, Any]] = []
    for m in _EP_LINK.finditer(page):
        href, inner = m.group(1), m.group(2)
        ep_pid = _PID.search(href).group(1)  # type: ignore[union-attr]
        if ep_pid == pid:
            continue
        text = _clean(inner)
        count = _EP_COUNT.search(text)
        title = re.sub(r"\s+", " ", _EP_COUNT.sub(" ", text)).strip(" -")
        eps.append(
            {
                "pid": ep_pid,
                "title": title,
                "part": int(count.group(1)) if count else None,
                "of": int(count.group(2)) if count else None,
            }
        )
    # De-dupe on pid, keep order.
    seen = set()
    out = []
    for e in eps:
        if e["pid"] not in seen:
            seen.add(e["pid"])
            out.append(e)
    return out


def match(title: str, year: Optional[str] = None) -> Dict[str, Any]:
    """Annotate a title with its BBC candidate. Never writes anything."""
    if not title:
        return {"status": "no-match", "query": "", "year": year}
    hits = search_programmes(title)
    if not hits:
        return {"status": "no-match", "query": title, "year": year}
    try:
        prog = programme(hits[0]["pid"])
    except BbcError as exc:
        return {
            "status": "error",
            "query": title,
            "year": year,
            "error": str(exc)[:120],
        }
    return {
        "status": "candidate",
        "query": title,
        "year": year,
        "candidate": prog,
        "alternates": hits[1:3],
    }


def gate_with_fallback(release: str) -> Dict[str, Any]:
    """TMDB gate first; BBC match only on TMDB no-match. Never writes.

    Ambiguous TMDB hits stay ambiguous — the BBC is a gap-filler, not an
    appeals court.
    """
    first = tmdb.gate(release)
    if first.get("status") != "no-match":
        first["via"] = "tmdb"
        return first
    title, year = tmdb.title_year(release)
    second = match(title, year)
    second["via"] = "bbc"
    second["tmdb_query"] = first.get("query")
    return second
