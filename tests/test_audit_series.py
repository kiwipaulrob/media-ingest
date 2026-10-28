"""audit_series.flags — pure flagging (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from audit_series import flags  # noqa: E402


def test_flags_all_present_is_clean():
    row = {"ProviderIds": {"Tmdb": "1", "Imdb": "tt1"}, "Overview": "x" * 60,
           "ImageTags": {"Primary": "a"}}
    assert flags(row) == []


def test_flags_each_gap():
    assert flags({}) == ["no-tmdb", "no-imdb", "ov0c", "no-img"]
    assert flags({"ProviderIds": {"Tmdb": "1", "Imdb": "t"},
                  "Overview": "x" * 46, "ImageTags": {"Primary": "a"}}) == ["ov46c"]
