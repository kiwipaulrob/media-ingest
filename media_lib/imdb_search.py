"""Best-effort IMDb-id discovery via a search engine (keyless Bing HTML).

IMDb publishes no API and TVmaze/TMDB do not know every title, so the last
resort is a web search for the IMDb title page. Never raises; returns "" on
any miss. Slow (one search + one redirect follow per candidate) — callers
run it only when every curated source has already failed.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def bing_results(query: str, limit: int = 6) -> List[Tuple[str, str, str]]:
    """(title, cite-url, href) organic results. Pure parse of keyless Bing HTML."""
    try:
        page = _fetch("https://www.bing.com/search?q=" + urllib.parse.quote(query))
    except Exception:  # noqa: BLE001 - search is best effort, silence is a miss
        return []
    out: List[Tuple[str, str, str]] = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', page, re.S):
        block = m.group(0)
        h2 = re.search(r"<h2>.*?</h2>", block, re.S)
        title = re.sub(r"<[^>]+>", "", h2.group(0)).strip() if h2 else "?"
        href_m = re.search(r'<a[^>]+href="([^"]+)"', block)
        href = html.unescape(href_m.group(1)) if href_m else ""
        cite = re.search(r"<cite[^>]*>(.*?)</cite>", block, re.S)
        url = html.unescape(re.sub(r"<[^>]+>", "", cite.group(1)).strip()) if cite else ""
        out.append((html.unescape(title), url, href))
        if len(out) >= limit:
            break
    return out


def _follow(url: str) -> str:
    """Follow redirects, return the landing URL (or ""). Never raises."""
    try:
        with urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor()).open(
                urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.geturl()
    except Exception:  # noqa: BLE001 - best effort
        return ""


def _tt_from_imdb_search_page(imdb_search_url: str) -> str:
    """Follow an IMDb find/search URL to its first title page. Returns tt id or ""."""
    return _tt_of_landing(_follow(imdb_search_url))


def _tt_of_landing(final_url: str) -> str:
    m = re.search(r"/title/(tt\d{7,8})", final_url or "")
    return m.group(1) if m else ""


def _tt_of_ck(href: str) -> str:
    """Bing wraps results in /ck/ links with the target base64'd in `u=`.
    Pure — no follow needed."""
    import base64

    m = re.search(r"[?&]u=([A-Za-z0-9_-]+)", href or "")
    if not m:
        return ""
    # Bing prefixes the payload with a 2-char version tag ("a1").
    payload = m.group(1)[2:]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 - malformed param is a miss
        return ""
    return _tt_of_landing(raw)


def discover_imdb(title: str, year: int = 0) -> Dict[str, str]:
    """Search-engine IMDb discovery. Returns {"imdb_id", "imdb_from"} or {}.

    Strategy: ask Bing for the IMDb title page directly; when only an IMDb
    *search* URL surfaces, follow it (IMDb redirects exact matches) and read
    the tt id off the landing URL. The id is evidence, not proof — the caller
    pins it only after its own title/year check.
    """
    queries = [f"site:imdb.com {title} {year or ''} documentary".strip(),
               f"{title} {year or ''} IMDb".strip()]
    for q in queries:
        for _t, cite, href in bing_results(q):
            if "imdb.com" not in cite:
                continue
            m = re.search(r"/title/(tt\d{7,8})", cite)
            if m:
                return {"imdb_id": m.group(1), "imdb_from": "search"}
            # Cite hides the path ("imdb.com › title"): decode Bing's wrapped
            # target first (pure), follow the link only as a fallback.
            if href:
                tt = _tt_of_ck(href)
                if not tt:
                    url = href if href.startswith("http") else "https://www.bing.com" + href
                    tt = _tt_of_landing(_follow(url))
                if tt:
                    return {"imdb_id": tt, "imdb_from": "search"}
    return {}
