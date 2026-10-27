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

#: Tokens that carry no title information: they are copied from release-name junk.
RELEASE_JUNK = set("""
2160p 1440p 1080p 720p 576p 480p 360p 4k uhd hdr web webrip webdl webdlrip hdtv pdtv
dvdrip dvdscr dvd5 dvd9 bluray brrip bdrip remux x264 x265 h264 h265 hevc avc xvid divx
aac ac3 dts dd5 ddp5 flac mp3 mkv mp4 avi ts m2ts 10bit 8bit proper repack extended
internal limited complete season multi sub subs eng english french ger ita spa hardcoded
mvgroup org forum yts rarbg miserable eztv bigdoc megusta scene rip xpost
""".split())

#: Broadcasters that prefix a title in release names but are not part of it.
NETWORKS = set("bbc c4 ch4 channel4 pbs hbo itv abc cbc sbs nfb arte zdf ard nhk".split())

#: A candidate may introduce at most this many extra distinctive words.
MAX_EXTRA_WORDS = 2

#: A candidate must be at least this many times taller to justify re-downloading.
MIN_GAIN = 1.25

#: Years either side of the library item's year that still count as the same programme.
#: A film's production year, broadcast year and release year commonly differ, so an
#: exact match is too strict — but a wide one lets a totally different programme through.
YEAR_TOLERANCE = 1

_EPISODE_RE = re.compile(r"\bs\d{1,2}\s*e\d{1,3}\b|\bs\d{2}\b|\bepisode\s*\d+\b")

#: Single-token episode markers that word-set comparison must ignore on BOTH
#: sides: a release's "S01E02" and a folder label's "E02" are the same slot.
_EP_TOKEN_RE = re.compile(r"^(s\d{1,2}e\d{1,3}|e\d{1,3}|s\d{2}|episode\d*)$")

#: Markers that make a release a DIFFERENT work, not a better copy of the same one.
_SEQUEL_RE = re.compile(r"\b(?:ii|iii|iv|part\s*[2-9]|vol(?:ume)?\s*[2-9])\b")


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


_YEAR_RE = re.compile(r"\b(19\d{2}|20[0-3]\d)\b")


def release_year(title: str) -> Optional[int]:
    """Year stated in a release name, if any."""
    m = _YEAR_RE.search(str(title or ""))
    return int(m.group(1)) if m else None


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _title_words(text: str, *, strip_junk: bool) -> set:
    """Significant words of a title, optionally dropping release-name noise.

    Episode-marker tokens are ALWAYS dropped (both sides): the release says
    S01E02 where the folder label says E02, and word-set comparison must not
    fail on that. Slot compatibility is enforced separately, in title_matches.
    """
    words = {w for w in _normalise(text).split() if len(w) > 2 and not w.isdigit()}
    words = {w for w in words if not _EP_TOKEN_RE.match(w)}
    if strip_junk:
        words -= RELEASE_JUNK
        words -= NETWORKS
    return words


def _slots_compatible(candidate: str, wanted: str) -> bool:
    """Same episode slot, or no slot conflict. Season 0 is a wildcard.

    A bare "E02" folder label ((0, 2)) matches a release's "S01E02" ((1, 2)):
    the label was derived from the release and carries no season. Different
    episode numbers are different programmes.
    """
    from . import slot_check  # deferred: quality stays import-light for tests

    cs, ce = slot_check.slot_numbers(candidate)
    ws, we = slot_check.slot_numbers(wanted)
    if not ce or not we:
        return True
    if ce != we:
        return False
    return not (cs and ws) or cs == ws


def title_matches(candidate: str, wanted: str) -> bool:
    """Is `candidate` plausibly the same programme as `wanted`?

    Guards against an unrelated release ranking highly for a title the indexers lack.
    Seen for real: "The Lone Ranger (2013)" offered for a music documentary, "Cooks
    Country S18E18 Cajun Cooking" for "Cajun Country", "Stranger Things S02E05 ...
    Dig Dug" for "Dig!", and a C4 wrestling upload for "The Filth and the Fury".

    Loose word overlap is not enough — those all passed it. So: every wanted word must
    appear, the candidate may add at most MAX_EXTRA_WORDS of its own, and an episodic
    release cannot stand in for a standalone film (or the reverse).
    """
    want = _title_words(wanted, strip_junk=False)
    if not want:
        return False

    cand = _title_words(candidate, strip_junk=True)
    if not want <= cand:
        return False
    # Same-slot check replaces the old episodic-asymmetry guard: S01E02 vs E02
    # is the same programme; S01E02 vs S01E03 is not. The regex guard below
    # survives only for markers no slot parser reconciled (one side episodic,
    # the other not, with no comparable slot numbers).
    if not _slots_compatible(candidate, wanted):
        return False
    if _EPISODE_RE.search(_normalise(candidate)) and not _EPISODE_RE.search(_normalise(wanted)):
        from . import slot_check as _sc

        cs, ws = _sc.slot_numbers(candidate), _sc.slot_numbers(wanted)
        if not (cs[1] and ws[1]):
            return False
    # A sequel is a different film: "Stairway To Heaven II" is not "Stairway to Heaven".
    if _SEQUEL_RE.search(_normalise(candidate)) and not _SEQUEL_RE.search(_normalise(wanted)):
        return False
    return len(cand - want) <= MAX_EXTRA_WORDS


def verdict(candidate_title: str, held_height: Optional[int],
            held_state: str = "held", wanted_title: Optional[str] = None,
            wanted_year: Optional[int] = None) -> Dict[str, object]:
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

    cand_year = release_year(candidate_title)
    if wanted_year and cand_year and abs(cand_year - int(wanted_year)) > YEAR_TOLERANCE:
        return {"candidate_height": release_height(candidate_title),
                "held_height": held_height, "verdict": "different-title", "queue": False,
                "reason": f"release year {cand_year} is more than {YEAR_TOLERANCE} "
                          f"away from {wanted_year} — wrong programme"}

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

    if cand >= held * MIN_GAIN:
        return {"candidate_height": cand, "held_height": held, "verdict": "upgrade",
                "queue": True, "reason": f"{cand}p beats the {held}p we hold"}

    if cand > held:
        pct = round(100 * (cand / held - 1))
        return {"candidate_height": cand, "held_height": held, "verdict": "marginal",
                "queue": False,
                "reason": f"{cand}p is only {pct}% taller than the {held}p we hold "
                          f"— not worth re-downloading"}

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
        height = info.get("h")
        if height:
            return {"state": "held", "height": int(height), "matched": item.get("Name")}
    return {"state": "unprobeable", "height": None, "matched": items[0].get("Name")}


def held_height_for(term: str) -> Optional[int]:
    """Convenience: the probed height if we hold a measurable copy, else None.

    Prefer held_state_for() when the difference between "nothing held" and "held but
    unmeasurable" matters — which, for the purpose of queueing a download, it does.
    """
    height = held_state_for(term).get("height")
    return int(height) if isinstance(height, int) else None


def decide(candidate_title: str, term: str,
           wanted_year: Optional[int] = None) -> Dict[str, object]:
    """verdict() against whatever the library actually holds for `term`."""
    st = held_state_for(term)
    height = st.get("height")
    return verdict(candidate_title, int(height) if isinstance(height, int) else None,
                   str(st.get("state")), wanted_title=term, wanted_year=wanted_year)
