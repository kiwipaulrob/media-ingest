"""identify_series / identify_movie — stubbed Jellyfin, no network."""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import identify_movie as imov  # noqa: E402
import identify_series as iser  # noqa: E402


def _wire(monkeypatch, rows, candidates, readback):
    import media_lib.jellyfin as jf

    monkeypatch.setattr(
        jf, "get_json",
        lambda path, params=None, **kw: (
            [{"Name": "Docos Series", "ItemId": "LIB1"},
             {"Name": "Docos", "ItemId": "LIB2"},
             {"Name": "Film Docos", "ItemId": "LIB3"}]
            if path == "/Library/VirtualFolders"
            else ({"Items": rows} if params and "Fields" in str(params) and "Path" in str(params) else readback)
        ),
    )
    monkeypatch.setattr(jf, "remote_search", lambda *a, **k: candidates)
    seen = {}
    monkeypatch.setattr(jf, "apply_identification", lambda i, b, **k: seen.setdefault("code", 204))
    monkeypatch.setattr(jf, "refresh_item", lambda i, **k: seen.setdefault("ref", i))
    monkeypatch.setattr(jf, "user_id", lambda: "U1")
    monkeypatch.setattr("identify_series.time.sleep", lambda s: None)
    monkeypatch.setattr("identify_movie.time.sleep", lambda s: None)
    return seen


SERIES_ROWS = [{"Id": "S1", "Path": "/data/docuseries/Demo (2020) [tmdbid-1]/"}]
CANDS = [{"Name": "Demo", "ProductionYear": 2020, "ProviderIds": {"Tmdb": "1"}}]
READ = {"Name": "Demo", "ProductionYear": 2020,
        "ProviderIds": {"Tmdb": "1", "Imdb": "tt1"},
        "Overview": "x" * 100, "ImageTags": {"Primary": "a"}}


def test_series_happy_path(monkeypatch):
    _wire(monkeypatch, SERIES_ROWS, CANDS, READ)
    out = iser.identify_series("Docos Series", "tmdbid-1", "Demo", 2020, "1", settle=0)
    assert out["ok"] is True and out["apply"] == 204
    assert out["providers"] == ["Imdb", "Tmdb"]


def test_series_missing_row(monkeypatch):
    _wire(monkeypatch, [], CANDS, READ)
    out = iser.identify_series("Docos Series", "nope", "Demo", 2020, "1", settle=0)
    assert out["ok"] is False and "not found" in out["error"]


def test_series_no_candidate(monkeypatch):
    _wire(monkeypatch, SERIES_ROWS, [{"Name": "Other", "ProviderIds": {}}], READ)
    out = iser.identify_series("Docos Series", "tmdbid-1", "Demo", 2020, "1", settle=0)
    assert out["ok"] is False and "candidates" in out


def test_series_readback_mismatch_fails(monkeypatch):
    bad = dict(READ, ProviderIds={"Tmdb": "999"})
    _wire(monkeypatch, SERIES_ROWS, CANDS, bad)
    out = iser.identify_series("Docos Series", "tmdbid-1", "Demo", 2020, "1", settle=0)
    assert out["ok"] is False


def test_movie_happy_path(monkeypatch):
    rows = [{"Id": "M1", "Path": "/data/documentaries/Demo (2020)/Demo.mkv"}]
    _wire(monkeypatch, rows, CANDS, READ)
    out = imov.identify_movie("Docos", "Demo (2020)", "Demo", 2020, "1", settle=0)
    assert out["ok"] is True and out["providers"] == ["Imdb", "Tmdb"]


def test_movie_main_returns_1_on_failure(monkeypatch):
    _wire(monkeypatch, [], CANDS, READ)
    assert iser.main(["--library", "X", "--frag", "nope", "--title", "T",
                      "--year", "2020", "--tmdb-id", "1", "--settle", "0"]) == 1
