"""Round 4: Abbey Road middle, Rock Family Trees, Dark Ages, Ireland E05, NY sab, dead rows."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, probe  # noqa: E402

cmds = {
    "abbey_mid": "find '/mnt/zmedia/videos/musicdocos-series/Live from Abbey Road (2007) [tmdbid-3697]' -type f -name '*.mkv' | sort | sed -n '1,40p'",
    "rft": "ls -laR '/mnt/zmedia/videos/musicdocos-series/Rock Family Trees (1995) [tmdbid-94774]/' 2>/dev/null | head -30",
    "darkages": "ls -la '/mnt/zmedia/videos/documentaries/The Dark Ages An Age of Light (2012)/'",
    "ireland": "ls -la '/mnt/zmedia/videos/DocuSeries/The Story of Ireland (2011) [tmdbid-111313]/' '/mnt/zmedia/videos/DocuSeries/The Story of Ireland (2011) [tmdbid-111313]/Season 01/' 2>/dev/null",
}
for k, c in cmds.items():
    rc, out = probe.query(c)
    print(f"== {k} (rc {rc}) ==")
    print(out[:1800])

print("\n== NY sab complete folder ==")
rc, out = probe.query("find /mnt/zmedia/downloads/sabnzbd/complete -maxdepth 3 | head -40")
print("rc:", rc)
print(out[:1800])

print("done")
