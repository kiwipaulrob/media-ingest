"""Config invariants.

These are not cosmetic: a wrong path here points ingest at the wrong pool, and
a wrong threshold silently re-opens a bug that wrote two wrong films into the
library. The library map was WRONG in the old script (6 of 8 libraries, and a
file host that no longer sees the data) until it was re-read from
`docker inspect` on 2026-10-20.
"""
from media_lib import config, probe


def test_library_map_covers_all_eight_live_libraries():
    assert len(config.JF_TO_DISK) == 8
    assert set(config.JF_TO_DISK) == {
        '/data/documentaries', '/data/docuseries', '/data/filmdocos',
        '/data/filmnoir', '/data/movies', '/data/musicdocos',
        '/data/musicdocos-series', '/data/tvshows',
    }


def test_library_paths_use_exact_live_spelling():
    # Read from docker inspect; case and word order both matter on the share.
    assert config.JF_TO_DISK['/data/documentaries'] == '/mnt/zmedia/videos/Documentaries'
    assert config.JF_TO_DISK['/data/docuseries'] == '/mnt/zmedia/videos/DocuSeries'
    assert config.JF_TO_DISK['/data/filmdocos'] == '/mnt/zmedia/videos/FilmDocumentaries'
    assert config.JF_TO_DISK['/data/filmnoir'] == '/mnt/zmedia/videos/filmnoir'
    assert config.JF_TO_DISK['/data/movies'] == '/mnt/zmedia/videos/Films'
    assert config.JF_TO_DISK['/data/musicdocos'] == '/mnt/zmedia/videos/musicdocos'
    assert config.JF_TO_DISK['/data/tvshows'] == '/mnt/zmedia/videos/TV'


def test_media_paths_are_reachable_only_from_the_media_host():
    # proxmox3's copy of this mount is stale (musicdocos only) — using it
    # produces ENOENT for every other library.
    assert config.MEDIA_HOST == 'root@192.168.214.109'
    assert config.MEDIA_CT.startswith('pct exec')
    for p in config.JF_TO_DISK.values():
        assert p.startswith(config.MEDIA_VOLUME + '/')


def test_save_path_mapping_covers_both_download_roots():
    assert config.SAVE_TO_REMOTE['/downloads-zmedia'] == '/mnt/zmedia/downloads'
    assert config.SAVE_TO_REMOTE['/downloads'] == '/mnt/zmedia/downloads'


def test_thresholds_are_the_proven_values():
    assert config.MIN_SQUASH == 0.80
    assert config.MIN_SHARED_DISTINCTIVE == 1
    assert config.MIN_CAND_COVERAGE == 0.60
    assert config.MIN_CONTAINMENT_SHARE == 0.60
    assert config.DURATION_TOLERANCE == 0.15
    assert config.MIN_EXACT_SQUASH_LEN == 6


def test_state_lives_in_this_profile_not_the_default_profile():
    assert config.STATE_DIR == config.PROFILE_DIR / 'state'
    assert not str(config.STATE_DIR).startswith('/root/.hermes/state')
    for p in (config.LEDGER, config.LOG, config.CHECK_LIST,
              config.LOCK, config.PROBE_CACHE, config.LIB_CACHE):
        assert p.parent == config.STATE_DIR, p


def test_filler_words_are_stopwords():
    for w in ('the', 'story', 'documentary', 'bbc', 'part'):
        assert w in config.STOPWORDS


def test_probe_wrapper_uses_the_jellyfin_image_and_mounts_the_volume():
    # Neither the host nor CT105 has ffprobe: the image is the only source.
    w = probe.probe_wrap(config.FFPROBE_ENTRY)
    assert 'docker run --rm' in w
    assert f'-v {config.MEDIA_VOLUME}:{config.MEDIA_VOLUME}' in w
    assert config.FFPROBE_ENTRY in w
    assert config.media_image() in w


def test_media_command_is_wrapped_for_ct105():
    cmd = probe.media_cmd('echo hi')
    assert cmd.startswith(config.MEDIA_CT)
    assert 'echo hi' in cmd
