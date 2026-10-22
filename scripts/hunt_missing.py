"""Hunt the genuinely-absent episodes of the music-doco series.

Two shapes of find, because the two are matched differently:

  * PER-EPISODE — the posting names the album/artist (Classic Albums postings read
    `Classic.Albums.S09E01.Black.Sabbath.Paranoid...`), so it can be matched to a
    ghost row by identity tokens;
  * SERIES PACK — a season/complete-set posting names only the series ("VA - Live
    from Abbey Road (E01-12) 2018"), so it is reported as a pack that may cover
    several ghosts rather than pretending to match one.

Ghosts are classified first (extras / duplicates / absent) so the hunt does not
chase episodes the library already holds under another slot.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from media_lib import jellyfin, rowcheck, source  # noqa: E402

EXTRA_MARKERS = ("bonus", "additional", "deleted scene", "extra")

#: Series -> the query that surfaces its postings.
QUERIES = {
    "Classic Albums": ["Classic Albums"],
    "Live from Abbey Road": ["Live from Abbey Road", "Abbey Road Live"],
    "Later... with Jools Holland": ["Later with Jools Holland", "Jools Holland"],
    "Rock Family Trees": ["Rock Family Trees"],
    "Jazz": ["Ken Burns Jazz"],
    "The Blues": ["The Blues Martin Scorsese"],
    "Latin Music USA": ["Latin Music USA"],
    "American Epic": ["American Epic"],
    "This Is Pop": ["This Is Pop"],
    "Punk Britannia": ["Punk Britannia"],
    "Howard Goodall's Story of Music": ["Howard Goodall Story of Music"],
    "Cinema Europe: The Other Hollywood": ["Cinema Europe Other Hollywood"],
}


def series_of(uid: str, name: str):
    found = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "Recursive": "true", "SearchTerm": name, "IncludeItemTypes": "Series",
        "Limit": 10}).get("Items") or []
    hits = [x for x in found if x.get("Type") == "Series"]
    return hits[0] if hits else None


def classify(uid: str, series_id: str):
    """(absent_names, duplicate_names, extras_count, held_count) for a series."""
    eps = jellyfin.get_json(f"/Shows/{series_id}/Episodes", params={
        "UserId": uid, "Fields": "Path", "Limit": 500}).get("Items") or []
    held = [e for e in eps if e.get("Path")]
    held_index = [(e.get("Name") or "", rowcheck.tokens(e.get("Name"))) for e in held]

    absent, dupes, extras = [], [], 0
    for e in eps:
        if e.get("Path"):
            continue
        name = (e.get("Name") or "").strip()
        if any(m in name.lower() for m in EXTRA_MARKERS):
            extras += 1
            continue
        want = rowcheck.tokens(name)
        best, score = "", 0.0
        for hname, htoks in held_index:
            if not htoks or not want:
                continue
            overlap = len(want.intersection(htoks)) / max(1, len(want))
            if overlap > score:
                best, score = hname, overlap
        (dupes if score >= 0.6 else absent).append(name)
    return absent, dupes, extras, len(held)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="*", default=None)
    ap.add_argument("--only-absent", action="store_true",
                    help="hunt every absent episode of the listed series")
    args = ap.parse_args()

    uid = jellyfin.user_id()
    names = args.series or list(QUERIES)
    report = {}

    for name in names:
        series = series_of(uid, name)
        if not series:
            print(f"\n### {name}: series not found")
            continue
        absent, dupes, extras, held = classify(uid, series["Id"])
        print(f"\n### {series.get('Name')}: {held} held, {extras} extras, "
              f"{len(dupes)} duplicates, {len(absent)} ABSENT")
        if not absent:
            continue

        pool = source.fetch_pool(QUERIES.get(name, [name]))
        print(f"    pool: {len(pool)} postings")

        matched, packs = [], []
        for posting, res in pool.items():
            ptoks = rowcheck.tokens(posting)
            if not ptoks:
                continue
            for episode in absent:
                want = rowcheck.tokens(episode)
                if not want:
                    continue
                overlap = len(want.intersection(ptoks)) / len(want)
                if overlap >= 0.6:
                    matched.append({
                        "episode": episode, "posting": posting,
                        "score": round(overlap, 2),
                        "gb": round((res.get("size") or 0) / 1e9, 2),
                        "seeds": res.get("seeders"),
                        "grabs": res.get("grabs"),
                        "protocol": res.get("protocol"),
                        "indexer": res.get("indexer"),
                    })
                    break
            else:
                # No single-episode match: does the posting name the SERIES?
                if rowcheck.tokens(name).intersection(ptoks) and (
                        res.get("protocol") == "usenet" or (res.get("seeders") or 0) > 0):
                    packs.append({
                        "posting": posting,
                        "gb": round((res.get("size") or 0) / 1e9, 2),
                        "seeds": res.get("seeders"),
                        "protocol": res.get("protocol"),
                        "indexer": res.get("indexer"),
                    })

        found_eps = {m["episode"] for m in matched}
        print(f"    per-episode matches: {len(found_eps)} of {len(absent)} absent")
        for m in sorted(matched, key=lambda m: -m["score"]):
            print(f"      [{m['score']:.2f}] {m['episode'][:38]:<39} "
                  f"{m['gb']:>5.2f}GB {str(m['seeds']):>4}s {m['protocol']:<6} "
                  f"{m['posting'][:44]}")
        still = [a for a in absent if a not in found_eps]
        if still:
            print(f"    still absent ({len(still)}): {', '.join(a[:26] for a in still[:8])}"
                  + (" ..." if len(still) > 8 else ""))
        if packs:
            print(f"    series packs (may cover several): {len(packs)}")
            for p in sorted(packs, key=lambda p: -p["gb"])[:6]:
                print(f"      {p['gb']:>5.2f}GB {str(p['seeds']):>4}s {p['protocol']:<6} "
                      f"{p['posting'][:56]}")
        report[name] = {"absent": absent, "matched": matched, "packs": packs}

    out = Path(__file__).resolve().parents[1] / "state" / "missing-hunt.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
