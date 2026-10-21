"""Gates for the NEW-item placement stage.

Each test asserts that a failing gate REMOVES the staged copy — the whole point
is that a half-good file never stays in the library.
"""
import json

import pytest

from media_lib import place


class FakeProbe:
    """Replaces only probe.on_media so the real command builders still run."""

    def __init__(self, stats, decode_fail=None, stage_out='COPIED=fresh'):
        self.stats = stats
        self.decode_fail = decode_fail or []
        self.stage_out = stage_out
        self.removed = []
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(place.probe, 'on_media', self.on_media)

    def on_media(self, inner, timeout=300):
        self.calls.append(inner)
        if inner.startswith('rm -f'):
            self.removed.append(inner)
            return ''
        if 'cp --reflink' in inner or 'mkdir -p' in inner:
            return self.stage_out
        if '-f null' in inner:                       # decode span
            if self.decode_fail:
                raise RuntimeError('decode error')
            return ''
        key = 'dst' if 'DST.mkv' in inner or 'S02E03' in inner else 'src'
        s = self.stats[key]
        return json.dumps({'streams': [{'width': s['w'], 'height': s['h'],
                                        'codec_name': s['codec']}],
                           'format': {'duration': str(s['duration']),
                                      'tags': {'title': s.get('title', '')}}})


SRC = {'w': 1920, 'h': 1080, 'codec': 'h264', 'duration': 3025.0}
DST = {'w': 1920, 'h': 1080, 'codec': 'h264', 'duration': 3025.0}


def test_good_placement_passes(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': DST})
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv',
                          '/mnt/zmedia/videos/lib/Season 02/S02E03 Thing.mkv',
                          min_height=700)
    assert out['ok'] and out['stage'] == 'PLACED'
    assert out['res'] == '1920x1080 h264'
    assert fp.removed == []


def test_low_resolution_is_rejected_and_removed(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': {'w': 720, 'h': 400, 'codec': 'mpeg4', 'duration': 3025.0}})
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv',
                          '/mnt/zmedia/videos/lib/Season 02/S02E03 Thing.mkv',
                          min_height=700)
    assert not out['ok'] and 'BELOW-MIN-HEIGHT' in out['stage']
    assert fp.removed, 'staged copy must be removed when a gate fails'


def test_duration_mismatch_is_rejected(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': {**DST, 'duration': 1800.0}})
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv',
                          '/mnt/zmedia/videos/lib/Season 02/S02E03 Thing.mkv',
                          expect_duration=3025.0)
    assert not out['ok'] and out['stage'] == 'DURATION-MISMATCH'
    assert fp.removed


def test_undecodable_output_is_rejected(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': DST}, decode_fail=True)
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv',
                          '/mnt/zmedia/videos/lib/Season 02/S02E03 Thing.mkv')
    assert not out['ok'] and out['stage'] == 'DECODE-FAIL'
    assert fp.removed


def test_copy_failure_never_reaches_a_gate(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': DST}, stage_out='STAGE=COPY-FAIL')
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv',
                          '/mnt/zmedia/videos/lib/Season 02/S02E03 Thing.mkv')
    assert not out['ok'] and out['stage'] == 'COPY-FAIL'


def test_bad_destination_extension_is_refused_before_any_copy(monkeypatch):
    fp = FakeProbe({'src': SRC, 'dst': DST})
    fp.install(monkeypatch)
    out = place.place_new('/downloads-zmedia/X.mkv', '/mnt/zmedia/videos/lib/note.txt')
    assert not out['ok'] and out['stage'] == 'BAD-DEST'
    assert fp.calls == []


def test_path_mapping_prefers_the_longest_prefix():
    assert place.to_disk('/data/musicdocos-series/X.mkv').endswith(
        'videos/musicdocos-series/X.mkv')
    assert place.to_disk('/downloads-zmedia/Y.mkv').endswith('downloads/Y.mkv')
    assert place.to_disk('/mnt/zmedia/keep/me.mkv') == '/mnt/zmedia/keep/me.mkv'
