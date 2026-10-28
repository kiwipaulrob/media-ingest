"""resolve_series chain order + never-raise (stubbed sources)."""
import sys

sys.path.insert(0, ".")

from media_lib import resolve  # noqa: E402
from media_lib import bbc, tmdb, tvmaze  # noqa: E402


def _stub(monkeypatch, tmdb_hit=None, maze_eps=(), bbc_hits=(), nfo=None):
    monkeypatch.setattr(tmdb, "search", lambda *a, **k: tmdb_hit)
    monkeypatch.setattr(tmdb, "_get", lambda *a, **k: {})
    monkeypatch.setattr(tvmaze, "show_episodes", lambda *a, **k: list(maze_eps))
    monkeypatch.setattr(bbc, "search_programmes", lambda *a, **k: list(bbc_hits))
    return nfo


def test_tmdb_first_bbc_fallback(monkeypatch):
    _stub(monkeypatch, tmdb_hit={"id": 1, "overview": "TMDB overview here."})
    out = resolve.resolve_series("Show")
    assert out["sources"] == ["tmdb"]
    assert out["overview_from"] == "tmdb"
    assert resolve.missing_fields(out) == ["imdb_id", "episodes"]


def test_chain_reaches_bbc_and_nfo(monkeypatch):
    _stub(monkeypatch, tmdb_hit=None, maze_eps=[],
          bbc_hits=[{"pid": "b0000001", "title": "Show"}])
    monkeypatch.setattr(bbc, "programme",
                        lambda *a, **k: {"synopsis": "BBC synopsis here.", "image": ""})
    out = resolve.resolve_series("Show", local_nfo={"imdb_id": "tt1", "plot": ""})
    assert out["sources"] == ["bbc", "nfo"]
    assert out["overview_from"] == "bbc:b0000001"
    assert out["imdb_id"] == "tt1"


def test_dead_source_never_stops_chain(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(tmdb, "search", boom)
    monkeypatch.setattr(tvmaze, "show_episodes", boom)
    monkeypatch.setattr(bbc, "search_programmes", boom)
    out = resolve.resolve_series("Show")
    assert out["sources"] == []
    assert resolve.missing_fields(out) == ["tmdb_id", "imdb_id", "overview", "episodes"]


def test_tvmaze_externals_discover_imdb(monkeypatch):
    import media_lib.tvmaze as tvmaze
    from media_lib import resolve

    def fake_get_json(url, params=None, timeout=20):
        if "singlesearch" in url:
            return {"externals": {"imdb": "tt1717474", "tvdb": 1},
                    "summary": "<p>x</p>"}
        return {"results": []} if "search" in url else []
    monkeypatch.setattr(tvmaze, "_get_json", fake_get_json)
    ext = tvmaze.show_externals("Syrian School")
    assert ext["imdb"] == "tt1717474"
    r = resolve.resolve_series("Syrian School NC")
    assert isinstance(r, dict) and "sources" in r
