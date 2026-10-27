"""MusicBrainz — identity only. Never prose.

MB answers the question the other providers cannot: *which* entity is this?
It supplies an MBID (the only cross-provider join key), a country, a type and a
disambiguation line ('New Zealand guitarist/bassist/engineer'), plus documented
aliases ('William Hammond Talbot' for Billy Talbot).

Keyless, but strict: ~1 request/second and a real User-Agent are required, so
the shared throttle is the load-bearing part. No biography, ever — a provider
that returns prose here would tempt the merge into trusting it.
"""

from __future__ import annotations

import urllib.parse
from typing import Dict, List, Optional

from .. import matcher
from . import cache
from .base import Candidate, Provider, Record

API = "https://musicbrainz.org/ws/2"


class MusicBrainzProvider(Provider):
    name = "musicbrainz"
    min_interval = 1.1  # MB asks for 1 req/s; stay just under it
    requires_key = False

    def available(self) -> bool:
        return True

    def _search_raw(self, query: str) -> dict:
        q = urllib.parse.urlencode(
            {"query": f'artist:"{query}"', "fmt": "json", "limit": 5}
        )
        return cache.cached(
            f"mb-search:{query.lower()}",
            lambda: cache.get_json(f"{API}/artist/?{q}", self.name, self.min_interval),
        )

    def search(self, query: str, kind: str = "artist") -> List[Candidate]:
        data = self._search_raw(query)
        out: List[Candidate] = []
        q = matcher.squash(matcher.clean_release(query))
        for a in (data or {}).get("artists") or []:
            name = a.get("name") or ""
            if not name:
                continue
            exact = matcher.squash(matcher.clean_release(name)) == q
            out.append(
                Candidate(
                    provider=self.name,
                    id=a.get("id") or "",
                    name=name,
                    kind="artist",
                    score=1.0 if exact else 0.4,
                    extra={
                        "country": a.get("country"),
                        "type": a.get("type"),
                        "disambiguation": a.get("disambiguation"),
                        "score": a.get("score"),
                    },
                )
            )
        out.sort(key=lambda c: -c.score)
        return out

    def fetch(self, cand: Candidate) -> Optional[Record]:
        """Aliases and identity fields. Deliberately sets NO biography."""
        if not cand.id:
            return None
        q = urllib.parse.urlencode({"inc": "aliases+url-rels", "fmt": "json"})
        data = cache.cached(
            f"mb-artist:{cand.id}",
            lambda: cache.get_json(
                f"{API}/artist/{cand.id}?{q}", self.name, self.min_interval
            ),
        )
        if not data:
            return None
        rec = Record(provider=self.name, id=cand.id)
        rec.set("name", data.get("name"))
        rec.set("mbid", data.get("id"))
        rec.set("country", data.get("country"))
        rec.set("artist_type", data.get("type"))
        rec.set("disambiguation", data.get("disambiguation"))
        aliases = [a.get("name") for a in data.get("aliases") or [] if a.get("name")]
        rec.set("aliases", aliases)
        # Review-P3.4: editor-mapped Wikidata/Wikipedia/Discogs/IMDb URLs bypass
        # same-name search collisions on Wikipedia entirely.
        urls = {}
        for rel in data.get("relations") or []:
            tgt = rel.get("url") or {}
            u = tgt.get("resource")
            if u:
                urls[str(rel.get("type") or "other")] = u
        if urls:
            rec.set("urls", urls)
        life = data.get("life-span") or {}
        if (life.get("begin") or "")[:4].isdigit():
            rec.set("birth_year", life["begin"][:4])
        return rec

    def identity(self, query: str) -> Dict[str, object]:
        """Best exact-name artist plus its aliases, for the identity gate."""
        cands = [c for c in self.search(query) if c.score >= 1.0]
        if not cands:
            return {}
        rec = self.fetch(cands[0])
        if not rec:
            return {}
        return {
            "mbid": rec.fields.get("mbid"),
            "name": rec.fields.get("name"),
            "country": rec.fields.get("country"),
            "type": rec.fields.get("artist_type"),
            "disambiguation": rec.fields.get("disambiguation"),
            "aliases": rec.fields.get("aliases") or [],
        }
