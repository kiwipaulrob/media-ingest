"""Should this release be downloaded at all? Compare it with what we already hold.

Written after getting this wrong (24 Oct 2026): four replacement releases were queued
for content already in the library, and three of them were the SAME quality — PDTV/x264
rips with no resolution tag, chosen because the search sorted on seeders and treated an
untagged title as merely "low". Downloading those costs bandwidth, disk and hours of
transcoding to arrive back where we started.

The rule: **queue only a proven upgrade.** An untagged SD release is not a proven
upgrade, so it is reported for a human decision rather than queued automatically.

    release_height("Some Doc 2024 1080p WEB-DL x264")   -> 1080
    release_height("BBC Storyville PDTV x264")          -> None   (SD, unspecified)
    verdict("... 720p ...", held_height=464)            -> upgrade,    queue=True
    verdict("... PDTV ...", held_height=832)            -> unknown,    queue=False
    verdict("... 720p ...", held_height=1080)           -> same-or-worse, queue=False
"""
from __future__ import annotations

import re
from typing import Optional

#: Ordinary height for each tag, used to compare against a probed height.
RES_HEIGHTS = {"2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "576p": 576,
               "480p": 480, "360p": 360}

#: Labels that mean "standard definition" without stating a pixel height.
SD_LABELS = ("pdtv", "hdtv", "dvdrip", "dvdscr", "webrip", "tvrip", "vhsrip", "satrip")


def release_height(title: str) -> Optional[int]:
    """Pixel height implied by a release name, or None when it does not say."""
    t = str(title or "").lower()
    for tag in ("2160p", "1440p", "1080p", "720p", "576p", "480p", "360p"):
        if tag in t:
            return RES_HEIGHTS[tag]
    if re.search(r"\b4k\b|\buhd\b", t):
        return 2160
    return None


def is_sd_release(title: str) -> bool:
    """An SD rip that names no height — indistinguishable from what we may already have."""
    return release_height(title) is None and any(lbl in str(title or "").lower() for lbl in SD_LABELS)


def verdict(candidate_title: str, held_height: Optional[int]) -> dict:
    """Decide whether `candidate_title` is worth downloading given what we hold.

    Returns {'candidate_height', 'held_height', 'verdict', 'queue', 'reason'} where
    verdict is one of upgrade / same-or-worse / unknown / no-local-copy.
    """
    cand = release_height(candidate_title)
    held = held_height or None

    if not held:
        return {"candidate_height": cand, "held_height": None, "verdict": "no-local-copy",
                "queue": True, "reason": "nothing held locally, so any release fills a gap"}

    if cand is None:
        why = "SD release with no stated height" if is_sd_release(candidate_title) \
              else "release states no resolution"
        return {"candidate_height": None, "held_height": held, "verdict": "unknown",
                "queue": False,
                "reason": f"{why} — cannot prove it beats the {held}p copy we hold"}

    if cand > held:
        return {"candidate_height": cand, "held_height": held, "verdict": "upgrade",
                "queue": True, "reason": f"{cand}p beats the {held}p we hold"}

    return {"candidate_height": cand, "held_height": held, "verdict": "same-or-worse",
            "queue": False, "reason": f"{cand}p is not better than the {held}p we hold"}

def held_height_for(term: str) -> Optional[int]:
    """Probed height of the library copy matching `term`, or None if we hold nothing.

    Uses Jellyfin for the path and the media host for the probe, so the comparison is
    against the real file rather than a guessed resolution.
    """
    from . import jellyfin, place, probe

    try:
        uid = jellyfin.user_id()
        rows = jellyfin.get_json(f"/Users/{uid}/Items", params={
            "searchTerm": term, "Recursive": "true", "Fields": "Path", "Limit": "5"})
    except Exception:
        return None
    items = [i for i in (rows.get("Items") or [])
             if i.get("Type") in ("Movie", "Episode") and i.get("Path")]
    if not items:
        return None
    try:
        disk = place.to_disk(items[0]["Path"])
        info = probe.probe(disk) or {}
        return int(info["h"]) if info.get("h") else None
    except Exception:
        return None


def decide(candidate_title: str, term: str) -> dict:
    """verdict() against whatever the library actually holds for `term`."""
    return verdict(candidate_title, held_height_for(term))
