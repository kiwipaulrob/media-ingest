"""Config invariants.

These look trivial; they are not. A wrong path here silently points ingest at
the wrong pool, and a wrong threshold silently re-opens a bug that wrote two
wrong films into the library (see the intake script's incident header).
"""
from media_lib import config


def test_library_map_covers_all_six_libraries():
    assert len(config.JF_TO_DISK) == 6
    assert set(config.JF_TO_DISK) == {
        '/data/musicdocos-series', '/data/documentaries', '/data/movies',
        '/data/filmnoir', '/data/tvshows', '/data/musicdocos',
    }


def test_library_paths_use_exact_upstream_spelling():
    # Upstream share dirs are deliberately mixed-case and 'musicdocos' has ONE c.
    assert config.JF_TO_DISK['/data/musicdocos'] == '/mnt/zmedia/videos/musicdocos'
    assert config.JF_TO_DISK['/data/musicdocos-series'] == '/mnt/zmedia/videos/musicdocos-series'
    assert config.JF_TO_DISK['/data/documentaries'] == '/mnt/zmedia/videos/Documentaries'
    assert config.JF_TO_DISK['/data/filmnoir'] == '/mnt/zmedia/videos/filmnoir'


def test_thresholds_are_the_proven_values():
    assert config.MIN_SQUASH == 0.80
    assert config.MIN_SHARED_DISTINCTIVE == 1
    assert config.MIN_CAND_COVERAGE == 0.60
    assert config.MIN_CONTAINMENT_SHARE == 0.60
    assert config.DURATION_TOLERANCE == 0.15
    assert config.MIN_EXACT_SQUASH_LEN == 6


def test_state_lives_in_this_profile_not_the_default_profile():
    assert config.STATE_DIR == config.PROFILE_DIR / 'state'
    assert 'media-maintainer' in str(config.STATE_DIR)
    assert not str(config.STATE_DIR).startswith('/root/.hermes/state')


def test_all_state_artifacts_derive_from_state_dir():
    for p in (config.LEDGER, config.LOG, config.CHECK_LIST,
              config.LOCK, config.PROBE_CACHE, config.LIB_CACHE):
        assert p.parent == config.STATE_DIR, p


def test_filler_words_are_stopwords():
    for w in ('the', 'story', 'documentary', 'bbc', 'part'):
        assert w in config.STOPWORDS


def test_media_root_paths_are_only_for_the_remote_host():
    # Guard against someone using these locally: they must be absolute remote
    # paths, and the module must not claim they exist locally.
    assert config.MEDIA_ROOT.startswith('/mnt/')
    assert config.PROXMOX3.startswith('root@')
