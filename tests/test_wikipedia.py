"""wikipedia episode-table parser (pure + fixture, minimal live)."""
import sys

sys.path.insert(0, ".")

from media_lib import wikipedia  # noqa: E402

WIKITEXT = """{{Episode list
 | EpisodeNumber=17
 | Title= Time Warp Wives
 | OriginalAirDate= {{Start date|2008|8|8|df=y}}
 | ShortSummary= Having rejected the independence that comes with being a modern working woman, Joanne is desperate.
 | LineColor= 333333
}}
{{Episode list
 | Title= Unrelated Show
 | ShortSummary= Short.
}}"""


def test_template_parse(monkeypatch):
    monkeypatch.setattr(wikipedia, "_raw_wikitext", lambda *a, **k: WIKITEXT)
    r = wikipedia.episode_overview("X", "Time Warp Wives")
    assert r["overview"].startswith("Having rejected") and r["overview_from"] == "wikipedia:X"


def test_miss_and_short(monkeypatch):
    monkeypatch.setattr(wikipedia, "_raw_wikitext", lambda *a, **k: WIKITEXT)
    assert wikipedia.episode_overview("X", "No Such Episode Here") == {}
    assert wikipedia.episode_overview("X", "Unrelated") == {}


def test_clean():
    assert wikipedia._clean("[[Michael Jackson]]'s [[Thriller (song)|Thriller]]") == \
        "Michael Jackson's Thriller"


def test_resolve_step6(monkeypatch):
    from media_lib import resolve
    import media_lib.tmdb as tmdb
    import media_lib.tvmaze as tvmaze
    import media_lib.bbc as bbc
    monkeypatch.setattr(tmdb, "search", lambda *a: None)
    monkeypatch.setattr(tvmaze, "show_episodes", lambda t: [])
    monkeypatch.setattr(tvmaze, "show_summary", lambda t: "")
    monkeypatch.setattr(tvmaze, "show_externals", lambda t: {})
    monkeypatch.setattr(bbc, "search_programmes", lambda *a, **k: [])
    monkeypatch.setattr(wikipedia, "_raw_wikitext", lambda *a, **k: WIKITEXT)
    r = resolve.resolve_series("Time Warp Wives", wiki_page="X")
    assert r.get("overview_from") == "wikipedia:X" and "wikipedia" in r["sources"]
