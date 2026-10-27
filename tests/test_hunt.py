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

    seen = {}
    monkeypatch.setattr(sab, "add_url", lambda u, t, category="tv": seen.setdefault("nzo", "NZO1"))
    out = hunt.queue({"title": "t", "protocol": "usenet", "downloadUrl": "http://x/y"})
    assert out == {"ok": True, "nzo": "NZO1"}
