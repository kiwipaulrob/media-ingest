"""Tests for scripts/concat_parts.py — the two-part-film joiner.

Each case is a regression guard for a real failure: the year-vs-part sort silently
reversed a conversion, and the duration gate is the only thing that stopped a 7-minute
fragment replacing 176 minutes of film.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from concat_parts import durations_match, part_no  # noqa: E402


def test_part_no_uses_part_marker_not_year():
    """'Movie (2007) - part2.mkv' is part 2, not part 2007.

    The first implementation sorted on the first number in the name, which picked the
    year and silently ordered (and would have joined) parts backwards.
    """
    assert part_no("Some Film (2007) - part2.mkv") == 2
    assert part_no("Some Film (2007) - part1.mkv") == 1
    parts = ["Some Film (2007) - part2.mkv", "Some Film (2007) - part1.mkv"]
    assert sorted(parts, key=part_no) == [
        "Some Film (2007) - part1.mkv",
        "Some Film (2007) - part2.mkv",
    ]


def test_part_no_roman_numerals():
    assert part_no("Ken Burns' America - Thomas Jefferson I.avi") == 1
    assert part_no("Ken Burns' America - Thomas Jefferson II.avi") == 2


def test_part_no_n_of_m():
    assert part_no("Show 1of5 Machines.mkv") == 1
    assert part_no("Show 5of5 Biology.mkv") == 5


@pytest.mark.parametrize(
    ("out_min", "total_min", "expected"),
    [
        (175.9, 175.9, True),     # exact
        (174.0, 175.9, True),     # within 2%
        (177.5, 175.9, True),     # within 2% over
        (170.0, 175.9, False),    # 3.4% short
        (7.3, 175.9, False),      # the TJ failure: 4% of the film
        (7.6, 183.2, False),      # the re-encode failure
        (0.0, 175.9, False),      # empty output
        (50.0, 0.0, False),       # unmeasurable parts: must not pass
    ],
)
def test_durations_match(out_min, total_min, expected):
    assert durations_match(out_min, total_min) is expected
