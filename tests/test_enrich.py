"""Enrichment write-path tests — no network, no Jellyfin.

The load-bearing cases are the refusals: a row that already has a bio must be
skipped, and a write must touch only Overview/LockedFields.
"""
import json

import pytest

from media_lib import enrich


class FakeJF:
    def __init__(self, rows):
        self.rows = dict(rows)          # id -> row
        self.posts = []

    def user_id(self):
        return 'uid'

    def item(self, uid, item_id, fields=None):
        return dict(self.rows[item_id])

    def post_item(self, item_id, body, key=None):
        self.posts.append((item_id, dict(body)))
        # emulate the server: apply Overview + LockedFields, keep the rest
        cur = self.rows[item_id]
        for k in ('Overview', 'LockedFields', 'Name'):
            if k in body:
                cur[k] = body[k]
        return 204

    def list_items(self, *a, **kw):
        return []


def test_existing_bio_is_never_overwritten(monkeypatch, tmp_path):
    jf = FakeJF({'p1': {'Id': 'p1', 'Name': 'X', 'Overview': 'already here',
                        'ProviderIds': {'Tmdb': '1'}, 'LockedFields': []}})
    monkeypatch.setattr(enrich, 'jellyfin', jf)
    monkeypatch.setattr(enrich, 'JOURNAL', tmp_path / 'j.jsonl')
    out = enrich.apply_person('p1', 'new bio ' * 20, 'lastfm')
    assert out['status'] == 'SKIP-ARRIVED'
    assert jf.posts == [], 'a skip must not write anything'
    assert jf.rows['p1']['Overview'] == 'already here'


def test_write_sets_overview_and_locks_only(monkeypatch, tmp_path):
    jf = FakeJF({'p2': {'Id': 'p2', 'Name': 'Y', 'Overview': '',
                        'ProviderIds': {'Tmdb': '2'},
                        'ImageTags': {'Primary': 'tag'}, 'LockedFields': []}})
    monkeypatch.setattr(enrich, 'jellyfin', jf)
    monkeypatch.setattr(enrich, 'JOURNAL', tmp_path / 'j.jsonl')
    bio = 'A' * 120
    out = enrich.apply_person('p2', bio, 'wikipedia')
    assert out['status'] == 'DONE'
    assert out['chars'] == 120
    assert out['clean'] is True
    _id, body = jf.posts[-1]
    assert body['Overview'] == bio
    assert 'Name' in body, 'an absent Name is stored as null'
    assert 'Genres' not in body, 'Genres 400s the whole update'
    assert set(body['LockedFields']) >= {'Name', 'Overview'}
    assert jf.rows['p2']['ProviderIds'] == {'Tmdb': '2'}, 'ids must not change'


def test_short_bio_is_reported_partial_not_done(monkeypatch, tmp_path):
    jf = FakeJF({'p3': {'Id': 'p3', 'Name': 'Z', 'Overview': '',
                        'ProviderIds': {}, 'LockedFields': []}})
    monkeypatch.setattr(enrich, 'jellyfin', jf)
    monkeypatch.setattr(enrich, 'JOURNAL', tmp_path / 'j.jsonl')
    out = enrich.apply_person('p3', 'Drummer for Pulp.', 'lastfm')
    assert out['status'] == 'PARTIAL'
    assert out['chars'] == len('Drummer for Pulp.')


def test_journal_records_every_write(monkeypatch, tmp_path):
    jf = FakeJF({'p4': {'Id': 'p4', 'Name': 'W', 'Overview': '',
                        'ProviderIds': {}, 'LockedFields': []}})
    j = tmp_path / 'j.jsonl'
    monkeypatch.setattr(enrich, 'jellyfin', jf)
    monkeypatch.setattr(enrich, 'JOURNAL', j)
    enrich.apply_person('p4', 'B' * 100, 'tmdb')
    rec = json.loads(j.read_text().splitlines()[-1])
    assert rec['id'] == 'p4' and rec['status'] == 'DONE'


def test_min_bio_threshold_is_explicit():
    assert enrich.MIN_BIO == 80


def test_gap_filter_is_not_fooled_by_precedence(monkeypatch, tmp_path):
    """Regression: `(x or '') or '' == ''` is True for EVERY row.

    That bug turned a ~420-name gap list into all 2,720 credited names and made
    a 10-minute plan run for 50+ minutes.
    """
    class JF:
        def user_id(self):
            return 'uid'

        def list_items(self, *a, **kw):
            fields = kw.get('fields') or ''
            if 'People' in fields:
                return [{'People': [{'Name': 'Has Bio'}, {'Name': 'No Bio'}]}]
            return [{'Id': '1', 'Name': 'Has Bio', 'Overview': 'real bio',
                     'ImageTags': {}},
                    {'Id': '2', 'Name': 'No Bio', 'Overview': '   ',
                     'ImageTags': {}}]

    monkeypatch.setattr(enrich, 'jellyfin', JF())
    monkeypatch.setattr(enrich, 'chain', type('C', (), {
        'resolve': staticmethod(lambda n, k: {'bio_len': 0, 'provenance': {},
                                              'fields': {}, 'identity': {},
                                              'conflicts': [], 'rejected': []})})())
    out = enrich.build_plan('lib', out_path=tmp_path / 'plan.json')
    names = [r['name'] for r in out['rows']]
    assert names == ['No Bio'], names
