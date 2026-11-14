"""Hunt replacements for the 15 stuck XviD recodes (minus Lewis&Clark: healthy)."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import source, hunt

WANTS = [
    ("star-s02e01", ["Star Stories S02", "Star Stories 2007"], ["star", "stories"], (2, 1)),
    ("star-s02e02", ["Star Stories S02", "Star Stories 2007"], ["star", "stories"], (2, 2)),
    ("star-s02e03", ["Star Stories S02", "Star Stories 2007"], ["star", "stories"], (2, 3)),
    ("star-s02e04", ["Star Stories S02", "Star Stories 2007"], ["star", "stories"], (2, 4)),
    ("kimjong", ["Kim Jong Il Comedy Club", "Storyville Kim Jong"], ["kim", "jong", "comedy", "club"], None),
    ("wasteland", ["Waste Land 2010"], ["waste", "land"], None),
    ("screenwipe", ["Screenwipe Oh Dear", "Screenwipe 2009"], ["screenwipe"], None),
    ("natparks-e01", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 1)),
    ("natparks-e02", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 2)),
    ("natparks-e03", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 3)),
    ("natparks-e04", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 4)),
    ("natparks-e05", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 5)),
    ("natparks-e06", ["National Parks Americas Best Idea", "National Parks 2009 Ken Burns"], ["national", "parks"], (1, 6)),
    ("ireland-e03", ["Story of Ireland 2011", "Story of Ireland Age of Revolution"], ["story", "ireland"], (1, 3)),
]

for tag, queries, keys, ep in WANTS:
    pool = source.fetch_pool(queries)
    cands = hunt.plan(list(pool.values()), keys=keys, episode=ep, min_seeders=0)
    print(f"== {tag} ({len(pool)} raw, {len(cands)} matching) ==")
    for r in cands[:6]:
        size = r.get("size")
        try:
            sz = f"{float(size)/1e9:.2f}GB" if size else "?"
        except (TypeError, ValueError):
            sz = "?"
        print(f"   {r.get('protocol')} seeds={r.get('seeders')} grabs={r.get('grabs')} {sz} "
              f"idx={r.get('indexer')} | {(r.get('title') or '')[:100]}")
    if not cands:
        sample = sorted(pool)[:4]
        for s in sample:
            print(f"   (raw) {s[:100]}")
