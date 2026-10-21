"""Sourcing stage — find releases for wanted titles and queue them.

This replaces four standalone scripts (hunt_classic_albums, queue_classic_albums,
deephunt_classic_albums, hunt_alternatives) that each reimplemented Prowlarr
search, title matching and the qBittorrent/SAB clients. That duplication caused
three real defects in one session:

  1. **Narrow-first search.** Per-title queries ("Classic Albums Carly Simon No
     Secrets") returned ZERO on ten consecutive targets, while one broad query
     ("classic albums") returned 458 postings and matched 9 of 10 in code.
     Indexers do not tokenise the way a long query assumes, so the pool is
     fetched broad and matched here.
  2. **Dotted filenames.** Usenet posts read ``Black.Sabbath.Paranoid...``;
     hand-rolled ``"black sabbath" in title.lower()`` missed three of four
     grabs. Matching now goes through ``matcher``'s normalisers.
  3. **Blind seed trust.** Advertised seeders of 63 and 95 delivered zero bytes
     and zero peers. Advertised counts are a floor for *offering* a pick, never
     evidence it is alive; ``rank`` never lets them outrank a completeness flag.

Two more rules are enforced here rather than in prose:
  * protocol is filtered explicitly (a "usenet" request returned torrents);
  * ``1of2``/``partN`` postings are flagged incomplete so a half-programme is
    never queued as if it were the whole episode.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config, matcher, qb, sab

#: Words that carry no identity in a release name.
_STOP = {'classic', 'albums', 'album', 'the', 'and', 'bbc', 'of', 'a', 'in', 'on',
         'vol', 'part', 'dvd', 'rip', 'hdtv', 'web', 'webrip', 'dl', 'x264', 'x265',
         'h264', 'hevc', 'aac', 'ac3', 'ddp', 'bluray', 'pdtv', 'mvgroup', 'org'}
#: Audio releases masquerade as episodes; they are never video.
_AUDIO = re.compile(r'\b(flac|mp3|m4a|alac|320kbps|320|v0|discography|box ?set|'
                    r'dvda|dvd-audio|cd rip|remastered|deluxe edition)\b', re.I)
_VIDEO_EXT = re.compile(r'\.(mkv|mp4|avi|m4v|ts|mov)$', re.I)
_PART = re.compile(r'(?i)\b(?:part\s*(\d{1,2})|(\d{1,2})\s*of\s*(\d{1,2})|s(\d{1,2})e(\d{1,3}))\b')


# ------------------------------------------------------------------ prowlarr
class SourceError(RuntimeError):
    pass


def _prowlarr(query: str, indexer_ids: Optional[str] = None,
              timeout: int = 90) -> List[Dict[str, Any]]:
    if not config.PROWLARR_URL:
        raise SourceError('PROWLARR_URL is not set')
    params = {'query': query, 'type': 'search', 'limit': 100}
    if indexer_ids:
        params['indexerIds'] = indexer_ids          # comma-separated STRING
    url = f"{config.PROWLARR_URL.rstrip('/')}/api/v1/search?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={'X-Api-Key': config.PROWLARR_KEY})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return json.loads(fh.read() or b'[]')
        except Exception:                            # noqa: BLE001
            if attempt:
                return []
            time.sleep(4)
    return []


def fetch_pool(queries: Optional[Sequence[str]] = None,
               indexer_ids: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Broad sweep, deduped by title. Returns {title: raw result}."""
    pool: Dict[str, Dict[str, Any]] = {}
    for q in (queries or config.SOURCE_POOL_QUERIES):
        for res in _prowlarr(q, indexer_ids=indexer_ids):
            title = (res.get('title') or '').strip()
            if title:
                pool[title] = res
    return pool


# ------------------------------------------------------------------- matching
def norm(text: str) -> str:
    """Normalise for matching — dotted release names must equal spaced queries."""
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def part_info(title: str) -> Optional[Tuple[int, int]]:
    """(part, total) when the release is a multi-part posting, else None.

    ``1of2``/``2 of 2`` give both numbers; a bare ``part 1`` gives part=1 and an
    unknown total (0), which still counts as incomplete.
    """
    m = _PART.search(title or '')
    if not m:
        return None
    if m.group(1):
        return int(m.group(1)), 0
    if m.group(2) and m.group(3):
        return int(m.group(2)), int(m.group(3))
    return None


def is_episode_identity(title: str, season: Optional[int] = None,
                        episode: Optional[int] = None) -> bool:
    """Does this release name carry the wanted SxxEyy marker?"""
    m = _PART.search(title or '')
    if not (season and episode):
        return False
    if not m or not m.group(4):
        return False
    return int(m.group(4)) == int(season) and int(m.group(5)) == int(episode)


def _identity_words(artist: str, album: str) -> List[str]:
    words = [w for w in norm(artist).split() if w not in _STOP and len(w) > 2]
    words += [w for w in norm(album).split() if w not in _STOP and len(w) > 2]
    return words


def matches(title: str, artist: str, album: str) -> bool:
    """Both the artist and at least one distinctive album word must appear.

    Normalised on both sides, so ``Black.Sabbath`` matches ``black sabbath``.
    """
    nt = norm(title)
    if 'classic' not in nt or 'album' not in nt:
        return False
    a = [w for w in norm(artist).split() if w not in _STOP and len(w) > 2]
    alb = [w for w in norm(album).split() if w not in _STOP and len(w) > 2]
    if not a or not alb:
        return False
    if not all(w in nt for w in a[:2]):
        return False
    return any(w in nt for w in alb)


def is_video_posting(res: Dict[str, Any]) -> bool:
    """Is this a video release at all?

    Separators are normalised to spaces first: ``Shpritsz_FLAC_VRTX`` keeps an
    underscore between word characters, so a ``\\bFLAC\\b`` test misses it and
    an album rip is offered as an episode.
    """
    title = res.get('title') or ''
    plain = re.sub(r'[_./-]+', ' ', title)
    if _AUDIO.search(plain):
        return False
    return not title.lower().endswith('.iso')       # ISOs need a mount, not an ingest


def hash_of(res: Dict[str, Any]) -> Optional[str]:
    """40-hex infohash from a magnet, guid or downloadUrl — else None."""
    for key in ('magnetUrl', 'guid'):
        m = re.search(r'btih:([0-9a-fA-F]{40})', res.get(key) or '')
        if m:
            return m.group(1).lower()
    blob = (res.get('guid') or '') + (res.get('downloadUrl') or '')
    m = re.search(r'[0-9a-fA-F]{40}', blob)
    return m.group(0).lower() if m else None


def queue_url(res: Dict[str, Any]) -> Optional[str]:
    """The best URL this result can be queued by."""
    for key in ('magnetUrl', 'guid'):
        val = res.get(key) or ''
        if val.startswith('magnet:'):
            return val
    h = hash_of(res)
    if h:
        return f"magnet:?xt=urn:btih:{h}&dn=" + urllib.parse.quote(res.get('title') or '')
    dl = res.get('downloadUrl') or ''
    if dl:
        sep = '&' if '?' in dl else '?'
        return f'{dl}{sep}apikey={config.PROWLARR_KEY}'
    return None


# -------------------------------------------------------------------- ranking
def _res_rank(title: str) -> int:
    t = (title or '').lower()
    for r, score in (('2160p', 4), ('1080p', 3), ('720p', 2), ('480p', 1)):
        if r in t:
            return score
    return 0


def rank(candidates: Iterable[Dict[str, Any]], protocol: Optional[str] = None,
         min_seeds: Optional[int] = None) -> List[Dict[str, Any]]:
    """Order picks: complete sets first, then resolution, then seeds.

    Seeds deliberately rank BELOW completeness and resolution: advertised
    counts are stale often enough (63 and 95 both delivered nothing) that they
    must never promote an incomplete or SD posting over a complete HD one.
    """
    floor = config.SOURCE_MIN_SEEDS if min_seeds is None else min_seeds
    out: List[Dict[str, Any]] = []
    for c in candidates:
        if protocol and (c.get('protocol') or '') != protocol:
            continue
        seeds = c.get('seeds') or 0
        if c.get('protocol') == 'torrent' and seeds < floor:
            continue
        parts = part_info(c.get('title') or '')
        c = dict(c)
        c['parts'] = parts
        c['complete'] = not (parts and (parts[1] == 0 or parts[0] < parts[1]))
        out.append(c)
    out.sort(key=lambda c: (not c['complete'], -_res_rank(c['title']),
                            -(c.get('seeds') or 0), c.get('gb') or 0))
    return out


def augment_for(pool: Dict[str, Dict[str, Any]], artist: str, album: str,
                queries: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Add per-target postings the broad pool missed.

    The pool and a narrow query fail on DIFFERENT postings: Queen, Deep Purple
    and Iron Maiden are absent from all four pool queries yet appear at once in
    a per-title search. So a pool miss triggers ONE targeted query rather than
    the old three per title.
    """
    for q in (queries or (f'Classic Albums {artist} {album}',)):
        for res in _prowlarr(q):
            title = (res.get('title') or '').strip()
            if title and title not in pool:
                pool[title] = res
    return pool


def candidates_for(pool: Dict[str, Dict[str, Any]], artist: str, album: str,
                   protocol: Optional[str] = None,
                   min_seeds: Optional[int] = None) -> List[Dict[str, Any]]:
    """Ranked candidates from a pool for one wanted title."""
    found = []
    for title, res in pool.items():
        if not matches(title, artist, album) or not is_video_posting(res):
            continue
        if protocol and (res.get('protocol') or '') != protocol:
            continue
        found.append({'title': title, 'seeds': res.get('seeders') or 0,
                      'gb': round((res.get('size') or 0) / 1e9, 2),
                      'grabs': res.get('grabs'), 'age': res.get('age'),
                      'protocol': res.get('protocol'), 'indexer': res.get('indexer'),
                      'hash': hash_of(res), 'url': queue_url(res)})
    return rank(found, protocol=protocol, min_seeds=min_seeds)


# -------------------------------------------------------------------- queueing
def queue(pick: Dict[str, Any], category: str = '') -> Dict[str, Any]:
    """Send one ranked pick to the right downloader."""
    url = pick.get('url')
    if not url:
        return {'ok': False, 'reason': 'no-url', 'title': pick.get('title')}
    try:
        if pick.get('protocol') == 'usenet':
            ids = sab.add_url(url, name=pick.get('title') or '', category=category or 'movies')
            return {'ok': bool(ids), 'kind': 'usenet', 'nzo_ids': ids,
                    'title': pick.get('title')}
        status = qb.add(url, category=category)
        return {'ok': status in (200, 204, 409), 'kind': 'torrent',
                'http': status, 'title': pick.get('title')}
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'reason': f'{type(exc).__name__}: {str(exc)[:120]}',
                'title': pick.get('title')}
