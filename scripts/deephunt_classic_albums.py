#!/usr/bin/env python3
"""Deep hunt the Classic Albums misses with alternate titles/queries."""
import json, os, re, time, urllib.parse, urllib.request

U = os.environ['PROWLARR_URL'].rstrip('/')
K = os.environ['PROWLARR_API_KEY']
OUT = '/root/.hermes/profiles/media-maintainer/state/ca-deep.json'

# miss -> alternate query set. Dutch titles are often unlisted by title, so try
# the broadcaster/compilation names too.
QUERIES = {
    'Jay-Z — Reasonable Doubt': ['Reasonable Doubt Classic Albums', 'Jay Z Reasonable Doubt documentary',
                                 'Classic Albums JayZ'],
    'Doors — LA Woman': ['L.A. Woman Classic Albums', 'LA Woman Doors documentary', 'Classic Albums Doors'],
    'Peter Gabriel — So': ['Classic Albums So', 'Peter Gabriel So documentary', 'Peter Gabriel Classic Albums'],
    'Who — Tommy': ['Classic Albums Tommy', 'The Who Tommy documentary', 'Amazing Journey The Who'],
    'Focus — Moving Waves': ['Moving Waves Focus', 'Focus Classic Albums', 'Klassieke Albums Focus'],
    'Herman Brood — Shpritsz': ['Herman Brood Shpritsz', 'Shpritsz Classic Albums', 'Herman Brood documentaire'],
    'De Dijk — Niemand in de stad': ['Niemand in de stad De Dijk', 'De Dijk Classic Albums'],
    'Shocking Blue — At Home': ['Shocking Blue At Home', 'Shocking Blue Classic Albums'],
    'Bettie Serveert — Palomine': ['Bettie Serveert Palomine', 'Palomine Classic Albums'],
    'Boudewijn de Groot — Voor de overlevenden': ['Boudewijn de Groot Voor de overlevenden',
                                                  'Voor de overlevenden Classic Albums'],
    'Cuby + Blizzards — Groeten uit Grollo': ['Cuby Blizzards Groeten uit Grollo',
                                              'Groeten uit Grollo Classic Albums'],
}

JUNK = re.compile(r'\b(flac|mp3|320kbps|discography|box ?set|audiobook|ebook)\b', re.I)


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def search(q):
    url = f"{U}/api/v1/search?" + urllib.parse.urlencode({'query': q, 'type': 'search', 'limit': 100})
    for attempt in range(2):
        try:
            r = urllib.request.Request(url, headers={'X-Api-Key': K})
            with urllib.request.urlopen(r, timeout=90) as f:
                return json.loads(f.read() or b'[]')
        except Exception:
            if attempt:
                return []
            time.sleep(4)
    return []


def wanted(title, key):
    t = norm(title)
    artist, album = [x.strip() for x in key.split('—', 1)]
    if JUNK.search(title or ''):
        return False
    a = norm(artist).split()[0]
    words = [w for w in norm(album).split() if len(w) > 2][:2]
    # accept a real episode hit, or any posting naming artist+album
    ep = ('classic' in t and 'album' in t)
    return a in t and (any(w in t for w in words) or ep) and (ep or any(w in t for w in words))


def main():
    res = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for key, qs in QUERIES.items():
        if res.get(key):
            continue
        hits = []
        for q in qs:
            for c in search(q):
                if wanted(c.get('title') or '', key):
                    hits.append({'title': c.get('title'), 'seeds': c.get('seeders'),
                                 'gb': round((c.get('size') or 0) / 1e9, 2),
                                 'indexer': c.get('indexer'), 'protocol': c.get('protocol'),
                                 'q': q})
            if hits:
                break
        seen, uniq = set(), []
        for h in hits:
            if h['title'] in seen:
                continue
            seen.add(h['title'])
            uniq.append(h)
        res[key] = uniq
        json.dump(res, open(OUT, 'w'), indent=1)
        best = max(uniq, key=lambda h: h.get('seeds') or 0) if uniq else None
        print(f"{'FOUND' if uniq else 'MISS':<6}{key[:44]:<45} {len(uniq)} hit(s)"
              + (f" best seeds={best['seeds']} {best['gb']}GB {best['indexer']}" if best else ''), flush=True)
    found = [k for k, v in res.items() if v]
    print(f'\nDEEP SUMMARY: {len(found)} found / {len(res)} searched', flush=True)


if __name__ == '__main__':
    main()
