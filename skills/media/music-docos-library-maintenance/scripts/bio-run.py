#!/usr/bin/env python3
"""Bio-fill worker: TMDB -> Last.fm (if key) -> Wikipedia -> Wikidata.

Run after bio-worklist.py: python3 -u bio-run.py [N]  (default batch 600).
Needs TMDB_API_KEY / JELLYFIN_URL / JELLYFIN_API_KEY in env; LASTFM_API_KEY
optional (Phase 3a activates only when present).

JSONL incremental results (SCRATCH/bio-run.jsonl): each outcome is appended
the moment it is decided, so a killed run loses nothing - re-running skips
names logged DONE/PARTIAL/SKIP-ARRIVED (resume). Never-clobber (re-reads
each row before writing). Classification: >=80 chars DONE, 15-79 PARTIAL
(locked one-liner by design), <15 WRITE-FAIL. WIKI-REJECT lines are the
human-confirm queue (alias/near-miss names) - see references/people-
enrichment.md Phase 3. Portraits: Base64 upload + byte-verify, imageless
rows only, >= 15 KB.
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

T = os.environ['TMDB_API_KEY']
J = os.environ['JELLYFIN_URL']
K = os.environ['JELLYFIN_API_KEY']
LFM = os.environ.get('LASTFM_API_KEY', '')
U = '9905188a302c4666952a8f69cca6cee9'
A = {'Authorization': 'MediaBrowser Token="%s"' % K,
     'User-Agent': 'HermesMediaBot/1.0'}
SCRATCH = '/root/.hermes/profiles/media-maintainer/cache/scratch/'
LOG = SCRATCH + 'bio-run.jsonl'
BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 600
PACE = 2.5


def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    s = s.split('Read more on Last.fm')[0]
    return re.sub(r'\s+', ' ', s).strip()


def jf_get(url):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=A), timeout=60))


def jf_post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
        headers=dict(A, **{'Content-Type': 'application/json'}), method='POST')
    return urllib.request.urlopen(req, timeout=60).status


# resume: names already DONE/PARTIAL this run
done = set()
if os.path.exists(LOG):
    for line in open(LOG):
        try:
            r = json.loads(line)
            if r.get('status') in ('DONE', 'PARTIAL', 'SKIP-ARRIVED'):
                done.add(r['name'])
        except Exception:
            pass
print('resume: %d names already logged DONE/PARTIAL' % len(done), flush=True)


def log_result(rec):
    with open(LOG, 'a') as fh:
        fh.write(json.dumps(rec) + '\n')


# ---- sources
def tmdb_bio(name, tmdb):
    pid = tmdb
    if not pid:
        try:
            with urllib.request.urlopen(
                'https://api.themoviedb.org/3/search/person?api_key=%s&query=%s'
                % (T, urllib.parse.quote(name)), timeout=30) as r:
                hits = json.load(r).get('results', [])
            exact = [h for h in hits if (h.get('name') or '').lower() == name.lower()]
            if not exact:
                return (None, None)
            pid = exact[0]['id']
        except Exception:
            return (None, None)
    try:
        with urllib.request.urlopen(
            'https://api.themoviedb.org/3/person/%s?api_key=%s' % (pid, T),
            timeout=30) as r:
            d = json.load(r)
        bio = strip_html(d.get('biography') or '')
        prof = d.get('profile_path')
        return (bio, ('https://image.tmdb.org/t/p/original' + prof) if prof else None)
    except Exception:
        return (None, None)


def lastfm_bio(name):
    if not LFM:
        return (None, None)
    try:
        q = urllib.parse.urlencode({'method': 'artist.getinfo', 'artist': name,
            'api_key': LFM, 'format': 'json'})
        with urllib.request.urlopen(
            'http://ws.audioscrobbler.com/2.0/?' + q, timeout=30) as r:
            d = json.load(r)
        artist = d.get('artist') or {}
        bio = strip_html((artist.get('bio') or {}).get('summary') or '')
        imgs = [i.get('text') for i in (artist.get('image') or []) if i.get('text')]
        return (bio or None, imgs[-1] if imgs else None)
    except Exception:
        return (None, None)


def wiki_batch(names):
    out = {}
    for i in range(0, len(names), 20):
        chunk = names[i:i + 20]
        q = urllib.parse.urlencode({
            'action': 'query', 'format': 'json',
            'titles': '|'.join(chunk),
            'prop': 'extracts|pageimages', 'exintro': '1',
            'explaintext': '1', 'pithumbsize': '800', 'redirects': '1'})
        try:
            req = urllib.request.Request(
                'https://en.wikipedia.org/w/api.php?' + q,
                headers={'User-Agent': 'HermesMediaBot/1.0 (bio sweep)'})
            d = json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            print('WIKI-BATCH-ERR %s' % str(e)[:60], flush=True)
            time.sleep(5)
            continue
        pages = (d.get('query') or {}).get('pages', {})
        inv = {r['to']: r['from'] for r in
               (d.get('query') or {}).get('redirects', []) or []}
        norm = {n['to']: n['from'] for n in
                (d.get('query') or {}).get('normalized', []) or []}
        for p in pages.values():
            title = p.get('title') or ''
            sent = inv.get(title, title)
            sent = norm.get(sent, sent)
            if p.get('missing') is not None:
                continue
            sent_n = re.sub(r'[\s_]+', ' ', sent).strip().lower()
            title_n = re.sub(r'[\s_]+', ' ', title).strip().lower()
            if sent_n != title_n:
                print('  WIKI-REJECT %s -> %s' % (sent, title), flush=True)
                continue
            extract = (p.get('extract') or '').strip()
            if len(extract) < 80:
                continue
            # disambiguation CONTENT guard: a disambig page's title IS the
            # exact name, so the title gate passes it - only the text reveals it
            low = extract[:400].lower()
            if 'may refer to' in low or \
               re.match(r'^\s*(\w[\w\' -]*,?\s*){1,4}(may refer to|can refer to)', low):
                print('  WIKI-DISAMB %s (disambiguation page)' % sent, flush=True)
                continue
            thumb = (p.get('thumbnail') or {}).get('source')
            out[sent] = (extract[:900], thumb)
        time.sleep(PACE)
    return out


def wikidata(name):
    try:
        q = urllib.parse.urlencode({'action': 'wbsearchentities', 'language': 'en',
            'search': name, 'format': 'json', 'limit': '3', 'type': 'item'})
        req = urllib.request.Request(
            'https://www.wikidata.org/w/api.php?' + q,
            headers={'User-Agent': 'HermesMediaBot/1.0 (bio sweep)'})
        d = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception:
        return None
    for hit in d.get('search', []):
        if re.sub(r'[\s_]+', ' ', hit.get('label') or '').strip().lower() != \
           re.sub(r'[\s_]+', ' ', name).strip().lower():
            continue
        desc = (hit.get('description') or '').strip()
        if len(desc) >= 15:
            return desc
    return None


def fetch_bytes(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=A),
                                  timeout=60).read()


def upload_portrait(pid, img):
    req = urllib.request.Request(
        '%s/Items/%s/Images/Primary' % (J, pid), data=base64.b64encode(img),
        headers=dict(A, **{'Content-Type': 'image/jpeg'}), method='POST')
    urllib.request.urlopen(req, timeout=60).status
    got = urllib.request.urlopen(urllib.request.Request(
        '%s/Items/%s/Images/Primary?maxWidth=5000' % (J, pid), headers=A),
        timeout=60).read()
    return hashlib.md5(got).hexdigest() == hashlib.md5(img).hexdigest()


def main():
    work = [w for w in json.load(open(SCRATCH + 'bio-worklist.json'))
            if w['name'] not in done][:BATCH]
    print('processing %d names (lastfm: %s)' % (
        len(work), 'key-present' if LFM else 'no-key'), flush=True)

    # Phase A: TMDB + Last.fm
    for w in work:
        bio, img = tmdb_bio(w['name'], w.get('tmdb'))
        if bio and len(bio) >= 80:
            w['_bio'], w['_src'], w['_img'] = bio, 'tmdb', img
        else:
            lbio, limg = lastfm_bio(w['name'])
            w['_img'] = img or limg
            if lbio and len(lbio) >= 80:
                w['_bio'], w['_src'] = lbio, 'lastfm'
        time.sleep(1.2)

    # Phase B: Wikipedia batched
    missing = [w['name'] for w in work if not w.get('_bio')]
    if missing:
        print('wiki batch for %d names' % len(missing), flush=True)
        wikis = wiki_batch(missing)
        for w in work:
            if not w.get('_bio') and w['name'] in wikis:
                # tuple: (extract, thumb) - unpack BOTH, never assign the
                # tuple itself to Overview (server 400s on a JSON array)
                w['_bio'], w['_img'] = wikis[w['name']]
                w['_src'] = 'wikipedia'

    # Phase C: Wikidata one-liner
    for w in work:
        if w.get('_bio'):
            continue
        desc = wikidata(w['name'])
        time.sleep(PACE)
        if desc:
            w['_bio'], w['_src'] = desc, 'wikidata'

    # write + verify + log each immediately
    for w in work:
        name, pid = w['name'], w['id']
        if not w.get('_bio'):
            print('GAP %s' % name, flush=True)
            log_result({'name': name, 'status': 'GAP'})
            continue
        try:
            row = jf_get('%s/Users/%s/Items/%s' % (J, U, pid))
            if row.get('Overview'):
                print('SKIP-ARRIVED %s' % name, flush=True)
                log_result({'name': name, 'status': 'SKIP-ARRIVED'})
                continue
            row['Overview'] = w['_bio']
            row['LockedFields'] = list(set(
                (row.get('LockedFields') or []) + ['Name', 'Overview']))
            try:
                jf_post('%s/Items/%s' % (J, pid), row)
            except Exception:
                # minimal-body retry (DTO field rejection trap)
                row2 = {'Name': row.get('Name'), 'Overview': w['_bio'],
                        'LockedFields': row['LockedFields']}
                try:
                    jf_post('%s/Items/%s' % (J, pid), row2)
                except urllib.error.HTTPError as he:
                    # the 400 BODY names the failing field - read it before
                    # changing the payload shape again
                    print('  POST400 body: %s' % he.read().decode()[:400], flush=True)
                    print('  DEBUG bio-src=%s len=%d repr-head=%r' % (
                        w.get('_src'), len(w['_bio']), w['_bio'][:120]), flush=True)
                    raise
            time.sleep(1)
            back = jf_get('%s/Users/%s/Items/%s?Fields=Overview' % (J, U, pid))
            got = len(back.get('Overview') or '')
            # >=80 chars = full prose (DONE); 15-79 = Wikidata one-liner (PARTIAL,
            # written+locked by design so a later pass can improve it)
            if got >= 80:
                status = 'DONE'
            elif got >= 15:
                status = 'PARTIAL'
            else:
                status = 'WRITE-FAIL'
            img_ok = True
            if w.get('_img') and not ((row.get('ImageTags') or {}).get('Primary')):
                try:
                    img = fetch_bytes(w['_img'])
                    if len(img) >= 15000:
                        img_ok = upload_portrait(pid, img)
                except Exception:
                    pass
            print('%s %s (%d chars, %s%s)' % (status, name, got, w['_src'],
                  ', img' if img_ok else ''), flush=True)
            log_result({'name': name, 'status': status, 'chars': got,
                        'src': w['_src'], 'img': img_ok})
        except Exception as e:
            print('ERR %s %s' % (name, str(e)[:70]), flush=True)
            log_result({'name': name, 'status': 'ERR', 'err': str(e)[:70]})
        time.sleep(PACE)

    # summary from the log (the audit trail), not from in-memory state
    c = Counter()
    if os.path.exists(LOG):
        for line in open(LOG):
            try:
                c[json.loads(line).get('status')] += 1
            except Exception:
                pass
    print('SUMMARY (log totals): %s' % dict(c), flush=True)
    print('WORKER-FINISHED', flush=True)


main()
