"""prune_lateral_dupes pure verdicts (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from prune_lateral_dupes import release_height, verdict  # noqa: E402

HELD = {"t-men": (1472, "/data/a"), "real men": (704, "/data/b"),
        "the asphalt jungle": (1480, "/data/c")}


def test_lateral_dupe_prunes():
    action, why = verdict("T-Men (1947) [BluRay] [1080p] [YTS AM]", HELD)
    assert action == "prune"


def test_upgrade_skips():
    action, why = verdict("Real Men (2020) 2160p WEB-DL", HELD)
    assert action == "skip" and "upgrade" in why.lower()


def test_no_match_skips():
    assert verdict("Unknown Film (2020) 1080p BluRay", HELD)[0] == "skip"


def test_multi_file_always_skips():
    assert verdict("D O A (1949) Upscale 1080p", HELD) == (
        "skip", "multi-file, needs review")


def test_release_height():
    assert release_height("Film 1080p BluRay") == 1080
    assert release_height("Film DVDRip") == 0
