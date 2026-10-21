"""TMDB episode lookup must match by NAME, never by number.

Two live incidents: season 4 episode 1 returned the Def Leppard *Hysteria*
episode for a Sex Pistols slot, and season 9 episode 2 returned Tom Petty for
John Lennon. Both wrote a wrong synopsis and a wrong still onto the row.
"""
from media_lib import tmdb


SEASONS = {
    4: [{'episode_number': 1, 'name': 'Def Leppard: Hysteria', 'overview': 'wrong'},
        {'episode_number': 2, 'name': 'The Sex Pistols: Never Mind The Bollocks',
         'overview': 'right'}],
    9: [{'episode_number': 1, 'name': 'Black Sabbath: Paranoid', 'overview': 'wrong'},
        {'episode_number': 2, 'name': 'Tom Petty and the Heartbreakers: Damn the Torpedoes',
         'overview': 'wrong'},
        {'episode_number': 3, 'name': 'John Lennon: Plastic Ono Band', 'overview': 'right'}],
}


def fake_get(path, params):
    season = int(path.rsplit('/', 1)[-1])
    if season not in SEASONS:
        raise RuntimeError('404')
    return {'episodes': SEASONS[season]}


def test_picks_the_named_episode_not_the_numbered_one(monkeypatch):
    monkeypatch.setattr(tmdb, '_get', fake_get)
    ep = tmdb.find_episode_by_name(9048, 'The Sex Pistols: Never Mind the Bollocks')
    assert ep['name'].startswith('The Sex Pistols')
    assert ep['overview'] == 'right'


def test_finds_an_episode_that_the_library_number_would_have_missed(monkeypatch):
    monkeypatch.setattr(tmdb, '_get', fake_get)
    ep = tmdb.find_episode_by_name(9048, 'John Lennon: Plastic Ono Band')
    assert ep['name'] == 'John Lennon: Plastic Ono Band'


def test_returns_none_when_no_episode_names_the_work(monkeypatch):
    monkeypatch.setattr(tmdb, '_get', fake_get)
    assert tmdb.find_episode_by_name(9048, 'Carly Simon: No Secrets') is None
