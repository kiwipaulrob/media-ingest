"""scan_aspect_flags pure classify (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from scan_aspect_flags import classify, parse_ratio  # noqa: E402


def test_gilbert_signature_flags_narrow():
    v, _ = classify("5:4", 720, 400)
    assert v == "suspect-narrow"


def test_normal_wide_ok():
    assert classify("16:9", 1920, 1080)[0] == "ok"
    assert classify("16:9", 720, 400)[0] == "ok"


def test_legit_anamorphic_ok():
    assert classify("16:9", 720, 576)[0] == "ok"
    assert classify("4:3", 640, 480)[0] == "ok"


def test_anamorphic_band_ok_but_square_flagged_wide_suspect():
    assert classify("16:9", 720, 576)[0] == "ok"
    assert classify("16:9", 640, 480)[0] == "ok"
    assert classify("16:9", 512, 512)[0] == "suspect-wide"


def test_unparseable_unknown():
    assert classify("?", 720, 400)[0] == "unknown"
    assert classify("16:9", 0, 0)[0] == "unknown"


def test_parse_ratio():
    assert abs(parse_ratio("16:9") - 1.7778) < 0.001
    assert parse_ratio("1.85:1") == 1.85
    assert parse_ratio("bogus") is None
