"""Audit a series: every episode row with a file must be named for the episode
that FILE actually contains.

Exists because of a real failure on 2026-10-22: placing a file into a ghost row
(no `Path`) caused Jellyfin to re-resolve that row from the provider by episode
NUMBER, and TMDB's numbering differs from this library's scene numbering — so
S03E05 came back named "Judas Priest: British Steel" (TMDB S03E05) while the file
on it was Elvis Presley (TMDB S03E06). Same for S10E02 vs TMDB S09E02.

The comparison itself lives in `media_lib/rowcheck.py` so this script, the
placement CLI (`cli/ingest_new`) and any future caller cannot slowly disagree
about what counts as a match.

Usage:
    python3 scripts/audit_series_names.py
    python3 scripts/audit_series_names.py --search "Live from Abbey Road"

Exits 1 on any mismatch, so it can gate a batch.
"""
import argparse
import sys

sys.path.insert(0, ".")
from media_lib import jellyfin, rowcheck  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Row-name audit for a series")
    ap.add_argument("--search", default="Classic Albums",
                    help="SearchTerm used to find the series")
    args = ap.parse_args()

    uid = jellyfin.user_id()
    found = jellyfin.get_json(
        f"/Users/{uid}/Items",
        params={"Recursive": "true", "SearchTerm": args.search,
                "IncludeItemTypes": "Series", "Limit": 10},
    )
    series = [x for x in found.get("Items") or [] if x.get("Type") == "Series"]
    if not series:
        print(f"no series matched {args.search!r}")
        return 2
    sid = series[0]["Id"]

    episodes = jellyfin.get_json(
        f"/Shows/{sid}/Episodes",
        params={"UserId": uid, "Fields": "Path,Overview,ImageTags,LockedFields"},
    ).get("Items") or []
    real = [e for e in episodes if e.get("Path")]

    print(f"series={series[0].get('Name')!r}  rows={len(episodes)}  with-file={len(real)}  "
          f"with-overview={sum(1 for e in real if e.get('Overview'))}  "
          f"with-image={sum(1 for e in real if (e.get('ImageTags') or {}).get('Primary'))}")

    bad = [e for e in real
           if not rowcheck.names_match(e.get("Path") or "", e.get("Name"))]

    print(f"\nname-vs-file mismatches: {len(bad)}")
    for e in bad:
        print(f"  S{e.get('ParentIndexNumber'):02d}E{e.get('IndexNumber'):02d}  "
              f"row={e.get('Name')!r}")
        print(f"      expected from file: "
              f"{rowcheck.identity_from_filename(e.get('Path') or '')!r}")
        print(f"      id={e.get('Id')}  locked={e.get('LockedFields')}")
    if bad:
        print("\nrepair: re-run the placement with "
              "`cli.ingest_new --fix-names --tmdb-series <id>`, which resolves the "
              "episode BY NAME (never by episode number) and locks it.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
