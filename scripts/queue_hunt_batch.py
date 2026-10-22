"""Queue an approved hunt batch.

Targets are matched by an explicit title fragment rather than by "best match",
because the Prowlarr pool contains near-duplicates (two Live-from-Abbey-Road 2018
sets differing by seed count, four Black Sabbath postings) and silently picking the
wrong one is how a batch ends up with the lower-seeded copy.

Also, because this *arr pipeline FORCES downloads (93 running against a cap of 25),
a plain add lands in `queuedDL` and never starts. Each added torrent is force-started
and then verified present — an add that is not verified is not a queued download.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
from media_lib import config, source  # noqa: E402

QB = "http://192.168.214.11:8090"

#: (label, prowlarr query, fragment that MUST appear, want_pack, why this one)
#:
#: `want_pack` rejects postings carrying an episode marker (S57E06). Without it the
#: fragment "jools holland s57" matched a single episode, and picking max-seeds then
#: chose the 4-seed episode over the 1-seed COMPLETE season — the opposite of intent.
TARGETS = [
    ("The Doors - L.A. Woman",
     "Doors L.A. Woman", "story of la woman 1080i", False,
     "1080i, 7 seeds (the 576p is the fallback)"),
    ("Jools S56", "Later with Jools Holland", "jools holland s56", True, "13.4 GB pack"),
    ("Jools S57", "Later with Jools Holland", "jools holland s57 complete", True,
     "10.1 GB pack (the S57E06 single is not a substitute)"),
    ("Jools S58", "Later with Jools Holland", "jools holland s58 complete", True, "10.2 GB pack"),
    ("Jools S61", "Later with Jools Holland", "jools holland s61 complete", True, "10.3 GB pack"),
    ("Jools S62", "Later with Jools Holland", "jools holland s62", True, "10.3 GB pack"),
]

# The pack test lives in media_lib.source.is_pack so this script, cli/source and
# any future caller cannot disagree about what a "pack" is.


def qb_get(path: str):
    with urllib.request.urlopen(f"{QB}/api/v2/{path}", timeout=60) as fh:
        return json.loads(fh.read() or b"[]")


def qb_post(path: str, data: dict):
    req = urllib.request.Request(
        f"{QB}/api/v2/{path}", data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        return fh.read()


def main() -> int:
    existing = {t["hash"].lower() for t in qb_get("torrents/info")}
    print(f"queue currently holds {len(existing)} torrents\n")

    chosen = []
    for label, query, fragment, want_pack, why in TARGETS:
        res = source._prowlarr(query)
        pool = {r.get("title"): r for r in res if r.get("title")}
        hits = [r for t, r in pool.items() if fragment in source.norm(t)]
        if want_pack:
            hits = [r for r in hits if source.is_pack(r.get("title") or "")]
        if not hits:
            print(f"  MISS  {label}: no posting matched {fragment!r}"
                  f"{' (pack required)' if want_pack else ''}")
            continue
        # Prefer the most-seeded posting among the matches.
        best = max(hits, key=lambda r: (r.get("seeders") or 0))
        chosen.append((label, best, why))
        print(f"  PICK  {label:<32} {(best.get('size') or 0)/1e9:6.2f}GB "
              f"{str(best.get('seeders')):>4}s  {(best.get('title') or '')[:52]}")

    print(f"\nadding {len(chosen)} torrent(s):")
    added = []
    for label, res, why in chosen:
        title = res.get("title") or ""
        infohash = (source.hash_of(res) or "").lower()
        if infohash and infohash in existing:
            print(f"  SKIP  {label}: already in the queue ({infohash[:12]})")
            continue
        out = source.queue({
            "title": title, "url": source.queue_url(res),
            "hash": infohash, "gb": round((res.get("size") or 0) / 1e9, 2),
            "seeds": res.get("seeders"), "protocol": res.get("protocol"),
        })
        if not out.get("ok"):
            print(f"  FAIL  {label}: {out.get('reason') or out}")
            continue
        # queue() force-starts and resolves the torrent by title; a result with no
        # infohash still reports the real queue state this way.
        print(f"  ADDED {label:<32} {(out.get('hash') or '?')[:12]} "
              f"state={out.get('state') or '?'} verified={out.get('verified')}  ({why})")
        added.append((label, out.get("hash") or title, title))
        time.sleep(1.5)

    time.sleep(6)
    print("\nverification (by title — infohash is often absent from a Prowlarr result):")
    ok = 0
    for label, _, title in added:
        entry = source.qb.by_title(title)
        if not entry:
            print(f"  MISSING {label} — added but not in the queue")
            continue
        ok += 1
        print(f"  OK  {label:<32} {entry['progress']*100:5.1f}%  "
              f"state={entry['state']:<12} {entry['size']/1e9:6.2f}GB")
    print(f"\nqueued and verified: {ok}/{len(added)}")
    return 0 if ok == len(added) else 1


if __name__ == "__main__":
    sys.exit(main())
