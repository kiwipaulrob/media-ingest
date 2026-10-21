#!/usr/bin/env python3
"""Queue every found Classic Albums release into qBittorrent / SABnzbd."""
import json, os, re, time, urllib.parse, urllib.request

U = os.environ['PROWLARR_URL'].rstrip('/')
K = os.environ['PROWLARR_API_KEY']
QB = 'http://192.168.214.11:8090'
SAB = os.environ['SABNZBD_URL'].rstrip('/')
SABK = os.environ['SABNZBD_API_KEY']
STATE = '/root/.hermes/profiles/media-maintainer/state'
CAT_T = 'musicdocos'
CAT_U = 'movies'


def prowlarr(q):
    url = f"{U}/api/v1/search?" + urllib.parse.urlencode({'query': q, 'type': 'search', 'limit': 100})
    r = urllib.request.Request(url, headers={'X-Api-Key': K})
    with urllib.request.urlopen(r, timeout=90) as f:
        return json.loads(f.read() or b'[]')


def fetch_url(res):
    """Best queueable URL for a Prowlarr result."""
    for k in ('magnetUrl', 'guid'):
        v = res.get(k) or ''
        if v.startswith('magnet:'):
            return v, 'magnet'
    blob = (res.get('guid') or '') + (res.get('downloadUrl') or '')
    m = re.search(r'[0-9a-fA-F]{40}', blob)
    if m:
        return f"magnet:?xt=urn:btih:{m.group(0)}&dn=" + urllib.parse.quote(res.get('title') or ''), 'magnet'
    d = res.get('downloadUrl') or ''
    if d:
        return d + ('&' if '?' in d else '?') + 'apikey=' + K, 'downloadUrl'
    return None, None


def qb_add(url):
    body = urllib.parse.urlencode({'urls': url, 'category': CAT_T}).encode()
    r = urllib.request.Request(f'{QB}/api/v2/torrents/add', data=body,
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(r, timeout=60) as f:
        return f.read()[:60]


def sab_add(url, name):
    qs = urllib.parse.urlencode({'mode': 'addurl', 'name': url, 'nzbname': name,
                                 'cat': CAT_U, 'apikey': SABK})
    with urllib.request.urlopen(f'{SAB}/api?{qs}&output=json', timeout=90) as f:
        return json.loads(f.read() or b'{}')


def main():
    short = json.load(open(f'{STATE}/ca-shortlist.json'))
    added, failed = [], []
    for item in short:
        key = item['album']
        artist, album = [x.strip() for x in key.split('—', 1)]
        target_title = item['title']
        res = None
        for q in (f'Classic Albums {artist} {album}', f'Classic Albums {artist}'):
            try:
                hits = prowlarr(q)
            except Exception as e:
                print(f'  search err {key}: {type(e).__name__}', flush=True)
                continue
            res = next((h for h in hits if h.get('title') == target_title), None)
            if res:
                break
        if not res:
            failed.append((key, 'result vanished'))
            print(f'MISS   {key[:44]:<45} (result no longer listed)', flush=True)
            continue
        url, kind = fetch_url(res)
        if not url:
            failed.append((key, 'no url'))
            print(f'NOURL  {key[:44]:<45}', flush=True)
            continue
        try:
            if res.get('protocol') == 'usenet':
                out = sab_add(url, re.sub(r'[^A-Za-z0-9 ._-]', '', key))
                ok = bool(out.get('nzo_ids'))
                print(f"{'QUEUED' if ok else 'FAIL':<7}{key[:44]:<45} usenet", flush=True)
            else:
                qb_add(url)
                ok = True
                print(f"{'SENT':<7}{key[:44]:<45} {kind} seeds={res.get('seeders')}", flush=True)
            (added if ok else failed).append((key, kind))
        except Exception as e:
            failed.append((key, f'{type(e).__name__}: {e}'))
            print(f'FAIL   {key[:44]:<45} {type(e).__name__}', flush=True)
        time.sleep(1.2)
    json.dump({'added': added, 'failed': failed}, open(f'{STATE}/ca-queued.json', 'w'), indent=1)
    print(f'\nsent: {len(added)}  failed: {len(failed)}', flush=True)
    for f in failed:
        print('  FAILED:', f)


if __name__ == '__main__':
    main()
