"""audio.verdict: Moana-rule advisory. Pure, no I/O."""

from media_lib.audio import verdict


def test_clean_aac_stereo():
    assert verdict([{"codec": "aac", "channels": 2, "lang": "eng"}]) == []


def test_moana_shape_flags_both():
    tracks = ([{"codec": "truehd", "channels": 8, "lang": "eng"}] +
              [{"codec": "ac3", "channels": 6, "lang": "eng"}] +
              [{"codec": "ac3", "channels": 6, "lang": L}
               for L in ("spa", "fre", "ita", "dan", "fin", "dut", "nor",
                         "rus", "swe", "chi")])
    notes = verdict(tracks)
    assert any(n.startswith("AUDIO-NO-AAC") for n in notes)
    bloat = next(n for n in notes if n.startswith("AUDIO-BLOAT"))
    assert "9 non-English/Italian" in bloat
    assert "ita" not in bloat  # Italian is always kept


def test_foreign_only_is_kept_not_bloat():
    notes = verdict([{"codec": "ac3", "channels": 2, "lang": "fre"}])
    assert not any(n.startswith("AUDIO-BLOAT") for n in notes)


def test_italian_only_is_clean():
    assert verdict([{"codec": "aac", "channels": 2, "lang": "ita"}]) == []


def test_no_audio_streams():
    assert verdict([]) == ["AUDIO-UNKNOWN: no audio streams probed"]
