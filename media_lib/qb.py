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


def delete(hashes: List[str], op=None, delete_files: bool = True) -> int:
    """Delete torrents by FULL hash. Refuses prefixes.

    Returns the number requested; callers must re-list to confirm.
    """
    bad = [h for h in hashes if len(str(h)) != 40]
    if bad:
        raise ValueError(f"refusing to delete non-40-char hashes: {bad}")
    if not hashes:
        return 0
    op = op or _opener()
    body = urllib.parse.urlencode(
        {
            "hashes": "|".join(hashes),
            "deleteFiles": "true" if delete_files else "false",
        }
    ).encode()
    req = urllib.request.Request(f"{_base()}/api/v2/torrents/delete", data=body)
    # qBittorrent answers 200 with an empty body on success.
    op.open(req, timeout=60).read()
    return len(hashes)


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
    op = op or _opener()
    body = urllib.parse.urlencode({
        "hashes": "|".join(hashes), "value": "true" if value else "false"}).encode()
    req = urllib.request.Request(
        f"{_base()}/api/v2/torrents/setForceStart",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with op.open(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


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


def torrent_for_path(path: str, op=None) -> Optional[Dict[str, Any]]:
    """The torrent whose content contains this file path.

    Used to attach a ledger hash to a placement, so `scan` stops re-offering a
    release that has already been ingested (the two-ledger split).
    """
    target = norm_name(path)
    if not target:
        return None
    all_t = torrents(op=op)
    for t in all_t:  # cheap: content_path first
        cp = norm_name(t.get("content_path") or "")
        if cp and (cp == target or target.startswith(cp)):
            return t
    for t in all_t:  # then per-file
        save = (t.get("save_path") or "").rstrip("/")
        for f in files(t.get("hash") or "", op=op):
            if norm_name(f"{save}/{f.get('name')}") == target:
                return t
    return None
