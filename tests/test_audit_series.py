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
    assert flags({}) == ["no-ids", "ov0c", "no-img"]
    assert flags({"ProviderIds": {"Tmdb": "1", "Imdb": "t"},
                  "Overview": "x" * 46, "ImageTags": {"Primary": "a"}}) == ["ov46c"]


def test_imdb_alone_satisfies_identity():
    """UK strands with no TMDB series entry are identified, not gaps."""
    row = {"ProviderIds": {"Imdb": "tt2200247"}, "Overview": "x" * 83,
           "ImageTags": {"Primary": "a"}}
    assert flags(row) == []


def test_tmdb_alone_satisfies_identity():
    row = {"ProviderIds": {"Tmdb": "55508"}, "Overview": "x" * 118,
           "ImageTags": {"Primary": "a"}}
    assert flags(row) == []
