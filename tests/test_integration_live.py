"""Live smoke tests against the real media host — the part CI cannot do.

Marked `integration` and deselected in CI, where none of these hosts exist. They are the
tests that would have caught the probe contract drift on real data, so they are worth
running from the machine that can reach the homelab:

    python3 -m pytest tests/test_integration_live.py -q -m integration
"""
import pytest

from media_lib import config, probe

pytestmark = pytest.mark.integration


def _host_reachable() -> bool:
    rc, _out = probe.query("true", timeout=25)
    return rc == 0


@pytest.fixture(scope="module")
def live():
    if not _host_reachable():
        pytest.skip("media host not reachable from here")
    return True


def test_probe_matches_the_declared_contract_on_a_real_file(live):
    """The exact check that would have caught the flat-vs-nested mismatch."""
    rc, listing = probe.query(
        f"find {config.MEDIA_VOLUME}/videos -maxdepth 2 -name '*.mkv' | head -1")
    if rc != 0 or not listing.strip():
        pytest.skip("no sample file on the media host")
    path = listing.strip().splitlines()[0]

    info = probe.probe(path)
    assert set(info) == set(probe.ProbeResult.__annotations__)
    assert "format" not in info, "consumers use 'dur', never the raw ffprobe block"
    assert info["dur"] and info["dur"] > 0, f"duration unreadable for {path}"
    assert info["size"] and info["size"] > 0
    assert info["codec"], "codec should be reported for a real video file"


def test_query_returns_a_negative_answer_without_raising(live):
    """A question about something absent is an answer of 1, not an exception."""
    assert probe.query("[ -d /nonexistent-path-for-the-contract-test ]")[0] == 1
