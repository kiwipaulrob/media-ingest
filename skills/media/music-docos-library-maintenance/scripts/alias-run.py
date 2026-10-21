#!/usr/bin/env python3
"""Alias-queue worker: write user-approved Wikipedia bios (group A, 30 names).

Each (row_name -> wiki_target) pair was human-confirmed. Never-clobber:
re-reads each row, skips if a bio arrived. Surgical writes (fresh DTO,
Overview+lock only, no Genres), minimal-body retry, post-write re-read +
untouched-field diff. Portraits only for person-target rows (band-target
rows skip portraits: a band photo is not a headshot). JSONL-append resume.
Run: python3 -u alias-run.py (env: JELLYFIN_URL, JELLYFIN_API_KEY).
"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

J = os.environ['JELLYFIN_URL']
K = os.environ['JELLYFIN_API_KEY']
U = '9905188a302c4666952a8f69cca6cee9'
A = {'Authorization': 'MediaBrowser Token="%s"' % K,
     'User-Agent': 'HermesMediaBot/1.0'}
UA = {'User-Agent': 'HermesMediaBot/1.0 (alias sweep)'}
SCRATCH = '/root/.hermes/profiles/media-maintainer/cache/scratch/'
LOG = SCRATCH + 'bio-run.jsonl'
PACE = 2.5

# (person-row name, wikipedia target page, band_page?)
PAIRS = [
    ('Aija Terauda', 'Aija Tērauda', False),
    ('Alex Acuna', 'Alex Acuña', False),
    ('Armand Leroi', 'Armand Marie Leroi', False),
    ("Eddie 'Bongo' Brown", 'Eddie Brown (musician)', False),
    ('Roger Charlery', 'Ranking Roger', False),
    ('Shirlie Kemp', 'Shirlie Holliman', False),
    ('Kentrick Patrick', 'Lord Creator', False),
    ('Jellybean Benítez', 'John Benitez', False),
    ('David Sitek', 'Dave Sitek', False),
    ('Dean Torrence', 'Jan and Dean', True),
    ('Patti Quatro', 'The Pleasure Seekers', True),
    ('Toni Baker', 'The Dakotas', True),
    ('Robb Reiner', 'Anvil (band)', True),
    ("Gavin Bain", "Silibil N' Brains", True),
    ('Valerie Gell', 'The Liverbirds', True),
    ('Donna Godchaux', 'Donna Jean Godchaux', False),
    ('Geoffrey MacCormack', 'Geoff MacCormack', False),
    ('J. Steven Soles', 'Steven Soles', False),
    ('James Hale', 'James Tracy Hale', False),
    ('Jeff Howlett', 'Jeffrey Howlett', False),
    ('Tom Fenner', 'Thomas Fenner', False),
    ('Phil Smith', 'Philip Smith', False),
    ('Charlie Thomas', 'Charles Thomas', False),
    ('Martyn Casey', 'Martyn P. Casey', False),
    ('Martin Bedford', 'Martyn Bedford', False),
    ('Francis Hanly', 'Frank Hanly', False),
    ('Brie Brandt', 'Brie Howard-Darling', False),
    ('Danielle Renfrew Behrens', 'Danielle Renfrew', False),
    ('Deborah Armstrong', 'Deborah A. Armstrong', False),
    ('Christopher Boone', 'Christopher G. Boone', False),
]


def log_result(rec):
    with open(LOG, 'a') as fh:
        fh.write(json.dumps(rec) + '\n')


def jf_get(url):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=A), timeout=60))


def jf_post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
        headers=dict(A, **{'Content-Type': 'application/json'}), method='POST')
    return urllib.request.urlopen(req, timeout=60).status


def wiki_fetch(targets):
    """Batch-fetch {target: (extract, thumb)}; guards disambig/stub."""
    out = {}
    for i in range(0, len(targets), 20):
        chunk = targets[i:i + 20]
        q = urllib.parse.urlencode({
            'action': 'query', 'format': 'json', 'titles': '|'.join(chunk),
            'prop': 'extracts|pageimages', 'exintro': '1',
            'explaintext': '1', 'pithumbsize': '800', 'redirects': '1'})
        try:
            req = urllib.request.Request(
                'https://en.wikipedia.org/w/api.php?' + q, headers=UA)
            d = json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            print('WIKI-BATCH-ERR %s' % str(e)[:60], flush=True)
            time.sleep(5)
            continue
        pages = (d.get('query') or {}).get('pages', {})
        redir = {r['to']: r['from'] for r in
                 (d.get('query') or {}).get('redirects', []) or []}
        norm = {n['to']: n['from'] for n in
                (d.get('query') or {}).get('normalized', []) or []}
        for p in pages.values():
            title = p.get('title') or ''
            if p.get('missing') is not None:
                print('  TARGET-MISSING %s' % title, flush=True)
                continue
            sent = redir.get(title, title)
            sent = norm.get(sent, sent)
            extract = (p.get('extract') or '').strip()
            low = extract[:400].lower()
            if 'may refer to' in low or 'can refer to' in low:
                print('  TARGET-DISAMB %s' % sent, flush=True)
                continue
            if len(extract) < 80:
                print('  TARGET-SHORT %s (%d)' % (sent, len(extract)), flush=True)
                continue
            thumb = (p.get('thumbnail') or {}).get('source')
            out[sent] = (extract[:900], thumb)
        time.sleep(PACE)
    return out


def main():
    work = json.load(open(SCRATCH + 'bio-worklist.json'))
    by_name = {w['name']: w for w in work}
    print('fetching %d targets' % len(PAIRS), flush=True)
    fetched = wiki_fetch([t for _, t, _ in PAIRS])
    print('targets with prose: %d/%d' % (len(fetched), len(PAIRS)), flush=True)
    for name, target, band_page in PAIRS:
        w = by_name.get(name)
        if not w:
            print('NO-ROW %s' % name, flush=True)
            log_result({'name': name, 'status': 'NO-ROW'})
            continue
        if target not in fetched:
            print('GAP-NO-PROSE %s' % name, flush=True)
            log_result({'name': name, 'status': 'GAP-NO-PROSE'})
            continue
        bio, thumb = fetched[target]
        pid = w['id']
        try:
            row = jf_get('%s/Users/%s/Items/%s' % (J, U, pid))
            if row.get('Overview'):
                print('SKIP-ARRIVED %s' % name, flush=True)
                log_result({'name': name, 'status': 'SKIP-ARRIVED'})
                continue
            before = (row.get('Name'), dict(row.get('ProviderIds') or {}),
                      bool((row.get('ImageTags') or {}).get('Primary')))
            row['Overview'] = bio
            row['LockedFields'] = list(set(
                (row.get('LockedFields') or []) + ['Name', 'Overview']))
            row.pop('Genres', None)
            try:
                jf_post('%s/Items/%s' % (J, pid), row)
            except Exception:
                row2 = {'Name': row.get('Name'), 'Overview': bio,
                        'LockedFields': row['LockedFields']}
                jf_post('%s/Items/%s' % (J, pid), row2)
            time.sleep(1)
            back = jf_get('%s/Users/%s/Items/%s?Fields=Overview,LockedFields'
                          % (J, U, pid))
            got = len(back.get('Overview') or '')
            status = 'DONE' if got >= 80 else ('PARTIAL' if got >= 15
                                               else 'WRITE-FAIL')
            img_ok, img_note = True, ''
            if thumb and not band_page and not before[2]:
                try:
                    raw = urllib.request.urlopen(urllib.request.Request(
                        thumb, headers=UA), timeout=60).read()
                    if len(raw) >= 15000 and not thumb.lower().endswith('.svg'):
                        req = urllib.request.Request(
                            '%s/Items/%s/Images/Primary' % (J, pid),
                            data=base64.b64encode(raw),
                            headers=dict(A, **{'Content-Type': 'image/jpeg'}),
                            method='POST')
                        urllib.request.urlopen(req, timeout=60).status
                        served = urllib.request.urlopen(
                            urllib.request.Request(
                                '%s/Items/%s/Images/Primary?maxWidth=5000'
                                % (J, pid), headers=A), timeout=60).read()
                        img_ok = (hashlib.md5(served).hexdigest()
                                  == hashlib.md5(raw).hexdigest())
                        img_note = ', portrait' if img_ok else ', portrait-FAIL'
                    else:
                        img_note = ', portrait-skipped-small'
                except Exception as e:
                    img_note = ', portrait-ERR'
            elif band_page:
                img_note = ', portrait-skipped-band'
            # untouched-field diff
            cur = jf_get('%s/Users/%s/Items/%s' % (J, U, pid))
            clean = (cur.get('Name') == before[0]
                     and dict(cur.get('ProviderIds') or {}) == before[1])
            print('%s %s (%d chars, wikipedia%s)%s' % (
                status, name, got, img_note,
                '' if clean else ' !!UNTOUCHED-DIRTY'), flush=True)
            log_result({'name': name, 'status': status, 'chars': got,
                        'src': 'wikipedia-alias', 'clean': clean})
        except Exception as e:
            print('ERR %s %s' % (name, str(e)[:70]), flush=True)
            log_result({'name': name, 'status': 'ERR', 'err': str(e)[:70]})
        time.sleep(PACE)
    print('ALIAS-WORKER-FINISHED', flush=True)


main()
