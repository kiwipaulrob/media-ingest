"""Hunt the individual titles, not the series pool.

The series-level pool misses episodes for two reasons proven today:
  * a posting may not name the series at all (the Classic Albums L.A. Woman episode
    is "BBC The Doors The Story of LA Woman");
  * the pool query returns a bounded slice, so a small run of episodes sits beyond it
    (Rock Family Trees returned only 19 postings for a series with 11 wanted).

So each wanted title gets its OWN query, and a hit is reported with size/seeds so it
can be judged before anything is queued.
"""
import sys

sys.path.insert(0, ".")
from media_lib import source  # noqa: E402

#: (label, [queries to try])
WANTED = [
    # This Is Pop S01E02 — the only ghost in that series.
    ("This Is Pop S01E02 'Hail Britpop!'",
     ["This Is Pop Hail Britpop", "This Is Pop S01E02", "This Is Pop S01 1080p"]),
    # Rock Family Trees — the five the series pool never surfaced.
    ("Rock Family Trees: The Birmingham Beat",
     ["Rock Family Trees Birmingham Beat", "Rock Family Trees Birmingham"]),
    ("Rock Family Trees: New York Punk",
     ["Rock Family Trees New York Punk", "Rock Family Trees New York"]),
    ("Rock Family Trees: California Dreamin'",
     ["Rock Family Trees California Dreamin", "Rock Family Trees California"]),
    ("Rock Family Trees: The Mersey Sound",
     ["Rock Family Trees Mersey Sound", "Rock Family Trees Mersey"]),
    ("Rock Family Trees: And God Created Manchester",
     ["Rock Family Trees God Created Manchester", "Rock Family Trees Manchester"]),
]


def report(label: str, res) -> None:
    seen = set()
    rows = []
    for r in res:
        title = (r.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        if not source.is_video_posting(r):
            continue
        if r.get("protocol") == "torrent" and (r.get("seeders") or 0) < 1:
            continue
        rows.append((r.get("size") or 0, r.get("seeders") or 0, r.get("protocol"), title))
    rows.sort(key=lambda x: (x[2] != "usenet", -x[0]))
    print(f"\n--- {label}: {len(rows)} video posting(s)")
    for size, seeds, proto, title in rows[:6]:
        print(f"    {size/1e9:6.2f}GB {seeds:>4}s {str(proto):<6} {title[:74]}")


def main() -> int:
    for label, queries in WANTED:
        pool, seen = {}, set()
        for q in queries:
            for r in source._prowlarr(q):
                t = (r.get("title") or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    pool[t] = r
        report(label, list(pool.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
