"""nfo builders — pure XML, escaping, sidecar naming."""
import sys

sys.path.insert(0, ".")

from media_lib import nfo  # noqa: E402


def test_episode_nfo_escapes_and_locks():
    xml = nfo.episode_nfo("A <B> & C", "plot & <more>", premiered="2020-01-01",
                          tmdb_id="5", imdb_id="tt1")
    assert "<title>A &lt;B&gt; &amp; C</title>" in xml
    assert "<lockdata>true</lockdata>" in xml
    assert '<uniqueid type="tmdb" default="true">5</uniqueid>' in xml
    assert "<premiered>2020-01-01</premiered>" in xml


def test_movie_nfo_omits_empty_ids():
    xml = nfo.movie_nfo("T", "p", year="1999")
    assert "uniqueid" not in xml
    assert "<year>1999</year>" in xml


def test_sidecar_for_replaces_extension():
    assert nfo.sidecar_for("/v/Show S01E01.mkv") == "/v/Show S01E01.nfo"


def test_write_sidecar_base64_roundtrip(monkeypatch):
    seen = {}

    def fake_on_media(cmd, timeout=300):
        seen["cmd"] = cmd
        return ""

    import media_lib.probe as probe

    monkeypatch.setattr(probe, "on_media", fake_on_media)
    assert nfo.write_sidecar("/v/a.mkv", "<x>ü</x>") is True
    assert "base64 -d > /v/a.nfo" in seen["cmd"]
