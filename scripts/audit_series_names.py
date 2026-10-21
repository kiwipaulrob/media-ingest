"""Audit the Classic Albums series: every episode row with a file must be named
for the episode its FILE actually contains.

This exists because of a real failure on 2026-10-22: placing a file into a ghost
row (no Path) caused Jellyfin to re-resolve that row from the provider by episode
NUMBER, and TMDB's numbering differs from this library's scene numbering — so
S03E05 came back named "Judas Priest: British Steel" (TMDB S03E05) while the file
on it was Elvis Presley (TMDB S03E06). Same for S10E02 vs TMDB S09E02.

Run after any placement into a previously-empty slot.
"""
import re
import sys

sys.path.insert(0, ".")
from media_lib import jellyfin  # noqa: E402

NOISE = {"the", "and", "of", "a", "vol", "part", "classic", "albums", "with"}


def norm(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str):
    # >= 3 so short album names still match ("Aja" is exactly 3 and was
    # reported as a mismatch when the filter was > 3)
    return {t for t in norm(s).split() if len(t) >= 3 and t not in NOISE}


def main() -> int:
    uid = jellyfin.user_id()
    d = jellyfin.get_json(
        f"/Users/{uid}/Items",
        params={"Recursive": "true", "SearchTerm": "Classic Albums",
                "IncludeItemTypes": "Series", "Limit": 5},
    )
    sid = [x for x in d.get("Items") or [] if x.get("Type") == "Series"][0]["Id"]
    eps = jellyfin.get_json(
        f"/Shows/{sid}/Episodes",
        params={"UserId": uid, "Fields": "Path,Overview,ImageTags,LockedFields"},
    ).get("Items") or []

    real = [e for e in eps if e.get("Path")]
    print(f"rows={len(eps)}  with-file={len(real)}  "
          f"with-overview={sum(1 for e in real if e.get('Overview'))}  "
          f"with-image={sum(1 for e in real if (e.get('ImageTags') or {}).get('Primary'))}")

    bad = []
    for e in real:
        fn = (e.get("Path") or "").rsplit("/", 1)[-1]
        m = re.search(r"S\d+E\d+\s+(.*?)\.(mkv|mp4|avi)$", fn, re.I)
        ident = m.group(1) if m else fn
        if not (tokens(ident).intersection(tokens(e.get("Name")))):
            bad.append((e.get("ParentIndexNumber"), e.get("IndexNumber"),
                        e.get("Name"), ident, e.get("Id")))

    print(f"\nname-vs-file mismatches: {len(bad)}")
    for s, ep, name, ident, iid in bad:
        print(f"  S{s:02d}E{ep:02d}  row={name!r}  file={ident!r}  id={iid}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
