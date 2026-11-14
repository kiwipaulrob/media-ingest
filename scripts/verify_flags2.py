"""Round 2: episode-level truth for remaining flags + Abbey Road disk + downloads census."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, probe  # noqa: E402

for term, itypes in [
    ("Jools Holland", "Episode"),
    ("American Masters", "Episode"),
    ("Travels with Vasari", "Episode"),
    ("Ascent of Money", "Episode"),
    ("British Pathe", "Episode"),
    ("This Is Pop", "Episode"),
]:
    page = jellyfin.get_json("/Items", params={
        "SearchTerm": term, "Recursive": "true",
        "IncludeItemTypes": itypes, "Limit": 60,
        "Fields": "Name,IndexNumber,ParentIndexNumber,Path,Width,Height,Size"}) or {}
    rows = page.get("Items") or []
    print(f"== {term} episodes ({len(rows)}) ==")
    for it in rows:
        print(f"   S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} | "
              f"{(it.get('Name') or '')[:55]} | {(it.get('Path') or '')[-55:]}")

for term in ["Many Lives of William Klein", "Disappearance of"]:
    page = jellyfin.get_json("/Items", params={
        "SearchTerm": term, "Recursive": "true", "Limit": 6,
        "Fields": "Name,Type,Path,IndexNumber,ParentIndexNumber"}) or {}
    print(f"== {term} ==")
    for it in (page.get("Items") or []):
        print(f"   {it.get('Type')} S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} | "
              f"{(it.get('Name') or '')[:55]} | {(it.get('Path') or '')[-60:]}")

cmds = {
    "abbeyroad": "find '/mnt/zmedia/videos/musicdocos-series/Live from Abbey Road (2007) [tmdbid-3697]' -type f | sort",
    "nyfolder": "ls -la '/mnt/zmedia/videos/documentaries/New York A Documentary Film (1999)/'",
    "megafolder": "ls -la '/mnt/zmedia/videos/documentaries/Megastructures - Building The World/'",
    "census_music": "find /mnt/zmedia/downloads -maxdepth 1 -type d | wc -l",
    "census_sab": "ls -la /mnt/zmedia/downloads/sabnzbd/complete/ /mnt/zmedia/downloads/sabnzbd/incomplete/ /mnt/zmedia/downloads/temp/ /mnt/zmedia/downloads/quarantine/ 2>&1 | head -40",
}
for k, c in cmds.items():
    rc, out = probe.query(c)
    print(f"\n== {k} (rc {rc}) ==")
    print(out[:2200])
