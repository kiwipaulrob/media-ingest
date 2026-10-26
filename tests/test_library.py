"""library._lib_for must match on a path boundary, not a string prefix.

/data/movies-extra/x.mkv startswith /data/movies and would corrupt the disk path
into .../Films-extra/... — place.to_disk() was fixed for this exact bug while
_lib_for was missed.
"""
from media_lib import library


def test_registered_path_resolves():
    assert library._lib_for("/data/movies/Chinatown (1974)/x.mkv") == "/data/movies"
    assert library._lib_for("/data/documentaries/Y/y.mkv") == "/data/documentaries"


def test_unregistered_sibling_prefix_does_not_match():
    assert library._lib_for("/data/movies-extra/foo.mkv") is None
    assert library._lib_for("/data/tvshows-archive/bar.mkv") is None


def test_exact_library_root_matches():
    assert library._lib_for("/data/movies") == "/data/movies"


def test_unknown_root_is_none():
    assert library._lib_for("/data/something-else/x.mkv") is None
