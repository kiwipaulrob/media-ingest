"""Central configuration for media ingest.

IMPORTANT — topology (re-verified 2026-10-20 against the running containers):
the media library and the downloads are BOTH only visible from CT105 on prox2.
`proxmox3:/mnt/zmedia/videos` holds a stale, partial copy (musicdocos only) and
has no `/mnt/zmedia/downloads` at all — file operations issued there silently
fail with ENOENT. The mapping below was read from
`docker inspect jellyfin --format {{.Mounts}}`, not from memory.

There is no ffprobe on prox2 or in CT105. The only binaries are inside the
jellyfin image, so probing/decode runs as a throwaway container with
/mnt/zmedia bind-mounted, using the same image tag the compose file pins.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- locations
# MEDIA_OPS_HOME exists so the suite can run anywhere. Without it, a test that
# merely asks "did this write anything?" stats a root-owned path and dies with
# PermissionError on a CI runner — which is what happened, and what hid the
# real contract (resolve() must not write) behind an unrelated error.
PROFILE_DIR = Path(os.environ.get("MEDIA_OPS_HOME")
                   or "/root/.hermes/profiles/media-maintainer")
STATE_DIR = PROFILE_DIR / "state"
LEDGER = STATE_DIR / "jellyfin-ingest-ledger.json"
LOG = STATE_DIR / "intake-runs.log"
CHECK_LIST = STATE_DIR / "needs-your-check.json"
LOCK = STATE_DIR / "intake.lock"
PROBE_CACHE = STATE_DIR / "probe-cache.json"
LIB_CACHE = STATE_DIR / "library-cache.json"

#: Host that can see BOTH trees, and the namespace wrapper that reaches them.
#: CT105 is unprivileged; media paths are only valid inside it.
MEDIA_HOST = "root@192.168.214.109"
MEDIA_CT = "pct exec 105 --"
#: Volume to bind into the throwaway probe container.
MEDIA_VOLUME = "/mnt/zmedia"
#: Image tag used for ffprobe/ffmpeg. Must match the compose pin; override with
#: MEDIA_IMAGE if the server is upgraded.
DEFAULT_MEDIA_IMAGE = "jellyfin/jellyfin:12.2"
FFPROBE_ENTRY = "/usr/lib/jellyfin-ffmpeg/ffprobe"
FFMPEG_ENTRY = "/usr/lib/jellyfin-ffmpeg/ffmpeg"

#: Jellyfin container path -> real path on the media host (case matters).
#: Read from docker inspect; 8 libraries, two of which the old map lacked.
JF_TO_DISK = {
    "/data/documentaries": f"{MEDIA_VOLUME}/videos/Documentaries",
    "/data/docuseries": f"{MEDIA_VOLUME}/videos/DocuSeries",
    "/data/filmdocos": f"{MEDIA_VOLUME}/videos/FilmDocumentaries",
    "/data/filmnoir": f"{MEDIA_VOLUME}/videos/filmnoir",
    "/data/movies": f"{MEDIA_VOLUME}/videos/Films",
    "/data/musicdocos": f"{MEDIA_VOLUME}/videos/musicdocos",
    "/data/musicdocos-series": f"{MEDIA_VOLUME}/videos/musicdocos-series",
    "/data/tvshows": f"{MEDIA_VOLUME}/videos/TV",
}

#: qBittorrent container save_path -> real host path. Resolved per torrent from
#: its own save_path; never assume a single mapping.
SAVE_TO_REMOTE = {
    "/downloads-zmedia": f"{MEDIA_VOLUME}/downloads",
    "/downloads": f"{MEDIA_VOLUME}/downloads",
}

# ---------------------------------------------------------------- costing
#: Measured sustained copy throughput (fsync-enforced). dd without conv=fsync
#: reports page-cache speed and overstates by ~4x.
COPY_MBPS = 158
PER_ITEM_OVERHEAD_S = 20
REFRESH_COST_S = 20
LIB_CACHE_STALE_S = 600
DUMP_REBUILD_COST_S = 90

# ---------------------------------------------------------------- matching
MIN_SQUASH = 0.80
MIN_SHARED_DISTINCTIVE = 1
MIN_CAND_COVERAGE = 0.60
DURATION_TOLERANCE = 0.15
MIN_CONTAINMENT_SHARE = 0.60
MIN_EXACT_SQUASH_LEN = 6
MIN_VIDEO_BYTES = 20 * 1024 * 1024

#: Words that appear in so many titles they carry no identity on their own.
STOPWORDS = frozenset(
    {
        "the",
        "of",
        "and",
        "a",
        "an",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "his",
        "her",
        "their",
        "our",
        "your",
        "my",
        "me",
        "we",
        "or",
        "but",
        "not",
        "no",
        "all",
        "any",
        "some",
        "more",
        "most",
        "very",
        "story",
        "stories",
        "life",
        "time",
        "world",
        "great",
        "best",
        "new",
        "old",
        "part",
        "parts",
        "pt",
        "cd",
        "disc",
        "disk",
        "vol",
        "volume",
        "chapter",
        "episode",
        "ep",
        "season",
        "series",
        "special",
        "full",
        "complete",
        "movie",
        "film",
        "documentary",
        "doc",
        "bbc",
        "itv",
        "ch4",
        "pbs",
        "hbo",
    }
)

#: Video container extensions. Never use os.path.splitext on a release name:
#: it splits at the LAST dot, so 'Good.Hair' becomes 'Good'.
VIDEO_EXTS = frozenset(
    {
        ".mkv",
        ".mp4",
        ".avi",
        ".m4v",
        ".mov",
        ".ts",
        ".wmv",
        ".flv",
        ".mpg",
        ".mpeg",
        ".divx",
        ".webm",
        ".ogm",
        ".vob",
        ".iso",
    }
)


def media_image() -> str:
    """Image tag for probe containers (env override, then the pinned default)."""
    return os.environ.get("MEDIA_IMAGE") or DEFAULT_MEDIA_IMAGE


def ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


# ---------------------------------------------------------------- sourcing
#: Prowlarr fans one query out to every enabled indexer (torrent + usenet).
PROWLARR_URL = os.environ.get("PROWLARR_URL", "")
PROWLARR_KEY = os.environ.get("PROWLARR_API_KEY", "")
#: SABnzbd is the Usenet downloader. `mode=addurl` takes a Prowlarr downloadUrl.
SAB_URL = os.environ.get("SABNZBD_URL", "")
SAB_KEY = os.environ.get("SABNZBD_API_KEY", "")

#: A posting with fewer advertised seeds than this is not offered as a pick.
#: Advertised seeders are routinely stale (63 and 95 "seeds" both delivered
#: zero bytes), so this is a floor, not evidence of health.
SOURCE_MIN_SEEDS = 2
#: Broad queries used to build the candidate pool. Narrow per-title queries
#: return nothing on these indexers (ten returned zero) while the pool below
#: returns hundreds, so matching happens in code instead.
SOURCE_POOL_QUERIES = (
    "classic albums",
    "Classic.Albums",
    "Classic Albums BBC",
    "Classic Albums 1080p",
)
