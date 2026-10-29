"""Review-3 adoptions: per-hash POSTs, tolerant JSON, episode cache rows."""
import sys

sys.path.insert(0, ".")


def test_post_hashes_one_per_hash(monkeypatch):
    from media_lib import qb
    calls = []

    class FakeOp:
        def open(self, req, timeout=60):
            calls.append(req.data)
            class R:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b""
            return R()
    monkeypatch.setattr(qb, "_opener", lambda: FakeOp())
    assert qb.delete(["a" * 40, "b" * 40]) == 2
    assert len(calls) == 2
    import urllib.parse
    assert all(len(urllib.parse.parse_qs(c.decode())["hashes"][0]) == 40 for c in calls)


def test_sab_tolerant_json():
    import json
    raw = b'{"name": "weird\x01title"}'
    assert json.loads(raw.decode("utf-8", "replace"), strict=False)["name"]


def test_library_row_episode_fields():
    from media_lib import library
    row = library._row({"Id": "x", "Type": "Episode", "Name": "E1",
                        "Path": "/data/docuseries/S/Show [tmdbid-1]/Season 01/E1.mkv",
                        "SeriesName": "S", "ParentIndexNumber": 1, "IndexNumber": 1,
                        "MediaSources": []})
    assert row and row["series"] == "S" and row["season"] == 1 and row["episode"] == 1


def test_nlink_helper_parses():
    from media_lib import place
    import media_lib.probe as probe
    real = probe.on_media
    probe.on_media = lambda inner, timeout=60: "2\n"
    try:
        assert place.remote_nlink("/mnt/zmedia/videos/x.mkv") == 2
    finally:
        probe.on_media = real
