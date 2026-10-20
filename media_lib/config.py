"""Central configuration for media ingest.

Every value here was extracted from jellyfin-media-intake.py (v4). The comments
record WHY each threshold exists, because each one was added after a real
misdirected write — see the source script's "WHY THIS IS SO CAREFUL" header.

Path changes from v4 (deliberate, approved 2026-10-20):
  * state now lives under this profile, not the default profile's /root/.hermes/state
  * STATE_DIR is a Path, so a missing directory is an explicit error, not a
    silent str concatenation into the wrong place
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------- locations
PROFILE_DIR = Path('/root/.hermes/profiles/media-maintainer')
STATE_DIR = PROFILE_DIR / 'state'
LEDGER = STATE_DIR / 'jellyfin-ingest-ledger.json'
LOG = STATE_DIR / 'intake-runs.log'
CHECK_LIST = STATE_DIR / 'needs-your-check.json'
LOCK = STATE_DIR / 'intake.lock'
PROBE_CACHE = STATE_DIR / 'probe-cache.json'
LIB_CACHE = STATE_DIR / 'library-cache.json'

# The media host. All MEDIA_ROOT paths below are only valid inside commands run
# over ssh to this host — this agent host cannot see the pools locally.
PROXMOX3 = 'root@192.168.214.131'
MEDIA_ROOT = '/mnt/zmedia/videos'

#: Jellyfin container path -> path on the share host. Case differs across the
#: container/host boundary (container lowercase, share CamelCase) — a
#: case-sensitive comparison against the host form misses every row.
JF_TO_DISK = {
    '/data/musicdocos-series': f'{MEDIA_ROOT}/musicdocos-series',
    '/data/documentaries': f'{MEDIA_ROOT}/Documentaries',
    '/data/movies': f'{MEDIA_ROOT}/Films',
    '/data/filmnoir': f'{MEDIA_ROOT}/filmnoir',
    '/data/tvshows': f'{MEDIA_ROOT}/TV',
    '/data/musicdocos': f'{MEDIA_ROOT}/musicdocos',
}

# ---------------------------------------------------------------- costing
#: Measured sustained download -> array copy throughput on proxmox3 (2026-09-13,
#: three fsync-enforced passes, 143-171 MB/s). dd WITHOUT conv=fsync reports
#: page-cache speed (~4x optimistic) and would size batches far too large.
COPY_MBPS = 158
PER_ITEM_OVERHEAD_S = 20   # ffprobe + mid-file decode + delete + ledger write
REFRESH_COST_S = 20
LIB_CACHE_STALE_S = 600
DUMP_REBUILD_COST_S = 90

# ---------------------------------------------------------------- matching
#: String similarity required before a REPLACE is considered at all.
MIN_SQUASH = 0.80
#: At least this many real title words must be shared (filler alone never counts).
MIN_SHARED_DISTINCTIVE = 1
#: Share of the RELEASE's identifying words the target must also carry. This is
#: the guard that catches 'Good.Hair' -> 'Good' (the target has no 'hair').
MIN_CAND_COVERAGE = 0.60
#: A copied file may differ from the original by this fraction of its duration.
DURATION_TOLERANCE = 0.15
#: Containment ('abc' inside 'abcdef') is only treated as evidence when the
#: shorter string is at least this share of the longer. 'good' inside
#: 'goodhair' is 0.5 and must never be boosted to a confident match.
MIN_CONTAINMENT_SHARE = 0.60
#: A squash-exact match shorter than this is too weak to auto-accept; a 2-char
#: title ('Up') collides with too much.
MIN_EXACT_SQUASH_LEN = 6

#: Words that appear in so many titles they carry no identity on their own.
STOPWORDS = frozenset({
    'the', 'of', 'and', 'a', 'an', 'to', 'in', 'on', 'at', 'for', 'with', 'from',
    'by', 'as', 'is', 'are', 'was', 'were', 'be', 'it', 'its', 'this', 'that',
    'these', 'those', 'his', 'her', 'their', 'our', 'your', 'my', 'me', 'we',
    'or', 'but', 'not', 'no', 'all', 'any', 'some', 'more', 'most', 'very',
    'story', 'stories', 'life', 'time', 'world', 'great', 'best', 'new', 'old',
    'part', 'parts', 'pt', 'cd', 'disc', 'disk', 'vol', 'volume', 'chapter',
    'episode', 'ep', 'season', 'series', 'special', 'full', 'complete',
    'movie', 'film', 'documentary', 'doc', 'bbc', 'itv', 'ch4', 'pbs', 'hbo',
})

#: Video container extensions stripped from the END of a name only. Never use
#: os.path.splitext on a release/folder name: it splits at the last dot, so
#: 'Good.Hair' becomes 'Good' and 'Anvil...x264-GRP' loses '-GRP'.
VIDEO_EXTS = frozenset({
    '.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.wmv', '.flv', '.mpg',
    '.mpeg', '.divx', '.webm', '.ogm', '.vob', '.iso',
})


def ensure_state_dir() -> Path:
    """Create the state directory if absent and return it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR
