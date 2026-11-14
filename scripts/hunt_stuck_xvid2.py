"""Round 2: targeted queries for Star Stories, Kim Jong, NatParks packs, Ireland E03."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import source

QUERIES = [
    ("star-s02e01", "Star Stories S02E01"),
    ("star-s02e02", "Star Stories S02E02"),
    ("star-s02e03", "Star Stories S02E03"),
    ("star-s02e04", "Star Stories S02E04"),
    ("star-broad", "Star Stories 2006"),
    ("kimjong-1", "Kim Jong Comedy Club"),
    ("kimjong-2", "Storyville Comedy Club"),
    ("kimjong-3", "Kim Jong Il Comedy"),
    ("natparks-pack", "National Parks Americas Best Idea complete"),
    ("ireland-1", "Story of Ireland 3of5"),
    ("ireland-2", "Story of Ireland complete"),
]


def show(tag, results, limit=8):
    print(f"== {tag} ({len(results)}) ==")
    for r in results[:limit]:
        size = r.get("size")
        try:
            sz = f"{float(size)/1e9:.2f}GB" if size else "?"
        except (TypeError, ValueError):
            sz = "?"
        print(f"   {r.get('protocol')} seeds={r.get('seeders')} grabs={r.get('grabs')} {sz} "
              f"idx={r.get('indexer')} age={r.get('age_days')} | {(r.get('title') or '')[:105]}")


for tag, q in QUERIES:
    try:
        res = source._prowlarr(q)
    except Exception as e:
        print(f"== {tag} ERROR {e}")
        continue
    if tag == "natparks-pack":
        torr = [r for r in res if (r.get("protocol") or "") == "torrent"]
        show(tag + " [torrents]", torr, 10)
    else:
        show(tag, res)
