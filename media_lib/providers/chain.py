"""The provider chain: search across providers, resolve with provenance.

Priority order is a product decision, not an accident:

  prose      tmdb > lastfm > genius > wikipedia
  portraits  tmdb > deezer > lastfm > wikipedia (wikimedia thumbnail)

TMDB leads prose because its ids are the ones the library already stores, so a
TMDB-sourced field can be re-verified. Deezer leads portraits after TMDB because
its XL images are real photos rather than Last.fm's shared placeholder.

A failing provider is REPORTED per provider, never silently treated as
'no results' — otherwise a 429 reads as 'this person has no biography'.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Candidate, Provider, Record, merge
from . import cache
from .deezer import DeezerProvider
from .genius import GeniusProvider
from .lastfm import LastfmProvider
from .musicbrainz import MusicBrainzProvider
from .theaudiodb import TheAudioDbProvider
from .tmdb_p import TmdbProvider
from .wikipedia import WikipediaProvider

#: TheAudioDB is NOT trusted for prose. Proven live: querying 'Heavenly' (the
#: Sarah Records indie-pop band) returns exactly ONE exact-name artist — a French
#: power-metal band — so an ambiguity guard cannot catch it and a 2,000-char bio
#: about the wrong band is the result. It is used for banners/portraits only
#: (it is the only provider here that supplies banners), unless a caller opts in.
PROSE_PRIORITY = ['tmdb', 'lastfm', 'genius', 'wikipedia']
PORTRAIT_PRIORITY = ['tmdb', 'deezer', 'lastfm', 'theaudiodb', 'wikipedia']


def default_providers() -> List[Provider]:
    return [TmdbProvider(), LastfmProvider(), GeniusProvider(),
            DeezerProvider(), WikipediaProvider(), TheAudioDbProvider(),
            MusicBrainzProvider()]


def search(name: str, kind: str = 'person',
           providers: Optional[List[Provider]] = None) -> Dict[str, Any]:
    """Candidates from every provider, with per-provider status."""
    providers = providers if providers is not None else default_providers()
    candidates: List[Candidate] = []
    errors: Dict[str, str] = {}
    skipped: List[str] = []
    for p in providers:
        if not p.available():
            skipped.append(p.name)
            continue
        try:
            candidates.extend(p.search(name, kind))
        except cache.ProviderError as exc:
            errors[p.name] = str(exc)[:160]
        except Exception as exc:                     # noqa: BLE001
            errors[p.name] = f'{type(exc).__name__}: {str(exc)[:120]}'
    candidates.sort(key=lambda c: -c.score)
    return {'query': name, 'kind': kind,
            'candidates': [c.to_dict() for c in candidates],
            '_candidates': candidates,
            'errors': errors, 'skipped': skipped}


def _name_ok(query: str, found: str, aliases=None) -> bool:
    """Is `found` plausibly the same subject as `query`?

    Provider search is fuzzy by design, so a record must not be trusted just
    because it came back. Proven live: Deezer answers 'Nick Banks' with
    'Nickelback' — a nickname mismatch that would have put a Nickelback photo on
    the Pulp drummer's row.

    `aliases` are documented alternate names (MusicBrainz), which is how a real
    alias like 'William Hammond Talbot' passes while a lookalike still fails.
    """
    import difflib

    from .. import matcher

    def sq(v):
        return matcher.squash(matcher.clean_release(v))

    q, f = sq(query), sq(found)
    if not q or not f:
        return False
    if q == f:
        return True
    if any(sq(a) == f for a in (aliases or [])):
        return True
    ratio = difflib.SequenceMatcher(None, q, f).ratio()
    if ratio >= 0.86:
        return True
    short, long_ = sorted((q, f), key=len)
    return short in long_ and len(short) / max(1, len(long_)) >= 0.6


def resolve(name: str, kind: str = 'person',
            providers: Optional[List[Provider]] = None) -> Dict[str, Any]:
    """Search, fetch details, and merge with per-field provenance."""
    providers = providers if providers is not None else default_providers()
    found = search(name, kind, providers)
    by_name = {p.name: p for p in providers}
    records: List[Record] = []
    rejected: List[Dict[str, Any]] = []
    # Identity first: MusicBrainz supplies documented aliases and an MBID for
    # the gate below. Identity-only — it never supplies prose.
    identity: Dict[str, Any] = {}
    mb = next((p for p in providers if p.name == 'musicbrainz'), None)
    mb_ident = getattr(mb, 'identity', None)
    if callable(mb_ident):
        try:
            raw = mb_ident(name) or {}
            identity = raw if isinstance(raw, dict) else {}
        except Exception:                       # noqa: BLE001
            identity = {}
    aliases = identity.get('aliases') or []
    # ONE candidate per provider: fetching every fuzzy hit makes unrelated
    # artists (The Kills, The Chicks for 'The Chills') part of the merge and
    # floods the conflict list. Best hit only, highest score first.
    best_per_provider: Dict[str, Candidate] = {}
    for cand in found['_candidates']:
        if cand.provider not in best_per_provider:
            best_per_provider[cand.provider] = cand
    for provider_name, cand in best_per_provider.items():
        provider = by_name.get(provider_name)
        if not provider:
            continue
        try:
            rec = provider.fetch(cand)
        except cache.ProviderError as exc:
            found['errors'][provider.name] = str(exc)[:160]
            continue
        except Exception as exc:                     # noqa: BLE001
            found['errors'][provider.name] = f'{type(exc).__name__}: {str(exc)[:120]}'
            continue
        if rec:
            found_name = str(rec.fields.get('name') or '')
            if not _name_ok(name, found_name, aliases):
                # Fuzzy search answers are not identities. Drop the record
                # rather than let a wrong bio or photo into the merge.
                rejected.append({'provider': provider_name, 'name': found_name,
                                 'reason': 'name-mismatch'})
                continue
            records.append(rec)

    merged = merge(records, PROSE_PRIORITY, PORTRAIT_PRIORITY)
    # A name 'conflict' between the query and a documented alias is not a
    # conflict: 'William Hammond Talbot' IS Billy Talbot.
    conflicts = dict(merged['conflicts'])
    if 'name' in conflicts and aliases:
        values = [v.get('value') for v in conflicts['name']]
        if all(_name_ok(name, str(v), aliases) for v in values if v):
            conflicts.pop('name')
    out = {
        'query': name,
        'kind': kind,
        'fields': merged['fields'],
        'provenance': merged['provenance'],
        'conflicts': conflicts,
        'rejected': rejected,
        'identity': identity,
        'sources': [r.to_dict() for r in records],
        'errors': found['errors'],
        'skipped': found['skipped'],
        'bio_len': len(str(merged['fields'].get('biography') or '')),
    }
    return out
