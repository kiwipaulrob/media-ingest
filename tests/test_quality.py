"""The replacement-quality gate.

Written after queueing replacement downloads for content already held at the SAME
quality (24 Oct 2026): a search sorted on seeders picked three PDTV rips with no
resolution tag to "replace" 832p copies we already had. The rule these tests pin is
simple — an unproven release is not an upgrade.
"""
from __future__ import annotations

import pytest

from media_lib import quality


@pytest.mark.parametrize("title,expected", [
    ("Some Doc 2024 1080p WEB-DL x264", 1080),
    ("Gig 2019 720p HDTV x264", 720),
    ("Film 2160p UHD BluRay", 2160),
    ("Old Thing 576p DVDRip", 576),
    ("Weird 4K Remaster", 2160),
])
def test_release_height_reads_resolution_tags(title, expected):
    assert quality.release_height(title) == expected


@pytest.mark.parametrize("title", [
    "BBC Storyville 2013 The Great Hip Hop Hoax PDTV x264",
    "Arena The Brian Epstein Story PDTV XviD",
    "Some Show DVDRip XviD-MEDiAMANiACS",
])
def test_untagged_sd_releases_have_no_height(title):
    assert quality.release_height(title) is None
    assert quality.is_sd_release(title) is True


def test_upgrade_is_the_only_thing_queued():
    v = quality.verdict("Urgh A Music War 1981 720p WEBRip", held_height=464)
    assert v["verdict"] == "upgrade" and v["queue"] is True


def test_same_quality_is_never_queued():
    v = quality.verdict("Doc 2024 1080p WEB-DL", held_height=1080)
    assert v["verdict"] == "same-or-worse"
    assert v["queue"] is False


def test_lower_quality_is_never_queued():
    v = quality.verdict("Doc 720p HDTV", held_height=1080)
    assert v["queue"] is False


def test_untagged_sd_against_a_held_copy_is_not_queued():
    """The exact mistake: a PDTV rip "replacing" an 832p copy it cannot beat."""
    v = quality.verdict("BBC Storyville 2013 The Great Hip Hop Hoax PDTV x264",
                        held_height=832)
    assert v["verdict"] == "unknown"
    assert v["queue"] is False
    assert "cannot prove" in v["reason"]


def test_a_confirmed_gap_is_worth_filling():
    v = quality.verdict("Cinema Perverso 720p WEBRip", held_height=None, held_state="none")
    assert v["verdict"] == "no-local-copy" and v["queue"] is True


def test_an_unmeasurable_held_copy_is_not_a_gap():
    """The second mistake: 'could not probe' was read as 'we hold nothing'."""
    for state in ("unprobeable", "ambiguous"):
        v = quality.verdict("Some Doc 720p WEBRip", held_height=None, held_state=state)
        assert v["verdict"] == "held-unknown", state
        assert v["queue"] is False, state


def test_an_unrelated_release_is_never_a_replacement():
    """A Lone Ranger release was once queued for a music documentary."""
    v = quality.verdict("The.Lone.Ranger.2013.MULTi.1080p.BluRay", held_height=480,
                        held_state="held", wanted_title="Urgh A Music War 1981")
    assert v["verdict"] == "different-title"
    assert v["queue"] is False


def test_title_matching_accepts_the_right_programme():
    assert quality.title_matches("Urgh A Music War 1981 720p WEBRip", "Urgh A Music War 1981")
    assert quality.title_matches("BBC Storyville 2013 The Great Hip Hop Hoax PDTV",
                                 "The Great Hip Hop Hoax")


def test_decide_uses_the_held_copy(monkeypatch):
    monkeypatch.setattr(quality, "held_state_for",
                        lambda term: {"state": "held", "height": 464, "matched": "Film"})
    assert quality.decide("Film 720p WEBRip", "Film")["queue"] is True
    monkeypatch.setattr(quality, "held_state_for",
                        lambda term: {"state": "held", "height": 1080, "matched": "Film"})
    assert quality.decide("Film 720p WEBRip", "Film")["queue"] is False

@pytest.mark.parametrize("candidate,wanted", [
    ("Cooks Country S18E18 Cajun Cooking 1080p WEB", "Cajun Country"),
    ("Stranger Things S02E05 Chapter Five Dig Dug 2160p", "Dig!"),
    ("The.Lone.Ranger.2013.MULTi.1080p.BluRay", "Urgh A Music War 1981"),
])
def test_unrelated_releases_do_not_match(candidate, wanted):
    assert quality.title_matches(candidate, wanted) is False


@pytest.mark.parametrize("candidate,wanted", [
    ("BBC Bob Harris My Nashville 720p HDTV x264 AAC MVGroup.org", "Bob Harris: My Nashville"),
    ("BBC Arena 1998 The Brian Epstein Story 1080p HDTV x264", "The Brian Epstein Story"),
    ("Urgh A Music War 1981 720p WEBRip AVC AAC", "Urgh A Music War 1981"),
])
def test_real_releases_do_match(candidate, wanted):
    assert quality.title_matches(candidate, wanted) is True


def test_a_wrong_year_is_a_different_programme():
    v = quality.verdict("c4 wrestling 2019 05 24 the filth and the fury 720p", 576,
                        wanted_title="The Filth and the Fury", wanted_year=2000)
    assert v["verdict"] == "different-title" and v["queue"] is False


def test_matching_year_still_upgrades():
    v = quality.verdict("BBC Arena 1998 The Brian Epstein Story 1080p", 480,
                        wanted_title="The Brian Epstein Story", wanted_year=1998)
    assert v["queue"] is True


def test_a_trivial_height_gain_is_not_worth_downloading():
    """468p -> 480p is 3%: all the cost, no visible benefit."""
    v = quality.verdict("Charles Bradley Soul of America 2012 480p HDTV", 468,
                        wanted_title="Charles Bradley: Soul of America")
    assert v["verdict"] == "marginal" and v["queue"] is False


def test_an_episode_cannot_stand_in_for_a_film():
    v = quality.verdict("Some Show S01E04 Cajun Country 1080p", 336,
                        wanted_title="Cajun Country")
    assert v["queue"] is False

@pytest.mark.parametrize("cand_year,expect", [
    (1998, True),   # exact
    (1997, True),   # one year earlier
    (1999, True),   # one year later
    (1996, False),  # two years out
    (2001, False),  # three years out
])
def test_year_tolerance_is_one_either_side(cand_year, expect):
    """Production, broadcast and release years legitimately differ by a year."""
    v = quality.verdict(f"Some Doc {cand_year} 1080p HDTV", 480,
                        wanted_title="Some Doc", wanted_year=1998)
    assert v["queue"] is expect


def test_a_release_with_no_year_is_not_rejected_for_it():
    v = quality.verdict("Bob Harris My Nashville 720p HDTV", 360,
                        wanted_title="Bob Harris My Nashville", wanted_year=1977)
    assert v["queue"] is True


def test_add_release_prefers_magnet_over_download_url(monkeypatch):
    """The 401 trap: a bare downloadUrl is accepted by qBittorrent and never downloads."""
    from media_lib import qb
    sent = {}

    def fake_add_verified(url, title, category=""):
        sent['url'] = url
        return {'ok': True, 'http': 200, 'verified': True, 'hash': 'x', 'state': 'dl'}

    monkeypatch.setattr(qb, 'add_verified', fake_add_verified)
    qb.add_release({'title': 'Thing', 'magnetUrl': 'magnet:?xt=urn:btih:' + 'a' * 40,
                    'downloadUrl': 'https://prowlarr/download/1'})
    assert sent['url'].startswith('magnet:'), 'a magnet must win over downloadUrl'

    qb.add_release({'title': 'Thing', 'downloadUrl': 'https://prowlarr/download/2'})
    assert 'apikey=' in sent['url'], 'the fallback must carry the API key, or it 401s'

    out = qb.add_release({'title': 'nothing'})
    assert out['ok'] is False and out['state'] == 'no-url'


def test_episode_slot_markers_match_across_forms():
    from media_lib import quality

    assert quality.title_matches(
        "Louis.Theroux.By.Reason.Of.Insanity.S01E02.720p.HDTV.x264-TASTETV",
        "Louis Theroux - By Reason of Insanity E02 (2015)") is True
    assert quality.title_matches("Show S01E02 720p", "Show S01E03") is False
    # episodic release still cannot stand in for a standalone film
    assert quality.title_matches("Show S01E02 720p", "Show") is False
