"""Release-name -> library-item matching.

Ported from jellyfin-media-intake.py::match_item, with TWO FIXES measured
against the production code on 2026-10-20 (see tests/test_matcher.py, which
pins both):

FIX 1 — run-together release names could never match.
    Production requires >=1 shared distinctive token. A name like
    'TheWarOnDemocracy' normalises to ONE token, so it can never share a token
    with 'The War on Democracy' ({war, democracy}). The squash signal exists
    precisely for this case (its docstring says so) but the token gate ran
    first and rejected it, so the signal was dead code. Result: false NEW
    classifications. Now a squash-exact match of sufficient length carries a
    match on its own.

FIX 2 — release noise poisoned the coverage guard.
    Coverage was computed over the release's raw tokens, so
    'Anvil.The.Story.Of.Anvil.2008.1080p.BluRay.x264-GRP' scored coverage
    2/6 = 0.33 against 'Anvil! The Story of Anvil' and was refused. Release
    tags are now stripped BEFORE tokenising, so coverage measures title words.

Unchanged and load-bearing: 'Good.Hair' must never match 'Good', filler words
never carry a match, and short token sets are never auto-accepted.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from . import config

# --------------------------------------------------------------- normalising
#: Known release tags. Stripped only as whole words.
_TAGS = re.compile(
    r"(?i)\b("
    r"1080p|720p|480p|576p|2160p|4k|uhd|hdtv|pdtv|sdtv|"
    r"web[- ]?dl|web[- ]?rip|blu[- ]?ray|bdrip|brrip|bdremux|dvdrip|dvdrip|dvd|hdrip|hdcam|camrip|telesync|"
    r"aac|ac3|eac3|dts(?:[- ]?hd)?|truehd|atmos|ddp?[257][._]?[01]|flac|mp3|opus|vorbis|"
    r"x264|x265|h264|h265|hevc|av1|xvid|divx|vp9|10bit|8bit|hi10p|"
    r"proper|repack|rerip|real|extended|unrated|uncut|remastered|remux|"
    r"multi|dual|subbed|dubbed|internal|limited|retail|complete|"
    # Edition/brand labels: they name the RELEASE, never the film, and left in
    # place they poison the TMDB query ('Leave Her to Heaven Criterion' = no match).
    r"criterion|eureka|arrow|kino|shout|scream[- ]?factory|olive|bfi|"
    r"warner[- ]?archive|wac|masters[- ]of[- ]cinema|janus|"
    r"collectors?[- ]?edition|special[- ]?edition|anniversary|"
    r"director'?s[- ]?cut|theatrical[- ]?cut|extras"
    r")\b"
)
#: A trailing '-GROUP' only when it follows whitespace, so hyphenated titles
#: ('Spider-Man', 'K-pop') are never eaten.
_TRAILING_GROUP = re.compile(r"(?<=\s)-\s*[A-Za-z0-9]{2,12}$")
#: Empty or tag-emptied bracket groups left behind by the tag strip —
#: 'Sudden Fear (1952) [BluRay] [1080p]' loses its tags and keeps '[( )] [ ]'.
_EMPTY_BRACKETS = re.compile(r"[\[\{]\s*[\]\}]|\(\s*\)")
#: Square-bracket groups (release site/group tags) — safe to drop wholesale.
_BRACKET_GROUP = re.compile(r"\[[^\]]*\]|\{[^\}]*\}")
#: Parenthesised groups that hold NO year: '(YTS)', '(Extended)'. A year is the
#: one thing that must survive, so those parens are kept.
_PAREN_NON_YEAR = re.compile(r"\((?!\s*(?:19|20)\d{2}\s*\))[^)]*\)")
_EXT_TAIL = re.compile(
    r"(?i)(" + "|".join(re.escape(e) for e in sorted(config.VIDEO_EXTS)) + r")$"
)


def strip_extension(name: str) -> str:
    """Drop a trailing VIDEO extension only.

    os.path.splitext splits at the last dot, which turns 'Good.Hair' into
    'Good' and '...x264-GRP' into '...x264'. That is fine for real filenames and
    wrong for release/folder names, so extension stripping is explicit.
    """
    return _EXT_TAIL.sub("", name or "")


def clean_release(name: str) -> str:
    """Release/folder name -> title-ish string with tags and separators gone."""
    s = strip_extension(name or "")
    s = re.sub(r"[._]+", " ", s)  # separators to spaces BEFORE tag strip
    s = _TAGS.sub(" ", s)
    # Bracket droppings must go AFTER the tag strip, or '[BluRay]' would still
    # be bracketed when we look for bracket groups. Years in parens stay.
    s = _BRACKET_GROUP.sub(" ", s)
    s = _PAREN_NON_YEAR.sub(" ", s)
    s = _EMPTY_BRACKETS.sub(" ", s)
    s = _TRAILING_GROUP.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def squash(s: str) -> str:
    """Alphanumeric-only lowercase, so 'TheWarOnDemocracy' == 'The War on Democracy'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tokens(s: str) -> Set[str]:
    """Lowercase alphanumeric tokens, filler words and pure numbers removed."""
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {
        t
        for t in s.split()
        if t not in config.STOPWORDS and not t.isdigit() and len(t) >= 2
    }


def distinctive(toks: Iterable[str]) -> Set[str]:
    """Title words that actually identify something (>=3 chars, not filler)."""
    return {
        t for t in toks if t not in config.STOPWORDS and not t.isdigit() and len(t) >= 3
    }


def _sorted_join(toks: Iterable[str]) -> str:
    return "".join(sorted(toks))


# ------------------------------------------------------------------- matching
MATCH, REJECT, REVIEW = "MATCH", "REJECT", "REVIEW"


def _item_rows(dump: Any) -> List[Dict[str, Any]]:
    """Accept either the production shape {'libs': {lib: [rows]}} or a flat list."""
    if isinstance(dump, dict):
        rows: List[Dict[str, Any]] = []
        for lib, items in (dump.get("libs") or {}).items():
            for it in items or []:
                rows.append(dict(it, _lib=it.get("_lib") or lib))
        return rows
    return [dict(it) for it in (dump or [])]


def _score_pair(
    cand_squash: str, cand_toks: Set[str], item: Dict[str, Any]
) -> Dict[str, Any]:
    """Score ONE release against ONE library row. Pure, no I/O."""
    it_name = item.get("Name") or item.get("name") or ""
    it_toks = set(item.get("tokens") or tokens(clean_release(it_name)))
    it_squash = item.get("squash") or squash(clean_release(it_name))

    ratio = difflib.SequenceMatcher(None, cand_squash, it_squash).ratio()
    # Sorted-token ratio: word-order and punctuation insensitive, so
    # 'Anvil! The Story of Anvil' lines up with 'Anvil The Story Of Anvil'.
    # GUARD: difflib scores '' vs '' as 1.0, so never compare empty joins —
    # two 2-char titles ('Up' / 'Up (2009)') both reduce to zero distinctive
    # tokens and would otherwise score a perfect match.
    c_srt, i_srt = _sorted_join(cand_toks), _sorted_join(it_toks)
    if c_srt and i_srt:
        ratio = max(ratio, difflib.SequenceMatcher(None, c_srt, i_srt).ratio())

    containment = False
    if (
        cand_squash
        and it_squash
        and (cand_squash in it_squash or it_squash in cand_squash)
    ):
        short, long_ = sorted((cand_squash, it_squash), key=len)
        share = len(short) / max(1, len(long_))
        if share >= config.MIN_CONTAINMENT_SHARE:
            containment = True
            ratio = max(ratio, 0.85)

    cd = distinctive(cand_toks)
    idd = distinctive(it_toks)
    shared = cd & idd
    unaccounted = cd - idd  # release title words the target does NOT carry
    coverage = (len(shared) / len(cd)) if cd else 0.0
    exact = bool(cand_squash) and cand_squash == it_squash

    return {
        "ratio": ratio,
        "exact": exact,
        "containment": containment,
        "shared": len(shared),
        "unaccounted": len(unaccounted),
        "coverage": coverage,
        "cand_distinctive": len(cd),
        "item_distinctive": len(idd),
    }


def match(release: str, dump: Any) -> Dict[str, Any]:
    """Decide whether `release` identifies an item in `dump`.

    Returns {'decision', 'score', 'reason', 'item'} where decision is MATCH,
    REJECT or REVIEW. REVIEW means "a human must look": never auto-acted on.
    """
    cand_clean = clean_release(release)
    cand_squash = squash(cand_clean)
    cand_toks = tokens(cand_clean)

    if not cand_squash:
        return {
            "decision": REVIEW,
            "score": 0.0,
            "reason": "empty-release",
            "item": None,
        }

    best: Optional[Dict[str, Any]] = None
    best_s: Optional[Dict[str, Any]] = None
    for item in _item_rows(dump):
        s = _score_pair(cand_squash, cand_toks, item)
        if best_s is None or s["ratio"] > best_s["ratio"]:
            best, best_s = item, s

    if best_s is None:
        return {
            "decision": REJECT,
            "score": 0.0,
            "reason": "empty-library",
            "item": None,
        }

    s = best_s
    score = round(s["ratio"], 3)

    # FIX 1: a squash-exact title is decisive on its own (handles run-together
    # names where token overlap is structurally zero). Length-guarded so a
    # 2-char title cannot auto-match.
    if s["exact"]:
        if len(cand_squash) >= config.MIN_EXACT_SQUASH_LEN:
            return {
                "decision": MATCH,
                "score": 1.0,
                "reason": "squash-exact",
                "item": best,
            }
        return {
            "decision": REVIEW,
            "score": 1.0,
            "reason": "squash-exact-too-short",
            "item": best,
        }

    # --- No distinctive tokens on either side: there is nothing to weigh, so a
    # human decides. Checked BEFORE the similarity gate because dropping digits
    # makes 'Up' and 'Up (2009)' produce identical token strings (ratio 1.0)
    # while carrying zero identifying words — a false perfect score.
    if s["cand_distinctive"] == 0 or s["item_distinctive"] == 0:
        return {
            "decision": REVIEW,
            "score": score,
            "reason": "no-distinctive-tokens",
            "item": best,
        }

    # --- similarity gate. A low score cannot become a match just because the
    # evidence set is small; smallness only justifies REVIEW when the release
    # is plausibly a truncated form of the target.
    if s["ratio"] >= config.MIN_SQUASH:
        if s["shared"] < config.MIN_SHARED_DISTINCTIVE:
            return {
                "decision": REJECT,
                "score": score,
                "reason": "no-shared-distinctive",
                "item": best,
            }
        if s["coverage"] < config.MIN_CAND_COVERAGE:
            # The release advertises words the target lacks -> a different work.
            # This is the guard that refuses 'Good.Hair' against 'Good'.
            return {
                "decision": REJECT,
                "score": score,
                "reason": "low-coverage",
                "item": best,
            }
        return {
            "decision": MATCH,
            "score": score,
            "reason": "guards-passed",
            "item": best,
        }

    # Below the similarity bar. REVIEW only when the release carries no title
    # word the target lacks — i.e. it could be a shortened form of the target
    # ('Batman' vs 'Batman Begins'). An unaccounted release word ('hair' vs
    # 'Good', 'reloaded' vs 'The Matrix') is positive evidence of a DIFFERENT
    # work, so it is refused outright.
    if s["unaccounted"] == 0:
        return {
            "decision": REVIEW,
            "score": score,
            "reason": "low-similarity-subset",
            "item": best,
        }
    return {
        "decision": REJECT,
        "score": score,
        "reason": "low-similarity",
        "item": best,
    }
