"""Verify dupe-sweep flags that need episode-level or closer truth."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, probe  # noqa: E402

terms = ["Reel Britannia", "Making of The French Connection",
         "Iron Maiden Number of the Beast", "Story of Tommy",
         "Glen Campbell Rhinestone Cowboy", "When China Met Africa",
         "Sheila Hancock Brilliant Bronte", "Shakers Hands to Work",
         "Ascent of Money", "Megastructures", "A Well-Spent Life",
         "Dog Called Money", "Louis Theroux By Reason of Insanity",
         "British Pathe", "Second Sex", "Cinema Perverso",
         "Travels with Vasari", "Discovering Cinema", "William Klein",
         "Paul Merton Birth of Hollywood", "This Is Pop",
         "Music of Man", "Dog Food", "Woody Allen Documentary",
         "NASA Triumph and Tragedy", "New York Documentary Film"]
for term in terms:
    page = jellyfin.get_json("/Items", params={
        "SearchTerm": term, "Recursive": "true", "Limit": 10,
        "Fields": "Name,Type,Path,IndexNumber,ParentIndexNumber"}) or {}
    rows = page.get("Items") or []
    print(f"== {term} -> {page.get('TotalRecordCount')}")
    for it in rows:
        print(f"   {it.get('Type')} S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} "
              f"| {(it.get('Name') or '')[:55]} | {(it.get('Path') or '')[-60:]}")

print("\n== Women Make Film series episodes ==")
page = jellyfin.get_json("/Items", params={
    "SearchTerm": "Women Make Film", "Recursive": "true",
    "IncludeItemTypes": "Episode,Movie,Video", "Limit": 30,
    "Fields": "Name,Type,Path,IndexNumber,ParentIndexNumber"}) or {}
for it in page.get("Items") or []:
    print(f"   {it.get('Type')} S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} "
          f"| {(it.get('Name') or '')[:50]} | {(it.get('Path') or '')[-60:]}")

print("\n== Abbey Road series episodes ==")
page = jellyfin.get_json("/Items", params={
    "SearchTerm": "Abbey Road", "Recursive": "true",
    "IncludeItemTypes": "Episode", "Limit": 30,
    "Fields": "Name,IndexNumber,ParentIndexNumber,Path"}) or {}
for it in page.get("Items") or []:
    print(f"   S{it.get('ParentIndexNumber')}E{it.get('IndexNumber')} "
          f"| {(it.get('Name') or '')[:60]}")

print("\n== Abbey Road disk dir ==")
rc, out = probe.query("ls -la /mnt/zmedia/videos/musicdocos-series/Live\\ from\\ Abbey\\ Road\\ \\(2007\\)\\ \\[tmdbid-3697\\]/ /mnt/zmedia/videos/musicdocos-series/Live\\ from\\ Abbey\\ Road\\ \\(2007\\)\\ \\[tmdbid-3697\\]/Season\\ 01/ 2>/dev/null | head -50")
print("rc:", rc)
print(out[:2500])
