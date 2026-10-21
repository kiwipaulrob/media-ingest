"""Gates for the NEW-item placement stage.

Two things are pinned here because they cost real time or real correctness when
they regress:
  * a failing gate must REMOVE the staged copy — a half-good file must never
    stay in the library;
  * a successful placement must use exactly TWO remote calls. The first version
    used six (probe src, stage, probe dst, three decode spans) and every one
    pays SSH plus container startup — ~5.7 s per file wasted.
"""
import json

from media_lib import place


def _probe_json(w, h, codec, duration, title=''):
    return json.dumps({'streams': [{'width': w, 'height': h, 'codec_name': codec}],
                       'format': {'duration': str(duration), 'tags': {'title': title}}})


class FakeHost:
    """Replaces only probe.on_media; the real command builders still run."""

    def __init__(self, src=(1920, 1080, 'h264', 3025.0), dst=(1920, 1080, 'h264', 3025.0),
                 size_src=1_400_000_000, size_dst=None, stage='STAGE-OK',
                 copied='fresh', decode_ok=True):
        self.src = src
        self.dst = dst
        self.size_src = size_src
        self.size_dst = size_dst if size_dst is not None else size_src
        self.stage = stage
        self.copied = copied
        self.decode_ok = decode_ok
        self.calls = []
        self.removed = []

    def install(self, monkeypatch):
        monkeypatch.setattr(place.probe, 'on_media', self.on_media)

    def on_media(self, inner, timeout=300):
        self.calls.append(inner)
        if inner.strip().startswith('rm -f'):
            self.removed.append(inner)
            return ''
        if 'DECODE=' in inner:                            # the decode-span script
            if not self.decode_ok:
                return 'DECODE=0.05=OK\nDECODE=0.50=FAIL\nDECODE=0.95=OK'
            return 'DECODE=0.05=OK\nDECODE=0.50=OK\nDECODE=0.95=OK'
        if 'cp --reflink' in inner or 'SIZE_SRC=' in inner:   # the stage script
            if self.stage != 'STAGE-OK':
                return self.stage
            return (f'COPIED={self.copied}\nSIZE_SRC={self.size_src}\nSIZE_DST={self.size_dst}\n'
                    f'{place.M_SRC}\n{_probe_json(*self.src)}\n{place.M_END}\n'
                    f'{place.M_DST}\n{_probe_json(*self.dst)}\n{place.M_END}\n')
        raise AssertionError(f'unexpected remote call: {inner[:80]}')


DEST = '/mnt/zmedia/videos/lib/Season 02/Show S02E03 Thing.mkv'
SRC = '/downloads-zmedia/X.mkv'


def test_good_placement_passes_with_two_remote_calls(monkeypatch):
    h = FakeHost()
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST, min_height=700)
    assert out['ok'] and out['stage'] == 'PLACED'
    assert out['res'] == '1920x1080 h264'
    assert len(h.calls) == 2, f'expected 2 remote calls, got {len(h.calls)}'
    assert h.removed == []


def test_size_mismatch_is_rejected(monkeypatch):
    h = FakeHost(size_dst=1_300_000_000)
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST)
    assert not out['ok'] and out['stage'] == 'SIZE-MISMATCH'
    assert h.removed


def test_low_resolution_is_rejected_and_removed(monkeypatch):
    h = FakeHost(dst=(720, 400, 'mpeg4', 3025.0))
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST, min_height=700)
    assert not out['ok'] and 'BELOW-MIN-HEIGHT' in out['stage']
    assert h.removed


def test_duration_is_checked_against_the_source_without_a_caller_hint(monkeypatch):
    h = FakeHost(dst=(1920, 1080, 'h264', 1800.0))
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST)              # note: no expect_duration passed
    assert not out['ok'] and out['stage'] == 'DURATION-MISMATCH'
    assert h.removed


def test_undecodable_output_is_rejected(monkeypatch):
    h = FakeHost(decode_ok=False)
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST)
    assert not out['ok'] and out['stage'] == 'DECODE-FAIL'
    assert h.removed


def test_copy_failure_never_reaches_a_gate(monkeypatch):
    h = FakeHost(stage='STAGE=COPY-FAIL')
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST)
    assert not out['ok'] and out['stage'] == 'COPY-FAIL'
    assert len(h.calls) == 1


def test_bad_destination_extension_is_refused_before_any_remote_call(monkeypatch):
    h = FakeHost()
    h.install(monkeypatch)
    out = place.place_new(SRC, '/mnt/zmedia/videos/lib/note.txt')
    assert not out['ok'] and out['stage'] == 'BAD-DEST'
    assert h.calls == []


def test_already_copied_is_not_recopied(monkeypatch):
    h = FakeHost(copied='already')
    h.install(monkeypatch)
    out = place.place_new(SRC, DEST)
    assert out['ok'] and out['copied'] == 'already'


def test_path_mapping_prefers_the_longest_prefix():
    assert place.to_disk('/data/musicdocos-series/X.mkv').endswith(
        'videos/musicdocos-series/X.mkv')
    assert place.to_disk('/downloads-zmedia/Y.mkv').endswith('downloads/Y.mkv')
    assert place.to_disk('/mnt/zmedia/keep/me.mkv') == '/mnt/zmedia/keep/me.mkv'
