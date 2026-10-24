"""Repair-path tests: the failure modes here silently damage media rather than error.

Two rules are load-bearing:
  * a SHORT audio track must be padded, never allowed to trim the picture;
  * a stream-copy trim cannot fix broken timestamps, so it must fall back to a retime.
"""
from media_lib import avsync


def _wire(monkeypatch, v_end, a_end, verdicts, tail=True):
    """Stub the probes so resync() can be driven without touching real media."""
    seen = {"cmds": [], "checks": 0}
    monkeypatch.setattr(avsync, "video_present_at_tail", lambda *a, **k: tail)

    def fake_check(path):
        seen["checks"] += 1
        state = verdicts[min(seen["checks"] - 1, len(verdicts) - 1)]
        return {"verdict": state, "problems": [], "start_delta_ms": 0.0, "ok": state != "fail"}

    monkeypatch.setattr(avsync, "check", fake_check)
    monkeypatch.setattr(avsync, "_derived_duration",
                        lambda path, sel: v_end if sel.startswith("v") else a_end)

    def fake_media(inner, timeout=300):
        seen["cmds"].append(inner)
        return ""

    monkeypatch.setattr(avsync.probe, "on_media", fake_media)
    return seen


def test_truncated_video_is_refused_without_scanning(monkeypatch):
    seen = _wire(monkeypatch, 1244.5, 3209.6, ["fail"], tail=False)
    out = avsync.resync("/x.mkv", "/y.mkv")
    assert out["ok"] is False and out["reason"] == "video-truncated"
    assert out.get("fast_path") is True
    assert seen["cmds"] == [], "a truncated file must not trigger an encode"


def test_short_audio_is_padded_not_trimmed(monkeypatch):
    """Audio 3 s short: padding keeps the picture; -shortest alone would cut it."""
    seen = _wire(monkeypatch, 4419.3, 4416.3, ["fail", "ok"])
    out = avsync.resync("/x.mkv", "/y.mkv")
    assert out["ok"] is True
    cmd = seen["cmds"][0]
    assert "apad" in cmd, "short audio must be padded to the picture"
    assert "-c:v copy" in cmd, "the video must never be re-encoded"


def test_long_audio_trims_then_falls_back_to_retime(monkeypatch):
    """A stream-copy trim leaves broken timestamps alone, so a retime must follow."""
    seen = _wire(monkeypatch, 2738.7, 2739.6, ["fail", "fail", "ok"])
    out = avsync.resync("/x.mkv", "/y.mkv")
    assert len(seen["cmds"]) == 2, "expected trim then retime"
    assert "-c copy" in seen["cmds"][0] and "-shortest" in seen["cmds"][0]
    assert "asetpts" in seen["cmds"][1]
    assert out["method"] == "trim-then-retime"
    assert out["ok"] is True


def test_start_offset_shifts_audio_losslessly(monkeypatch):
    seen = {"cmds": [], "checks": 0}
    monkeypatch.setattr(avsync, "video_present_at_tail", lambda *a, **k: True)
    monkeypatch.setattr(avsync, "_derived_duration", lambda p, sel: 1000.0)

    def fake_check(path):
        seen["checks"] += 1
        state = "fail" if seen["checks"] == 1 else "ok"
        return {"verdict": state, "problems": [], "start_delta_ms": 400.0, "ok": state != "fail"}

    monkeypatch.setattr(avsync, "check", fake_check)
    monkeypatch.setattr(avsync.probe, "on_media",
                        lambda inner, timeout=300: seen["cmds"].append(inner) or "")
    out = avsync.resync("/x.mkv", "/y.mkv")
    cmd = seen["cmds"][0]
    assert "-itsoffset -0.400" in cmd, "a late audio track must be pulled earlier"
    assert "-c copy" in cmd
    # input 1 is the OFFSET copy; mapping 0:a:0 copies the unshifted audio and the repair
    # reports success while changing nothing (measured on real media: still +400 ms).
    assert "-map 1:a:0" in cmd and "-map 0:a:0" not in cmd
    assert out["ok"] is True


def test_every_repair_can_overwrite_a_leftover_temp(monkeypatch):
    """Without -y ffmpeg refuses an existing output, so a retry silently keeps the old file."""
    for v_end, a_end in ((1000.0, 1004.0), (1000.0, 996.0)):
        seen = _wire(monkeypatch, v_end, a_end, ["fail", "ok"])
        avsync.resync("/x.mkv", "/y.mkv")
        assert seen["cmds"], "expected a repair command"
        assert " -y -v error" in seen["cmds"][0], (
            f"repair command must force overwrite: {seen['cmds'][0][:80]}")


def test_damaged_index_is_detected(monkeypatch):
    """Goldwyn: durations agree, frames complete, yet most packets carry no timestamp."""
    monkeypatch.setattr(avsync.probe, "query",
                        lambda inner, timeout=300: (0, "N/A\n" * 53 + "12.5\n" * 7))
    out = avsync.index_health("/x.avi")
    assert out["verdict"] == "unreliable"
    assert out["sampled"] == 60 and out["untimed"] == 53


def test_healthy_index_passes(monkeypatch):
    monkeypatch.setattr(avsync.probe, "query",
                        lambda inner, timeout=300: (0, "\n".join(str(i * 0.033) for i in range(49))))
    out = avsync.index_health("/x.mkv")
    assert out["verdict"] == "ok" and out["ratio"] == 0.0


def test_unreadable_sample_is_unknown_not_ok(monkeypatch):
    """A probe that returns nothing must not be read as a clean bill of health."""
    monkeypatch.setattr(avsync.probe, "query", lambda inner, timeout=300: (0, ""))
    assert avsync.index_health("/x.mkv")["verdict"] == "unknown"
