"""Contract tests against CAPTURED payloads, not hand-written stubs.

Every other test in this suite stubs the remote boundary (298 monkeypatches), which means a
producer and a consumer can agree with the stubs and still disagree with reality. That is
exactly how `probe.probe()`'s flat dict came to be read as raw ffprobe JSON in
completeness.py: every test passed, and production refused every flat file.

These fixtures were recorded from the live systems. They are the shapes the code must meet.
"""
import json
from pathlib import Path

from media_lib import probe

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_probe_returns_exactly_the_declared_keys(monkeypatch):
    """If probe() gains or loses a field, ProbeResult must change in the same commit.

    A consumer reading an undeclared key is a bug the type checker can only catch if the
    declaration is honest about the real payload.
    """
    raw = (FIXTURES / "ffprobe_mkv.json").read_text()
    monkeypatch.setattr(probe, "on_media", lambda *a, **k: raw)
    result = probe.probe("/x/y.mkv")
    assert set(result) == set(probe.ProbeResult.__annotations__), (
        f"probe() returned {sorted(result)}, ProbeResult declares "
        f"{sorted(probe.ProbeResult.__annotations__)}")


def test_probe_exposes_duration_and_size_but_no_raw_format_block(monkeypatch):
    """The specific mismatch that broke ingest.

    ffprobe returns {"format": {"duration": ...}}; probe() deliberately flattens it to
    "dur". A consumer asking for info["format"] must get nothing — the contract says so.
    """
    raw = (FIXTURES / "ffprobe_mkv.json").read_text()
    monkeypatch.setattr(probe, "on_media", lambda *a, **k: raw)
    result = probe.probe("/x/y.mkv")
    assert "format" not in result, "probe() must stay flat; consumers use 'dur'/'size'"
    assert result["dur"] and result["dur"] > 0
    assert result["size"] and result["size"] > 0


def test_captured_probe_result_matches_the_live_shape():
    """The recorded fixture and a fresh flatten must not drift apart."""
    recorded = _fixture("probe_result.json")
    assert set(recorded) == set(probe.ProbeResult.__annotations__)
    assert recorded["dur"] > 0 and recorded["codec"]


def test_qb_torrent_payload_has_every_field_consumers_read():
    """qb.torrents() feeds the hunt, the status report and the ledger.

    Consumers read these keys; a qBittorrent upgrade that renamed one would silently turn
    every 'no seeders' decision into a default value.
    """
    torrent = _fixture("qb_torrent.json")
    required = {"hash", "name", "state", "progress", "size", "category", "save_path",
                "content_path", "num_seeds"}
    missing = required - set(torrent)
    assert not missing, f"captured torrent payload is missing {sorted(missing)}"


def test_jellyfin_item_payload_has_every_field_consumers_read():
    """Item rows drive placement tracking, dead-row detection and watch state."""
    shape = _fixture("jellyfin_item_shape.json")
    required = {"Id", "Name", "Path", "RunTimeTicks", "Type"}
    missing = required - set(shape["keys"])
    assert not missing, f"captured Jellyfin item is missing {sorted(missing)}"
    assert shape["shape"]["Path"] == "str", "Path must stay a single string"
