"""multipart_to_series._norm — punctuation must not defeat provider match."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import multipart_to_series as mts  # noqa: E402


def test_norm_squashes_punctuation():
    assert mts._norm("Oh! You Pretty Things: The Story") == "ohyouprettythingsthestory"
    assert "ohyouprettythings" in mts._norm("Oh! You Pretty Things: The Story of British Music and Fashion")
