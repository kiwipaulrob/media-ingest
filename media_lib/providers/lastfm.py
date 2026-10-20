"""Last.fm — artist prose and images.

The highest-yield prose source for obscure musicians: it carries a
`bio.summary` where TMDB and Wikipedia hold empty shells. Image URLs are
frequently Last.fm's shared placeholder, so a portrait from here is only
trustworthy once its bytes are checked for a known stub hash.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

from .. import jellyfin
from . import cache
from .base import Candidate, Provider, Record

API = 'https://ws.audioscrobbler.com/2.0/'

#: Last.fm serves this image for artists with no artwork. It is not a portrait.
PLACEHOLDER_HASH = '2a96cbd8b46e442fc41c2b86b821562f'


def strip_html(s: str) -> str:
    s = re.sub(r'<[^>]+>', '', s or '')
    s = s.split('Read more on Last.fm')[0]
    return re.sub(r'\s+', ' ', s).strip()


def best_image(images) -> Optional[str]:
    """Largest non-placeholder image URL, or None."""
    urls = [i.get('#text') for i in images or [] if i.get('#text')]
    for url in reversed(urls):                 # list runs smallest -> largest
        if PLACEHOLDER_HASH not in url:
            return url
    return None


class LastfmProvider(Provider):
    name = 'lastfm'
    min_interval = 0.35
    requires_key = True

    def _key(self) -> str:
        key = jellyfin.env('LASTFM_API_KEY')
        if not key:
            raise cache.ProviderError('lastfm: LASTFM_API_KEY not set')
        return key

    def available(self) -> bool:
        return bool(jellyfin.env('LASTFM_API_KEY'))

    def _get(self, artist: str) -> dict:
        q = urllib.parse.urlencode({'method': 'artist.getinfo', 'artist': artist,
                                    'api_key': self._key(), 'format': 'json'})
        return cache.cached(f'lastfm:{artist.lower()}',
                            lambda: cache.get_json(f'{API}?{q}', self.name,
                                                   self.min_interval))

    def search(self, query: str, kind: str = 'artist') -> List[Candidate]:
        try:
            data = self._get(query)
        except cache.ProviderError:
            return []
        artist = (data or {}).get('artist') or {}
        if not artist.get('name'):
            return []
        return [Candidate(provider=self.name, id=artist['name'],
                          name=artist['name'], kind='artist', score=1.0,
                          url=artist.get('url'))]

    def fetch(self, cand: Candidate) -> Optional[Record]:
        data = self._get(cand.id)
        artist = (data or {}).get('artist') or {}
        if not artist:
            return None
        rec = Record(provider=self.name, id=cand.id)
        rec.set('name', artist.get('name'))
        rec.set('biography', strip_html((artist.get('bio') or {}).get('summary')))
        rec.set('portrait', best_image(artist.get('image')))
        rec.set('source_url', artist.get('url'))
        stats = artist.get('stats') or {}
        if stats.get('listeners'):
            rec.set('listeners', stats['listeners'])
        tags = [t.get('name') for t in (artist.get('tags') or {}).get('tag') or []
                if t.get('name')]
        rec.set('genres', tags[:5] or None)
        return rec
