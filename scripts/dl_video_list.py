"""List video files in download dirs with type, hardlink count, size, path."""
import sys
sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import probe

FIND = (
    "find /mnt/zmedia/downloads/ -type f "
    r"\( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' "
    r"-o -iname '*.ts' -o -iname '*.m2ts' -o -iname '*.wmv' "
    r"-o -iname '*.m4b' \) "
    "-printf '%y|%n|%s|%p\\n' 2>/dev/null | sort -t '|' -k 3 -n -r"
)
rc, out = probe.query(FIND)
print("rc:", rc)
print(out)
