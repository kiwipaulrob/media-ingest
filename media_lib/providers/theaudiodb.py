"""TheAudioDB — artist prose, thumbnails and banners.

Keyless access works today with their shared public test key `123` (the older
`2` returns 404). A private key now needs their Patreon tier, so this provider
falls back to the shared key rather than blocking on registration.

**Same-name collisions are severe here.** Querying 'Heavenly' (the Sarah Records
indie-pop band) returns a power-metal band with a 2,000-character biography.
Writing that would repeat the 'Welsh darts player' incident. This adapter
therefore REFUSES to return prose when the query matches more than one artist
exactly: ambiguity is reported, never resolved by guessing.
"""

from __future__ import annotations

import urllib.parse
from typing import List, Optional

from .. import jellyfin
from . import cache
from .base import Candidate, Provider, Record

API = "https://www.theaudiodb.com/api/v1/json"
PUBLIC_KEY = "123"  # documented shared test key; works without signup


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


class TheAudioDbProvider(Provider):
    name = "theaudiodb"
    min_interval = 0.4
    requires_key = False

    def _key(self) -> str:
        return jellyfin.env("THEAUDIODB_API_KEY") or PUBLIC_KEY

    def available(self) -> bool:
        return True

    def _search(self, query: str) -> dict:
        q = urllib.parse.urlencode({"s": query})
        return cache.cached(
            f"tadb:{query.lower()}",
            lambda: cache.get_json(
                f"{API}/{self._key()}/search.php?{q}", self.name, self.min_interval
            ),
        )

    def search(self, query: str, kind: str = "artist") -> List[Candidate]:
        data = self._search(query)
        artists = (data or {}).get("artists") or []
        exact = [a for a in artists if _norm(a.get("strArtist")) == _norm(query)]
        if len(exact) > 1:
            # Refuse to choose. Surfaced as a single candidate carrying the
            # ambiguity so the caller reports it instead of taking a bio.
            return [
                Candidate(
                    provider=self.name,
                    id="",
                    name=query,
                    kind="artist",
                    score=0.0,
                    extra={
                        "ambiguous": len(exact),
                        "candidates": [a.get("strArtist") for a in exact],
                    },
                )
            ]
        if not exact:
            return []
        a = exact[0]
        return [
            Candidate(
                provider=self.name,
                id=str(a.get("idArtist") or _norm(query)),
                name=a.get("strArtist"),
                kind="artist",
                score=0.95,
                url=a.get("strWebsite"),
                extra={"mbid": a.get("strMusicBrainzID")},
            )
        ]

    def fetch(self, cand: Candidate) -> Optional[Record]:
        if cand.extra.get("ambiguous"):
            return None
        data = self._search(cand.name)
        artists = (data or {}).get("artists") or []
        exact = [a for a in artists if _norm(a.get("strArtist")) == _norm(cand.name)]
        if len(exact) != 1:
            return None
        a = exact[0]
        rec = Record(provider=self.name, id=str(a.get("idArtist") or cand.id))
        rec.set("name", a.get("strArtist"))
        rec.set("biography", (a.get("strBiography") or "").strip() or None)
        rec.set("portrait", a.get("strArtistThumb"))
        rec.set("banner", a.get("strBanner"))
        rec.set("mbid", a.get("strMusicBrainzID"))
        rec.set(
            "genres", [g for g in (a.get("strGenre"), a.get("strStyle")) if g] or None
        )
        if (a.get("intFormedYear") or "").isdigit():
            rec.set("formed_year", a["intFormedYear"])
        rec.set("source_url", a.get("strWebsite"))
        return rec
