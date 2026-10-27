"""Safety primitives — lock, ledger, check list.

The hash-prefix test is the one that matters most: its absence let every
already-ingested torrent be re-processed on every run for weeks.
"""
import json

import pytest

from media_lib import safety


# ------------------------------------------------------------------- hashes
def test_hash_prefix_truncates_to_twelve():
    assert safety.hash_prefix('a' * 40) == 'a' * 12
    assert safety.hash_prefix('ABCDEF0123456789') == 'abcdef012345'


def test_ledger_contains_matches_a_40_char_hash_against_a_stored_prefix():
    # The production bug: 12-char prefixes vs 40-char hashes never matched.
    ledger = {'aabbccddeeff': {'title': 'X'}}
    assert safety.ledger_contains(ledger, 'aabbccddeeff' + '0' * 28) is True


def test_ledger_contains_rejects_unknown_and_empty():
    assert safety.ledger_contains({'aabbccddeeff': {}}, 'ffffeeeedddd' + '0' * 28) is False
    assert safety.ledger_contains({}, '') is False


def test_ledger_contains_is_case_insensitive():
    assert safety.ledger_contains({'aabbccddeeff': {}}, 'AABBCCDDEEFF' + '0' * 28) is True


# ------------------------------------------------------------------- ledger
def test_record_is_per_item_and_immediately_persisted(tmp_path):
    led = tmp_path / 'ledger.json'
    safety.record(led, hash='b' * 40, title='One')
    # Re-read from disk: proves it was written, not just held in memory.
    assert json.loads(led.read_text())['bbbbbbbbbbbb']['title'] == 'One'
    safety.record(led, hash='c' * 40, title='Two')
    data = json.loads(led.read_text())
    assert set(data) == {'bbbbbbbbbbbb', 'cccccccccccc'}


def test_record_stores_the_full_hash_for_audit(tmp_path):
    led = tmp_path / 'ledger.json'
    safety.record(led, hash='D' * 40, title='X')
    entry = json.loads(led.read_text())['dddddddddddd']
    assert entry['full_hash'] == 'd' * 40


def test_record_requires_a_hash(tmp_path):
    with pytest.raises(ValueError):
        safety.record(tmp_path / 'l.json', title='no hash')


def test_load_ledger_tolerates_missing_and_corrupt(tmp_path):
    assert safety.load_ledger(tmp_path / 'nope.json') == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    assert safety.load_ledger(bad) == {}


# --------------------------------------------------------------------- lock
def test_lock_refuses_a_second_holder(tmp_path):
    lock = tmp_path / 'x.lock'
    with safety.acquire(lock):
        with pytest.raises(safety.LockHeld):
            with safety.acquire(lock):
                pass


def test_lock_is_released_after_the_block(tmp_path):
    lock = tmp_path / 'x.lock'
    with safety.acquire(lock):
        pass
    # Re-acquiring must succeed once the previous holder is gone.
    with safety.acquire(lock):
        pass


def test_lock_records_pid(tmp_path):
    lock = tmp_path / 'x.lock'
    with safety.acquire(lock):
        assert 'pid=' in lock.read_text()


# --------------------------------------------------------------- check list
def test_check_list_is_byte_stable(tmp_path):
    p = tmp_path / 'check.json'
    rows = [
        {'title': 'B', 'recommend': 'NEW', 'score': 0.5},
        {'title': 'A', 'recommend': 'REVIEW-lower-res', 'score': 0.9},
    ]
    safety.write_check_list(rows, p)
    first = p.read_bytes()
    safety.write_check_list(list(reversed(rows)), p)
    second = p.read_bytes()
    assert first == second, 'reordered input must produce identical bytes'


def test_check_list_sorts_by_recommend_then_title(tmp_path):
    p = tmp_path / 'check.json'
    safety.write_check_list([
        {'title': 'Z', 'recommend': 'NEW'},
        {'title': 'A', 'recommend': 'NEW'},
    ], p)
    data = json.loads(p.read_text())
    assert [r['title'] for r in data] == ['A', 'Z']


# ------------------------------------------------------- Review-8 guards
def test_is_playing_matches_now_playing_item(monkeypatch):
    from media_lib import jellyfin

    monkeypatch.setattr(
        jellyfin, "get_json",
        lambda *a, **k: [{"UserName": "paul", "NowPlayingItem": {"Id": "abc123"}}, {"UserName": "x"}],
    )
    assert jellyfin.is_playing("abc123") is True
    assert jellyfin.is_playing("other") is False


def test_is_playing_false_when_sessions_unreadable(monkeypatch):
    from media_lib import jellyfin

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(jellyfin, "get_json", boom)
    assert jellyfin.is_playing("abc123") is False


def test_bio_mutating_commands_hold_the_lock():
    import inspect

    from cli import bio

    for fn in (bio.cmd_fill, bio.cmd_alias, bio.cmd_wikidata):
        assert "safety.acquire" in inspect.getsource(fn), fn.__name__
