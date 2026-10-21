"""TMDB — person biographies and portraits.

TMDB is first in both chains because its ids are the ones this library already
stores on rows, so a TMDB-sourced field can be re-verified later.
"""

from __future__ import annotations

import urllib.parse
from typing import List, Optional

from .. import jellyfin
from . import cache
from .base import Candidate, Provider, Record

IMG = "https://image.tmdb.org/t/p/original"


class TmdbProvider(Provider):
    name = "tmdb"
    min_interval = 0.15
    requires_key = True

    def _key(self) -> str:
        key = jellyfin.env("TMDB_API_KEY")
        if not key:
            raise cache.ProviderError("tmdb: TMDB_API_KEY not set")
        return key

    def available(self) -> bool:
        return bool(jellyfin.env("TMDB_API_KEY"))

    def search(self, query: str, kind: str = "person") -> List[Candidate]:
        if kind not in ("person",):
            return []
        q = urllib.parse.urlencode({"query": query, "api_key": self._key()})
        data = cache.cached(
            f"tmdb-search:{query.lower()}",
            lambda: cache.get_json(
                f"https://api.themoviedb.org/3/search/person?{q}",
                self.name,
                self.min_interval,
            ),
        )
        out: List[Candidate] = []
        for i, hit in enumerate((data or {}).get("results") or []):
            out.append(
                Candidate(
                    provider=self.name,
                    id=str(hit.get("id")),
                    name=hit.get("name") or "",
                    kind="person",
                    score=max(0.1, 1.0 - i * 0.1),
                    extra={"department": hit.get("known_for_department")},
                )
            )
        return out

    def fetch(self, cand: Candidate) -> Optional[Record]:
        q = urllib.parse.urlencode({"api_key": self._key()})
        data = cache.cached(
            f"tmdb-person:{cand.id}",
            lambda: cache.get_json(
                f"https://api.themoviedb.org/3/person/{cand.id}?{q}",
                self.name,
                self.min_interval,
            ),
        )
        if not data:
            return None
        rec = Record(provider=self.name, id=cand.id)
        rec.set("name", data.get("name"))
        rec.set("biography", (data.get("biography") or "").strip() or None)
        if data.get("profile_path"):
            rec.set("portrait", IMG + data["profile_path"])
        rec.set("imdb_id", data.get("imdb_id"))
        rec.set("tmdb_id", str(data.get("id") or ""))
        birthday = data.get("birthday") or ""
        if birthday[:4].isdigit():
            rec.set("birth_year", birthday[:4])
        if data.get("place_of_birth"):
            rec.set("place_of_birth", data["place_of_birth"])
        return rec


def normalise(results) -> List[Candidate]:
    """Pure helper kept for tests."""
    return [
        Candidate(
            provider="tmdb",
            id=str(r.get("id")),
            name=r.get("name") or "",
            kind="person",
        )
        for r in results or []
    ]
