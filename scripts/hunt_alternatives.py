#!/usr/bin/env python3
"""Find a LIVE alternative release for every Classic Albums torrent that is dead.

A torrent at 0% with 0 seeds is a dead swarm, not a slow one: reannouncing proved
that (1 of 116 gained a peer). The fix is a different release, so this looks for
another posting of the same album with real seeds, skipping any hash already in
the queue.
"""
import json, os, re, time, urllib.parse, urllib.request

U = os.environ['PROWLARR_URL'].rstrip('/')
K = os.environ['PROWLARR_API_KEY']
QB = 'http://192.168.214.11:8090'
STATE = '/root/.hermes/profiles/media-maintainer/state'
OUT = f'{STATE}/ca-alternatives.json'
JUNK = re.compile(r'\b(flac|mp3|320|discography|box ?set|audiobook|ebook|dvd-audio|dvda)\b', re.I)


def qb(path, data=None):
    if data is not None:
        r = urllib.request.Request(f'{QB}/api/v2/{path}',
                                   data=urllib.parse.urlencode(data).encode(),
                                   headers={'Content-Type': 'application/x-www-form-urlencoded'})
    else:
        r = urllib.request.Request(f'{QB}/api/v2/{path}')
    with urllib.request.urlopen(r, timeout=60) as f:
        return f.read()


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


def hash_of(res):
    for k in ('magnetUrl', 'guid'):
        v = res.get(k) or ''
        m = re.search(r'btih:([0-9a-fA-F]{40})', v)
        if m:
            return m.group(1).lower()
    blob = (res.get('guid') or '') + (res.get('downloadUrl') or '')
    m = re.search(r'[0-9a-fA-F]{40}', blob)          # bare hash: group(0)
    return m.group(0).lower() if m else None


def url_of(res):
    for k in ('magnetUrl', 'guid'):
        v = res.get(k) or ''
        if v.startswith('magnet:'):
            return v
    h = hash_of(res)
    if h:
        return f"magnet:?xt=urn:btih:{h}&dn=" + urllib.parse.quote(res.get('title') or '')
    d = res.get('downloadUrl') or ''
    return (d + ('&' if '?' in d else '?') + 'apikey=' + K) if d else None


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def main():
    torrents = json.loads(qb('torrents/info') or b'[]')
    have = {t['hash'].lower() for t in torrents}
    # dead = no data at all, or stalled with no seeds and below 90%
    dead = [t for t in torrents
            if 'classic' in (t.get('name') or '').lower()
            and (t.get('progress', 0) == 0
                 or ((t.get('num_seeds') or 0) == 0 and t.get('progress', 0) < 0.9))]
    print(f'dead/stalled Classic Albums: {len(dead)}', flush=True)
    results = json.load(open(OUT)) if os.path.exists(OUT) else {}
    added = []

    for t in dead:
        name = t.get('name') or ''
        if name in results:
            continue
        # album identity from the torrent name: strip the series tokens
        words = [w for w in re.findall(r"[A-Za-z0-9']+", name)
                 if w.lower() not in {'classic', 'albums', 'the', 'bbc', 'and', 'of', 'a'}]
        artist = words[0] if words else ''
        album = ' '.join(words[1:4]) if len(words) > 1 else ''
        picks = []
        for q in (f'Classic Albums {artist} {album}', f'Classic Albums {artist}', f'{artist} {album} classic albums'):
            for c in search(q):
                title = c.get('title') or ''
                if JUNK.search(title):
                    continue
                nt = norm(title)
                if 'classic' not in nt or 'album' not in nt:
                    continue
                if norm(artist).split()[0] not in nt:
                    continue
                h = hash_of(c)
                if not h or h in have:
                    continue
                if (c.get('seeders') or 0) < 2:
                    continue
                picks.append({'title': title, 'seeds': c.get('seeders'), 'hash': h,
                              'gb': round((c.get('size') or 0) / 1e9, 2),
                              'indexer': c.get('indexer'), 'url': url_of(c)})
            if picks:
                break
        picks.sort(key=lambda p: -p['seeds'])
        seen, uniq = set(), []
        for p in picks:
            if p['hash'] in seen:
                continue
            seen.add(p['hash'])
            uniq.append(p)
        results[name] = uniq
        json.dump(results, open(OUT, 'w'), indent=1)
        if uniq:
            best = uniq[0]
            try:
                qb('torrents/add', {'urls': best['url'], 'category': 'musicdocos'})
                have.add(best['hash'])
                added.append((name, best))
                print(f"  QUEUED alt {best['seeds']:>3} seeds {best['gb']:>5}GB  {name[:44]:<45} <- {best['title'][:50]}", flush=True)
            except Exception as exc:                 # noqa: BLE001
                print(f'  ADD-FAIL {name[:44]} {type(exc).__name__}', flush=True)
        else:
            print(f'  NO-ALT    {name[:60]}', flush=True)
        time.sleep(1.0)

    print(f'\nalternatives queued: {len(added)} / {len(dead)} dead', flush=True)


if __name__ == '__main__':
    main()
