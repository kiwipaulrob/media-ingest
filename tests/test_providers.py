"""Provider chain tests — pure logic, no network.

The two rules worth protecting here are:
  * a failing provider is REPORTED, never silently downgraded to 'no results'
  * every merged field records which provider supplied it
"""
import pytest

from media_lib.providers import cache, chain
from media_lib.providers.base import Candidate, Provider, Record, merge
from media_lib.providers.deezer import picture_of
from media_lib.providers.genius import html_to_text
from media_lib.providers.lastfm import best_image, strip_html
from media_lib.providers.wikipedia import is_disambiguation, titles_match


# ------------------------------------------------------------------- merge
def _rec(provider, **fields):
    r = Record(provider=provider, id='x')
    for k, v in fields.items():
        r.set(k, v)
    return r


def test_prose_follows_priority_order():
    out = merge([_rec('wikipedia', biography='wiki bio'),
                 _rec('tmdb', biography='tmdb bio')],
                chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert out['fields']['biography'] == 'tmdb bio'
    assert out['provenance']['biography'] == 'tmdb'


def test_prose_falls_to_next_provider_when_the_first_is_empty():
    out = merge([_rec('tmdb'),
                 _rec('lastfm', biography='lastfm bio')],
                chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert out['fields']['biography'] == 'lastfm bio'
    assert out['provenance']['biography'] == 'lastfm'


def test_portrait_priority_prefers_deezer_over_lastfm():
    out = merge([_rec('lastfm', portrait='lastfm.jpg'),
                 _rec('deezer', portrait='deezer.jpg')],
                chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert out['fields']['portrait'] == 'deezer.jpg'
    assert out['provenance']['portrait'] == 'deezer'


def test_conflicting_years_are_surfaced_not_hidden():
    out = merge([_rec('tmdb', year='1963'),
                 _rec('wikipedia', year='1964')],
                chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert 'year' in out['conflicts']
    assert len(out['conflicts']['year']) == 2


# ------------------------------------------------------- provider normalisers
def test_lastfm_strips_html_and_read_more():
    raw = '<a href="x">Artist</a> is a band. <b>Bold</b>. Read more on Last.fm'
    assert strip_html(raw) == 'Artist is a band. Bold.'


def test_lastfm_placeholder_image_is_rejected():
    images = [{'#text': 'https://img/2a96cbd8b46e442fc41c2b86b821562f.png'}]
    assert best_image(images) is None


def test_lastfm_picks_the_largest_real_image():
    images = [{'#text': 'https://img/small.jpg'},
              {'#text': 'https://img/large.jpg'}]
    assert best_image(images) == 'https://img/large.jpg'


def test_genius_html_flattens_to_prose():
    assert html_to_text('<p>Hello<br/>world &amp; more</p>') == 'Hello world & more'


def test_deezer_prefers_xl_picture():
    assert picture_of({'picture': 'a.jpg', 'picture_xl': 'xl.jpg'}) == 'xl.jpg'
    assert picture_of({}) is None


def test_wikipedia_title_gate_rejects_near_misses():
    assert titles_match('Chris Rodley', 'Chris Rodley')
    assert not titles_match('Chris Rodley', 'Chris Rodley (disambiguation)')
    assert not titles_match('Martin Philips', 'Martin Phillipps')


def test_wikipedia_disambiguation_content_guard():
    assert is_disambiguation('<name> may refer to: a Welsh darts player')
    assert not is_disambiguation('Christopher Alexander Knox is a New Zealand musician')


# -------------------------------------------------------------- chain behaviour
class _Boom(Provider):
    name = 'boom'
    requires_key = False

    def search(self, query, kind='person'):
        raise cache.ProviderError('boom: HTTP 429')

    def fetch(self, cand):
        raise cache.ProviderError('boom: HTTP 429')


class _Quiet(Provider):
    name = 'quiet'
    requires_key = False

    def search(self, query, kind='person'):
        return [Candidate(provider='quiet', id='1', name=query, score=0.5)]

    def fetch(self, cand):
        return _rec('quiet', name=cand.name, biography='quiet bio')


class _Keyed(Provider):
    name = 'keyed'
    requires_key = True

    def available(self):
        return False

    def search(self, query, kind='person'):
        raise AssertionError('should never be called when unavailable')

    def fetch(self, cand):
        raise AssertionError('should never be called when unavailable')


def test_failing_provider_is_reported_not_treated_as_empty():
    out = chain.search('Anyone', providers=[_Boom(), _Quiet()])
    assert out['errors'].get('boom', '').startswith('boom:')
    assert [c['provider'] for c in out['candidates']] == ['quiet']


def test_unavailable_provider_is_skipped_not_errored():
    out = chain.search('Anyone', providers=[_Keyed(), _Quiet()])
    assert 'keyed' in out['skipped']
    assert 'keyed' not in out['errors']


def test_resolve_returns_provenance_and_bio_len():
    out = chain.resolve('Anyone', providers=[_Quiet()])
    assert out['fields']['biography'] == 'quiet bio'
    assert out['provenance']['biography'] == 'quiet'
    assert out['bio_len'] == len('quiet bio')


def test_resolve_never_writes_anything():
    # read-only contract: no state files created by resolve
    before = set(cache.CACHE_DIR.glob('*')) if cache.CACHE_DIR.exists() else set()
    chain.resolve('Anyone', providers=[_Quiet()])
    after = set(cache.CACHE_DIR.glob('*')) if cache.CACHE_DIR.exists() else set()
    assert after == before


def test_metric_fields_are_not_reported_as_conflicts():
    a = _rec('lastfm', listeners='100', biography='x')
    b = _rec('deezer', album_count='29')
    out = merge([a, b], chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert 'listeners' not in out['conflicts']
    assert 'album_count' not in out['conflicts']


def test_source_url_belongs_to_the_prose_provider():
    tmdb = _rec('tmdb', biography='tmdb bio', source_url='https://tmdb/x')
    lastfm = _rec('lastfm', source_url='https://lastfm/y')
    out = merge([tmdb, lastfm], chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert out['provenance']['source_url'] == 'tmdb'
    assert out['fields']['source_url'] == 'https://tmdb/x'


# ------------------------------------------------------------- TheAudioDB
def test_theaudiodb_refuses_when_two_artists_share_the_exact_name(monkeypatch):
    from media_lib.providers import theaudiodb as tad

    p = tad.TheAudioDbProvider()
    monkeypatch.setattr(p, '_search', lambda q: {'artists': [
        {'strArtist': 'Heavenly', 'strGenre': 'Power Metal', 'strBiography': 'metal'},
        {'strArtist': 'Heavenly', 'strGenre': 'Indie', 'strBiography': 'indie'},
    ]})
    cands = p.search('Heavenly')
    assert len(cands) == 1
    assert cands[0].extra['ambiguous'] == 2
    # And it must NOT fetch a biography for an ambiguous name.
    assert p.fetch(cands[0]) is None


def test_theaudiodb_accepts_a_single_exact_match(monkeypatch):
    from media_lib.providers import theaudiodb as tad

    p = tad.TheAudioDbProvider()
    monkeypatch.setattr(p, '_search', lambda q: {'artists': [
        {'strArtist': 'The Chills', 'strBiography': 'Dunedin band',
         'strArtistThumb': 'thumb.jpg', 'strMusicBrainzID': 'mb-1',
         'strGenre': 'Indie', 'intFormedYear': '1980'},
    ]})
    cands = p.search('The Chills')
    assert len(cands) == 1 and not cands[0].extra.get('ambiguous')
    rec = p.fetch(cands[0])
    assert rec.fields['biography'] == 'Dunedin band'
    assert rec.fields['formed_year'] == '1980'


def test_theaudiodb_ignores_fuzzy_non_exact_hits(monkeypatch):
    from media_lib.providers import theaudiodb as tad

    p = tad.TheAudioDbProvider()
    monkeypatch.setattr(p, '_search', lambda q: {'artists': [
        {'strArtist': 'Heavenly Bodies', 'strBiography': 'not it'},
    ]})
    assert p.search('Heavenly') == []


def test_parenthetical_variant_is_not_a_name_conflict():
    out = merge([_rec('lastfm', name='The Chills'),
                 _rec('genius', name='The Chills (NZL)')],
                chain.PROSE_PRIORITY, chain.PORTRAIT_PRIORITY)
    assert 'name' not in out['conflicts']


def test_theaudiodb_is_never_a_default_prose_source():
    # Proven live: 'Heavenly' resolves to a power-metal band with a 2,000-char
    # bio and no ambiguity signal. Never trust it for prose.
    assert 'theaudiodb' not in chain.PROSE_PRIORITY


def test_resolve_fetches_one_candidate_per_provider():
    class _Many(Provider):
        name = 'many'
        requires_key = False

        def search(self, query, kind='person'):
            return [Candidate(provider='many', id=str(i), name=f'{query} {i}',
                              score=1.0 - i * 0.1) for i in range(4)]

        def fetch(self, cand):
            return _rec('many', biography='bio')

    out = chain.resolve('Anyone', providers=[_Many()])
    assert len(out['sources']) == 1
