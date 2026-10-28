"""imdb_search pure parses + verified resolve step (no live network)."""
import sys

sys.path.insert(0, ".")

from media_lib import imdb_search, resolve  # noqa: E402


def test_ck_decode_strips_version_tag():
    href = ("https://www.bing.com/ck/a?!&&p=8b0&u="
            "a1aHR0cHM6Ly93d3cuaW1kYi5jb20vdGl0bGUvdHQyOTQ4Mzcy&ntb=1")
    assert imdb_search._tt_of_ck(href) == "tt2948372"


def test_ck_decode_miss():
    assert imdb_search._tt_of_ck("https://www.bing.com/ck/a?!&&p=x") == ""
    assert imdb_search._tt_of_ck("") == ""


def test_bing_parse_shape():
    page = ('<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=xyz">'
            'Soul (2020) - IMDb</a></h2><cite>https://www.imdb.com › title</cite></li>')
    import media_lib.imdb_search as m
    real = m._fetch
    m._fetch = lambda url, timeout=30: page
    try:
        out = m.bing_results("x")
    finally:
        m._fetch = real
    assert out == [("Soul (2020) - IMDb", "https://www.imdb.com › title",
                    "https://www.bing.com/ck/a?u=xyz")]


def test_resolve_refuses_wrong_film(monkeypatch):
    """Soul Britannia must NOT pin Soul 2020's tt2948372."""
    import media_lib.imdb_search as m
    import media_lib.tvmaze as tvmaze
    monkeypatch.setattr(m, "discover_imdb",
                        lambda t, y=0: {"imdb_id": "tt2948372", "imdb_from": "search"})
    monkeypatch.setattr(tvmaze, "by_imdb", lambda tt: {"name": "Soul"})
    r = resolve.resolve_series("Soul Britannia ZZ", local_nfo=None)
    assert r.get("imdb_id") != "tt2948372"


def test_resolve_accepts_verified(monkeypatch):
    import media_lib.imdb_search as m
    import media_lib.tvmaze as tvmaze
    monkeypatch.setattr(m, "discover_imdb",
                        lambda t, y=0: {"imdb_id": "tt1717474", "imdb_from": "search"})
    monkeypatch.setattr(tvmaze, "by_imdb",
                        lambda tt: {"name": "Syrian School", "premiered": "2010-02-10"})
    monkeypatch.setattr(tvmaze, "show_episodes", lambda t: [])
    monkeypatch.setattr(tvmaze, "show_summary", lambda t: "")
    monkeypatch.setattr(tvmaze, "show_externals", lambda t: {})
    import media_lib.tmdb as tmdb
    monkeypatch.setattr(tmdb, "search", lambda *a: None)
    import media_lib.bbc as bbc
    monkeypatch.setattr(bbc, "search_programmes", lambda *a, **k: [])
    r = resolve.resolve_series("Syrian School ZZ")
    assert r.get("imdb_id") == "tt1717474" and r.get("imdb_from") == "search"
