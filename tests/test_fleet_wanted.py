"""fleet.render (pure) + wanted.load_wanted schema (no network)."""

import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from media_lib import fleet  # noqa: E402
from wanted import load_wanted  # noqa: E402


def test_render_marks_failures():
    rows = [
        {"service": "jellyfin", "ok": True, "detail": "10 libraries"},
        {"service": "prowlarr", "ok": False, "detail": "TimeoutError"},
    ]
    out = fleet.render(rows)
    assert "ok   jellyfin" in out
    assert "FAIL  prowlarr" in out


def test_load_wanted_validates(tmp_path):
    good = tmp_path / "w.json"
    good.write_text(json.dumps({"wanted": [{"query": "q", "keys": ["a"], "note": "n"}]}))
    assert load_wanted(str(good))[0]["episode"] is None
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps([{"query": "no-keys"}]))
    try:
        load_wanted(str(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("bad entry accepted")


def test_load_wanted_episode_tuple(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps([{"query": "q", "keys": ["a"], "episode": [1, 3]}]))
    assert load_wanted(str(p))[0]["episode"] == (1, 3)
