"""scan_series_shapes — pure-function tests (episode parsing, grouping)."""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from scan_series_shapes import episode_no, strip_episode_markers  # noqa: E402


def test_episode_no_shapes():
    assert episode_no("Show S01E02") == (1, 2)
    assert episode_no("Show 3x04") == (3, 4)
    assert episode_no("Show ep05") == (1, 5)
    assert episode_no("Show.e01") == (1, 1)
    assert episode_no("Show part 2") == (1, 2)
    assert episode_no("Show 2of5") == (2, 5)
    assert episode_no("Show pt2") == (1, 2)
    assert episode_no("Just a Film 2009") is None
    assert episode_no("Grade 8 Readers") is None


def test_strip_markers_groups_parts():
    a = strip_episode_markers("The Genius Of Photography ep04.WnA")
    b = strip_episode_markers("The Genius of Photography ep02.WnA")
    assert a == b and len(a) > 5


def test_scan_groups_files(tmp_path, monkeypatch):
    from scan_series_shapes import scan

    monkeypatch.setattr("scan_series_shapes.tmdb.search", lambda *a, **k: None)

    (tmp_path / "Show S01E01.mkv").write_bytes(b"x")
    (tmp_path / "Show S01E02.mkv").write_bytes(b"x")
    (tmp_path / "Lone Film 2020.mkv").write_bytes(b"x")
    groups = scan(str(tmp_path))
    assert len(groups) == 1
    assert groups[0]["files"] == 2
    assert groups[0]["episodes"] == [(1, 1), (1, 2)]
