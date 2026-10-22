"""Restore the Jools S57 episode names a provider refresh flattened to "Episode N".

The lineups come from Wikipedia's episode list, cross-checked against the BBC and
TVmaze pages. TMDB genuinely has only "Episode 1..6" for this season, so a refresh
always overwrites the informative names — the repair has to lock them.

Refuses to write on a row with no file (it would look repaired while being a ghost)
and refuses when the row already carries a real name (never clobber).
"""
import sys
import time

sys.path.insert(0, ".")

from media_lib import jellyfin

LINEUPS = {
    1: ("Arlo Parks, Kings of Leon, Sleaford Mods", "19 February 2021"),
    2: ("Moses Boyd, Rag'n'Bone Man, Peggy Seeger", "26 February 2021"),
    3: ("Tom Jones, Wolf Alice, Pa Salieu and Mahalia", "5 March 2021"),
    4: ("Laura Mvula, Dry Cleaning, Paul Weller", "12 March 2021"),
    5: ("Lenny Henry, Enny, Nubiyan Twist and K.O.G.", "19 March 2021"),
    6: ("Olly Alexander, Griff, Nitin Sawhney and Ayanna Witter-Johnson", "26 March 2021"),
}


def main() -> int:
    uid = jellyfin.user_id()
    series = [
        i for i in (
            jellyfin.get_json("/Items", params={"Recursive": "true", "SearchTerm": "Jools Holland",
                                                "IncludeItemTypes": "Series", "Limit": 5}).get("Items") or []
        ) if "jools" in (i.get("Name") or "").lower()
    ]
    if not series:
        print("series not found")
        return 1
    sid = series[0]["Id"]
    eps = jellyfin.get_json(
        f"/Shows/{sid}/Episodes",
        params={"UserId": uid, "Fields": "Path,Overview,LockedFields", "Limit": 1000},
    ).get("Items") or []
    by_season = {e.get("IndexNumber"): e for e in eps if e.get("ParentIndexNumber") == 57}

    for n, (name, date) in sorted(LINEUPS.items()):
        row = by_season.get(n)
        if not row:
            print(f"  E{n:02d} no row")
            continue
        if not row.get("Path"):
            print(f"  E{n:02d} SKIP - row has no file")
            continue
        current = row.get("Name") or ""
        if current and not current.startswith("Episode "):
            print(f"  E{n:02d} SKIP - already named {current[:40]!r}")
            continue
        overview = (
            f"Later... with Jools Holland, series 57 episode {n} ({date}). "
            f"Jools Holland is joined by {name}."
        )
        body = {
            "Name": name,
            "Overview": overview,
            "IndexNumber": n,
            "ParentIndexNumber": 57,
            "LockedFields": ["Name", "Overview"],
        }
        try:
            jellyfin.post_item(row["Id"], body)
        except Exception as exc:  # noqa: BLE001
            print(f"  E{n:02d} WRITE-FAIL {type(exc).__name__}: {exc}")
            continue
        time.sleep(0.4)
        chk = jellyfin.get_json(f'/Users/{uid}/Items/{row["Id"]}',
                                params={"Fields": "Name,Overview,LockedFields"})
        ok = chk.get("Name") == name and "Name" in (chk.get("LockedFields") or [])
        print(f"  E{n:02d} {'FIXED+LOCKED' if ok else 'CHECK'}  {name[:52]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
