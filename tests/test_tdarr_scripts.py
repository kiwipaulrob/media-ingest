"""Tests for the operational Tdarr scripts.

Each case here corresponds to a bug that actually happened on 23 Oct 2026, so these
are regression guards rather than coverage for its own sake.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


# ---------------------------------------------------------------- open_window
def test_blocks_until_crosses_midnight():
    """Starting at 22:00 for a 05:00 cut-off must open TOMORROW's early hours.

    The first implementation matched only the current day's prefix, so an overnight
    run opened 22-23 and nothing after it.
    """
    from tdarr_kick import blocks_until
    blocks = blocks_until(dt.datetime(2026, 10, 23, 22, 30), 5)   # Friday 22:30
    assert blocks[0] == "Fri:22-23"
    assert "Fri:23-00" in blocks
    assert "Sat:00-01" in blocks
    assert "Sat:04-05" in blocks


def test_blocks_until_same_day_is_bounded():
    from tdarr_kick import blocks_until
    blocks = blocks_until(dt.datetime(2026, 10, 23, 20, 10), 23)
    assert blocks == ["Fri:20-21", "Fri:21-22", "Fri:22-23", "Fri:23-00"]


def test_blocks_until_single_hour():
    from tdarr_kick import blocks_until
    assert blocks_until(dt.datetime(2026, 10, 23, 14, 5), 14) == ["Fri:14-15"]


# ------------------------------------------------------------------ scan scope
def test_library_path_is_the_library_own_folder():
    from tdarr_scan import library_path
    assert library_path("musicdocos") == "/media/videos/musicdocos"
    assert library_path("musicdocos-series") == "/media/videos/musicdocos-series"


def test_library_path_rejects_unknown_and_parent():
    """A parent path indexed 1,266 foreign files once; it must be impossible."""
    from tdarr_scan import library_path
    with pytest.raises(SystemExit):
        library_path("..")
    with pytest.raises(SystemExit):
        library_path("videos")


@pytest.mark.parametrize("text,expected", [
    ("Extracting info:2259/2259 Scanner mem: 138.4MB/2715.8MB", (2259, 2259)),
    ("Extracting info:900/2260 Scanner mem: 105.4MB/2187.7MB", (900, 2260)),
    # enumeration finished, extraction not yet reported: still busy
    ("Found: 5920 Checking:0 Scanner mem: 90.1MB/2121.7MB", (0, 1)),
])
def test_scanner_progress_ignores_the_memory_pair(text, expected):
    """The status text carries TWO 'x/y' pairs; the memory one must not be read."""

    class FakeAPI:
        @staticmethod
        def __call__(path, payload=None):
            return {"raw": text}
    import tdarr_scan
    original = tdarr_scan.api
    tdarr_scan.api = FakeAPI()
    try:
        assert tdarr_scan.scanner_progress("any") == expected
    finally:
        tdarr_scan.api = original


def test_unparseable_status_is_not_reported_as_busy():
    import tdarr_scan

    class FakeAPI:
        @staticmethod
        def __call__(path, payload=None):
            return {"raw": "idle"}
    original = tdarr_scan.api
    tdarr_scan.api = FakeAPI()
    try:
        assert tdarr_scan.scanner_progress("any") is None
        assert tdarr_scan.scanner_busy("any") is False
    finally:
        tdarr_scan.api = original


# ------------------------------------------------------- watch-state keying
def test_key_survives_a_container_change():
    """Tdarr rewrites .avi to .mkv; the key must not change with it."""
    from tdarr_preserve_userdata import key_for
    a = key_for("/data/musicdocos/Foo (2009)/Foo.avi")
    b = key_for("/data/musicdocos/Foo (2009)/Foo.mkv")
    assert a == b == "/data/musicdocos/Foo (2009)/Foo"


def test_key_keeps_parts_distinct():
    from tdarr_preserve_userdata import key_for
    p1 = key_for("/data/musicdocos/Bar/Bar - part1.avi")
    p2 = key_for("/data/musicdocos/Bar/Bar - part2.avi")
    assert p1 != p2


def test_importing_a_tdarr_script_needs_no_api_key(monkeypatch):
    """CI has no /root/.hermes/.env and no TDARR_API_KEY.

    The key was validated at module import, so pytest died during COLLECTION with
    SystemExit — the whole suite, not one test. Deferring it to _key() means importing is
    free and only a real API call requires the credential.
    """
    import importlib.util

    from media_lib import jellyfin

    monkeypatch.delenv("TDARR_API_KEY", raising=False)
    # load_env_file's default argument is bound at definition time, so patching ENV_FILE
    # would not change what it reads — patch the reader itself.
    monkeypatch.setattr(jellyfin, "load_env_file", lambda *a, **k: {})
    for name in ("tdarr_kick", "tdarr_enable", "tdarr_attach_libraries", "tdarr_node_enable"):
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)          # must not raise SystemExit
        assert callable(module._key)
        with pytest.raises(SystemExit):
            module._key()                        # the error is deferred to the call
