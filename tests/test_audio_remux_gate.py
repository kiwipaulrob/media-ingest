"""Audio-remux output gate (pure)."""
import sys

sys.path.insert(0, ".")


def gate(src, out):
    """(ok, reasons) — video codec unchanged, AAC stereo first, DTS kept,
    durations within 2s. Pure; dicts shaped like probe results."""
    bad = []
    if out.get("vcodec") != src.get("vcodec"):
        bad.append(f"video changed {src.get('vcodec')}->{out.get('vcodec')}")
    aud = out.get("audio") or []
    if not aud or aud[0].get("codec") != "aac" or aud[0].get("ch") != 2:
        bad.append(f"first audio not aac-stereo: {aud[:1]}")
    if "dts" in [a.get("codec") for a in src.get("audio") or []] and \
       "dts" not in [a.get("codec") for a in aud]:
        bad.append("source DTS not kept")
    if src.get("dur") and out.get("dur") and abs(src["dur"] - out["dur"]) > 2:
        bad.append(f"duration drift {src['dur']} vs {out['dur']}")
    return (not bad, bad)


def test_gate():
    src = {"vcodec": "hevc", "audio": [{"codec": "dts", "ch": 6}], "dur": 5780.0}
    ok, _ = gate(src, {"vcodec": "hevc",
                       "audio": [{"codec": "aac", "ch": 2}, {"codec": "dts", "ch": 6}],
                       "dur": 5780.1})
    assert ok
    bad, reasons = gate(src, {"vcodec": "hevc",
                              "audio": [{"codec": "aac", "ch": 6}],
                              "dur": 5780.0})
    assert not bad and any("stereo" in r for r in reasons)
    bad, _ = gate(src, {"vcodec": "h264",
                        "audio": [{"codec": "aac", "ch": 2}], "dur": 5780.0})
    assert not bad
