"""tvmaze pure matchers + series_slot_from_path (no network)."""
import sys

sys.path.insert(0, ".")

from cli.ingest_new import series_slot_from_path  # noqa: E402
from media_lib import tvmaze  # noqa: E402

EPS = [
    {"season": 1, "number": 1, "name": "The Pilot Episode",
     "summary": "<p>A fine start.</p>", "airdate": "2020-01-01"},
    {"season": 1, "number": 2, "name": "Something Else Entirely",
     "summary": None, "airdate": "2020-01-08"},
]


def test_strip_html():
    assert tvmaze.strip_html("<p>A fine start.</p>") == "A fine start."
    assert tvmaze.strip_html(None) == ""


def test_by_number(monkeypatch):
    monkeypatch.setattr(tvmaze, "show_episodes", lambda *a: EPS)
    hit = tvmaze.by_number("Show", 1, 1)
    assert hit["overview"] == "A fine start."
    assert tvmaze.by_number("Show", 9, 9) is None


def test_by_name_prefers_word_overlap(monkeypatch):
    monkeypatch.setattr(tvmaze, "show_episodes", lambda *a: EPS)
    hit = tvmaze.by_name("Show", "pilot episode")
    assert hit["episode_number"] == 1
    assert tvmaze.by_name("Show", "zzzzzz") is None


def test_series_slot_from_path():
    s, se, ep = series_slot_from_path(
        "/data/docuseries/Later... (2000)/Season 57/Later S57E03 Guests.mkv")
    assert s == "Later..."
    assert (se, ep) == (57, 3)
    assert series_slot_from_path("/data/films/Some Film (2020).mkv") == (None, 0, 0)
