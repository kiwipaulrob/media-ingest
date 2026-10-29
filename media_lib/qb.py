"""qBittorrent helpers — queue read, file listing, exact-hash delete.

The delete path carries three hard-won rules:
  * form-encoded POST fields (--data-urlencode equivalent), never query params
  * FULL 40-character hashes: a prefix is silently accepted and matches nothing
  * assert the expected match count BEFORE deleting, then re-list afterwards
"""

from __future__ import annotations

import json
import re
import urllib.error
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import config, jellyfin

#: qB container save_path -> real path on the media host. Each root maps
#: separately: a naive prefix replace of '/downloads' mangles '/downloads-zmedia'.
#: Single source of truth lives in config (verified against the live mount).
SAVE_TO_REMOTE = config.SAVE_TO_REMOTE

VIDEO_EXT = frozenset(
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
    }
)


def save_to_remote(save_path: str) -> str:
    """Map a qB save_path to its real host path. Raises on an unknown root."""
    for prefix, remote in SAVE_TO_REMOTE.items():
        if save_path == prefix or save_path.startswith(prefix + "/"):
            return remote + save_path[len(prefix):]
    raise ValueError(f"unknown qB save_path prefix: {save_path!r}")


def _base() -> str:
    return (jellyfin.env("QB_URL") or "http://192.168.214.11:8090").rstrip("/")


def _opener():
    cj = urllib.request.HTTPCookieProcessor()
    op = urllib.request.build_opener(cj)
    data = urllib.parse.urlencode(
        {
            "username": jellyfin.env("QB_USER") or "admin",
            "password": jellyfin.env("QB_PASS") or "",
        }
    ).encode()
    op.open(
        urllib.request.Request(f"{_base()}/api/v2/auth/login", data=data), timeout=30
    ).read()
    return op


def torrents(op=None) -> List[Dict[str, Any]]:
    op = op or _opener()
    with op.open(f"{_base()}/api/v2/torrents/info", timeout=60) as r:
        return json.load(r)


def files(hash_: str, op=None) -> List[Dict[str, Any]]:
    op = op or _opener()
    with op.open(f"{_base()}/api/v2/torrents/files?hash={hash_}", timeout=40) as r:
        return json.load(r)


def video_files(
    torrent: Dict[str, Any], op=None, min_bytes: int = 20 * 1024 * 1024
) -> List[Dict[str, Any]]:
    """Video files in a torrent, largest first, with resolved remote paths."""
    out: List[Dict[str, Any]] = []
    for f in files(torrent.get("hash", ""), op=op):
        name = f.get("name") or ""
        if _ext(name) not in VIDEO_EXT:
            continue
        if (f.get("size") or 0) < min_bytes:
            continue
        try:
            remote = (
                save_to_remote(torrent.get("save_path") or "/downloads") + "/" + name
            )
        except ValueError:
            continue  # unknown root: never guess a filesystem location
        out.append(
            {"name": name.split("/")[-1], "remote": remote, "size": f.get("size") or 0}
        )
    out.sort(key=lambda x: -x["size"])
    return out


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def _post_hashes(endpoint: str, hashes: List[str], extra: Dict[str, str] | None = None,
                 op=None) -> int:
    """One POST per hash. qBittorrent v5.x mutating endpoints reject pipe-joined
    hashes with HTTP 400 (review-3 caught this: every call site worked only
    because our server still tolerates the old form). Returns successes."""
    op = op or _opener()
    done = 0
    for h in hashes:
        body = urllib.parse.urlencode({"hashes": h, **(extra or {})}).encode()
        req = urllib.request.Request(
            f"{_base()}{endpoint}", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with op.open(req, timeout=60) as r:
                r.read()
            done += 1
        except urllib.error.HTTPError:
            continue
    return done


def delete(hashes: List[str], op=None, delete_files: bool = True) -> int:
    """Delete torrents by FULL hash. Refuses prefixes.

    Returns the number requested; callers must re-list to confirm.
    """
    bad = [h for h in hashes if len(str(h)) != 40]
    if bad:
        raise ValueError(f"refusing to delete non-40-char hashes: {bad}")
    if not hashes:
        return 0
    return _post_hashes("/api/v2/torrents/delete", [str(h) for h in hashes],
                        {"deleteFiles": "true" if delete_files else "false"}, op)


def completed_candidates(
    ledger_hashes: set, op=None
) -> Tuple[List[Dict[str, Any]], Any]:
    """Completed video torrents that are NOT already in the ledger."""
    op = op or _opener()
    rows = [
        t
        for t in torrents(op)
        if (t.get("progress") or 0) >= 0.999
        and str(t.get("hash", "")).lower()[:12] not in ledger_hashes
    ]
    return rows, op


def add(url: str, category: str = "", op=None) -> int:
    """Queue a magnet or a Prowlarr downloadUrl. Returns the HTTP status.

    Success is 200 **or 202** (qBittorrent answers 202 for an accepted add — a caller
    that only accepts 200 reports a successful add as a failure), and 409 means the
    release is already queued, which is also success for our purposes.
    """
    op = op or _opener()
    data = {"urls": url}
    if category:
        data["category"] = category
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        f"{_base()}/api/v2/torrents/add",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with op.open(req, timeout=90) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        # urllib raises for 4xx, so 409 ("already queued") looked like a failure
        # even though it is the expected duplicate outcome. Report the code and
        # let the caller decide.
        return exc.code


def force_start(hashes: List[str], op=None) -> int:
    """Force-start torrents so they actually download.

    Needed on this install: the *arr pipeline runs ~93 downloads against a
    `max_active_downloads` cap of 25 (they arrive force-started), so a torrent
    added without force sits in `queuedDL` and never begins — an add that looks
    successful and downloads nothing.
    """
    return set_force(hashes, True, op)


def set_force(hashes: List[str], value: bool, op=None) -> int:
    """Explicitly force-start or un-force torrents.

    Un-forcing is how dead weight is handed back to the queue: this install carries far
    more forced torrents than `max_active_downloads`, and while they are all forced none
    of them makes progress and nothing new can start.
    """
    hashes = [h for h in hashes if h]
    if not hashes:
        return 0
    return _post_hashes("/api/v2/torrents/setForceStart", hashes,
                        {"value": "true" if value else "false"}, op)


#: Public announce URLs used when a queued magnet carries NO tracker at all.
#: A hash-only magnet (synthesised from an indexer's infohash) arrives with just
#: DHT/PeX/LSD, and DHT alone frequently never retrieves metadata — the torrent
#: then sits in forcedMetaDL at 0 peers forever and reads as a dead release.
PUBLIC_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
)


def trackers(hash_: str, op=None) -> List[Dict[str, Any]]:
    """Announce entries for one torrent (status 2 = working, 4 = not working)."""
    op = op or _opener()
    with op.open(f"{_base()}/api/v2/torrents/trackers?hash={hash_}", timeout=40) as r:
        return json.load(r)


def add_trackers(hash_: str, urls=PUBLIC_TRACKERS, op=None) -> int:
    """Append announce URLs. Answers HTTP 204 on success — not 200."""
    op = op or _opener()
    body = urllib.parse.urlencode(
        {"hash": hash_, "urls": "\n".join(urls)}
    ).encode()
    req = urllib.request.Request(
        f"{_base()}/api/v2/torrents/addTrackers",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with op.open(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def reannounce(hashes: List[str], op=None) -> int:
    """Reannounce one or more torrents. There is no reannounceAll endpoint."""
    hashes = [h for h in hashes if h]
    if not hashes:
        return 0
    return _post_hashes("/api/v2/torrents/reannounce", hashes, None, op)


def ensure_trackers(hash_: str, op=None) -> Dict[str, Any]:
    """Give a tracker-less torrent public announces, then reannounce.

    Only ever ADDS trackers — an existing swarm's own announce is never replaced,
    so this cannot move a torrent off its private tracker.
    """
    op = op or _opener()
    try:
        rows = trackers(hash_, op=op)
    except Exception:  # noqa: BLE001 - verification must not fail the add
        return {"injected": 0, "reason": "tracker-read-failed"}
    real = [t for t in rows if not str(t.get("url", "")).startswith("**")]
    if real:
        return {"injected": 0, "existing": len(real)}
    status = add_trackers(hash_, op=op)
    reannounce([hash_], op=op)
    return {"injected": len(PUBLIC_TRACKERS), "addTrackers": status}


def add_file(content: bytes, filename: str = "release.torrent",
             category: str = "") -> int:
    """Add a .torrent by uploading its BYTES.

    Needed because an indexer's download URL is usually protected by the indexer's own API
    key: handing that URL to qBittorrent makes qB fetch it unauthenticated, get 401, and
    report nothing — the add returns 200 while no torrent is ever created (12 episodes went
    missing that way). Fetch the .torrent ourselves, then upload the file.
    """
    boundary = "----hermesqb" + str(len(content))
    parts = []
    if category:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"category\""
                     f"\r\n\r\n{category}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"torrents\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: application/x-bittorrent"
                 f"\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{_base()}/api/v2/torrents/add", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with _opener().open(req, timeout=120) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def add_verified(url: str, title: str, category: str = "", settle: int = 8) -> Dict[str, Any]:
    """Add a release, force-start it, and confirm it is really in the queue.

    The add status alone is not evidence: 202 means accepted, 409 means already there, and
    an unforced add sits in queuedDL forever because this install runs far more downloads
    than max_active_downloads. So verify by TITLE after a short settle, then force-start.

    Returns {'ok', 'http', 'verified', 'hash', 'state'}. A failed verification is not a
    failed add — keep the two apart, or a lookup error will rewrite a success.
    """
    status = add(url, category=category)
    result: Dict[str, Any] = {"ok": status in (200, 202, 409), "http": status,
                              "verified": False, "hash": None, "state": None}
    if not result["ok"]:
        return result
    try:
        found = None
        for _ in range(3):
            time.sleep(settle)
            found = by_title(title)
            if found:
                break
        if found:
            result.update(verified=True, hash=found.get("hash"), state=found.get("state"))
            force_start([found["hash"]])
    except Exception:
        pass
    return result


def by_title(fragment: str, op=None) -> Optional[Dict[str, Any]]:
    """The queued torrent whose NAME matches `fragment` (normalised), else None.

    Verify additions with this, never with an infohash: a Prowlarr result often
    carries none, so a hash-keyed check reports "not in the queue" for a torrent
    that was added perfectly well.
    """
    return find_torrent(fragment, op=op)


_VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov")


def resolve_media_path(content_path: str, hash_: str, op=None) -> str:
    """A queueable file path for a torrent.

    qBittorrent's ``content_path`` is a FOLDER for most multi-file torrents, and
    handing that to the placement stage fails ``STAGE=SRC-MISSING`` — the file
    list's ``name`` is relative to ``save_path``, so the naive join produces a
    doubled path (``/<folder>/<folder>/<file>``). Pick the largest video inside.
    """
    if not content_path:
        return ""
    low = content_path.lower()
    if low.endswith(_VIDEO_EXT):
        return content_path
    files = [
        f
        for f in globals()["files"](hash_, op=op)
        if (f.get("name") or "").lower().endswith(_VIDEO_EXT)
    ]
    if not files:
        return content_path
    biggest = max(files, key=lambda f: f.get("size") or 0)
    t = [x for x in globals()["torrents"](op=op) if x.get("hash") == hash_]
    save_path = (t[0].get("save_path") if t else "").rstrip("/")
    return f"{save_path}/{biggest['name']}" if save_path else content_path


_NORM_RX = re.compile(r"[^a-z0-9]+")


def norm_name(text: Optional[str]) -> str:
    """Normalise a name for comparison (dots, hyphens and spaces are equivalent)."""
    return _NORM_RX.sub(" ", (text or "").lower()).strip()


def find_torrent(fragment: str, op=None) -> Optional[Dict[str, Any]]:
    """Look a torrent up by name, tolerant of separators.

    Bit us three times in one session: ``'Stevie Wonder' in name`` misses
    ``Classic-Albums-Stevie-Wonder-...``. Anything searching the queue by name
    must go through here rather than a raw substring test.
    """
    needle = norm_name(fragment)
    if not needle:
        return None
    for t in torrents(op=op):
        if needle in norm_name(t.get("name")):
            return t
    return None


def _to_qb_namespace(path: str) -> str:
    """Map a host path back into qBittorrent's container namespace.

    content_path arrives as /downloads-zmedia/... while callers often hold the
    host form (/mnt/zmedia/...); comparing across namespaces never matches, so
    the old code fell through to the per-torrent files() loop every time.
    """
    for qb_prefix, remote in SAVE_TO_REMOTE.items():
        if path == remote or path.startswith(remote + "/"):
            return qb_prefix + path[len(remote):]
    return path


def torrent_for_path(path: str, op=None) -> Optional[Dict[str, Any]]:
    """The torrent whose content contains this file path.

    Used to attach a ledger hash to a placement, so `scan` stops re-offering a
    release that has already been ingested (the two-ledger split).
    """
    if not path:
        return None
    qp = _to_qb_namespace(path)
    all_t = torrents(op=op)
    for t in all_t:  # cheap: content_path first, exact and boundary-aware
        cp = t.get("content_path") or ""
        if cp and (qp == cp or qp.startswith(cp + "/")):
            return t
    target = norm_name(path)
    if not target:
        return None
    # Distinctive tokens only: save_path contributes nothing but generic words
    # (/downloads-zmedia matches everything), and bare basenames like movie.mkv
    # match nothing. Without the stop set the filter either skips nothing or,
    # as first written, skips the right torrent too (basename 'movie' is disjoint
    # from every release name).
    _STOP = {"downloads", "zmedia", "mnt", "media", "videos", "movie", "mkv",
             "mp4", "avi", "m4v", "mov", "ts"}
    want = {w for w in target.split() if len(w) >= 4} - _STOP
    for t in all_t:  # then per-file, but only on plausible torrents
        hay = set(norm_name(t.get("name")).split())
        # Quorum: one shared word proves nothing ('Rush Hour' shares 'rush' with
        # the 'Rush' torrent — the exact false positive this fix removes). Need
        # two shared tokens, or one when the path yields only one.
        need = min(2, len(want))
        if want and len(want & hay) < need:
            continue  # shares nothing distinctive: one HTTP call saved
        save = (t.get("save_path") or "").rstrip("/")
        for f in files(t.get("hash") or "", op=op):
            if norm_name(f"{save}/{f.get('name')}") == target:
                return t
    return None

def add_release(result: Dict[str, Any], category: str = "") -> Dict[str, Any]:
    """Queue one Prowlarr search result, resolving it to something qBittorrent accepts.

    Prowlarr's ``downloadUrl`` needs the API key appended; without it qBittorrent answers
    401 and the add looks accepted while nothing is created. Two scripts had each hand-rolled
    this (curl the URL, read the Location header, else fall back to the bare URL — the 401
    path). source.queue_url() already does it correctly, so this is the only entry point.
    """
    from . import source  # local import: source imports config, qb is imported widely

    url = source.queue_url(result) or ""
    if not url:
        return {"ok": False, "http": None, "verified": False, "state": "no-url",
                "hash": None}
    return add_verified(url, str(result.get("title") or ""), category=category)
