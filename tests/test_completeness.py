"""Completeness gate: a source missing parts must never be placed or encoded.

Both cases below are real, found in the library on 24 Oct 2026. `place.py` gates the
COPY (size, probe, decode) — none of which can tell that the source was already
incomplete when it arrived.
"""
from __future__ import annotations

from media_lib import completeness


def _fake_remote(monkeypatch, listing: str, probe_lines: str = ""):
    """Stub the two shapes of remote call the module makes."""
    def fake(script, timeout=900):
        if "ls -1" in script or "case" in script:
            return listing
        return probe_lines
    monkeypatch.setattr(completeness, "_remote", fake)


def test_missing_vob_in_the_middle_is_refused(monkeypatch):
    """Gil Scott-Heron: VTS_01_1, _2, _4 — a hole of ~17 minutes."""
    rows = "\n".join([
        f"VTS_01_1.VOB {1_073_741_824} 953.7",
        f"VTS_01_2.VOB {1_073_741_824} 939.8",
        f"VTS_01_4.VOB {869_117_952} 779.4",
    ])
    _fake_remote(monkeypatch, rows)
    problems = completeness.vob_chain_problems("/x/VIDEO_TS")
    assert any("incomplete" in p and "missing [3]" in p for p in problems), problems


def test_contiguous_chain_with_sane_bitrates_passes(monkeypatch):
    rows = "\n".join([
        f"VTS_01_1.VOB {1_073_709_056} 2283.5",
        f"VTS_01_2.VOB {1_073_709_056} 1970.4",
    ])
    _fake_remote(monkeypatch, rows)
    assert completeness.vob_chain_problems("/x/VIDEO_TS") == []


def test_stalled_timestamps_are_flagged(monkeypatch):
    """Moog: 1 GiB file reporting 55s works out at 155 Mbps — impossible for DVD."""
    rows = f"VTS_01_1.VOB {1_073_741_824} 55.2"
    _fake_remote(monkeypatch, rows)
    problems = completeness.vob_chain_problems("/x/VIDEO_TS")
    assert any("stalled timestamps" in p for p in problems), problems


def test_unreadable_duration_is_flagged(monkeypatch):
    rows = f"VTS_01_1.VOB {1_073_741_824} 0"
    _fake_remote(monkeypatch, rows)
    assert any("duration unreadable" in p
               for p in completeness.vob_chain_problems("/x/VIDEO_TS"))


def test_short_duration_against_expected_is_flagged(monkeypatch):
    """44.5 min of VOBs against a 62 min film is the shape of a missing segment."""
    monkeypatch.setattr(completeness, "_remote", lambda s, timeout=900: "2673.0")
    problems = completeness._duration_gap([], "/x/VIDEO_TS", 62 * 60)
    assert problems and "parts are missing" in problems[0]


def test_gap_total_is_suppressed_when_a_hole_is_already_reported():
    """One clear reason beats two overlapping ones."""
    assert completeness._duration_gap(["hole"], "/x/VIDEO_TS", 62 * 60) == []
