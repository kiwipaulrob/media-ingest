"""Row discovery and ledger backfill.

Both exist because of a specific failure in production:

  * the placement CLI polled per item with a 90 s budget, which both blocked the
    run past its caller's timeout and declared healthy placements "missing" —
    a row later appeared **nine minutes** after its file (scan backlog);
  * placements made before the ledger was wired in were absent from it, so
    `media_scan` re-offered them as new every time (15 candidates, 2 new).
"""
import json

from media_lib import awaiting


def test_find_row_requires_an_exact_path_match(monkeypatch):
    from media_lib import jellyfin as jf
    rows = [
        {'Id': 'wrong', 'Path': '/data/lib/S01E01 Old Thing.mkv'},
        {'Id': 'right', 'Path': '/data/lib/S01E06 Fleetwood Mac - Rumours.mkv'},
    ]
    monkeypatch.setattr(jf, 'user_id', lambda: 'uid')
    monkeypatch.setattr(jf, 'get_json', lambda path, params=None: {'Items': rows})
    assert awaiting.find_row_by_path('/data/lib/S01E06 Fleetwood Mac - Rumours.mkv') == 'right'
    assert awaiting.find_row_by_path('/data/lib/S01E06 Not There.mkv') == ''


def test_await_rows_returns_found_ids_and_reports_misses(monkeypatch):
    from media_lib import jellyfin as jf
    calls = {'n': 0}

    def fake_get(path, params=None):
        calls['n'] += 1
        # first poll: nothing; second poll: the first spec resolves
        if calls['n'] < 2:
            return {'Items': []}
        return {'Items': [{'Id': 'abc', 'Path': '/d/a.mkv'}]}

    monkeypatch.setattr(jf, 'user_id', lambda: 'uid')
    monkeypatch.setattr(jf, 'get_json', fake_get)
    monkeypatch.setattr(jf, 'notify_media', lambda paths: 204)
    specs = [{'dest': '/d/a.mkv'}, {'dest': '/d/b.mkv'}]
    out = awaiting.await_rows(specs, budget=4, interval=0.01, notify_each=1)
    assert out['/d/a.mkv']['found'] is True and out['/d/a.mkv']['item_id'] == 'abc'
    # a row that never appears is reported, not raised, and never marks failure
    assert out['/d/b.mkv']['found'] is False


def test_backfill_is_idempotent_and_skips_unresolvable(monkeypatch, tmp_path):
    from cli import ingest_new as cli

    journal = tmp_path / 'ingest-new.jsonl'
    journal.write_text('\n'.join([
        json.dumps({'ok': True, 'dest': '/d/a.mkv', 'hash': 'a' * 40}),
        json.dumps({'ok': False, 'dest': '/d/failed.mkv', 'hash': 'b' * 40}),
        json.dumps({'ok': True, 'dest': '/d/c.mkv', 'src': '/downloads/nope.mkv'}),
    ]))

    ledger = {}
    recorded = []
    monkeypatch.setattr(cli.safety, 'load_ledger', lambda: dict(ledger))
    monkeypatch.setattr(cli.safety, 'ledger_contains',
                        lambda lg, h: h in lg)
    monkeypatch.setattr(cli.safety, 'record',
                        lambda **kw: (recorded.append(kw), ledger.__setitem__(kw['hash'], kw)))
    monkeypatch.setattr(cli.qb, 'torrent_for_path', lambda p: None)

    assert cli.backfill_ledger(journal) == 1          # only the resolvable, successful one
    assert recorded[0]['hash'] == 'a' * 40
    assert cli.backfill_ledger(journal) == 0          # idempotent
