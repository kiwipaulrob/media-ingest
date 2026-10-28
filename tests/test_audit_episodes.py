"""audit_episodes pure flagging + summarising (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from audit_episodes import episode_flags, summarise  # noqa: E402

REAL = "/data/docuseries/Show (2020)/Season 01/Show S01E01.mkv"


def test_clean_row():
    row = {"Path": REAL, "ProviderIds": {"Tmdb": "1"}, "Overview": "x" * 60,
           "ImageTags": {"Primary": "a"}}
    assert episode_flags(row) == []


def test_imdb_only_is_identified():
    assert episode_flags({"Path": REAL, "ProviderIds": {"Imdb": "tt1"},
                          "Overview": "x" * 60, "ImageTags": {"Primary": "a"}}) == []


def test_no_still_reported_separately():
    f = episode_flags({"Path": REAL, "ProviderIds": {"Tmdb": "1"},
                       "Overview": "x" * 60})
    assert f == ["no-still"]


def test_virtual_slot_is_not_a_gap():
    """Jellyfin's empty TMDB placeholder rows must not read as missing media."""
    assert episode_flags({"ProviderIds": {}, "Overview": "", "Path": None}) == ["virtual"]
    assert episode_flags({"ProviderIds": {}, "Overview": ""}) == ["virtual"]


def test_summarise_combos_and_series():
    rows = [
        {"Path": REAL, "SeriesName": "A", "ProviderIds": {}, "Overview": ""},
        {"Path": REAL, "SeriesName": "A", "ProviderIds": {"Tmdb": "1"},
         "Overview": "x" * 60, "ImageTags": {"Primary": "p"}},
        {"SeriesName": "A", "ProviderIds": {}, "Overview": ""},  # virtual slot
    ]
    s = summarise(rows)
    assert s["total"] == 3
    assert s["combos"]["no-ids+no-ov+no-still"] == 1
    assert s["combos"]["clean"] == 1
    assert s["combos"]["virtual"] == 1
    assert s["worst"][0][0] == "A" and s["worst"][0][1] == 1
