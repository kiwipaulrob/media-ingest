"""A/V sync check: behaviour contracts, no source reading.

The cases that matter are the ones where a broken file would otherwise pass:
audio that starts at a different offset, drift that only shows at the end, a
missing track, and — most of all — a failed probe, which must never read as
"sync ok" because that would hand a broken file to a viewer with a green tick.
"""
from media_lib import avsync


def _streams(video_start=0.0, video_dur=100.0, audio=(0.0, 100.0)):
    rows = [{"index": 0, "codec_type": "video", "codec_name": "h264",
             "start_time": video_start, "duration": video_dur}]
    tracks = [audio] if isinstance(audio, tuple) else audio
    for i, (start, dur) in enumerate(tracks):
        rows.append({"index": i + 1, "codec_type": "audio", "codec_name": "aac",
                     "start_time": start, "duration": dur, "channels": 6})
    return rows


def test_aligned_streams_pass(monkeypatch):
    monkeypatch.setattr(avsync, "streams", lambda p: _streams())
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is True
    assert r["verdict"] == "ok"
    assert "sync ok" in avsync.describe(r)


def test_audio_late_by_a_lot_fails(monkeypatch):
    # 400 ms late is plainly audible — must not pass silently.
    monkeypatch.setattr(avsync, "streams", lambda p: _streams(audio=(0.4, 100.0)))
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert r["verdict"] == "fail"
    assert abs(r["start_delta_ms"] - 400.0) < 1
    assert "starts +400 ms" in avsync.describe(r)


def test_small_offset_warns_but_passes(monkeypatch):
    # 150 ms sits between noticeable and clearly audible: warn, do not block.
    monkeypatch.setattr(avsync, "streams", lambda p: _streams(audio=(0.15, 100.0)))
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is True
    assert r["verdict"] == "warn"


def test_duration_drift_is_detected(monkeypatch):
    # Audio 2 s short: fine at the start, visibly drifting by the credits.
    monkeypatch.setattr(avsync, "streams", lambda p: _streams(audio=(0.0, 98.0)))
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert any("duration differs" in p for p in r["problems"])


def test_missing_audio_is_a_failure(monkeypatch):
    monkeypatch.setattr(avsync, "streams", lambda p: [
        {"index": 0, "codec_type": "video", "codec_name": "h264",
         "start_time": "0", "duration": "10"}])
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert r["verdict"] == "no-audio"


def test_probe_failure_is_not_a_pass(monkeypatch):
    # The important one: an unreadable file must never read as "sync ok".
    monkeypatch.setattr(avsync, "streams", lambda p: [])
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert r["verdict"] == "probe-failed"


def test_audio_tracks_disagreeing_with_each_other(monkeypatch):
    monkeypatch.setattr(avsync, "streams",
                        lambda p: _streams(audio=[(0.0, 100.0), (0.5, 100.0)]))
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert any("disagree" in p for p in r["problems"])


def test_negative_offset_is_reported_with_sign(monkeypatch):
    # Audio EARLY is as much a fault as late, and the sign gives the fix direction.
    monkeypatch.setattr(avsync, "streams", lambda p: _streams(audio=(-0.3, 100.0)))
    r = avsync.check("/x/y.mkv")
    assert r["ok"] is False
    assert abs(r["start_delta_ms"] + 300.0) < 1


def test_missing_start_time_is_unknown_not_aligned(monkeypatch):
    """Coercing an absent start_time to 0.0 made an unprobeable stream read as ok."""
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264",
                "duration": "100"},
               {"index": 1, "codec_type": "audio", "codec_name": "aac", "duration": "100"}]
    monkeypatch.setattr(avsync, "streams", lambda p: streams)
    r = avsync.check("/x/y.mkv")
    assert r["verdict"] == "warn", "an unknown offset must not report as ok"
    assert any("unknown" in w for w in (r.get("warnings") or []))
