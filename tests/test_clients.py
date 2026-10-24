"""Client, probe and TMDB unit tests — pure logic only, no network."""
import pytest

from media_lib import jellyfin, probe, tmdb


# --------------------------------------------------------------- jellyfin
def test_auth_header_shape():
    assert jellyfin.auth_header('KEY') == {'Authorization': 'MediaBrowser Token="KEY"'}


def test_coerce_bio_flattens_the_wikipedia_tuple():
    # Assigning the tuple itself serialises a JSON array and 400s the write.
    assert jellyfin.coerce_bio(('text', 'thumb-url')) == 'text'
    assert jellyfin.coerce_bio(['just-text']) == 'just-text'
    assert jellyfin.coerce_bio('text') == 'text'
    assert jellyfin.coerce_bio(None) == ''


def test_credentials_prefer_the_process_environment(monkeypatch):
    monkeypatch.setenv('JELLYFIN_URL', 'http://env-host:8096')
    assert jellyfin.base_url() == 'http://env-host:8096'


def test_user_id_is_not_hardcoded(monkeypatch):
    # Four scripts used to carry the same literal id; a re-created user broke
    # all of them at once.
    monkeypatch.delenv('JELLYFIN_USER_ID', raising=False)
    monkeypatch.setattr(jellyfin, 'load_env_file', lambda path=None: {})
    with pytest.raises(jellyfin.JellyfinError):
        jellyfin.user_id()


def test_env_file_parser_ignores_comments(monkeypatch, tmp_path):
    f = tmp_path / '.env'
    f.write_text('# comment\nKEY=value\nQUOTED="v2"\nEMPTY=\n')
    parsed = jellyfin.load_env_file(f)
    assert parsed == {'KEY': 'value', 'QUOTED': 'v2', 'EMPTY': ''}


# ------------------------------------------------------------------ probe
def test_mkv_magic():
    assert probe.container_from_head(bytes.fromhex('1a45dfa3') + b'\x00' * 12) == 'matroska'


def test_avi_magic():
    assert probe.container_from_head(b'RIFF\x00\x00\x00\x00AVI ') == 'avi'


def test_mp4_magic():
    assert probe.container_from_head(b'\x00\x00\x00\x18ftypisom') == 'mp4'


def test_mpegts_magic():
    assert probe.container_from_head(b'\x47\x40\x11\x10') == 'mpegts'


def test_zero_filled_head_is_poisoned():
    # A poisoned swarm delivers complete pieces of zero-filled garbage.
    assert probe.container_from_head(b'\x00' * 16) == 'poisoned'
    assert probe.container_from_head(b'\x00' * 64) == 'poisoned'


def test_unknown_and_empty_heads_never_claim_a_container():
    assert probe.container_from_head(b'') == 'unknown'
    assert probe.container_from_head(b'junkjunkjunk') == 'unknown'


def test_extension_follows_container_never_the_filename():
    assert probe.extension_for_container('matroska') == '.mkv'
    assert probe.extension_for_container('poisoned') is None


# ------------------------------------------------------------------- tmdb
def test_title_year_extracts_clean_title():
    assert tmdb.title_year('Some.Film.2005.1080p.BluRay.x264-GRP') == ('Some Film', '2005')
    assert tmdb.title_year('The War on Democracy') == ('The War on Democracy', None)


def test_title_year_keeps_hyphenated_titles():
    title, year = tmdb.title_year('Spider-Man.2002.1080p')
    assert title == 'Spider-Man' and year == '2002'


def test_title_year_does_not_mangle_names_without_a_year():
    assert tmdb.title_year('Good.Hair') == ('Good Hair', None)


def test_gate_is_annotate_only_and_never_raises_on_empty_name():
    v = tmdb.gate('')
    assert v['status'] == 'no-match'
    assert set(v) >= {'status', 'query'}


def test_fields_param_never_splits_a_string_into_characters():
    # The bug: ','.join('Path,MediaSources') -> 'P,a,t,h,...', which the server
    # accepts and answers with NO Path field at all.
    assert jellyfin._fields_param('Path,MediaSources') == 'Path,MediaSources'
    assert jellyfin._fields_param(['Path', 'MediaSources']) == 'Path,MediaSources'
    assert jellyfin._fields_param(None) is None
    assert jellyfin._fields_param([]) is None
