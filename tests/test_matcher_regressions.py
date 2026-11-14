"""Regressions for matcher false-matches (pure, no I/O)."""
import sys

sys.path.insert(0, ".")

from media_lib import matcher  # noqa: E402


def test_hyphen_letter_joins_neighbour():
    assert matcher.tokens("T-Men 1947") == {"tmen"}
    assert matcher.tokens("X-Men Origins") == {"xmen", "origins"}


def test_t_men_is_not_real_men():
    """A 1.0 MATCH here once queued a noir film over a TV episode; only the
    duration gate refused the swap."""
    assert matcher.tokens("T-Men 1947") != matcher.tokens("Real Men - 1")


def test_cinemania_release_group_does_not_survive_clean():
    """'CBGB CINEMANIA' no-matched TMDB; the group names the release, not the film (WAC precedent)."""
    assert matcher.clean_release("CBGB.2013.Blu.Ray.1080p.CINEMANIA.mkv") == "CBGB 2013"
