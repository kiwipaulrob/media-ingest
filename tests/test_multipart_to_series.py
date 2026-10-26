"""Tests for scripts/multipart_to_series.py pure helpers.

The script used to execute on import (network + file moves + sleeps at module
level), so none of this was testable. The body now lives in main(); what remains
importable is pinned here. Each case is a real incident.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_import_has_no_side_effects():
    """Importing must not touch the network or the filesystem: it used to run
    the whole conversion (moves + 95 s sleep) as a side effect of import."""
    if "multipart_to_series" in sys.modules:
        del sys.modules["multipart_to_series"]
    import multipart_to_series as m

    assert callable(m.main)
    assert callable(m.series_library_id)


def test_series_library_id_uses_current_name():
    """ids["Documentary Series"] KeyErrored after the rename to Docos Series —
    after moving the files and sleeping 95 s."""
    from multipart_to_series import series_library_id

    ids = {"Docos Series": "abc123", "Movies": "def456"}
    assert series_library_id(ids, "Docos Series") == "abc123"
    with pytest.raises(ValueError, match="Docos Series"):
        series_library_id({"Movies": "def456"}, "Docos Series")


def test_part_no_prefers_part_marker_over_year():
    from multipart_to_series import _part_no

    assert _part_no("Some Film (2007) - part2.mkv") == 2
    assert _part_no("Some Film (2007) - part1.mkv") == 1


def test_part_no_reads_roman_numerals():
    """'Thomas Jefferson I.avi' has no digits: every part scored 0 and the join
    order was whatever find(1) returned."""
    from multipart_to_series import _part_no

    assert _part_no("Ken Burns' America - Thomas Jefferson I.avi") == 1
    assert _part_no("Ken Burns' America - Thomas Jefferson II.avi") == 2
    assert _part_no("Mark Twain Part IV.avi") == 4


def test_part_no_ignores_title_roman():
    """A trailing X is a title (Malcolm X), not part 10."""
    from multipart_to_series import _part_no

    assert _part_no("Malcolm X.mkv") == 0
    assert _part_no("Malcolm X (1992).mkv") == 1992  # year fallback, not part 10
