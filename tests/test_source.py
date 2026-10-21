"""Sourcing-stage tests.

Every test here corresponds to a defect that actually happened while hunting:

  * dotted release names (``Black.Sabbath``) silently matched nothing;
  * a "usenet" query returned torrents, so a torrent was reported as a find;
  * advertised seeds (63, 95) outranked reality and queued dead swarms;
  * ``1of2`` postings were queued as if they were the whole programme;
  * ``hash_of`` called ``group(1)`` on a group-less regex and crashed.
"""
import json

import pytest

from media_lib import source


# ------------------------------------------------------------------ normalising
def test_norm_strips_dots_so_usenet_names_match_queries():
    assert source.norm('Classic.Albums.S09E01.Black.Sabbath.Paranoid.1080p') == \
        'classic albums s09e01 black sabbath paranoid 1080p'
    assert source.norm('Black  Sabbath') == 'black sabbath'


def test_matches_accepts_a_dotted_release_name():
    # Regression: this returned False when the title was compared without
    # normalisation, which is why three of four Usenet grabs initially failed.
    assert source.matches('Classic.Albums.S09E01.Black.Sabbath.Paranoid.1080p.HDTV.H264-LiNKLE',
                          'Black Sabbath', 'Paranoid')
    assert source.matches('Classic.Albums.S03E04.Lou.Reed.Transformer.1080p',
                          'Lou Reed', 'Transformer')


def test_matches_rejects_a_different_artist():
    assert not source.matches('Classic.Albums.S02E02.Phil.Collins.Face.Value',
                              'Lou Reed', 'Transformer')


def test_matches_requires_the_series_token():
    assert not source.matches('Black Sabbath Paranoid 1080p documentary',
                              'Black Sabbath', 'Paranoid')


# --------------------------------------------------------------------- parts
@pytest.mark.parametrize('title,expected', [
    ('Classic.Albums.Rush.2112.1of2.1080p', (1, 2)),
    ('Classic Albums Tom Petty 2 of 2 x264', (2, 2)),
    ('Classic Albums Elvis part 2', (2, 0)),
    ('Classic.Albums.S09E03.Rush.1080p', None),
])
def test_part_info(title, expected):
    assert source.part_info(title) == expected


def test_ranking_puts_a_complete_set_above_an_incomplete_hd_one():
    cands = [
        {'title': 'Classic Albums X 1of2 1080p', 'seeds': 50, 'protocol': 'torrent'},
        {'title': 'Classic Albums X 720p', 'seeds': 2, 'protocol': 'torrent'},
    ]
    ranked = source.rank(cands)
    assert ranked[0]['complete'] is True
    assert '720p' in ranked[0]['title']
    assert ranked[1]['complete'] is False


def test_seeds_never_promote_a_dead_or_incomplete_pick():
    cands = [
        {'title': 'Classic Albums X 1of2', 'seeds': 999, 'protocol': 'torrent'},
        {'title': 'Classic Albums X', 'seeds': 2, 'protocol': 'torrent'},
    ]
    assert source.rank(cands)[0]['title'] == 'Classic Albums X'


# ------------------------------------------------------------------ filtering
def test_protocol_filter_excludes_the_other_side():
    cands = [
        {'title': 'a', 'seeds': 10, 'protocol': 'torrent'},
        {'title': 'b', 'seeds': 0, 'protocol': 'usenet'},
    ]
    assert [c['title'] for c in source.rank(cands, protocol='usenet')] == ['b']
    assert [c['title'] for c in source.rank(cands, protocol='torrent')] == ['a']


def test_seed_floor_applies_to_torrents_only():
    cands = [
        {'title': 'dead', 'seeds': 0, 'protocol': 'torrent'},
        {'title': 'nzb', 'seeds': None, 'protocol': 'usenet'},
    ]
    ranked = source.rank(cands, min_seeds=2)
    assert [c['title'] for c in ranked] == ['nzb']


def test_audio_and_iso_postings_are_not_video():
    assert not source.is_video_posting({'title': 'Herman Brood - Shpritsz_FLAC_VRTX'})
    assert not source.is_video_posting({'title': 'Shocking Blue At Home [320 KBPS]'})
    assert not source.is_video_posting({'title': 'Classic Albums Metallica Metallica.ISO'})
    assert source.is_video_posting({'title': 'Classic.Albums.Suede.Coming.Up.1080p.mkv'})


# ---------------------------------------------------------------------- hash
def test_hash_of_reads_a_bare_40_hex_guid():
    # Regression: this raised IndexError (no capture group) and killed a hunt.
    h = 'a' * 40
    assert source.hash_of({'guid': f'https://indexer/x/{h}'}) == h


def test_hash_of_prefers_an_explicit_magnet():
    h = 'b' * 40
    assert source.hash_of({'magnetUrl': f'magnet:?xt=urn:btih:{h}'}) == h


# --------------------------------------------------------------- candidates
POOL = {
    'Classic.Albums.S09E01.Black.Sabbath.Paranoid.1080p.HDTV': {
        'title': 'Classic.Albums.S09E01.Black.Sabbath.Paranoid.1080p.HDTV',
        'protocol': 'usenet', 'seeders': None, 'grabs': 948, 'size': 2_500_000_000,
        'downloadUrl': 'https://prowlarr/dl/1'},
    'Classic Albums Black Sabbath Paranoid 1of2 PDTV': {
        'title': 'Classic Albums Black Sabbath Paranoid 1of2 PDTV',
        'protocol': 'torrent', 'seeders': 40, 'size': 400_000_000, 'guid': 'c' * 40},
    'Black Sabbath Paranoid FLAC': {
        'title': 'Black Sabbath Paranoid FLAC', 'protocol': 'torrent', 'seeders': 90},
    'Classic Albums Judas Priest British Steel TVrip': {
        'title': 'Classic Albums Judas Priest British Steel TVrip',
        'protocol': 'torrent', 'seeders': 3, 'guid': 'd' * 40},
}


def test_candidates_for_finds_the_usenet_episode_and_skips_the_audio_rip():
    picks = source.candidates_for(POOL, 'Black Sabbath', 'Paranoid', protocol='usenet')
    assert len(picks) == 1
    assert picks[0]['protocol'] == 'usenet'
    assert picks[0]['url'].startswith('https://prowlarr/dl/1')


def test_candidates_for_returns_nothing_for_an_absent_title():
    assert source.candidates_for(POOL, 'Carly Simon', 'No Secrets') == []


def test_queue_without_a_url_reports_it_rather_than_raising():
    out = source.queue({'title': 'x'})
    assert out['ok'] is False and out['reason'] == 'no-url'


# ------------------------------------------------------------- cli behaviour
def test_queue_refuses_an_incomplete_pick_unless_allowed(tmp_path, monkeypatch):
    from cli import source as cli_source

    plan = {'rows': [{'id': 1, 'artist': 'Rush', 'album': '2112',
                      'picks': [{'title': 'Classic Albums Rush 1of2', 'protocol': 'torrent',
                                 'complete': False, 'url': 'magnet:?xt=urn:btih:' + 'e' * 40}]}]}
    pf = tmp_path / 'plan.json'
    pf.write_text(json.dumps(plan))
    sent = []
    monkeypatch.setattr(cli_source, 'PLAN_PATH', pf)
    monkeypatch.setattr(cli_source, 'QUEUE_LOG', tmp_path / 'q.jsonl')
    monkeypatch.setattr(cli_source.source, 'queue', lambda p, category='': sent.append(p) or {'ok': True})

    rc = cli_source.main(['queue', '--ids', '1'])
    assert sent == [], 'an incomplete set must not be queued by default'
    assert rc == 1

    rc = cli_source.main(['queue', '--ids', '1', '--allow-incomplete'])
    assert len(sent) == 1
    assert rc == 0


def test_queue_rejects_ids_that_are_not_in_the_plan(tmp_path, monkeypatch):
    from cli import source as cli_source
    pf = tmp_path / 'plan.json'
    pf.write_text(json.dumps({'rows': [{'id': 1, 'picks': []}]}))
    monkeypatch.setattr(cli_source, 'PLAN_PATH', pf)
    with pytest.raises(SystemExit):
        cli_source.main(['queue', '--ids', '7'])


def test_folder_content_path_resolves_to_the_largest_video(monkeypatch):
    from media_lib import qb as qbmod
    monkeypatch.setattr(qbmod, 'files', lambda h, op=None: [
        {'name': 'Rel/Show.1080p.mp4', 'size': 900},
        {'name': 'Rel/Show.srt', 'size': 1},
        {'name': 'Rel/extra.mkv', 'size': 10},
    ])
    monkeypatch.setattr(qbmod, 'torrents', lambda op=None: [{'hash': 'h1', 'save_path': '/downloads-zmedia'}])
    # The file name is relative to save_path: joining content_path doubles the folder.
    assert qbmod.resolve_media_path('/downloads-zmedia/Rel', 'h1') == \
        '/downloads-zmedia/Rel/Show.1080p.mp4'
    # A single-file torrent is returned untouched.
    assert qbmod.resolve_media_path('/downloads-zmedia/x.mkv', 'h1') == '/downloads-zmedia/x.mkv'


# ------------------------------------------------- queue lookups (defect: 3x)
def test_find_torrent_is_tolerant_of_hyphens_and_dots(monkeypatch):
    from media_lib import qb as qbmod
    monkeypatch.setattr(qbmod, 'torrents', lambda op=None: [
        {'hash': 'h1', 'name': 'Classic-Albums-Stevie-Wonder-Songs-In-The-Key-Of-Life-x264'},
        {'hash': 'h2', 'name': 'Classic.Albums.Lou.Reed.Transformer.1080p'},
    ])
    # `'Stevie Wonder' in name` failed; the normalised lookup must not.
    assert qbmod.find_torrent('Stevie Wonder')['hash'] == 'h1'
    assert qbmod.find_torrent('lou reed')['hash'] == 'h2'
    assert qbmod.find_torrent('nonexistent') is None


def test_torrent_for_path_matches_a_file_inside_a_folder(monkeypatch):
    from media_lib import qb as qbmod
    monkeypatch.setattr(qbmod, 'torrents', lambda op=None: [
        {'hash': 'h1', 'save_path': '/downloads-zmedia',
         'content_path': '/downloads-zmedia/Some Release [YTS.MX]'}])
    monkeypatch.setattr(qbmod, 'files', lambda h, op=None: [
        {'name': 'Some Release [YTS.MX]/Movie.1080p.mp4', 'size': 900}])
    got = qbmod.torrent_for_path('/downloads-zmedia/Some Release [YTS.MX]/Movie.1080p.mp4')
    assert got and got['hash'] == 'h1'


def test_retryable_failures_only_accept_a_small_shortfall(monkeypatch):
    from media_lib import sab as sabmod
    slots = [
        {'nzo_id': 'a', 'status': 'Failed', 'name': 'one short', 'size': '1 GB',
         'fail_message': 'Repair failed, not enough repair blocks (1 short)'},
        {'nzo_id': 'b', 'status': 'Failed', 'name': 'many short', 'size': '1 GB',
         'fail_message': 'Repair failed, not enough repair blocks (99 short)'},
        {'nzo_id': 'c', 'status': 'Failed', 'name': 'cant complete', 'size': '1 GB',
         'fail_message': 'Aborted, cannot be completed - https://sabnzbd.org/not-complete'},
        {'nzo_id': 'd', 'status': 'Completed', 'name': 'fine', 'size': '1 GB', 'storage': '/x'},
    ]
    monkeypatch.setattr(sabmod, 'history', lambda limit=20: slots)
    out = sabmod.retryable_failures()
    assert [f['nzo_id'] for f in out] == ['a']


def test_ingest_new_records_the_ledger_on_success(tmp_path, monkeypatch):
    """Without this, media_scan re-offers an already-ingested release."""
    from cli import ingest_new as cli_ingest
    from media_lib import jellyfin as jf, place as placemod, qb as qbmod, safety as safetymod

    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps([{'src': '/downloads-zmedia/X.mkv',
                                 'dest': '/mnt/zmedia/videos/lib/X.mkv'}]))
    recorded = {}
    monkeypatch.setattr(cli_ingest, 'config', type('C', (), {
        'ensure_state_dir': staticmethod(lambda: None),
        'STATE_DIR': tmp_path})())
    monkeypatch.setattr(qbmod, 'torrent_for_path', lambda p: {'hash': 'a' * 40})
    monkeypatch.setattr(qbmod, 'resolve_media_path', lambda s, h: s)
    monkeypatch.setattr(placemod, 'place_new',
                        lambda *a, **k: {'ok': True, 'stage': 'PLACED', 'res': '1920x1080 h264'})
    monkeypatch.setattr(safetymod, 'record', lambda **kw: recorded.update(kw))
    monkeypatch.setattr(jf, 'notify_media', lambda paths: 204)
    monkeypatch.setattr(jf, 'refresh_library', lambda lib: 204)

    rc = cli_ingest.main(['--spec', str(spec)])
    assert rc == 0
    assert recorded.get('action') == 'place'
    assert recorded.get('hash') == 'a' * 40
    assert recorded.get('dest') == '/mnt/zmedia/videos/lib/X.mkv'
