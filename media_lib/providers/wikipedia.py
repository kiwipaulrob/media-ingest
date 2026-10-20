"""Wikipedia — prose and portraits for anyone with a page.

Two guards earn their place here:

* **exact title match** — a near-miss title is usually a different life
* **disambiguation CONTENT guard** — a disambiguation page's title IS the exact
  name, so the title gate passes it by construction. Only the text reveals it
  ('may refer to' / a bare sense list). 29 of 542 names failed this way in one
  sweep.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

from . import cache
from .base import Candidate, Provider, Record

API = 'https://en.wikipedia.org/w/api.php'
DISAMBIG = re.compile(r'(may refer to|can refer to|may also refer to)', re.I)


def is_disambiguation(text: str) -> bool:
    head = (text or '')[:400]
    return bool(DISAMBIG.search(head))


def titles_match(sent: str, page_title: str) -> bool:
    norm = lambda s: re.sub(r'[\s_]+', ' ', s or '').strip().lower()
    return norm(sent) == norm(page_title)


class WikipediaProvider(Provider):
    name = 'wikipedia'
    min_interval = 2.5            # Wikimedia throttles by IP, not by token
    requires_key = False

    def available(self) -> bool:
        return True

    def _query(self, title: str) -> dict:
        q = urllib.parse.urlencode({
            'action': 'query', 'format': 'json', 'titles': title,
            'prop': 'extracts|pageimages', 'exintro': '1', 'explaintext': '1',
            'pithumbsize': '800', 'redirects': '1'})
        return cache.cached(
            f'wiki:{title.lower()}',
            lambda: cache.get_json(f'{API}?{q}', self.name, self.min_interval))

    def search(self, query: str, kind: str = 'person') -> List[Candidate]:
        data = self._query(query)
        pages = ((data or {}).get('query') or {}).get('pages') or {}
        for p in pages.values():
            if p.get('missing') is not None:
                continue
            title = p.get('title') or ''
            extract = (p.get('extract') or '').strip()
            if not title or not titles_match(query, title):
                continue
            if is_disambiguation(extract):
                return [Candidate(provider=self.name, id=title, name=title,
                                  kind=kind, score=0.2,
                                  extra={'disambiguation': True})]
            return [Candidate(provider=self.name, id=title, name=title,
                              kind=kind, score=0.9,
                              url='https://en.wikipedia.org/wiki/' +
                                  urllib.parse.quote(title.replace(' ', '_')),
                              extra={'thumbnail': (p.get('thumbnail') or {}).get('source')})]
        return []

    def fetch(self, cand: Candidate) -> Optional[Record]:
        if cand.extra.get('disambiguation'):
            return None                        # nothing prose-shaped to take
        data = self._query(cand.id)
        pages = ((data or {}).get('query') or {}).get('pages') or {}
        for p in pages.values():
            extract = (p.get('extract') or '').strip()
            if not extract or is_disambiguation(extract):
                return None
            rec = Record(provider=self.name, id=cand.id)
            rec.set('name', p.get('title'))
            rec.set('biography', extract[:1200])
            rec.set('portrait', (p.get('thumbnail') or {}).get('source'))
            rec.set('source_url', 'https://en.wikipedia.org/wiki/' +
                    urllib.parse.quote((p.get('title') or '').replace(' ', '_')))
            return rec
        return None
