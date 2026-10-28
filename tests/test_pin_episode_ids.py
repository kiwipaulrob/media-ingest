"""pin_episode_ids matching: name-only, never by number, ties refused."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pin_episode_ids import best_match, plan, season_index, tokens  # noqa: E402

INDEX = {
    "british steel": {"id": 101, "name": "British Steel"},
    "never mind bollocks": {"id": 102, "name": "Never Mind the Bollocks"},
    "hysteria": {"id": 103, "name": "Hysteria"},
}


def test_tokens_drop_filler():
    assert tokens("The Part 2 of Episode") == set()
    assert "steel" in tokens("British Steel")


def test_best_match_unique():
    assert best_match("Judas Priest: British Steel", INDEX)["id"] == 101


def test_best_match_no_overlap_returns_none():
    assert best_match("Something Entirely Different", INDEX) is None


def test_tie_between_different_episodes_is_refused():
    # two distinct episodes sharing the only word they have in common
    idx = {"blues one": {"id": 1}, "blues two": {"id": 2}}
    assert best_match("Blues", idx) is None


def test_plan_buckets():
    rows = [
        {"Id": "a", "Name": "British Steel"},
        {"Id": "b", "Name": "Unknown Programme"},
    ]
    p = plan(rows, INDEX)
    assert len(p["matched"]) == 1 and len(p["unmatched"]) == 1


def test_season_index_never_raises(monkeypatch):
    import media_lib.tmdb as t

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(t, "_get", boom)
    assert season_index(1, max_season=1) == {}
