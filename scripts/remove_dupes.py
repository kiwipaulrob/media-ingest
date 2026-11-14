"""Remove confirmed dupe torrents + leftover dupe payloads (user approved)."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import probe, qb  # noqa: E402

NAMES = ["Storyville.When.China.Met.Africa",
         "The.Shakers.Hands.to.Work.Hearts.to.God",
         "A Well-Spent Life 1971 1080p BluRay",
         "The.Ascent.of.Money.S01E01",
         "Classic Albums S03E01 Iron Maiden",
         "A.Dog.Called.Money.2019.1080p.BluRay"]

ts = qb.torrents()
victims = [t["hash"] for t in ts
           if any(n.lower() in (t.get("name") or "").lower() for n in NAMES)]
print("matched torrents:", len(victims))
for t in ts:
    if t["hash"] in victims:
        print(f"  {round((t.get('progress') or 0)*100,1)}% {(t.get('name') or '')[:60]}")
print("qb.delete ->", qb.delete(victims, delete_files=True))

gone = qb.torrents()
print("verified gone:", all(h not in {t['hash'] for t in gone} for h in victims))

cmds = [
    "rm -fv '/mnt/zmedia/downloads/BBC.The.Story.of.British.Pathe.4of4.Around.the.World.PDTV.x264.AAC.MVGroup.Forum.mkv'",
    "rm -rfv '/mnt/zmedia/downloads/sabnzbd/complete/Louis.Theroux.By.Reason.Of.Insanity.S01E02.720p.HDTV.x264-TASTETV-Obfuscated/'",
    "rm -rfv '/mnt/zmedia/downloads/sabnzbd/complete/Rock Family Trees - Deep Purple &amp; Black Sabbath/'",
]
for c in cmds:
    rc, out = probe.query(c + " 2>&1; echo ---; ls /mnt/zmedia/downloads/ | grep -iE 'british.pathe|theroux' || echo NOMATCH_LEFT")
    print("rc:", rc)
    print(out[:600])
