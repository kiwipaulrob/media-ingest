"""Tests for scripts/find_dups.py grouping.

The two rules under test are what keep the report honest: provider-id-first identity
(Metroland 1998 vs Metro-Land 1973 must never group), and the distinct-paths rule
(one multi-version row must never read as a duplicate of itself).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from find_dups import find_duplicate_groups, group_key, norm  # noqa: E402
from classify_dups import display_name, display_year, verdict_for  # noqa: E402


def _row(lib, name, year, path, prov=None):
    return {
        "lib": lib, "Name": name, "ProductionYear": year,
        "path": path, "id": f"{lib}:{name}",
        "ProviderIds": prov or {},
    }


def test_provider_id_wins_over_name():
    """Same normalised title, different films: ids must keep them apart."""
    a = _row("Docos", "Metroland", 1998, "/data/documentaries/Metroland (1998)/x.mkv",
             {"Tmdb": "30950"})
    b = _row("Docos", "Metro-Land", 1973, "/data/documentaries/Metro-Land (1973)/y.mkv",
             {"Tmdb": "378086"})
    assert group_key(a) != group_key(b)
    assert find_duplicate_groups([a, b]) == []


def test_imdb_beats_tmdb():
    a = _row("Docos", "X", 2005, "/data/documentaries/X/a.mkv",
             {"Imdb": "tt0370926", "Tmdb": "111"})
    b = _row("Film Docos", "X", 2005, "/data/filmdocos/X/b.mkv",
             {"Imdb": "tt0370926", "Tmdb": "222"})
    assert group_key(a) == group_key(b) == "imdb:tt0370926"


def test_cross_library_pair_groups():
    rows = [
        _row("Docos", "The Making of Psycho", 2005,
             "/data/documentaries/The Making of Psycho (2005)/p1.mkv", {"Imdb": "tt0370926"}),
        _row("Film Docos", "The Making of Psycho", 2005,
             "/data/filmdocos/The Making of Psycho (2005)/p1.mkv", {"Imdb": "tt0370926"}),
    ]
    dups = find_duplicate_groups(rows)
    assert len(dups) == 1
    assert dups[0]["key"] == "imdb:tt0370926"


def test_single_multi_version_row_is_not_a_duplicate():
    """One row on one path is never a group, however many sources it carries."""
    rows = [_row("Docos", "Taxi to the Dark Side", 2007,
                 "/data/documentaries/Taxi to the Dark Side (2007)/Taxi.mkv",
                 {"Tmdb": "13362"})]
    assert find_duplicate_groups(rows) == []


def test_same_path_twice_is_not_a_duplicate():
    rows = [
        _row("Docos", "Same", 2010, "/data/documentaries/Same/x.mkv"),
        _row("Docos", "Same", 2010, "/data/documentaries/Same/x.mkv"),
    ]
    assert find_duplicate_groups(rows) == []


def test_norm_strips_parens():
    assert norm("Metroland (1998)") == "metroland"


def test_classify_tolerates_both_row_key_shapes(monkeypatch):
    """find_dups writes API-shape rows (Name/ProductionYear); older candidate
    files use lowercase. classify_dups crashed with KeyError on the new shape."""
    assert display_name({"Name": "X", "ProductionYear": 2005}) == "X"
    assert display_name({"name": "X", "year": 2005}) == "X"
    assert display_year({"Name": "X", "ProductionYear": 2005}) == 2005
    assert display_year({"name": "X", "year": 2005}) == 2005
    assert verdict_for({"a": 2, "b": 3}, {"a": 9, "b": 9}) == "HARDLINK (no disk cost)"
    assert verdict_for({"a": 1, "b": 1}, {"a": 9, "b": 9}) == "COPY (same size)"
    assert verdict_for({"a": 1, "b": 1}, {"a": 9, "b": 8}) == "DIFFERENT VERSION/SIZE"
