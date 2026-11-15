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
mvgroup org forum yts rarbg miserable eztv bigdoc megusta scene rip xpost classics
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


#: Jellyfin's provider-id folder tag — 'Ace in the Hole (1951) [tmdbid-25364]'.
#: It is library METADATA, not part of the title, and left in it makes every
#: comparison fail: the word 'tmdbid' joins the wanted set and the gate answers
#: 'different-title' for the exact film it was asked about.
_TMDB_TAG = re.compile(r"[\[{]\s*tmdb(?:id)?[-_ ]?\d+\s*[\]}]", re.I)


def _normalise(text: str) -> str:
    text = _TMDB_TAG.sub(" ", str(text or ""))
    text = text.lower()
    # Initialisms stay one word ("D.O.A." -> "doa", "T-Men" -> "tmen"): dropping
    # the letters made D.O.A. an empty word-set that matched nothing, and once
    # made T-Men identical to "Real Men".
    text = re.sub(r"\b([a-z])-([a-z0-9])", r"\1\2", text)
    text = re.sub(r"(?<=[a-z])\.(?=[a-z]\b|\.(?:\s|$))", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _title_words(text: str, *, strip_junk: bool) -> set:
    """Significant words of a title, optionally dropping release-name noise.

    Episode-marker tokens are ALWAYS dropped (both sides): the release says
    S01E02 where the folder label says E02, and word-set comparison must not
    fail on that. Slot compatibility is enforced separately, in title_matches.
    """
    words = {w for w in _normalise(text).split() if len(w) > 2 and not w.isdigit()}
    words = {w for w in words if not _EP_TOKEN_RE.match(w)}
    # Plural tolerance ("Internets" vs "Internet" blocked a genuine ingest):
    # add the singular variant, keep both. Strictness survives — the subset
    # rule still requires every wanted word present in some form.
    words |= {w[:-1] for w in words if w.endswith("s") and len(w) > 4
              and not w.endswith("ss")}
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


def title_matches(candidate: str, wanted: str, *, max_extra: int = MAX_EXTRA_WORDS,
                    same_year: bool = False) -> bool:
    """Is `candidate` plausibly the same programme as `wanted`?

    Guards against an unrelated release ranking highly for a title the indexers lack.
    Seen for real: "The Lone Ranger (2013)" offered for a music documentary, "Cooks
    Country S18E18 Cajun Cooking" for "Cajun Country", "Stranger Things S02E05 ...
    Dig Dug" for "Dig!", and a C4 wrestling upload for "The Filth and the Fury".

    Loose word overlap is not enough — those all passed it. So: every wanted word must
    appear, the candidate may add at most MAX_EXTRA_WORDS of its own, and an episodic
    release cannot stand in for a standalone film (or the reverse).

    same_year: the candidate and wanted years agree exactly. Year agreement is
    strong corroboration, so verbose-but-legit release names (aka titles like
    "[Dead on Arrival]", encoder credits) get double the extras budget. The
    subset rule still holds — a wrong film fails there regardless of year.
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
    budget = max_extra * 2 + 2 if same_year else max_extra
    return len(cand - want) <= budget


def _years_agree(candidate_title: str, wanted_title: str,
                 wanted_year: Optional[int] = None) -> bool:
    """Exact year agreement between candidate and wanted. Pure.

    Compares release_year() both sides, falling back to the explicit
    wanted_year (the destination folder's year). Exact match only — ±1
    stays strict here because this widens the extras budget.
    """
    cy, wy = release_year(candidate_title), release_year(wanted_title)
    if wanted_year and str(wanted_year).isdigit():
        wy = wy or int(wanted_year)
    return bool(cy and wy and cy == wy)


def verdict(candidate_title: str, held_height: Optional[int],
            held_state: str = "held", wanted_title: Optional[str] = None,
            wanted_year: Optional[int] = None, max_extra: int = MAX_EXTRA_WORDS) -> Dict[str, object]:
    """Decide whether `candidate_title` is worth downloading.

    held_state is what held_state_for() found:
      'none'         nothing matching is held — a genuine gap
      'held'         a copy is held and its height is known
      'unprobeable'  a copy is held but its resolution could not be read
      'ambiguous'    several different items matched; no height can be attributed

    Only 'none', or a proven height increase, sets queue=True.
    """
    if wanted_title and not title_matches(candidate_title, wanted_title,
                                           max_extra=max_extra,
                                           same_year=_years_agree(candidate_title,
                                                                  wanted_title,
                                                                  wanted_year)):
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


def _short_title(term: str) -> Optional[str]:
    """First significant words — the broad query when the full phrase misses.

    A full-phrase `searchTerm` returns zero while the bare title hits; that is
    a query fault, not proof of absence, so the lookup retries broad and
    filters in code.
    """
    words = [w for w in _normalise(term).split()
             if len(w) > 2 and not w.isdigit() and not _EP_TOKEN_RE.match(w)]
    short = " ".join(words[:4])
    return short or None


def _shares_a_word(a: str, b: str) -> bool:
    """Lenient relevance for fuzzy search hits: one significant word in common.

    Either side wordless (a one-letter title like "M") means "cannot judge" —
    keep the row rather than acquit the library on an empty comparison.
    """
    aw = _title_words(a, strip_junk=False)
    bw = _title_words(b, strip_junk=False)
    return True if not aw or not bw else bool(aw & bw)


def _episode_slot_wanted(ep: Dict[str, object], term: str) -> bool:
    """Does this episode row fill the slot `term` asks about (if any)?

    A term naming no slot is a whole-work want: every held episode is
    evidence. An unnumbered row cannot be excluded — let the probe speak.
    Season 0 (bare "E02" labels) is the wildcard.
    """
    from . import slot_check  # deferred: quality stays import-light for tests

    ts, te = slot_check.slot_numbers(term)
    if not te:
        return True
    es = int(ep.get("ParentIndexNumber") or 0)
    ee = int(ep.get("IndexNumber") or 0)
    if not ee:
        return True
    if ee != te:
        return False
    return not (ts and es) or ts == es


#: Extensions that make a file library media (qb.VIDEO_EXT plus legacy rips).
_VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".mov",
               ".wmv", ".flv", ".mpg", ".mpeg", ".divx", ".webm", ".ogm", ".vob")


def _folder_videos(disk_folder: str, cap: int = 8) -> Optional[list]:
    """Video files inside a folder-shelved holding. None when unlistable.

    None is "unknown", not "empty": a failed listing must never read as proof
    the folder holds nothing.
    """
    import shlex

    from . import probe as _probe

    try:
        out = _probe.on_media(
            "find %s -maxdepth 2 -type f | head -50" % shlex.quote(disk_folder),
            timeout=120)
    except Exception:
        return None
    vids = [ln for ln in out.splitlines()
            if ln.strip().lower().endswith(_VIDEO_EXTS)]
    return vids[:cap]


def held_state_for(term: str) -> Dict[str, object]:
    """What do we hold for `term`, and can we measure it?

    Returns {'state', 'height', 'matched'}. A probe failure is 'unprobeable', NEVER
    'none' — conflating the two caused a dozen unnecessary downloads.

    Series- and folder-aware. A `searchTerm` hit on a Series row owns episodes
    the search itself never lists — filtering to Movie/Episode read a fully
    held season as 'none' and queued a byte-identical duplicate. A
    folder-shelved documentary surfaces only as a Folder row; its files are
    enumerated on disk and probed, and a Folder that yields no measurable
    file is 'unprobeable', never 'none' (child listings against Folder rows
    return zero, so the Folder row itself is the holding evidence).
    """
    from . import jellyfin, place, probe

    def _search(q: str) -> list:
        try:
            uid = jellyfin.user_id()
            rows = jellyfin.get_json(f"/Users/{uid}/Items", params={
                "searchTerm": q, "Recursive": "true",
                "Fields": "Path,ParentIndexNumber,IndexNumber,ProductionYear",
                "Limit": "20"})
        except Exception:
            return []
        return list((rows or {}).get("Items") or [])

    rows = _search(term)
    if not rows:
        short = _short_title(term)
        if short and _normalise(short) != _normalise(term):
            rows = _search(short)
    # In-code relevance: Jellyfin's search is fuzzy; a hit sharing no
    # significant word with the term is not the programme.
    rows = [r for r in rows if _shares_a_word(str(r.get("Name") or ""), term)]
    if not rows:
        return {"state": "none", "height": None, "matched": None}

    direct = [i for i in rows
              if i.get("Type") in ("Movie", "Episode") and i.get("Path")]
    series = [i for i in rows if i.get("Type") == "Series"]
    folders = [i for i in rows if i.get("Type") == "Folder" and i.get("Path")]

    unknown = False  # holdings seen but not resolved to measurable files
    slot_gap = False  # series held, but no episode fills the wanted slot
    for s in series:
        try:
            eps = jellyfin.get_json(
                f"/Shows/{s.get('Id')}/Episodes",
                params={"Fields": "Path,ParentIndexNumber,IndexNumber"}) or {}
        except Exception:
            unknown = True
            continue
        listed = list(eps.get("Items") or [])
        if not listed:
            # An empty listing is a busy server as often as an empty series —
            # never evidence of a gap.
            unknown = True
            continue
        pathed = [e for e in listed if e.get("Path")]
        if not pathed:
            continue  # provider/virtual slots only: this series holds no media
        matched = [e for e in pathed if _episode_slot_wanted(e, term)]
        if not matched:
            slot_gap = True
            continue
        direct.extend(matched)

    for f in folders:
        try:
            disk = place.to_disk(f["Path"])
            vids = _folder_videos(disk)
        except Exception:
            unknown = True
            continue
        if not vids:
            # Unlistable, or no measurable file: the Folder row itself is the
            # holding evidence — held-but-unmeasurable, never a gap.
            unknown = True
            continue
        direct.extend({"Path": v, "Name": f.get("Name")} for v in vids)

    # Year-aware: a same-stem different-year item is a different programme.
    # "D.O.A. (1949)" must not match the 1980 punk doc — that refusal blocked
    # a genuine gap fill. Items whose path-year disagrees are not the same film.
    want_year = release_year(term)
    if want_year and direct:
        same = [i for i in direct
                if (release_year(str(i.get("Path") or "")) or want_year) == want_year
                or abs((release_year(str(i.get("Path") or "")) or want_year)
                       - want_year) <= YEAR_TOLERANCE]
        if same:
            direct = same
        else:
            return {"state": "none", "height": None, "matched": None}

    paths = {i["Path"] for i in direct}
    if len(paths) > 3:
        return {"state": "ambiguous", "height": None, "matched": len(paths)}

    for item in direct:
        try:
            info = probe.probe(place.to_disk(item["Path"])) or {}
        except Exception:
            continue
        height = info.get("h")
        if height:
            return {"state": "held", "height": int(height), "matched": item.get("Name")}
    if direct:
        # Files seen, none measurable: probe failure, never a gap.
        return {"state": "unprobeable", "height": None, "matched": direct[0].get("Name")}
    if slot_gap and not unknown:
        # The series is readable and the wanted slot verifiably has no media.
        return {"state": "none", "height": None, "matched": None}
    if unknown or folders or series:
        return {"state": "unprobeable", "height": None,
                "matched": (series + folders)[0].get("Name")}
    return {"state": "none", "height": None, "matched": None}


def held_height_for(term: str) -> Optional[int]:
    """Convenience: the probed height if we hold a measurable copy, else None.

    Prefer held_state_for() when the difference between "nothing held" and "held but
    unmeasurable" matters — which, for the purpose of queueing a download, it does.
    """
    height = held_state_for(term).get("height")
    return int(height) if isinstance(height, int) else None


def decide(candidate_title: str, term: str,
           wanted_year: Optional[int] = None, max_extra: int = MAX_EXTRA_WORDS) -> Dict[str, object]:
    """verdict() against whatever the library actually holds for `term`."""
    st = held_state_for(term)
    height = st.get("height")
    return verdict(candidate_title, int(height) if isinstance(height, int) else None,
                   str(st.get("state")), wanted_title=term, wanted_year=wanted_year,
                   max_extra=max_extra)
