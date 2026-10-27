"""Gates for the NEW-item placement stage.

Two things are pinned here because they cost real time or real correctness when
they regress:
  * a failing gate must REMOVE the staged copy — a half-good file must never
    stay in the library;
  * a successful placement must use exactly THREE remote calls: the slot-collision
    `ls` (Review-P3.3, ~50 ms, before a single byte is copied) plus the two the
    copy itself needs. The first version used six (probe src, stage, probe dst,
    three decode spans) and every one pays SSH plus container startup.
"""
import json

from media_lib import config, place


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
        # These cases exercise the COPY gates. Completeness is a separate gate with
        # its own tests; leaving it live here would refuse before staging ever runs.
        monkeypatch.setattr(place.completeness, 'source_problems', lambda *a, **k: [])

    def on_media(self, inner, timeout=300):
        self.calls.append(inner)
        if inner.strip().startswith('rm -f'):
            self.removed.append(inner)
            return ''
        if inner.strip().startswith('mv -f'):
            # only issued after a successful re-sync replaces the staged copy
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
        if inner.strip().startswith('ls -1'):               # the slot-collision check
            return ''
        raise AssertionError(f'unexpected remote call: {inner[:80]}')


DEST = '/mnt/zmedia/videos/lib/Season 02/Show S02E03 Thing.mkv'
SRC = '/downloads-zmedia/X.mkv'


def test_good_placement_passes_with_two_remote_calls(monkeypatch):
    h = FakeHost()
    h.install(monkeypatch)
    # the sync gate probes on its own; stub it so this test keeps asserting the two
    # remote calls the copy itself needs
    monkeypatch.setattr(place.avsync, 'check',
                        lambda p: {'verdict': 'ok', 'problems': [], 'start_delta_ms': 0.0})
    out = place.place_new(SRC, DEST, min_height=700)
    assert out['ok'] and out['stage'] == 'PLACED'
    assert out['res'] == '1920x1080 h264'
    assert out['av_sync'] == 'ok'
    assert len(h.calls) == 3, f'expected 3 remote calls, got {len(h.calls)}'
    assert h.calls[0].strip().startswith('ls -1'), 'slot check runs before any copy'
    assert h.removed == []


def test_bad_sync_is_repaired_rather_than_rejected(monkeypatch):
    """An offset or a long audio track is fixable by stream-copying the picture."""
    h = FakeHost()
    h.install(monkeypatch)

    calls = {'n': 0}

    def fake_check(path):
        # out of sync until the repair has run, then clean
        if '.resync' in str(path) or calls['n']:
            return {'verdict': 'ok', 'problems': [], 'start_delta_ms': 0.0}
        calls['n'] += 1
        return {'verdict': 'fail',
                'problems': ['audio #1 (aac) duration differs by +920 ms'],
                'start_delta_ms': 0.0}

    monkeypatch.setattr(place.avsync, 'check', fake_check)
    monkeypatch.setattr(place.avsync, 'resync',
                        lambda src, out: {'ok': True, 'method': 'audio-retime',
                                          'reason': 'verified'})
    out = place.place_new(SRC, DEST, min_height=700)
    assert out['ok'] and out['stage'] == 'PLACED'
    assert out['av_sync'] == 'repaired'
    assert h.removed == [], 'a repairable file must NOT be deleted'


def test_unrepairable_sync_is_rejected_and_removed(monkeypatch):
    """Truncated picture cannot be re-synced — the file has to go."""
    h = FakeHost()
    h.install(monkeypatch)
    monkeypatch.setattr(place.avsync, 'check', lambda p: {
        'verdict': 'fail', 'problems': ['audio #1 (aac) duration differs by +1965 ms'],
        'start_delta_ms': 0.0})
    monkeypatch.setattr(place.avsync, 'resync',
                        lambda src, out: {'ok': False, 'reason': 'video-truncated'})
    out = place.place_new(SRC, DEST, min_height=700)
    assert not out['ok'] and out['stage'] == 'AV-SYNC-FAIL'
    assert 'video-truncated' in out['detail']
    assert h.removed, 'an unrepairable file must not be left in the library'


def test_a_tail_gap_is_only_a_warning(monkeypatch):
    """Credits with no sound leave audio a couple of seconds short — not a defect."""
    h = FakeHost()
    h.install(monkeypatch)
    monkeypatch.setattr(place.avsync, 'check', lambda p: {
        'verdict': 'fail', 'problems': ['audio #1 (ac3) duration differs by -2600 ms'],
        'start_delta_ms': 0.0})
    out = place.place_new(SRC, DEST, min_height=700)
    assert out['ok'] and out['stage'] == 'PLACED'
    assert out['av_sync'] == 'warn'


def test_a_real_start_offset_is_refused(monkeypatch):
    h = FakeHost()
    h.install(monkeypatch)
    monkeypatch.setattr(place.avsync, 'check', lambda p: {
        'verdict': 'fail', 'problems': ['audio #1 starts +400 ms from video'],
        'start_delta_ms': 400.0})
    out = place.place_new(SRC, DEST, min_height=700)
    assert not out['ok'] and out['stage'] == 'AV-SYNC-FAIL'


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
    assert len(h.calls) == 2  # slot ls, then the failed copy — no gate runs after


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


def test_incomplete_source_is_refused_before_anything_is_staged(monkeypatch):
    """A source missing parts must fail BEFORE the copy, not after it.

    Every other gate inspects the copy, so an incomplete source passes them all.
    """
    host = FakeHost()
    host.install(monkeypatch)
    monkeypatch.setattr(place.completeness, 'source_problems',
                        lambda *a, **k: ['VOB chain incomplete: missing [3]'])

    res = place.place_new('/data/movies/X/X.mkv', '/data/movies/X/X.mkv')
    assert res['ok'] is False
    assert res['stage'] == 'INCOMPLETE-SOURCE'
    assert 'missing [3]' in res['detail']
    assert not host.calls, 'nothing should touch the media host when the source is refused'


def test_to_jf_round_trips_every_library_mapping():
    """Jellyfin sees /data/..., the host sees /mnt/zmedia/videos/.... notify_media() and
    await_rows() must be handed the container form or Jellyfin ignores the path."""
    for jf_prefix, disk_prefix in config.JF_TO_DISK.items():
        disk = disk_prefix + "/Show/Season 01/ep.mkv"
        jf = place.to_jf(disk)
        assert jf == jf_prefix + "/Show/Season 01/ep.mkv", jf
        assert place.to_disk(jf) == disk


def test_to_disk_needs_a_path_boundary_not_a_string_prefix():
    """'/data/movies-extra' must not be rewritten by the '/data/movies' mapping."""
    assert place.to_disk("/data/movies-extra/x.mkv") == "/data/movies-extra/x.mkv"
    assert place.to_jf("/mnt/zmedia/videos/musicdocos-old/x.mkv") == \
        "/mnt/zmedia/videos/musicdocos-old/x.mkv"


def test_a_sync_check_that_crashes_refuses_the_file(monkeypatch):
    """A gate that cannot run is not a gate that passed.

    The old code recorded verdict 'probe-failed' and then set sync_stage = 'ok'
    unconditionally, so a crashed check placed the copy as though it had been verified.
    """
    h = FakeHost()
    h.install(monkeypatch)

    def boom(path):
        raise RuntimeError("probe host unreachable")

    monkeypatch.setattr(place.avsync, 'check', boom)
    out = place.place_new(SRC, DEST, min_height=700)
    assert out['ok'] is False
    assert out['stage'] == 'AV-SYNC-UNVERIFIED'
    assert h.removed, 'an unverifiable copy must not be left in the library'


def test_slot_collision_refuses_before_copy(monkeypatch):
    """Review-P3.3: an occupied slot refuses with SLOT-COLLISION, no copy."""
    h = FakeHost()
    h.install(monkeypatch)
    orig = h.on_media

    def occupied(inner, timeout=300):
        h.calls.append(inner)
        if inner.strip().startswith('ls -1'):
            return 'Show S02E03 Other Title.mkv\n'
        return orig(inner, timeout)

    monkeypatch.setattr(place.probe, 'on_media', occupied)
    out = place.place_new(SRC, DEST)
    assert not out['ok'] and out['stage'] == 'SLOT-COLLISION'
    assert len(h.calls) == 1 and 'cp --reflink' not in ''.join(h.calls)
