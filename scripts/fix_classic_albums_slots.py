"""Put the Classic Albums season 4/5/6 files back on the library's own numbering.

What went wrong: the library numbers these episodes differently from TMDB, and a
provider refresh re-resolves NAMES by slot number. Placing by TMDB's slot put a
second file on S04E01 (already The Sex Pistols) and left Deep Purple on the slot
TMDB calls Sex Pistols. The rule that matters is the skill's: write the numbering
yourself and lock the names, never renumber to make the provider fit.

Target state (library numbering, matching the ghost rows that already existed):

    S04E01 The Sex Pistols - Never Mind the Bollocks   (existing file, untouched)
    S04E02 Deep Purple - Machine Head                  (was ghost)
    S04E03 Def Leppard - Hysteria                      (new, free slot)
    S04E04 Dark Side of the Moon                       (existing file, untouched)
    S06E01 Simply Red - Stars                          (was ghost)

Every name is then locked, because TMDB lists no episodes past season 10 and
resolves the rest by number — which is what renamed these rows in the first place.
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")

from media_lib import config, jellyfin

LIB = "/mnt/zmedia/videos/musicdocos-series/Classic Albums (1997) [tmdbid-9048]"
CT = config.MEDIA_HOST

MOVES = [
    (f"{LIB}/Season 04/Classic Albums (1997) S04E03 Deep Purple - Machine Head.avi",
     f"{LIB}/Season 04/Classic Albums (1997) S04E02 Deep Purple - Machine Head.avi"),
    (f"{LIB}/Season 04/Classic Albums (1997) S04E01 Def Leppard - Hysteria.mp4",
     f"{LIB}/Season 04/Classic Albums (1997) S04E03 Def Leppard - Hysteria.mp4"),
    (f"{LIB}/Season 05/Classic Albums (1997) S05E01 Simply Red - Stars.mkv",
     f"{LIB}/Season 06/Classic Albums (1997) S06E01 Simply Red - Stars.mkv"),
]

# Slot -> (Name, Overview). Written with locks so a refresh cannot rename them.
TITLES = {
    (4, 1): ("The Sex Pistols: Never Mind the Bollocks",
             "Classic Albums: the making of the Sex Pistols' 1977 album Never Mind the Bollocks, Here's the Sex Pistols."),
    (4, 2): ("Deep Purple: Machine Head",
             "Classic Albums: the making of Deep Purple's 1972 album Machine Head, recorded in Montreux."),
    (4, 3): ("Def Leppard: Hysteria",
             "Classic Albums: the making of Def Leppard's 1987 album Hysteria."),
    (4, 4): ("Pink Floyd: The Dark Side of the Moon",
             "Classic Albums: the making of Pink Floyd's 1973 album The Dark Side of the Moon."),
    (6, 1): ("Simply Red: Stars",
             "Classic Albums: the making of Simply Red's 1991 album Stars."),
}


def run(cmd: str) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", CT, f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=600,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    for src, dest in MOVES:
        out = run(f'mkdir -p "{dest.rsplit("/", 1)[0]}" && mv -f "{src}" "{dest}" && echo OK')
        print(f"  {'moved' if 'OK' in out else 'SKIP/FAIL'} {src.rsplit('/', 1)[-1][:56]}")
        if "OK" not in out:
            print(f"      {out.strip()[:120]}")

    uid = jellyfin.user_id()
    eps = jellyfin.get_json(
        "/Shows/1b993dd6fd01cfe0d3dca0524d08ccf6/Episodes",
        params={"UserId": uid, "Fields": "Path", "Limit": 400},
    ).get("Items") or []
    by = {(e.get("ParentIndexNumber"), e.get("IndexNumber")): e for e in eps}

    for (se, ep), (name, overview) in sorted(TITLES.items()):
        row = by.get((se, ep))
        if not row:
            print(f"  S{se:02d}E{ep:02d} no row yet (scanner has not bound the file)")
            continue
        if not row.get("Path"):
            print(f"  S{se:02d}E{ep:02d} SKIP - ghost, no file")
            continue
        body = {"Name": name, "Overview": overview, "IndexNumber": ep,
                "ParentIndexNumber": se, "LockedFields": ["Name", "Overview"]}
        try:
            jellyfin.post_item(row["Id"], body)
        except Exception as exc:  # noqa: BLE001
            print(f"  S{se:02d}E{ep:02d} WRITE-FAIL {type(exc).__name__}: {exc}")
            continue
        time.sleep(0.4)
        chk = jellyfin.get_json(f'/Users/{uid}/Items/{row["Id"]}',
                                params={"Fields": "Name,LockedFields"})
        ok = chk.get("Name") == name and "Name" in (chk.get("LockedFields") or [])
        print(f"  S{se:02d}E{ep:02d} {'NAMED+LOCKED' if ok else 'CHECK'}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
