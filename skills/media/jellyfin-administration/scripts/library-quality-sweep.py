#!/usr/bin/env python3
"""Library quality sweep: band every item by resolution and flag legacy codec/container.

Usage:  python3 library-quality-sweep.py [LibraryName ...]
        (default: Documentaries and "Music Docos")

Writes /root/library_sweep.json, /root/lowres_under720.csv, /root/recode_candidates.csv

IMPORTANT: Fields=MediaSources is what returns the streams. Fields=MediaStreams returns
nothing at all (no error), which makes every item read back as 'unknown'.
"""
import collections
import csv
import json
import sys
import urllib.request

ENV = '/root/.hermes/.env'
THRESHOLD = 720
DEFAULT_LIBS = ['Documentaries', 'Music Docos']

RECODE_V = {'mpeg2video', 'mpeg4', 'msmpeg4v3', 'msmpeg4v2', 'msmpeg4',
            'mpeg1video', 'wmv1', 'wmv2', 'wmv3', 'vc1'}
RECODE_C = {'avi', 'mpeg', 'mpg', 'vob', 'wmv', 'asf', 'flv', 'rm', 'rmvb'}


def load_env(path=ENV):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            out[k] = v
    return out


E = load_env()
JF = E['JELLYFIN_URL'].rstrip('/')
H = {'Authorization': 'MediaBrowser Token="%s"' % E['JELLYFIN_API_KEY']}


def get(path):
    req = urllib.request.Request(JF + path, headers=H)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def library_ids(want):
    out = {}
    for lib in get('/Library/VirtualFolders'):
        if lib.get('Name') in want:
            out[lib['Name']] = lib.get('ItemId')
    return out


def scan(lib_name, lib_id):
    rows, start = [], 0
    while True:
        page = (f"/Items?ParentId={lib_id}&Recursive=true"
                f"&IncludeItemTypes=Movie,Video,Episode"
                f"&Fields=Path,MediaSources,ProductionYear"
                f"&Limit=500&StartIndex={start}")
        items = get(page).get('Items', [])
        for i in items:
            w = h = vc = ac = cont = None
            for s in (i.get('MediaSources') or []):
                cont = cont or s.get('Container')
                for st in (s.get('MediaStreams') or []):
                    if st.get('Type') == 'Video' and w is None:
                        w, h, vc = st.get('Width'), st.get('Height'), st.get('Codec')
                    elif st.get('Type') == 'Audio' and ac is None:
                        ac = st.get('Codec')
                if w:
                    break
            rows.append({'lib': lib_name, 'name': i['Name'], 'year': i.get('ProductionYear'),
                         'w': w, 'h': h, 'vcodec': vc, 'acodec': ac,
                         'container': cont, 'path': i.get('Path')})
        if len(items) < 500:
            break
        start += 500
    return rows


def band(h):
    h = h or 0
    if h == 0:
        return 'unknown'
    if h <= 288:
        return '<=288p'
    if h <= 480:
        return '289-480p'
    if h <= 576:
        return '481-576p'
    if h < THRESHOLD:
        return '577-719p'
    if h <= 720:
        return '720p'
    if h <= 1080:
        return '1080p'
    return '>1080p'


def main():
    want = sys.argv[1:] or DEFAULT_LIBS
    ids = library_ids(set(want))
    if not ids:
        print('no matching libraries; want=%r' % want)
        return 1

    rows = []
    for name, lid in ids.items():
        got = scan(name, lid)
        print('  %-16s %d items' % (name, len(got)))
        rows += got

    json.dump(rows, open('/root/library_sweep.json', 'w'), indent=1)
    known = [r for r in rows if r['h']]
    if rows and not known:
        print('\n!! every item read back as unknown - the Fields= parameter is wrong '
              '(use Fields=MediaSources, not MediaStreams)')
        return 1

    counts = collections.Counter(band(r['h']) for r in rows)
    print('\n=== resolution ===')
    for k in ('<=288p', '289-480p', '481-576p', '577-719p',
              '720p', '1080p', '>1080p', 'unknown'):
        if counts.get(k):
            print('  %-10s %4d' % (k, counts[k]))

    low = [r for r in rows if (r['h'] or 0) and r['h'] < THRESHOLD]
    rec = [r for r in rows if r['vcodec'] in RECODE_V or r['container'] in RECODE_C]
    print('  below %dp: %d' % (THRESHOLD, len(low)))
    print('  recode candidates: %d  by codec %s'
          % (len(rec), dict(collections.Counter(r['vcodec'] for r in rec))))
    print('  library codec mix: %s' % dict(collections.Counter(r['vcodec'] for r in rows)))

    fields = ['h', 'w', 'vcodec', 'acodec', 'container', 'lib', 'year', 'name', 'path']
    for fn, data in (('/root/lowres_under720.csv', low),
                     ('/root/recode_candidates.csv', rec)):
        with open(fn, 'w', newline='') as f:
            wr = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            wr.writeheader()
            for r in sorted(data, key=lambda x: (x['h'] or 0)):
                wr.writerow(r)
        print('  wrote %s (%d rows)' % (fn, len(data)))

    print('\n=== worst offenders ===')
    for r in sorted(low, key=lambda x: (x['h'] or 0))[:25]:
        print('  %4dp [%s] %s %s' % (r['h'], r['lib'][:4], r['year'] or '----', r['name'][:60]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
