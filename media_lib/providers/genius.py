"""Genius — artist prose.

Genius search is keyless (verified 2026-10-20: 10 hits for 'The Chills', first
'The Chills (NZL)'). Artist *descriptions* need a free client token; without
one this provider still contributes candidates and images, which is why it is
not gated on a key.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

from .. import jellyfin
from . import cache
from .base import Candidate, Provider, Record

SEARCH = "https://genius.com/api/search/artist"
ARTIST = "https://api.genius.com/artists/"


def html_to_text(html: str) -> str:
    """Genius descriptions arrive as a DOM dump; flatten to prose."""
    s = re.sub(r"<br\s*/?>", " ", html or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#x27;", "'")
    return re.sub(r"\s+", " ", s).strip()


class GeniusProvider(Provider):
    name = "genius"
    min_interval = 1.0
    requires_key = False  # search works keyless

    def _token(self) -> str:
        return jellyfin.env("GENIUS_API_TOKEN") or ""

    def available(self) -> bool:
        return True

    def search(self, query: str, kind: str = "artist") -> List[Candidate]:
        q = urllib.parse.urlencode({"q": query})
        data = cache.cached(
            f"genius-search:{query.lower()}",
            lambda: cache.get_json(f"{SEARCH}?{q}", self.name, self.min_interval),
        )
        hits = ((data or {}).get("response") or {}).get("sections") or []
        results = (hits[0].get("hits") if hits else []) or []
        out: List[Candidate] = []
        for i, h in enumerate(results):
            r = h.get("result") or {}
            if not r.get("name"):
                continue
            out.append(
                Candidate(
                    provider=self.name,
                    id=str(r.get("id")),
                    name=r["name"],
                    kind="artist",
                    score=max(0.1, 1.0 - i * 0.08),
                    url=r.get("url"),
                    extra={"image": r.get("image_url")},
                )
            )
        return out

    def fetch(self, cand: Candidate) -> Optional[Record]:
        rec = Record(provider=self.name, id=cand.id)
        rec.set("name", cand.name)
        rec.set("source_url", cand.url)
        if cand.extra.get("image"):
            rec.set("portrait", cand.extra["image"])
        token = self._token()
        if not token:
            return rec  # candidates + image only, by design
        headers = {"Authorization": f"Bearer {token}"}
        try:
            data = cache.cached(
                f"genius-artist:{cand.id}",
                lambda: cache.get_json(
                    f"{ARTIST}{cand.id}", self.name, self.min_interval, headers=headers
                ),
            )
        except cache.ProviderError:
            return rec
        artist = ((data or {}).get("response") or {}).get("artist") or {}
        desc = artist.get("description") or {}
        text = html_to_text(desc.get("dom") or "") or (desc.get("plain") or "")
        rec.set("biography", text[:1200] or None)
        if artist.get("image_url"):
            rec.set("portrait", artist["image_url"])
        return rec
