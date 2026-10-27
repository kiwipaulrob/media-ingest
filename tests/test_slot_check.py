"""slot_check pure: slot parsing, season dirs, collision rule."""
import sys

sys.path.insert(0, ".")

from media_lib import slot_check as sc  # noqa: E402


def test_slot_numbers():
    assert sc.slot_numbers("Show S02E03 Name.mkv") == (2, 3)
    assert sc.slot_numbers("Show 2x03.mkv") == (0, 0)
    assert sc.slot_of("Show S02E03.mkv") == "S02E03"


def test_season_dir_of():
    d = sc.season_dir_of("/mnt/zmedia/videos/DocuSeries/Show (2020)/Season 02/Show S02E03.mkv")
    assert d == "/mnt/zmedia/videos/DocuSeries/Show (2020)/Season 02"
    assert sc.season_dir_of("/mnt/zmedia/videos/Films/Show (2020).mkv") is None


def test_collision_same_slot_different_stem():
    listing = ["Show S02E01 A.mkv", "Show S02E03 B.mkv"]
    assert sc.collision(listing, "Show S02E03 C.mkv") == "Show S02E03 B.mkv"
    assert sc.collision(listing, "Show S02E03 B.mkv") is None
    assert sc.collision(listing, "Show S02E04 C.mkv") is None
    assert sc.collision(listing, "Film Without Slot.mkv") is None
