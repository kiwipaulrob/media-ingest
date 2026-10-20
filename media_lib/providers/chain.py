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
            DeezerProvider(), WikipediaProvider(), TheAudioDbProvider()]


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


def resolve(name: str, kind: str = 'person',
            providers: Optional[List[Provider]] = None) -> Dict[str, Any]:
    """Search, fetch details, and merge with per-field provenance."""
    providers = providers if providers is not None else default_providers()
    found = search(name, kind, providers)
    by_name = {p.name: p for p in providers}
    records: List[Record] = []
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
            records.append(rec)

    merged = merge(records, PROSE_PRIORITY, PORTRAIT_PRIORITY)
    out = {
        'query': name,
        'kind': kind,
        'fields': merged['fields'],
        'provenance': merged['provenance'],
        'conflicts': merged['conflicts'],
        'sources': [r.to_dict() for r in records],
        'errors': found['errors'],
        'skipped': found['skipped'],
        'bio_len': len(str(merged['fields'].get('biography') or '')),
    }
    return out
