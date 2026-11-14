"""Round 3: Abbey Road full listing, Ascent disk, usenet-complete checks."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, probe  # noqa: E402

for term in ["Oliver Sacks Musicophilia", "Rock Family Trees",
             "Dark Ages Age of Light", "Story of Ireland",
             "Drinking to Oblivion", "Louis Theroux S01E01",
             "Moog", "One Giant Leap"]:
    page = jellyfin.get_json("/Items", params={
        "SearchTerm": term, "Recursive": "true", "Limit": 8,
        "Fields": "Name,Type,Path,IndexNumber,ParentIndexNumber"}) or {}
    print(f"== {term} -> {page.get('TotalRecordCount')}")
    for it in (page.get("Items") or []):
        print(f"   {it.get('Type')} S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} | "
              f"{(it.get('Name') or '')[:50]} | {(it.get('Path') or '')[-55:]}")

cmds = {
    "abbey_rest": "find '/mnt/zmedia/videos/musicdocos-series/Live from Abbey Road (2007) [tmdbid-3697]' -type f | sort | tail -30",
    "ascent": "ls -laR '/mnt/zmedia/videos/DocuSeries/The Ascent of Money (2008)/' 2>/dev/null | head -30 || ls /mnt/zmedia/videos/DocuSeries/ | grep -i ascent",
    "vasari": "ls -laR '/mnt/zmedia/videos/DocuSeries/Travels with Vasari (2008) [tmdbid-205394]/' 2>/dev/null | head -20",
    "pathe": "ls -laR '/mnt/zmedia/videos/FilmDocuSeries/The Story of British Pathe (2011)/' 2>/dev/null | head -25",
    "ny_sab": "find '/mnt/zmedia/downloads/sabnzbd/complete/New York A Documentary Film' -type f 2>/dev/null | head; find '/mnt/zmedia/downloads/temp/American Experience.1999.New York - A Documentary Film Series.DvDRip.Divx5.480p' -type f 2>/dev/null | head",
}
for k, c in cmds.items():
    rc, out = probe.query(c)
    print(f"\n== {k} (rc {rc}) ==")
    print(out[:2000])
