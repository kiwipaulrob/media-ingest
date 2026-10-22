"""Classify a series' ghost rows: extras, duplicates, or genuinely absent?

A raw ghost count overstates what is missing. Three different things produce a row
with no file:

  * EXTRA      — "Bonus Material" / "Additional Interviews" rows: companion pieces,
                 not standalone episodes;
  * DUPLICATE  — the same episode the library already holds at a different slot,
                 because the provider's season numbering differs from the library's
                 scene numbering (this is why "The Who: Who's Next" showed as a gap
                 while sitting in the library under another season);
  * ABSENT     — no copy anywhere: the only rows worth hunting.

Reporting them as one number is how "56 missing episodes" becomes a wrong answer.
"""
import argparse
import sys

sys.path.insert(0, ".")
from media_lib import jellyfin, rowcheck  # noqa: E402

EXTRA_MARKERS = ("bonus", "additional", "deleted scene", "extra")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="Classic Albums")
    args = ap.parse_args()

    uid = jellyfin.user_id()
    found = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "Recursive": "true", "SearchTerm": args.series,
        "IncludeItemTypes": "Series", "Limit": 10}).get("Items") or []
    series = [x for x in found if x.get("Type") == "Series"]
    if not series:
        print(f"no series matched {args.series!r}")
        return 2
    sid = series[0]["Id"]

    eps = jellyfin.get_json(f"/Shows/{sid}/Episodes", params={
        "UserId": uid, "Fields": "Path", "Limit": 500}).get("Items") or []
    held = [e for e in eps if e.get("Path")]
    ghosts = [e for e in eps if not e.get("Path")]
    held_index = [(e.get("Name") or "", rowcheck.tokens(e.get("Name"))) for e in held]

    def held_as(name: str):
        """The held episode this ghost duplicates, or None."""
        want = rowcheck.tokens(name)
        if not want:
            return None
        best, score = "", 0.0
        for hname, htoks in held_index:
            if not htoks:
                continue
            overlap = len(want.intersection(htoks)) / max(1, len(want))
            if overlap > score:
                best, score = hname, overlap
        return best if score >= 0.6 else None

    extras, dupes, absent = [], [], []
    for g in ghosts:
        name = (g.get("Name") or "").strip()
        if any(marker in name.lower() for marker in EXTRA_MARKERS):
            extras.append(name)
            continue
        match = held_as(name)
        if match:
            dupes.append((name, match))
        else:
            absent.append(name)

    print(f"series={series[0].get('Name')!r}: {len(eps)} rows = "
          f"{len(held)} held + {len(ghosts)} ghost")
    print(f"  {len(extras):>3}  extras / bonus-material rows (not standalone episodes)")
    print(f"  {len(dupes):>3}  DUPLICATES — the episode is already held at another slot")
    print(f"  {len(absent):>3}  GENUINELY ABSENT — the only ones worth hunting")

    print("\ngenuinely absent episodes:")
    for name in sorted(set(absent)):
        print("   -", name)

    print("\nduplicate ghost rows (episode held elsewhere):")
    for name, match in sorted(set(dupes)):
        print(f"   - {name[:46]:<47} already held as: {match[:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
