"""Should this release be downloaded at all? Compare it with what we already hold.

Written after getting this wrong twice (24 Oct 2026). First: four "replacements" were
queued for content already held, three at the SAME quality (untagged PDTV rips chosen on
seeders). Then, having fixed that, the first gate treated "could not determine what we
hold" as "we hold nothing" — and queued a dozen more, including a completely unrelated
film that happened to rank highly in the search.

Two rules follow, and both are enforced here:

1. **Queue only a proven upgrade.** An untagged SD release is not proof. Neither is a
   720p rip against a 1080p copy.
2. **Uncertainty never resolves to "download".** If we cannot probe what we hold, or the
   candidate is not clearly the same title, the answer is "ask a human", not "queue".

    release_height("Some Doc 2024 1080p WEB-DL")     -> 1080
    release_height("BBC Storyville PDTV x264")       -> None      (SD, unstated)
    verdict("... 720p ...", 464)                     -> upgrade       (queue)
    verdict("... PDTV ...", 832)                     -> unknown       (no queue)
    verdict(... , held_state="unprobeable")          -> held-unknown  (no queue)
"""
from __future__ import annotations

import re
from typing import Dict, Optional

RES_HEIGHTS = {"2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "576p": 576,
               "480p": 480, "360p": 360}
SD_LABELS = ("pdtv", "hdtv", "dvdrip", "dvdscr", "webrip", "tvrip", "vhsrip", "satrip")

#: Fraction of the wanted title's words that must appear in a candidate.
MATCH_THRESHOLD = 0.6


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
    """An SD rip naming no height — indistinguishable from what we may already hold."""
    return release_height(title) is None and any(
        lbl in str(title or "").lower() for lbl in SD_LABELS)


def _words(text: str) -> set:
    return {w for w in re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()
            if len(w) > 2}


def title_matches(candidate: str, wanted: str) -> bool:
    """Is `candidate` plausibly the same programme as `wanted`?

    Guards against an unrelated release ranking highly for a title the indexers lack —
    a "The Lone Ranger (2013)" release was once queued as a replacement for a music
    documentary because nothing checked the two were related.
    """
    want = _words(wanted)
    if not want:
        return False
    return len(want & _words(candidate)) / len(want) >= MATCH_THRESHOLD


def verdict(candidate_title: str, held_height: Optional[int],
            held_state: str = "held", wanted_title: Optional[str] = None) -> Dict[str, object]:
    """Decide whether `candidate_title` is worth downloading.

    held_state is what held_state_for() found:
      'none'         nothing matching is held — a genuine gap
      'held'         a copy is held and its height is known
      'unprobeable'  a copy is held but its resolution could not be read
      'ambiguous'    several different items matched; no height can be attributed

    Only 'none', or a proven height increase, sets queue=True.
    """
    if wanted_title and not title_matches(candidate_title, wanted_title):
        return {"candidate_height": release_height(candidate_title),
                "held_height": held_height, "verdict": "different-title", "queue": False,
                "reason": "not clearly the same programme — not a replacement"}

    cand = release_height(candidate_title)

    if held_state == "none":
        return {"candidate_height": cand, "held_height": None, "verdict": "no-local-copy",
                "queue": True, "reason": "nothing held locally, so any release fills a gap"}

    if held_state in ("unprobeable", "ambiguous"):
        return {"candidate_height": cand, "held_height": held_height,
                "verdict": "held-unknown", "queue": False,
                "reason": f"a copy is held but its resolution is unknown ({held_state}) "
                          f"— resolve that before downloading a replacement"}

    held = held_height or None
    if not held:
        return {"candidate_height": cand, "held_height": None, "verdict": "held-unknown",
                "queue": False, "reason": "held copy has no measurable height"}

    if cand is None:
        why = "SD release with no stated height" if is_sd_release(candidate_title) \
              else "release states no resolution"
        return {"candidate_height": None, "held_height": held, "verdict": "unknown",
                "queue": False,
                "reason": f"{why} — cannot prove it beats the {held}p we hold"}

    if cand > held:
        return {"candidate_height": cand, "held_height": held, "verdict": "upgrade",
                "queue": True, "reason": f"{cand}p beats the {held}p we hold"}

    return {"candidate_height": cand, "held_height": held, "verdict": "same-or-worse",
            "queue": False, "reason": f"{cand}p is not better than the {held}p we hold"}


def held_state_for(term: str) -> Dict[str, object]:
    """What do we hold for `term`, and can we measure it?

    Returns {'state', 'height', 'matched'}. A probe failure is 'unprobeable', NEVER
    'none' — conflating the two caused a dozen unnecessary downloads.
    """
    from . import jellyfin, place, probe

    try:
        uid = jellyfin.user_id()
        rows = jellyfin.get_json(f"/Users/{uid}/Items", params={
            "searchTerm": term, "Recursive": "true", "Fields": "Path", "Limit": "8"})
    except Exception:
        return {"state": "unprobeable", "height": None, "matched": None}

    items = [i for i in (rows.get("Items") or [])
             if i.get("Type") in ("Movie", "Episode") and i.get("Path")]
    if not items:
        return {"state": "none", "height": None, "matched": None}

    paths = {i["Path"] for i in items}
    if len(paths) > 3:
        return {"state": "ambiguous", "height": None, "matched": len(paths)}

    for item in items:
        try:
            info = probe.probe(place.to_disk(item["Path"])) or {}
        except Exception:
            continue
        if info.get("h"):
            return {"state": "held", "height": int(info["h"]), "matched": item.get("Name")}
    return {"state": "unprobeable", "height": None, "matched": items[0].get("Name")}


def held_height_for(term: str) -> Optional[int]:
    """Convenience: the probed height if we hold a measurable copy, else None.

    Prefer held_state_for() when the difference between "nothing held" and "held but
    unmeasurable" matters — which, for the purpose of queueing a download, it does.
    """
    height = held_state_for(term).get("height")
    return int(height) if isinstance(height, int) else None


def decide(candidate_title: str, term: str) -> Dict[str, object]:
    """verdict() against whatever the library actually holds for `term`."""
    st = held_state_for(term)
    height = st.get("height")
    return verdict(candidate_title, int(height) if isinstance(height, int) else None,
                   str(st.get("state")), wanted_title=term)
