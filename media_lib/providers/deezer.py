"""Deezer — portraits.

Keyless and reliable (verified 2026-10-20: 'The Chills', 29 albums,
`picture_xl` present). It carries no prose, so it is a portrait source ONLY —
never a biography source.
"""
from __future__ import annotations

import urllib.parse
from typing import List, Optional

from . import cache
from .base import Candidate, Provider, Record

SEARCH = 'https://api.deezer.com/search/artist'


def picture_of(artist: dict) -> Optional[str]:
    """Largest available picture, preferring the explicit XL field."""
    for key in ('picture_xl', 'picture_big', 'picture_medium', 'picture'):
        if artist.get(key):
            return artist[key]
    return None


class DeezerProvider(Provider):
    name = 'deezer'
    min_interval = 0.25
    requires_key = False

    def available(self) -> bool:
        return True

    def search(self, query: str, kind: str = 'artist') -> List[Candidate]:
        q = urllib.parse.urlencode({'q': query, 'limit': 8})
        data = cache.cached(f'deezer-search:{query.lower()}',
                            lambda: cache.get_json(f'{SEARCH}?{q}', self.name,
                                                   self.min_interval))
        out: List[Candidate] = []
        for i, a in enumerate((data or {}).get('data') or []):
            if not a.get('name'):
                continue
            out.append(Candidate(provider=self.name, id=str(a.get('id')),
                                 name=a['name'], kind='artist',
                                 score=max(0.1, 1.0 - i * 0.08),
                                 url=a.get('link'),
                                 extra={'picture': picture_of(a),
                                        'albums': a.get('nb_album')}))
        return out

    def fetch(self, cand: Candidate) -> Optional[Record]:
        rec = Record(provider=self.name, id=cand.id)
        rec.set('name', cand.name)
        rec.set('portrait', cand.extra.get('picture'))
        rec.set('source_url', cand.url)
        rec.set('album_count', cand.extra.get('albums'))
        return rec                        # no prose: deliberately
