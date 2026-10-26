"""Tests for media_lib.jellyfin.resolve_library.

`cli.meta plan --library Docos` passed the NAME as ParentId and the server 400d.
Names resolve; ids pass through; anything else raises before a request is made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from media_lib.jellyfin import resolve_library  # noqa: E402


def test_id_passes_through_untouched():
    lib_id = "fd1a953b7d62de4b14994039ca9a7b07"
    assert resolve_library(lib_id, _libs={}) == lib_id


def test_name_resolves_via_mapping():
    libs = {"Docos": "fd1a953b7d62de4b14994039ca9a7b07", "Movies": "aa" * 16}
    assert resolve_library("Docos", _libs=libs) == "fd1a953b7d62de4b14994039ca9a7b07"


def test_unknown_name_raises_before_any_request():
    with pytest.raises(ValueError, match="unknown library"):
        resolve_library("No Such Library", _libs={"Docos": "bb" * 16})
