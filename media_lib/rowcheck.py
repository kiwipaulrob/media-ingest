"""Does a library row's Name match the episode its FILE actually contains?

Written after a real failure (2026-10-22). Placing a file into a **ghost** row
(one with no `Path`) makes Jellyfin's post-placement refresh resolve that slot
from the provider **by episode number** — and this library uses *scene* numbering
while TMDB does not. Two placements silently inherited a neighbour's metadata:

    S03E05  file "Elvis Presley - Elvis Presley"  ->  row named "Judas Priest: British Steel"
    S10E02  file "Tom Petty ... Damn the Torpedoes" ->  row named "Peter Gabriel: So"

The file was never wrong; only the row. Nothing detected it because the placement
gates check the media, not the metadata — so the check belongs here, next to the
placement, and the comparison lives in one place rather than in every script that
needs it.

Matching is deliberately loose: release filenames and Jellyfin row names differ in
punctuation and conjunctions ("Tom Petty and the Heartbreakers" vs "Tom Petty & The
Heartbreakers", "Soul II Soul Club Classics Vol 1" vs "Soul II Soul: Club Classics
Vol. One"). It compares distinctive tokens, so it catches a *different episode*
without tripping on formatting.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Set, Tuple

#: Tokens too generic to identify an episode on their own.
NOISE = {
    "the", "and", "with", "vol", "volume", "part", "classic", "albums",
    "album", "disc", "cd1", "cd2", "1080p", "720p", "x264", "ac3", "web",
}

#: `... S03E05 Elvis Presley - Elvis Presley.mkv` -> the identity text.
_EPISODE_RE = re.compile(r"S\d+\s*E\d+\s+(?P<ident>.+?)\.(?:mkv|mp4|avi|m4v|ts)$", re.I)


def identity_from_filename(filename: str) -> str:
    """The episode identity carried by a library filename (falls back to the name)."""
    name = filename.rsplit("/", 1)[-1]
    match = _EPISODE_RE.search(name)
    return (match.group("ident") if match else name).strip()


def tokens(text: Optional[str]) -> Set[str]:
    """Distinctive lowercase tokens, for a punctuation-insensitive comparison.

    Tokens of length >= 3 are kept so short album names still count — "Aja" is
    exactly 3 and was falsely reported as a mismatch when the floor was > 3.
    """
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower().replace("&", " and "))
    return {t for t in cleaned.split() if len(t) >= 3 and t not in NOISE}


def names_match(filename: str, row_name: Optional[str]) -> bool:
    """True when the row name and the file's identity share an identifying token."""
    file_tokens = tokens(identity_from_filename(filename))
    row_tokens = tokens(row_name)
    if not file_tokens or not row_tokens:
        # Nothing distinctive to compare — do not claim a mismatch.
        return True
    return bool(file_tokens.intersection(row_tokens))


def describe_mismatch(filename: str, row_name: Optional[str]) -> str:
    """A one-line explanation naming both sides, for a log or the check list."""
    return (f"row name {row_name!r} does not match the file "
            f"{identity_from_filename(filename)!r}")


def first_mismatch(rows: Iterable[Tuple[str, Optional[str]]]) -> Optional[str]:
    """(path, row_name) pairs -> the first mismatch description, or None."""
    for path, row_name in rows:
        if not names_match(path, row_name):
            return describe_mismatch(path, row_name)
    return None
