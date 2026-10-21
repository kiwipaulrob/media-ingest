#!/usr/bin/env python3
"""Emit the bio-less person worklist for Music Docos credits.

Reads live: Person rows (paged, global) + musicdocos credits (paged).
Writes <scratch>/bio-worklist.json: [{name, films, id, img, tmdb}] sorted
by film count. Read-only against Jellyfin; re-run before each bio-run to
refresh the list (never plan against a stale worklist).

Run: python3 -u bio-worklist.py
"""
import json
import urllib.parse
import urllib.request
from collections import Counter

J = __import__('re').search(r'JELLYFIN_URL=(\S+)', open('/root/.hermes/.env').read()).group(1)
K = __import__('re').search(r'JELLYFIN_API_KEY=(\S+)', open('/root/.hermes/.env').read()).group(1)
U = '9905188a302c4666952a8f69cca6cee9'
A = {'Authorization': 'MediaBrowser Token="%s"' % K}
SCRATCH = '/root/.hermes/profiles/media-maintainer/cache/scratch/'


def fetch(q):
    return json.load(urllib.request.urlopen(
        urllib.request.Request('%s/Users/%s/Items?%s' % (J, U, q), headers=A),
        timeout=90))


# Person rows (global, deduped by name)
persons = {}
start = 0
while True:
    d = fetch(urllib.parse.urlencode({'Recursive': 'true', 'IncludeItemTypes': 'Person',
        'Fields': 'Overview,ImageTags,ProviderIds', 'StartIndex': start, 'Limit': 200}))
    rows = d.get('Items', [])
    if not rows:
        break
    for r in rows:
        if r.get('Name') and r['Name'] not in persons:
            persons[r['Name']] = r
    start += len(rows)
    if start >= d.get('TotalRecordCount', 0):
        break
print('person rows:', len(persons))

# musicdocos credits
films = Counter()
nmovies = 0
start = 0
while True:
    d = fetch(urllib.parse.urlencode({'Recursive': 'true', 'IncludeItemTypes': 'Movie',
        'ParentId': '5f95760bae4c07e31d840d70c3a46111', 'Fields': 'People',
        'StartIndex': start, 'Limit': 300}))
    rows = d.get('Items', [])
    if not rows:
        break
    nmovies += len(rows)
    for x in rows:
        for p in x.get('People', []):
            films[p.get('Name')] += 1
    start += len(rows)
    if start >= d.get('TotalRecordCount', 0):
        break
print('musicdocos movies:', nmovies, '| credited names:', len(films))

work = []
for name, cnt in films.most_common():
    pr = persons.get(name)
    if not pr or pr.get('Overview'):
        continue
    work.append({'name': name, 'films': cnt, 'id': pr.get('Id'),
                 'img': bool((pr.get('ImageTags') or {}).get('Primary')),
                 'tmdb': (pr.get('ProviderIds') or {}).get('Tmdb')})
json.dump(work, open(SCRATCH + 'bio-worklist.json', 'w'))
print('bio-less with rows:', len(work))
for w in work[:22]:
    print('  %2d films | img:%s | tmdb:%s | %s | %s' % (
        w['films'], 'Y' if w['img'] else '-', w['tmdb'] or '-', w['name'], w['id']))
