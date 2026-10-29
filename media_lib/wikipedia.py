"""Wikipedia episode-table overviews (keyless, no API token).

Proven on Channel 4's First Cut: TMDB covered the wrong years, TVmaze had
titles with 0-char overviews, and the Wikipedia series page carried a full
episode table with a description cell per episode. TVDB would be richer but
needs an API key this install does not hold.

Strategy: fetch the show page wikitext (action=raw), find the quoted episode
title row, take the description cell from the following row. Returns "" on
any miss — never raises.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Dict


def _raw_wikitext(page: str, timeout: int = 30) -> str:
    url = ("https://en.wikipedia.org/w/index.php?title="
           + urllib.parse.quote(page.replace(" ", "_")) + "&action=raw")
    req = urllib.request.Request(url, headers={"User-Agent": "media-ops/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def _clean(cell: str) -> str:
    """Strip links, refs, quotes and markup from a table cell. Pure."""
    cell = re.sub(r"<ref[^/]*/?>.*?(</ref>)?", "", cell)
    cell = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\[\[([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\[https?://[^\s\]]*([^\]]*)\]", r"\1", cell)
    cell = re.sub(r"'''?", "", cell)
    cell = cell.replace("&nbsp;", " ").replace("&ndash;", "-")
    return re.sub(r"\s+", " ", cell).strip(' |"')


def episode_overview(page: str, episode: str) -> Dict[str, str]:
    """ShortSummary for `episode` on Wikipedia `page`. {} on miss.

    Handles both `{{Episode list | Title= .. | ShortSummary= ..}}` templates
    (First Cut) and plain wikitable rows with a description cell after the
    title row.
    """
    try:
        text = _raw_wikitext(page)
    except Exception:  # noqa: BLE001 - best effort, silence is a miss
        return {}
    want = re.sub(r"[^a-z0-9]+", " ", episode.lower()).strip()
    # Templates nest ({{Start date|..}} inside), so a lazy }} match stops
    # early — walk brace depth from each {{Episode list instead.
    bodies = []
    for m in re.finditer(r"\{\{[Ee]pisode list", text):
        depth, i = 0, m.start()
        while i < len(text):
            if text.startswith("{{", i):
                depth += 1
                i += 2
            elif text.startswith("}}", i):
                depth -= 1
                i += 2
                if depth == 0:
                    break
            else:
                i += 1
        bodies.append(text[m.end():i])
    for body in bodies:
        title = re.search(r"\|\s*[Tt]itle\s*=\s*(.+)", body)
        summ = re.search(r"\|\s*[Ss]hort[Ss]ummary\s*=\s*(.+?)(?=\n\s*\||\n\s*\}\})",
                         body, re.S)
        if not title or not summ:
            continue
        have = re.sub(r"[^a-z0-9]+", " ", _clean(title.group(1)).lower()).strip()
        desc = _clean(summ.group(1))
        if want and len(want) >= 6 and want in have and len(desc) > 60:
            return {"overview": desc, "overview_from": f"wikipedia:{page}"}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        have = re.sub(r"[^a-z0-9]+", " ", _clean(line).lower()).strip()
        if want and want in have and len(want) >= 6:
            for j in range(i + 1, min(i + 4, len(lines))):
                desc = _clean(lines[j])
                if len(desc) > 60 and "directed by" not in desc.lower()[:40]:
                    return {"overview": desc, "overview_from": f"wikipedia:{page}"}
    return {}
