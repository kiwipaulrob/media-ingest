"""hunt.plan — fixtures, no network."""

import sys

sys.path.insert(0, ".")

from media_lib import hunt  # noqa: E402

ROWS = [
    {"title": "BBC.The.Show.S01E01.720p", "protocol": "torrent", "seeders": 5, "size": 1},
    {"title": "BBC The Show S01E02 1080p", "protocol": "torrent", "seeders": 0, "size": 2},
    {"title": "The.Show.S01E01.NZB", "protocol": "usenet", "grabs": 3, "downloadUrl": "http://x/y"},
    {"title": "Unrelated Cooking E01", "protocol": "torrent", "seeders": 99, "size": 1},
]


def test_episode_of():
    assert hunt.episode_of("Show S01E02") == (1, 2)
    assert hunt.episode_of("Show 2x03") == (2, 3)
    assert hunt.episode_of("No Numbers Here") is None


def test_title_keys_normalization():
    # dotted and spaced forms produce the same tokens — the old bug.
    assert hunt.title_keys("BBC.The.Show.S01E01") == hunt.title_keys("BBC The Show S01E01")
    assert "s01e01" in hunt.title_keys("Show S01E01")


def test_plan_filters_and_ranks():
    out = hunt.plan(ROWS, keys=["show"], protocol="torrent")
    assert [r["title"] for r in out] == ["BBC.The.Show.S01E01.720p"]
    out = hunt.plan(ROWS, keys=["show"], episode=(1, 1))
    assert [r["title"] for r in out] == ["BBC.The.Show.S01E01.720p", "The.Show.S01E01.NZB"]


def test_plan_usenet_ignores_seeder_floor():
    out = hunt.plan(ROWS, keys=["show"], protocol="usenet")
    assert len(out) == 1 and out[0]["protocol"] == "usenet"


def test_queue_refuses_urlless():
    assert hunt.queue({"title": "x", "protocol": "usenet"})["ok"] is False
    assert hunt.queue({"title": "x", "protocol": "torrent"})["ok"] is False


def test_queue_usenet_calls_sab(monkeypatch):
    import media_lib.sab as sab
    from media_lib import source as _source

    seen = {}
    monkeypatch.setattr(sab, "add_url", lambda u, t, category="tv": seen.setdefault("nzo", "NZO1"))
    monkeypatch.setattr(_source, "guard_pick", lambda pick, wanted=None,
                        wanted_year=None: {"ok": True, "verdict": "no-local-copy"})
    out = hunt.queue({"title": "t", "protocol": "usenet", "downloadUrl": "http://x/y"})
    assert out == {"ok": True, "nzo": "NZO1"}


def test_queue_refuses_a_held_copy_without_adding(monkeypatch):
    """A pick the library already holds never reaches SAB or qB."""
    from media_lib import quality

    monkeypatch.setattr(quality, "held_state_for",
                        lambda term: {"state": "held", "height": 720,
                                      "matched": "The Show"})
    out = hunt.queue({"title": "The.Show.S01E02.1080p", "protocol": "usenet",
                      "downloadUrl": "http://x/y"}, wanted="The Show")
    assert out["ok"] is False
    assert str(out.get("error", "")).startswith("gate-refused:")


def test_post_age_days_prefers_tracker_minutes():
    assert hunt.post_age_days({"ageMinutes": 2880}) == 2
    assert hunt.post_age_days({"age": 3}) == 3
    assert hunt.post_age_days({}) is None
    assert hunt.post_age_days({"ageMinutes": "junk"}) is None


def test_rank_flags_stale_usenet_posts():
    old = {"title": "Ancient.Post.2018.NZB", "protocol": "usenet",
           "grabs": 65, "downloadUrl": "http://x/y", "ageMinutes": 3000000}
    new = {"title": "Fresh.Post.NZB", "protocol": "usenet",
           "grabs": 1, "downloadUrl": "http://x/z", "ageMinutes": 60}
    tor = {"title": "Show.S01E01.720p", "protocol": "torrent",
           "seeders": 5, "size": 1}
    out = hunt.plan([old, new, tor])
    by_title = {r["title"]: r for r in out}
    assert by_title["Ancient.Post.2018.NZB"]["stale_post"] is True
    assert by_title["Ancient.Post.2018.NZB"]["age_days"] == 2083
    assert by_title["Fresh.Post.NZB"]["stale_post"] is False
    assert "stale_post" not in by_title["Show.S01E01.720p"]
