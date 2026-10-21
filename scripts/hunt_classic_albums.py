#!/usr/bin/env python3
"""Hunt missing Classic Albums episodes across Prowlarr (torrent + usenet)."""
import json, os, re, sys, time, urllib.parse, urllib.request

U = os.environ['PROWLARR_URL'].rstrip('/')
K = os.environ['PROWLARR_API_KEY']
OUT = '/root/.hermes/profiles/media-maintainer/state/ca-hunt.json'

TARGETS = [
    ("Paul Simon", "Graceland"), ("Grateful Dead", "Anthem to Beauty"),
    ("Stevie Wonder", "Songs in the Key of Life"), ("Fleetwood Mac", "Rumours"),
    ("U2", "The Joshua Tree"), ("Phil Collins", "Face Value"),
    ("Meat Loaf", "Bat Out of Hell"), ("Iron Maiden", "Number of the Beast"),
    ("Elton John", "Goodbye Yellow Brick Road"), ("Metallica", "Metallica"),
    ("Lou Reed", "Transformer"), ("Elvis Presley", "Elvis Presley"),
    ("Judas Priest", "British Steel"), ("Sex Pistols", "Never Mind the Bollocks"),
    ("Deep Purple", "Machine Head"), ("Def Leppard", "Hysteria"),
    ("Simply Red", "Stars"), ("Queen", "A Night at the Opera"),
    ("Jay-Z", "Reasonable Doubt"), ("John Lennon", "Plastic Ono Band"),
    ("Duran Duran", "Rio"), ("Black Sabbath", "Paranoid"),
    ("Tom Petty", "Damn the Torpedoes"), ("Rush", "2112 Moving Pictures"),
    ("Doors", "LA Woman"), ("Primal Scream", "Screamadelica"),
    ("Peter Gabriel", "So"), ("Who", "Tommy"), ("Don McLean", "American Pie"),
    ("Carly Simon", "No Secrets"), ("Tears for Fears", "Songs from the Big Chair"),
    ("Suede", "Coming Up"), ("Gil Scott-Heron", "Pieces of a Man"),
    ("Frank Zappa", "Freak Out"), ("Focus", "Moving Waves"),
    ("Herman Brood", "Shpritsz"), ("De Dijk", "Niemand in de stad"),
    ("Shocking Blue", "At Home"), ("Bettie Serveert", "Palomine"),
    ("Boudewijn de Groot", "Voor de overlevenden"), ("Cuby + Blizzards", "Groeten uit Grollo"),
]

JUNK = re.compile(r'\b(flac|mp3|320|discography|box set|audiobook|ebook)\b', re.I)


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def search(q):
    qs = {'query': q, 'type': 'search', 'limit': 100}
    url = f"{U}/api/v1/search?" + urllib.parse.urlencode(qs)
    for attempt in range(2):
        try:
            r = urllib.request.Request(url, headers={'X-Api-Key': K})
            with urllib.request.urlopen(r, timeout=90) as f:
                return json.loads(f.read() or b'[]')
        except Exception as e:
            if attempt:
                print(f'  ERR {q}: {type(e).__name__}', flush=True)
                return []
            time.sleep(4)
    return []


def matches(title, artist, album):
    t = norm(title)
    if 'classic' not in t or 'album' not in t:
        return False
    a = norm(artist).split()[0]           # first word of artist
    words = [w for w in norm(album).split() if len(w) > 2][:2]
    if a not in t:
        return False
    return any(w in t for w in words)


def hunt():
    results = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for i, (artist, album) in enumerate(TARGETS, 1):
        key = f'{artist} — {album}'
        if key in results:
            continue
        hits = []
        for q in (f'Classic Albums {artist} {album}', f'Classic Albums {artist}'):
            for c in search(q):
                title = c.get('title') or ''
                if JUNK.search(title) or not matches(title, artist, album):
                    continue
                hits.append({'title': title,
                             'seeds': c.get('seeders'),
                             'size_gb': round((c.get('size') or 0) / 1e9, 2),
                             'indexer': c.get('indexer'),
                             'protocol': c.get('protocol'),
                             'magnet': (c.get('magnetUrl') or '').startswith('magnet:'),
                             'guid_hash': (re.search(r'[0-9a-fA-F]{40}', c.get('guid') or '') or [None])[0]
                             if re.search(r'[0-9a-fA-F]{40}', c.get('guid') or '') else None})
            if hits:
                break
        # dedupe by title
        seen, uniq = set(), []
        for h in hits:
            if h['title'] in seen:
                continue
            seen.add(h['title'])
            uniq.append(h)
        results[key] = uniq
        json.dump(results, open(OUT, 'w'), indent=1)
        best = max(uniq, key=lambda h: (h['seeds'] or 0)) if uniq else None
        state = 'FOUND' if uniq else 'MISS'
        print(f"[{i:>2}/{len(TARGETS)}] {state} {key[:44]:<45} "
              f"{len(uniq)} hit(s)" + (f" best seeds={best['seeds']} {best['size_gb']}GB {best['indexer']}" if best else ''),
              flush=True)
    found = [k for k, v in results.items() if v]
    print(f'\nSUMMARY: {len(found)} found / {len(results)} searched', flush=True)


if __name__ == '__main__':
    hunt()
